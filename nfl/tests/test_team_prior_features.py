import unittest

from nfl.research.team_prior_features import TeamPriorFeatureError, build_prior_team_features


def row(season, week, team="KC", opponent="DEN", **overrides):
    value = {
        "game_id": f"{season}_{week:02d}_{opponent}_{team}",
        "season": season,
        "week": week,
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "attempts": 30,
        "passing_yards": 240,
        "sacks_suffered": 2,
        "passing_epa": 4.0,
        "carries": 28,
        "rushing_yards": 126,
    }
    value.update(overrides)
    return value


class TeamPriorFeatureTests(unittest.TestCase):
    def test_first_game_has_no_prior_features(self):
        built = build_prior_team_features([row(2025, 1)])
        self.assertEqual(built[0]["prior_games_n"], 0)
        self.assertIsNone(built[0]["prior_mean_offensive_play_proxy"])
        self.assertIs(built[0]["neutral_pass_rate_available"], False)
        self.assertIs(built[0]["true_pace_available"], False)
        self.assertIs(built[0]["expected_plays_available"], False)

    def test_current_game_never_enters_its_own_features(self):
        rows = [row(2025, 1, attempts=20, sacks_suffered=0, carries=20), row(2025, 2, attempts=60, sacks_suffered=5, carries=5)]
        built = build_prior_team_features(rows)
        week2 = next(item for item in built if item["week"] == 2)
        self.assertEqual(week2["prior_games_n"], 1)
        self.assertEqual(week2["prior_mean_attempts"], 20.0)
        self.assertEqual(week2["prior_mean_offensive_play_proxy"], 40.0)
        self.assertEqual(week2["prior_mean_dropback_share_proxy"], 0.5)

    def test_future_row_cannot_change_prior_target_features(self):
        first_two = [row(2025, 1, attempts=20), row(2025, 2, attempts=30)]
        baseline = build_prior_team_features(first_two)
        extended = build_prior_team_features(first_two + [row(2025, 3, attempts=70)])
        self.assertEqual(baseline, extended[:2])

    def test_input_order_does_not_create_lookahead(self):
        rows = [row(2025, 3, attempts=50), row(2025, 1, attempts=10), row(2025, 2, attempts=30)]
        built = build_prior_team_features(rows, rolling_window=2)
        self.assertEqual([item["week"] for item in built], [1, 2, 3])
        self.assertEqual(built[2]["prior_mean_attempts"], 20.0)

    def test_history_crosses_season_boundary_legitimately(self):
        rows = [row(2025, 18, attempts=20), row(2026, 1, attempts=40)]
        built = build_prior_team_features(rows)
        self.assertEqual(built[1]["prior_games_n"], 1)
        self.assertEqual(built[1]["prior_mean_attempts"], 20.0)

    def test_rolling_window_is_respected(self):
        rows = [row(2025, week, attempts=week * 10) for week in range(1, 5)]
        built = build_prior_team_features(rows, rolling_window=2)
        self.assertEqual(built[-1]["prior_games_n"], 2)
        self.assertEqual(built[-1]["prior_mean_attempts"], 25.0)

    def test_play_and_dropback_proxies_have_explicit_semantics(self):
        rows = [row(2025, 1, attempts=30, sacks_suffered=3, carries=27)]
        current = build_prior_team_features(rows + [row(2025, 2)])[1]
        self.assertEqual(current["prior_mean_dropback_proxy"], 33.0)
        self.assertEqual(current["prior_mean_offensive_play_proxy"], 60.0)
        self.assertAlmostEqual(current["prior_mean_dropback_share_proxy"], 0.55)
        self.assertEqual(current["feature_semantics"], "STRICTLY_PRIOR_REG_TEAM_BOX_SCORE")

    def test_duplicate_team_week_fails_closed(self):
        with self.assertRaisesRegex(TeamPriorFeatureError, "duplicate team/week"):
            build_prior_team_features([row(2025, 1), row(2025, 1, opponent="LV")])

    def test_postseason_is_rejected_not_misordered(self):
        with self.assertRaisesRegex(TeamPriorFeatureError, "REG rows only"):
            build_prior_team_features([row(2025, 1, season_type="POST")])

    def test_missing_and_invalid_source_fields_fail_closed(self):
        bad = row(2025, 1)
        del bad["sacks_suffered"]
        with self.assertRaisesRegex(TeamPriorFeatureError, "missing required"):
            build_prior_team_features([bad])
        with self.assertRaisesRegex(TeamPriorFeatureError, "attempts must be non-negative"):
            build_prior_team_features([row(2025, 1, attempts=-1)])
        with self.assertRaisesRegex(TeamPriorFeatureError, "offensive play proxy must be positive"):
            build_prior_team_features([row(2025, 1, attempts=0, sacks_suffered=0, carries=0)])

    def test_team_and_opponent_must_differ(self):
        with self.assertRaisesRegex(TeamPriorFeatureError, "must differ"):
            build_prior_team_features([row(2025, 1, opponent="KC")])

    def test_invalid_window_fails_closed(self):
        for value in [0, -1, True, 2.5]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(TeamPriorFeatureError, "positive integer"):
                    build_prior_team_features([row(2025, 1)], rolling_window=value)


if __name__ == "__main__":
    unittest.main()
