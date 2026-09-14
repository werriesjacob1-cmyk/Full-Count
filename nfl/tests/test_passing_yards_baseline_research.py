#!/usr/bin/env python3
"""Contracts for the offline passing-yard baseline comparison."""
import unittest

from nfl.research.passing_yards_baseline_research import (
    paired_delta,
    rolling_predictions,
)


class RollingPredictionTests(unittest.TestCase):
    def test_zero_attempt_appearance_is_control_only_and_target_is_prior_safe(self):
        rows = [
            {"player_id": "p1", "season": 2025, "week": 1, "season_type": "REG", "game_id": "g1", "attempts": 10.0, "passing_yards": 100.0},
            {"player_id": "p1", "season": 2025, "week": 2, "season_type": "REG", "game_id": "g2", "attempts": 0.0, "passing_yards": 0.0},
            {"player_id": "p1", "season": 2025, "week": 3, "season_type": "REG", "game_id": "g3", "attempts": 20.0, "passing_yards": 200.0},
            {"player_id": "p1", "season": 2025, "week": 4, "season_type": "REG", "game_id": "g4", "attempts": 30.0, "passing_yards": 300.0},
            {"player_id": "p1", "season": 2025, "week": 5, "season_type": "REG", "game_id": "g5", "attempts": 50.0, "passing_yards": 999.0},
        ]
        result = rolling_predictions(rows)[-1]
        self.assertEqual(result["b0"], 150.0)
        self.assertEqual(result["c1_passing_role_last5"], 200.0)
        self.assertEqual(result["c2_attempts3_times_ypa8"], 200.0)

    def test_paired_delta_uses_only_common_population(self):
        rows = [
            {"actual": 100.0, "b0": 90.0, "challenger": 80.0},
            {"actual": 100.0, "b0": 0.0, "challenger": None},
        ]
        result = paired_delta(rows, "challenger")
        self.assertEqual(result["n"], 1)
        self.assertEqual(result["mae_delta_vs_b0"], 10.0)


if __name__ == "__main__":
    unittest.main()
