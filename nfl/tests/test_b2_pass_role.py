#!/usr/bin/env python3
"""Tests for preregistered B2-ROLE pass-attempt challenger."""
import unittest

from nfl.research import b2_pass_role


class B2PassRoleTests(unittest.TestCase):
    def test_aligned_history_preserves_frozen_b0(self):
        prediction = b2_pass_role.predict_attempts(
            b0_prediction=31.2,
            projected_team_pass_attempts=36.0,
            last_attempt_share=0.95,
            alignment_category="ALIGNED",
        )
        self.assertAlmostEqual(prediction, 31.2)

    def test_missed_games_use_current_team_volume_times_last_share(self):
        prediction = b2_pass_role.predict_attempts(
            b0_prediction=24.0,
            projected_team_pass_attempts=35.0,
            last_attempt_share=0.80,
            alignment_category="MISSED_GAMES",
        )
        self.assertAlmostEqual(prediction, 28.0)

    def test_team_change_uses_same_preregistered_role_formula(self):
        prediction = b2_pass_role.predict_attempts(
            b0_prediction=20.0,
            projected_team_pass_attempts=40.0,
            last_attempt_share=0.75,
            alignment_category="TEAM_CHANGE",
        )
        self.assertAlmostEqual(prediction, 30.0)

    def test_window_length_mismatch_uses_role_formula(self):
        prediction = b2_pass_role.predict_attempts(
            b0_prediction=18.0,
            projected_team_pass_attempts=32.0,
            last_attempt_share=0.50,
            alignment_category="WINDOW_LENGTH_MISMATCH",
        )
        self.assertAlmostEqual(prediction, 16.0)

    def test_share_outside_unit_interval_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "share"):
            b2_pass_role.predict_attempts(
                b0_prediction=30.0,
                projected_team_pass_attempts=35.0,
                last_attempt_share=1.01,
                alignment_category="MISSED_GAMES",
            )

    def test_no_history_is_not_a_valid_challenger_row(self):
        with self.assertRaisesRegex(ValueError, "NO_HISTORY"):
            b2_pass_role.predict_attempts(
                b0_prediction=30.0,
                projected_team_pass_attempts=35.0,
                last_attempt_share=0.9,
                alignment_category="NO_HISTORY",
            )

    def test_unknown_alignment_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "alignment"):
            b2_pass_role.predict_attempts(
                b0_prediction=30.0,
                projected_team_pass_attempts=35.0,
                last_attempt_share=0.9,
                alignment_category="UNKNOWN",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
