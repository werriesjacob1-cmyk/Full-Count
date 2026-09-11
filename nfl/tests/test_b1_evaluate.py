#!/usr/bin/env python3
"""Contract for B1 vs B0 same-population evaluation."""
import unittest

from nfl.research import b1_evaluate as ev


def row(*, b0_rec, b1_features, actual=5.0, position="WR",
        history_n=5, team_history_n=5, season=2025):
    return {
        "season": season,
        "week": 5,
        "season_type": "REG",
        "position": position,
        "history_n": history_n,
        "team_history_n": team_history_n,
        "b0_features": {
            "rolling_targets": 8.0,
            "rolling_receptions": b0_rec,
        },
        "features": b1_features,
        "target": {"receptions": actual},
    }


class SamePopulation(unittest.TestCase):
    def test_b0_and_b1_are_scored_on_exact_same_rows(self):
        rows = [
            row(
                b0_rec=4.0,
                b1_features={
                    "projected_team_targets": 32.0,
                    "prior_player_target_share": 0.20,
                    "prior_player_catch_rate": 0.75,
                },
                actual=5.0,
            ),
            row(
                b0_rec=2.0,
                b1_features={
                    "projected_team_targets": 30.0,
                    "prior_player_target_share": 0.10,
                    "prior_player_catch_rate": 0.50,
                },
                actual=1.0,
            ),
        ]
        m = ev.compare_market(
            rows, "receptions", test_season=2025,
            min_history=3, min_team_history=3,
        )
        self.assertEqual(m["comparison_n"], 2)
        self.assertEqual(m["b0_reference_n"], 2)
        self.assertEqual(m["coverage_ratio"], 1.0)
        # B0 errors: |-1|, |+1| => 1.0 MAE.
        self.assertAlmostEqual(m["b0_mae"], 1.0)
        # B1: 4.8 vs 5 => .2; 1.5 vs 1 => .5 => .35.
        self.assertAlmostEqual(m["b1_mae"], 0.35)
        self.assertAlmostEqual(m["delta_mae"], -0.65)

    def test_missing_b1_feature_reduces_coverage_not_b0_reference(self):
        rows = [
            row(
                b0_rec=4.0,
                b1_features={
                    "projected_team_targets": 32.0,
                    "prior_player_target_share": 0.20,
                    "prior_player_catch_rate": 0.75,
                },
            ),
            row(
                b0_rec=3.0,
                b1_features={
                    "projected_team_targets": 30.0,
                    "prior_player_target_share": None,
                    "prior_player_catch_rate": 0.7,
                },
            ),
        ]
        m = ev.compare_market(
            rows, "receptions", test_season=2025,
            min_history=3, min_team_history=3,
        )
        self.assertEqual(m["b0_reference_n"], 2)
        self.assertEqual(m["comparison_n"], 1)
        self.assertEqual(m["coverage_ratio"], 0.5)

    def test_wrong_position_is_not_in_reference_population(self):
        rows = [
            row(
                b0_rec=0.0,
                b1_features={
                    "projected_team_targets": 30.0,
                    "prior_player_target_share": 0.0,
                    "prior_player_catch_rate": 0.0,
                },
                actual=0.0,
                position="CB",
            )
        ]
        m = ev.compare_market(
            rows, "receptions", test_season=2025,
            min_history=3, min_team_history=3,
        )
        self.assertEqual(m["b0_reference_n"], 0)
        self.assertEqual(m["comparison_n"], 0)

    def test_min_team_history_is_enforced_without_looking_at_current_game(self):
        rows = [
            row(
                b0_rec=4.0,
                b1_features={
                    "projected_team_targets": 32.0,
                    "prior_player_target_share": 0.2,
                    "prior_player_catch_rate": 0.75,
                },
                team_history_n=2,
            )
        ]
        m = ev.compare_market(
            rows, "receptions", test_season=2025,
            min_history=3, min_team_history=3,
        )
        self.assertEqual(m["comparison_n"], 0)
        self.assertEqual(m["b0_reference_n"], 1)


class Suite(unittest.TestCase):
    def test_first_wave_contains_all_six_markets(self):
        report = ev.compare_first_wave(
            [], test_season=2025, min_history=3, min_team_history=3
        )
        self.assertEqual(
            set(report["markets"]),
            {
                "pass_attempts", "rush_attempts", "receptions",
                "passing_yards", "rushing_yards", "receiving_yards",
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
