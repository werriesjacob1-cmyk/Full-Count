#!/usr/bin/env python3
"""Contracts for the frozen SHRINK50 team pass-attempt baseline."""
import math
import unittest

from nfl.research import team_attempt_shrink50 as shrink50


class TeamAttemptShrink50Tests(unittest.TestCase):
    def test_frozen_constants_cannot_drift(self):
        self.assertEqual(shrink50.TEAM_WINDOW, 5)
        self.assertEqual(shrink50.MIN_HISTORY, 3)
        self.assertEqual(shrink50.SHRINK_WEIGHT, 0.50)

    def test_prediction_is_exact_halfway_between_b0_and_prior_league_mean(self):
        result = shrink50.predict_from_prior(
            [40, 38, 42, 36, 44],
            [30, 32, 34, 36],
        )
        self.assertAlmostEqual(result["team_mean"], 40.0)
        self.assertAlmostEqual(result["league_prior_mean"], 33.0)
        self.assertAlmostEqual(result["prediction"], 36.5)

    def test_only_last_five_team_games_enter_b0(self):
        result = shrink50.predict_from_prior(
            [99, 99, 30, 31, 32, 33, 34],
            [30, 30, 30],
        )
        self.assertAlmostEqual(result["team_mean"], 32.0)
        self.assertAlmostEqual(result["prediction"], 31.0)
        self.assertEqual(result["team_history_used"], 5)

    def test_three_prior_team_games_are_allowed(self):
        result = shrink50.predict_from_prior(
            [30, 33, 36],
            [33, 33],
        )
        self.assertAlmostEqual(result["prediction"], 33.0)
        self.assertEqual(result["team_history_used"], 3)

    def test_fewer_than_three_team_games_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "team history"):
            shrink50.predict_from_prior([30, 31], [32, 33])

    def test_empty_league_prior_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "league prior"):
            shrink50.predict_from_prior([30, 31, 32], [])

    def test_negative_or_nonfinite_values_fail_closed(self):
        with self.assertRaises(ValueError):
            shrink50.predict_from_prior([30, -1, 32], [31, 32])
        with self.assertRaises(ValueError):
            shrink50.predict_from_prior([30, 31, 32], [31, math.inf])


if __name__ == "__main__":
    unittest.main(verbosity=2)
