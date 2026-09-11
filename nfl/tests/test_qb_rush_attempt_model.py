#!/usr/bin/env python3
"""Tests for the preregistered B2-QB rush-attempt decomposition."""
import unittest

from nfl.research import qb_rush_attempt_model as model


class ComponentAwareQBAttempts(unittest.TestCase):
    def test_only_designed_rushes_receive_team_volume_rescaling(self):
        prediction = model.predict_attempts(
            rolling_designed_rush_attempts=4.0,
            rolling_scramble_attempts=3.0,
            rolling_kneel_attempts=2.0,
            team_window_scale=1.25,
        )
        self.assertAlmostEqual(prediction, 10.0)

    def test_unit_scale_collapses_exactly_to_component_sum(self):
        prediction = model.predict_attempts(
            rolling_designed_rush_attempts=4.0,
            rolling_scramble_attempts=3.0,
            rolling_kneel_attempts=2.0,
            team_window_scale=1.0,
        )
        self.assertAlmostEqual(prediction, 9.0)

    def test_zero_designed_role_makes_team_scale_irrelevant(self):
        a = model.predict_attempts(
            rolling_designed_rush_attempts=0.0,
            rolling_scramble_attempts=2.5,
            rolling_kneel_attempts=1.0,
            team_window_scale=0.5,
        )
        b = model.predict_attempts(
            rolling_designed_rush_attempts=0.0,
            rolling_scramble_attempts=2.5,
            rolling_kneel_attempts=1.0,
            team_window_scale=1.8,
        )
        self.assertAlmostEqual(a, b)

    def test_negative_attempt_component_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            model.predict_attempts(
                rolling_designed_rush_attempts=-1.0,
                rolling_scramble_attempts=2.0,
                rolling_kneel_attempts=1.0,
                team_window_scale=1.0,
            )

    def test_negative_team_scale_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            model.predict_attempts(
                rolling_designed_rush_attempts=1.0,
                rolling_scramble_attempts=2.0,
                rolling_kneel_attempts=1.0,
                team_window_scale=-0.5,
            )

    def test_nonfinite_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            model.predict_attempts(
                rolling_designed_rush_attempts=1.0,
                rolling_scramble_attempts=float("nan"),
                rolling_kneel_attempts=1.0,
                team_window_scale=1.0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
