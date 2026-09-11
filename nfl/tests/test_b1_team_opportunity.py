#!/usr/bin/env python3
"""Contract tests for B1: team opportunity -> player share -> outcome.

B1 is the first causal improvement over B0. It must predict TEAM volume first,
then allocate that volume to the player using PRIOR shares, then apply PRIOR
efficiency. Current-game target usage must never be consulted.
"""
import unittest

from nfl.research import b1_team_opportunity as b1


class OpportunityDecomposition(unittest.TestCase):
    def test_qb_attempts_are_team_attempts_times_prior_qb_share(self):
        features = {
            "projected_team_pass_attempts": 36.0,
            "prior_player_pass_attempt_share": 0.94,
        }
        self.assertAlmostEqual(
            b1.predict_pass_attempts(features),
            33.84,
            places=8,
        )

    def test_rush_attempts_are_team_carries_times_prior_player_share(self):
        features = {
            "projected_team_carries": 28.0,
            "prior_player_carry_share": 0.55,
        }
        self.assertAlmostEqual(
            b1.predict_rush_attempts(features),
            15.4,
            places=8,
        )

    def test_receptions_chain_uses_targets_then_catch_rate(self):
        features = {
            "projected_team_targets": 34.0,
            "prior_player_target_share": 0.25,
            "prior_player_catch_rate": 0.70,
        }
        self.assertAlmostEqual(
            b1.predict_receptions(features),
            5.95,
            places=8,
        )

    def test_receiving_yards_chain_uses_targets_share_and_yards_per_target(self):
        features = {
            "projected_team_targets": 34.0,
            "prior_player_target_share": 0.25,
            "prior_player_yards_per_target": 8.4,
        }
        self.assertAlmostEqual(
            b1.predict_receiving_yards(features),
            71.4,
            places=8,
        )

    def test_passing_yards_chain_uses_attempts_and_prior_yards_per_attempt(self):
        features = {
            "projected_team_pass_attempts": 36.0,
            "prior_player_pass_attempt_share": 0.94,
            "prior_player_pass_yards_per_attempt": 7.3,
        }
        self.assertAlmostEqual(
            b1.predict_passing_yards(features),
            247.032,
            places=8,
        )

    def test_rushing_yards_chain_uses_carries_share_and_prior_yards_per_carry(self):
        features = {
            "projected_team_carries": 28.0,
            "prior_player_carry_share": 0.55,
            "prior_player_rush_yards_per_carry": 4.6,
        }
        self.assertAlmostEqual(
            b1.predict_rushing_yards(features),
            70.84,
            places=8,
        )


class FailClosed(unittest.TestCase):
    def test_missing_component_does_not_become_zero(self):
        with self.assertRaisesRegex(ValueError, "missing B1 feature"):
            b1.predict_receiving_yards({
                "projected_team_targets": 34.0,
                "prior_player_target_share": 0.25,
            })

    def test_nonfinite_component_is_refused(self):
        with self.assertRaisesRegex(ValueError, "non-finite B1 feature"):
            b1.predict_pass_attempts({
                "projected_team_pass_attempts": float("nan"),
                "prior_player_pass_attempt_share": 0.9,
            })

    def test_share_outside_probability_range_is_refused(self):
        with self.assertRaisesRegex(ValueError, "share outside"):
            b1.predict_rush_attempts({
                "projected_team_carries": 28.0,
                "prior_player_carry_share": 1.2,
            })

    def test_negative_team_volume_is_refused(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            b1.predict_pass_attempts({
                "projected_team_pass_attempts": -2.0,
                "prior_player_pass_attempt_share": 0.9,
            })


if __name__ == "__main__":
    unittest.main(verbosity=2)
