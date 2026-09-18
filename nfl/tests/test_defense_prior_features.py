#!/usr/bin/env python3
import unittest

from nfl.research.defense_prior_features import (
    DefensePriorFeatureError,
    build_prior_defense_features,
)


def row(game_id, season, week, team, opponent, *, attempts, pass_yards, sacks, passing_epa, carries, rush_yards):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "attempts": attempts,
        "passing_yards": pass_yards,
        "sacks_suffered": sacks,
        "passing_epa": passing_epa,
        "carries": carries,
        "rushing_yards": rush_yards,
    }


class DefensePriorFeaturesTests(unittest.TestCase):
    def test_current_game_opponent_output_never_enters_current_features(self):
        rows = [
            row("g1", 2025, 1, "DEN", "KC", attempts=30, pass_yards=250, sacks=2, passing_epa=4, carries=25, rush_yards=110),
            row("g1", 2025, 1, "KC", "DEN", attempts=40, pass_yards=320, sacks=3, passing_epa=8, carries=20, rush_yards=85),
            row("g2", 2025, 2, "DEN", "LV", attempts=25, pass_yards=190, sacks=1, passing_epa=-2, carries=30, rush_yards=150),
            row("g2", 2025, 2, "LV", "DEN", attempts=50, pass_yards=410, sacks=5, passing_epa=12, carries=15, rush_yards=60),
        ]
        built = build_prior_defense_features(rows, rolling_window=5)
        den_w1 = next(r for r in built if r["team"] == "DEN" and r["week"] == 1)
        den_w2 = next(r for r in built if r["team"] == "DEN" and r["week"] == 2)

        self.assertEqual(den_w1["prior_games_n"], 0)
        self.assertIsNone(den_w1["prior_mean_opp_passing_yards_allowed"])
        self.assertEqual(den_w2["prior_games_n"], 1)
        # Week 2 DEN features contain only KC's Week 1 output (320), not LV Week 2 (410).
        self.assertEqual(den_w2["prior_mean_opp_passing_yards_allowed"], 320.0)
        self.assertEqual(den_w2["prior_mean_sacks_generated_proxy"], 3.0)

    def test_history_may_cross_season_boundary(self):
        rows = [
            row("old", 2025, 18, "KC", "DEN", attempts=35, pass_yards=280, sacks=1, passing_epa=5, carries=23, rush_yards=95),
            row("old", 2025, 18, "DEN", "KC", attempts=31, pass_yards=210, sacks=4, passing_epa=-3, carries=27, rush_yards=125),
            row("new", 2026, 1, "KC", "LV", attempts=38, pass_yards=300, sacks=2, passing_epa=7, carries=22, rush_yards=90),
            row("new", 2026, 1, "LV", "KC", attempts=29, pass_yards=205, sacks=3, passing_epa=-1, carries=28, rush_yards=130),
        ]
        built = build_prior_defense_features(rows)
        kc_2026 = next(r for r in built if r["team"] == "KC" and r["season"] == 2026)
        self.assertEqual(kc_2026["prior_games_n"], 1)
        self.assertEqual(kc_2026["prior_mean_opp_passing_yards_allowed"], 210.0)

    def test_nonreciprocal_game_fails_closed(self):
        rows = [
            row("g1", 2025, 1, "DEN", "KC", attempts=30, pass_yards=250, sacks=2, passing_epa=4, carries=25, rush_yards=110),
            row("g1", 2025, 1, "KC", "LV", attempts=40, pass_yards=320, sacks=3, passing_epa=8, carries=20, rush_yards=85),
        ]
        with self.assertRaisesRegex(DefensePriorFeatureError, "non-reciprocal"):
            build_prior_defense_features(rows)

    def test_incomplete_game_pair_fails_closed(self):
        rows = [
            row("g1", 2025, 1, "DEN", "KC", attempts=30, pass_yards=250, sacks=2, passing_epa=4, carries=25, rush_yards=110),
        ]
        with self.assertRaisesRegex(DefensePriorFeatureError, "exactly two"):
            build_prior_defense_features(rows)

    def test_postseason_rows_are_rejected(self):
        bad = row("g1", 2025, 1, "DEN", "KC", attempts=30, pass_yards=250, sacks=2, passing_epa=4, carries=25, rush_yards=110)
        bad["season_type"] = "POST"
        other = row("g1", 2025, 1, "KC", "DEN", attempts=40, pass_yards=320, sacks=3, passing_epa=8, carries=20, rush_yards=85)
        other["season_type"] = "POST"
        with self.assertRaisesRegex(DefensePriorFeatureError, "REG rows only"):
            build_prior_defense_features([bad, other])


if __name__ == "__main__":
    unittest.main()
