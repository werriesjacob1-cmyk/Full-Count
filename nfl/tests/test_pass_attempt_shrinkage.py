#!/usr/bin/env python3
"""Tests for the frozen NFL team pass-attempt shrinkage challenger."""
import math
import unittest

from nfl.research import pass_attempt_shrinkage


class PassAttemptShrinkageTests(unittest.TestCase):
    def test_weight_is_frozen_at_half(self):
        self.assertEqual(pass_attempt_shrinkage.SHRINK_WEIGHT, 0.50)

    def test_prediction_is_exact_half_shrinkage(self):
        self.assertAlmostEqual(
            pass_attempt_shrinkage.predict_team_attempts(
                rolling_team_attempts=40.0,
                strictly_prior_league_mean=32.0,
            ),
            36.0,
        )

    def test_equal_inputs_are_preserved(self):
        self.assertAlmostEqual(
            pass_attempt_shrinkage.predict_team_attempts(
                rolling_team_attempts=33.25,
                strictly_prior_league_mean=33.25,
            ),
            33.25,
        )

    def test_negative_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            pass_attempt_shrinkage.predict_team_attempts(
                rolling_team_attempts=-1.0,
                strictly_prior_league_mean=33.0,
            )

    def test_non_finite_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            pass_attempt_shrinkage.predict_team_attempts(
                rolling_team_attempts=math.inf,
                strictly_prior_league_mean=33.0,
            )

    def test_none_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-numeric"):
            pass_attempt_shrinkage.predict_team_attempts(
                rolling_team_attempts=None,
                strictly_prior_league_mean=33.0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
