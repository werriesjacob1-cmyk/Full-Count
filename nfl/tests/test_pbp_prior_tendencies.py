import unittest

from nfl.research.pbp_prior_tendencies import PbpPriorTendencyError, build_prior_pbp_tendencies


def play(season, week, play_id, team="KC", opponent="DEN", **overrides):
    value = {
        "game_id": f"{season}_{week:02d}_{opponent}_{team}",
        "play_id": play_id,
        "season": season,
        "week": week,
        "season_type": "REG",
        "posteam": team,
        "defteam": opponent,
        "down": 1,
        "half_seconds_remaining": 900,
        "wp": 0.50,
        "qb_dropback": 1,
        "rush_attempt": 0,
        "qb_kneel": 0,
        "qb_spike": 0,
    }
    value.update(overrides)
    return value


class PbpPriorTendencyTests(unittest.TestCase):
    def test_first_game_has_no_prior_tendency(self):
        built = build_prior_pbp_tendencies([play(2025, 1, 1)])
        self.assertEqual(built[0]["prior_games_n"], 0)
        self.assertIsNone(built[0]["prior_neutral_dropback_rate_v1"])

    def test_current_game_never_enters_own_feature(self):
        rows = [
            play(2025, 1, 1, qb_dropback=1, rush_attempt=0),
            play(2025, 1, 2, qb_dropback=0, rush_attempt=1),
            play(2025, 2, 1, qb_dropback=1, rush_attempt=0),
        ]
        built = build_prior_pbp_tendencies(rows)
        week2 = next(row for row in built if row["week"] == 2)
        self.assertEqual(week2["prior_games_n"], 1)
        self.assertEqual(week2["prior_neutral_plays_n"], 2)
        self.assertEqual(week2["prior_neutral_dropbacks_n"], 1)
        self.assertEqual(week2["prior_neutral_dropback_rate_v1"], 0.5)

    def test_scramble_is_classified_once_as_dropback(self):
        rows = [play(2025, 1, 1, qb_dropback=1, rush_attempt=1), play(2025, 2, 1)]
        week2 = build_prior_pbp_tendencies(rows)[1]
        self.assertEqual(week2["prior_scrimmage_plays_n"], 1)
        self.assertEqual(week2["prior_dropbacks_n"], 1)
        self.assertEqual(week2["prior_neutral_dropback_rate_v1"], 1.0)

    def test_neutral_filter_boundaries_and_two_minute_exclusion(self):
        rows = [
            play(2025, 1, 1, wp=0.20),
            play(2025, 1, 2, wp=0.80, qb_dropback=0, rush_attempt=1),
            play(2025, 1, 3, wp=0.19),
            play(2025, 1, 4, half_seconds_remaining=120),
            play(2025, 1, 5, down=3),
            play(2025, 2, 1),
        ]
        week2 = build_prior_pbp_tendencies(rows)[1]
        self.assertEqual(week2["prior_scrimmage_plays_n"], 5)
        self.assertEqual(week2["prior_neutral_plays_n"], 2)
        self.assertEqual(week2["prior_neutral_dropbacks_n"], 1)

    def test_kneels_spikes_and_non_scrimmage_rows_are_ignored(self):
        rows = [
            play(2025, 1, 1, qb_dropback=0, rush_attempt=0, posteam="", defteam="", down="", wp="", half_seconds_remaining=""),
            play(2025, 1, 2, qb_dropback=0, rush_attempt=1, qb_kneel=1, posteam="", defteam="", down="", wp="", half_seconds_remaining=""),
            play(2025, 1, 3),
            play(2025, 2, 1),
        ]
        built = build_prior_pbp_tendencies(rows)
        self.assertEqual(built[1]["prior_scrimmage_plays_n"], 1)

    def test_weighted_rate_uses_prior_play_counts_not_mean_of_game_rates(self):
        rows = [
            play(2025, 1, 1),
            play(2025, 1, 2),
            play(2025, 2, 1, qb_dropback=0, rush_attempt=1),
            play(2025, 3, 1),
        ]
        week3 = build_prior_pbp_tendencies(rows)[2]
        self.assertEqual(week3["prior_neutral_plays_n"], 3)
        self.assertEqual(week3["prior_neutral_dropbacks_n"], 2)
        self.assertAlmostEqual(week3["prior_neutral_dropback_rate_v1"], 2 / 3)

    def test_history_crosses_season_boundary(self):
        rows = [play(2025, 18, 1), play(2026, 1, 1)]
        week1 = build_prior_pbp_tendencies(rows)[1]
        self.assertEqual(week1["prior_games_n"], 1)
        self.assertEqual(week1["prior_neutral_dropback_rate_v1"], 1.0)

    def test_future_game_does_not_change_earlier_features(self):
        base = [play(2025, 1, 1), play(2025, 2, 1)]
        self.assertEqual(build_prior_pbp_tendencies(base), build_prior_pbp_tendencies(base + [play(2025, 3, 1)])[:2])

    def test_duplicate_play_fails_closed(self):
        with self.assertRaisesRegex(PbpPriorTendencyError, "duplicate game/play"):
            build_prior_pbp_tendencies([play(2025, 1, 1), play(2025, 1, 1)])

    def test_postseason_and_bad_probabilities_fail_closed(self):
        with self.assertRaisesRegex(PbpPriorTendencyError, "REG rows only"):
            build_prior_pbp_tendencies([play(2025, 1, 1, season_type="POST")])
        with self.assertRaisesRegex(PbpPriorTendencyError, "wp must be between"):
            build_prior_pbp_tendencies([play(2025, 1, 1, wp=1.1)])

    def test_missing_columns_and_bad_binary_fail_closed(self):
        value = play(2025, 1, 1)
        del value["wp"]
        with self.assertRaisesRegex(PbpPriorTendencyError, "missing required"):
            build_prior_pbp_tendencies([value])
        with self.assertRaisesRegex(PbpPriorTendencyError, "must be 0 or 1"):
            build_prior_pbp_tendencies([play(2025, 1, 1, qb_dropback=2)])

    def test_output_definition_is_explicit(self):
        built = build_prior_pbp_tendencies([play(2025, 1, 1), play(2025, 2, 1)])
        definition = built[1]["neutral_definition"]
        self.assertEqual(definition["downs"], [1, 2])
        self.assertEqual(definition["wp_low_inclusive"], 0.20)
        self.assertEqual(definition["wp_high_inclusive"], 0.80)
        self.assertEqual(definition["minimum_half_seconds_exclusive"], 120)
        self.assertIs(definition["scrambles_classified_as_dropbacks"], True)
        self.assertIs(built[1]["true_pace_available"], False)
        self.assertIs(built[1]["expected_plays_available"], False)


if __name__ == "__main__":
    unittest.main()
