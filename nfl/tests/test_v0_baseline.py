#!/usr/bin/env python3
"""Scientific contract for the NFL V0 rolling-history baseline.

B0 is intentionally weak. Its job is to establish a reproducible floor that
future opportunity/coaching/matchup models must beat out of sample.

The evaluator must:
- consume ONLY prebuilt prior-only features
- never fall back to the current target when a feature is missing
- require an explicit minimum history
- report sample size and error, not a single flattering score
- keep each outcome family separate
"""
import math
import unittest

from nfl.research import v0_baseline as b0


class RollingBaselineMetrics(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "season": 2025, "week": 1, "season_type": "REG",
                "position": "WR", "history_n": 0,
                "features": {"rolling_targets": None,
                             "rolling_receptions": None,
                             "rolling_receiving_yards": None},
                "target": {"receptions": 4.0, "receiving_yards": 50.0},
            },
            {
                "season": 2025, "week": 2, "season_type": "REG",
                "position": "WR", "history_n": 1,
                "features": {"rolling_targets": 6.0,
                             "rolling_receptions": 4.0,
                             "rolling_receiving_yards": 50.0},
                "target": {"receptions": 6.0, "receiving_yards": 80.0},
            },
            {
                "season": 2025, "week": 3, "season_type": "REG",
                "position": "WR", "history_n": 2,
                "features": {"rolling_targets": 7.0,
                             "rolling_receptions": 5.0,
                             "rolling_receiving_yards": 65.0},
                "target": {"receptions": 3.0, "receiving_yards": 35.0},
            },
        ]

    def test_min_history_is_a_real_gate(self):
        m = b0.evaluate_stat(
            self.rows, "receptions", test_season=2025, min_history=2
        )
        self.assertEqual(m["n"], 1)
        self.assertEqual(m["mae"], 2.0)
        self.assertEqual(m["rmse"], 2.0)
        self.assertEqual(m["bias"], 2.0)  # predicted - actual = 5 - 3

    def test_no_target_fallback_when_feature_is_missing(self):
        m = b0.evaluate_stat(
            self.rows, "receptions", test_season=2025, min_history=0
        )
        # Week 1 has target=4 but rolling feature=None; it must be excluded,
        # not turned into a perfect self-prediction.
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["mae"], 2.0)

    def test_error_arithmetic_is_literal(self):
        m = b0.evaluate_stat(
            self.rows, "receiving_yards", test_season=2025, min_history=1
        )
        # Errors: 50-80=-30, 65-35=+30.
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["mae"], 30.0)
        self.assertEqual(m["rmse"], 30.0)
        self.assertEqual(m["bias"], 0.0)
        self.assertEqual(m["mean_prediction"], 57.5)
        self.assertEqual(m["mean_actual"], 57.5)

    def test_test_season_and_type_are_hard_filters(self):
        rows = self.rows + [{
            "season": 2024, "week": 18, "season_type": "REG", "position": "WR", "history_n": 10,
            "features": {"rolling_targets": 99.0, "rolling_receptions": 99.0},
            "target": {"receptions": 0.0},
        }, {
            "season": 2025, "week": 19, "season_type": "POST", "position": "WR", "history_n": 10,
            "features": {"rolling_targets": 99.0, "rolling_receptions": 99.0},
            "target": {"receptions": 0.0},
        }]
        m = b0.evaluate_stat(
            rows, "receptions", test_season=2025,
            test_season_type="REG", min_history=1
        )
        self.assertEqual(m["n"], 2)

    def test_irrelevant_positions_cannot_make_zero_props_look_easy(self):
        rows = list(self.rows) + [{
            "season": 2025, "week": 4, "season_type": "REG",
            "position": "CB", "history_n": 12,
            "features": {"rolling_targets": 0.0,
                         "rolling_receptions": 0.0,
                         "rolling_receiving_yards": 0.0},
            "target": {"receptions": 0.0, "receiving_yards": 0.0},
        }]
        m = b0.evaluate_stat(
            rows, "receptions", test_season=2025, min_history=1
        )
        self.assertEqual(
            m["n"], 2,
            "a CB with structural 0 receptions is not an eligible receiving-prop row"
        )

    def test_prior_role_gate_excludes_structural_zeroes_without_current_leakage(self):
        rows = [
            {
                "season": 2025, "week": 5, "season_type": "REG",
                "position": "WR", "history_n": 4,
                "features": {
                    "rolling_carries": 0.0,
                    "rolling_rushing_yards": 0.0,
                },
                "target": {"carries": 0.0, "rushing_yards": 0.0},
            },
            {
                "season": 2025, "week": 5, "season_type": "REG",
                "position": "RB", "history_n": 4,
                "features": {
                    "rolling_carries": 10.0,
                    "rolling_rushing_yards": 45.0,
                },
                "target": {"carries": 12.0, "rushing_yards": 50.0},
            },
        ]
        m = b0.evaluate_stat(
            rows, "carries", test_season=2025, min_history=1
        )
        self.assertEqual(
            m["n"], 1,
            "position alone is insufficient: prior opportunity must show a real role"
        )
        self.assertEqual(m["mae"], 2.0)

    def test_empty_evaluable_population_is_explicit(self):
        m = b0.evaluate_stat(
            self.rows, "receptions", test_season=2026, min_history=1
        )
        self.assertEqual(m["n"], 0)
        self.assertIsNone(m["mae"])
        self.assertIsNone(m["rmse"])
        self.assertIsNone(m["bias"])


class SuiteContract(unittest.TestCase):
    def test_first_wave_keeps_markets_separate_and_position_eligible(self):
        rows = [
            {
                "season": 2025, "week": 1, "season_type": "REG",
                "position": "QB", "history_n": 5,
                "features": {
                    "rolling_attempts": 30.0, "rolling_carries": 4.0,
                    "rolling_targets": 0.0, "rolling_receptions": 0.0,
                    "rolling_passing_yards": 250.0,
                    "rolling_rushing_yards": 20.0, "rolling_receiving_yards": 0.0,
                },
                "target": {
                    "attempts": 32.0, "carries": 5.0, "receptions": 0.0,
                    "passing_yards": 260.0, "rushing_yards": 25.0,
                    "receiving_yards": 0.0,
                },
            },
            {
                "season": 2025, "week": 1, "season_type": "REG",
                "position": "RB", "history_n": 5,
                "features": {
                    "rolling_attempts": 0.0, "rolling_carries": 12.0,
                    "rolling_targets": 4.0, "rolling_receptions": 3.0,
                    "rolling_passing_yards": 0.0,
                    "rolling_rushing_yards": 55.0, "rolling_receiving_yards": 25.0,
                },
                "target": {
                    "attempts": 0.0, "carries": 10.0, "receptions": 4.0,
                    "passing_yards": 0.0, "rushing_yards": 45.0,
                    "receiving_yards": 35.0,
                },
            },
            {
                "season": 2025, "week": 1, "season_type": "REG",
                "position": "WR", "history_n": 5,
                "features": {
                    "rolling_attempts": 0.0, "rolling_carries": 0.0,
                    "rolling_targets": 7.0, "rolling_receptions": 4.0,
                    "rolling_passing_yards": 0.0,
                    "rolling_rushing_yards": 0.0, "rolling_receiving_yards": 60.0,
                },
                "target": {
                    "attempts": 0.0, "carries": 0.0, "receptions": 5.0,
                    "passing_yards": 0.0, "rushing_yards": 0.0,
                    "receiving_yards": 72.0,
                },
            },
        ]
        report = b0.evaluate_first_wave(rows, test_season=2025, min_history=1)
        self.assertEqual(
            set(report["markets"]),
            {
                "pass_attempts", "rush_attempts", "receptions",
                "passing_yards", "rushing_yards", "receiving_yards",
            },
        )
        self.assertEqual(report["markets"]["pass_attempts"]["n"], 1)
        self.assertEqual(report["markets"]["rush_attempts"]["n"], 2)
        self.assertEqual(report["markets"]["receiving_yards"]["n"], 2)
        self.assertEqual(report["markets"]["receiving_yards"]["mae"], 11.0)

    def test_unknown_stat_is_refused_not_silently_zeroed(self):
        with self.assertRaisesRegex(ValueError, "unsupported V0 stat"):
            b0.evaluate_stat([], "fantasy_magic", test_season=2025)


if __name__ == "__main__":
    unittest.main(verbosity=2)
