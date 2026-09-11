#!/usr/bin/env python3
"""Tests for preregistered B3 QB rush-attempt architecture."""
import unittest

from nfl.research import qb_scramble_opportunity_model as model


class DropbackAwareQBAttempts(unittest.TestCase):
    def test_only_scrambles_receive_dropback_rescaling(self):
        prediction = model.predict_attempts(
            rolling_designed_rush_attempts=2.0,
            rolling_scramble_attempts=3.0,
            rolling_kneel_attempts=1.0,
            dropback_window_scale=1.20,
        )
        self.assertAlmostEqual(prediction, 6.6)

    def test_unit_dropback_scale_collapses_to_b0_component_sum(self):
        prediction = model.predict_attempts(
            rolling_designed_rush_attempts=2.0,
            rolling_scramble_attempts=3.0,
            rolling_kneel_attempts=1.0,
            dropback_window_scale=1.0,
        )
        self.assertAlmostEqual(prediction, 6.0)

    def test_zero_scramble_role_makes_dropback_scale_irrelevant(self):
        low = model.predict_attempts(
            rolling_designed_rush_attempts=2.0,
            rolling_scramble_attempts=0.0,
            rolling_kneel_attempts=1.0,
            dropback_window_scale=0.5,
        )
        high = model.predict_attempts(
            rolling_designed_rush_attempts=2.0,
            rolling_scramble_attempts=0.0,
            rolling_kneel_attempts=1.0,
            dropback_window_scale=1.5,
        )
        self.assertAlmostEqual(low, high)

    def test_negative_inputs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            model.predict_attempts(
                rolling_designed_rush_attempts=1.0,
                rolling_scramble_attempts=2.0,
                rolling_kneel_attempts=1.0,
                dropback_window_scale=-0.1,
            )

    def test_nonfinite_inputs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            model.predict_attempts(
                rolling_designed_rush_attempts=1.0,
                rolling_scramble_attempts=float("nan"),
                rolling_kneel_attempts=1.0,
                dropback_window_scale=1.0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
