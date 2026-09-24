#!/usr/bin/env python3
"""Tests for the unit-consistent pregame target-share stage (Mission 10)
and the locked holdout's statistics helpers."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

from nfl.research.pregame_target_share import (
    PregameTargetShareError,
    team_targets_per_dropback,
    unit_consistent_expected_receptions,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engineering" / "nfl_pregame_target_share_20260923"))
from stats_lib import (  # noqa: E402
    player_clustered_diff_ci,
    poisson_brier,
    poisson_log_score,
    poisson_prob_over,
)


class TeamTargetsPerDropbackTests(unittest.TestCase):
    def test_ratio_of_sums_not_mean_of_ratios(self):
        # (season, week, dropbacks, team_targets). Mean of ratios would be
        # (0.5 + 1.0) / 2 = 0.75; ratio of sums is 45 / 50 = 0.9.
        games = [(2025, 1, 10.0, 5.0), (2025, 2, 40.0, 40.0)]
        result = team_targets_per_dropback(games, target_season=2025, target_week=3)
        self.assertAlmostEqual(result["ratio"], 0.9)
        self.assertEqual(result["games_used"], 2)

    def test_never_uses_the_target_week_or_later(self):
        games = [(2025, w, 40.0, 30.0) for w in range(1, 4)] + [(2025, 4, 40.0, 40.0), (2025, 5, 40.0, 40.0)]
        result = team_targets_per_dropback(games, target_season=2025, target_week=4)
        self.assertAlmostEqual(result["ratio"], 0.75)
        self.assertEqual(result["games_used"], 3)

    def test_window_keeps_only_the_most_recent_prior_games(self):
        games = [(2024, w, 40.0, 20.0) for w in range(1, 10)] + [(2025, 1, 40.0, 36.0), (2025, 2, 40.0, 36.0)]
        result = team_targets_per_dropback(games, target_season=2025, target_week=3, window=2)
        self.assertAlmostEqual(result["ratio"], 0.9)

    def test_crosses_season_boundary(self):
        games = [(2024, 17, 40.0, 32.0), (2024, 18, 40.0, 32.0)]
        result = team_targets_per_dropback(games, target_season=2025, target_week=1)
        self.assertAlmostEqual(result["ratio"], 0.8)

    def test_no_prior_games_abstains_without_a_constant(self):
        result = team_targets_per_dropback([], target_season=2025, target_week=1)
        self.assertIsNone(result["ratio"])
        self.assertEqual(result["reason"], "NO_REAL_PRIOR_TEAM_DROPBACKS")

    def test_negative_real_volume_fails_closed(self):
        with self.assertRaises(PregameTargetShareError):
            team_targets_per_dropback([(2025, 1, -3.0, 10.0)], target_season=2025, target_week=2)

    def test_non_positive_window_rejected(self):
        with self.assertRaises(PregameTargetShareError):
            team_targets_per_dropback([], target_season=2025, target_week=2, window=0)


class UnitConsistentExpectedReceptionsTests(unittest.TestCase):
    def test_applies_share_to_team_targets_not_dropbacks(self):
        result = unit_consistent_expected_receptions(
            predicted_team_dropbacks=40.0, targets_per_dropback=0.8, target_share=0.25, catch_rate=0.6,
        )
        self.assertAlmostEqual(result["expected_targets"], 8.0)
        self.assertAlmostEqual(result["projection"], 4.8)

    def test_equals_legacy_chain_scaled_by_the_ratio(self):
        legacy = 40.0 * 0.25 * 0.6
        result = unit_consistent_expected_receptions(
            predicted_team_dropbacks=40.0, targets_per_dropback=0.83, target_share=0.25, catch_rate=0.6,
        )
        self.assertAlmostEqual(result["projection"], legacy * 0.83)

    def test_missing_ratio_abstains(self):
        result = unit_consistent_expected_receptions(
            predicted_team_dropbacks=40.0, targets_per_dropback=None, target_share=0.25, catch_rate=0.6,
        )
        self.assertIsNone(result["projection"])
        self.assertEqual(result["reason"], "MISSING_REQUIRED_INPUT")

    def test_out_of_range_inputs_abstain_with_reason(self):
        cases = [
            ({"predicted_team_dropbacks": 0.0}, "NON_POSITIVE_TEAM_DROPBACKS"),
            ({"targets_per_dropback": 0.0}, "TARGETS_PER_DROPBACK_OUT_OF_RANGE"),
            ({"target_share": 1.2}, "TARGET_SHARE_OUT_OF_RANGE"),
            ({"catch_rate": 0.0}, "CATCH_RATE_OUT_OF_RANGE"),
        ]
        base = {"predicted_team_dropbacks": 40.0, "targets_per_dropback": 0.8, "target_share": 0.25, "catch_rate": 0.6}
        for override, reason in cases:
            result = unit_consistent_expected_receptions(**{**base, **override})
            self.assertIsNone(result["projection"])
            self.assertEqual(result["reason"], reason)


class StatsLibTests(unittest.TestCase):
    def test_poisson_prob_over_matches_closed_form(self):
        lam = 3.0
        expected = 1.0 - math.exp(-lam) * (1 + lam + lam ** 2 / 2)
        self.assertAlmostEqual(poisson_prob_over(2.5, lam), expected)

    def test_brier_and_log_score_prefer_the_better_center(self):
        self.assertLess(poisson_brier(4.0, 4.0), poisson_brier(8.0, 4.0))
        self.assertLess(poisson_log_score(4.0, 4.0), poisson_log_score(8.0, 4.0))

    def test_clustered_ci_resamples_whole_players(self):
        rows = [{"player_id": f"p{i}", "a": 1.0, "b": 0.0} for i in range(10)]
        result = player_clustered_diff_ci(rows, lambda r: r["a"], lambda r: r["b"], n_resamples=200)
        self.assertAlmostEqual(result["point"], 1.0)
        self.assertEqual(result["ci95"], [1.0, 1.0])
        self.assertEqual(result["n_clusters"], 10)

    def test_clustered_ci_is_deterministic_for_a_seed(self):
        rows = [{"player_id": f"p{i % 7}", "a": float(i % 3), "b": float(i % 2)} for i in range(50)]
        one = player_clustered_diff_ci(rows, lambda r: r["a"], lambda r: r["b"], n_resamples=300)
        two = player_clustered_diff_ci(rows, lambda r: r["a"], lambda r: r["b"], n_resamples=300)
        self.assertEqual(one, two)

    def test_clustered_ci_needs_two_clusters(self):
        with self.assertRaises(ValueError):
            player_clustered_diff_ci([{"player_id": "p", "a": 1.0, "b": 0.0}], lambda r: r["a"], lambda r: r["b"])


if __name__ == "__main__":
    unittest.main()
