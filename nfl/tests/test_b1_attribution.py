#!/usr/bin/env python3
"""Tests for B1 component attribution identities.

These tests intentionally describe the scientific claim we need to prove:
B1's yard/reception predictions differ from B0 only through the player-
opportunity projection when both use the same prior-game window. The ratio-
of-sums efficiency term is therefore not an independent source of signal.
"""
import unittest

from nfl.research import b1_attribution as at


class OpportunityAttribution(unittest.TestCase):
    def test_team_window_rescaling_identity(self):
        row = {
            "b0_features": {
                "rolling_carries": 12.0,
                "rolling_rushing_yards": 54.0,
            },
            "features": {
                "projected_team_carries": 30.0,
                "prior_player_carry_share": 0.50,
                "prior_player_rush_yards_per_carry": 4.5,
            },
        }
        c = at.opportunity_components(row, "rushing_yards")
        self.assertAlmostEqual(c["b0_player_opportunity"], 12.0)
        self.assertAlmostEqual(c["player_window_team_volume"], 24.0)
        self.assertAlmostEqual(c["projected_team_volume"], 30.0)
        self.assertAlmostEqual(c["team_window_scale"], 1.25)
        self.assertAlmostEqual(c["b1_player_opportunity"], 15.0)

    def test_efficiency_layer_reconstructs_from_b0_and_opportunity_scale(self):
        row = {
            "b0_features": {
                "rolling_targets": 8.0,
                "rolling_receiving_yards": 64.0,
            },
            "features": {
                "projected_team_targets": 40.0,
                "prior_player_target_share": 0.25,
                "prior_player_yards_per_target": 8.0,
            },
        }
        # B1 targets = 10, so the same prior aggregate efficiency must turn
        # B0's 64 yards into 80 yards solely via the 10/8 opportunity scale.
        self.assertAlmostEqual(
            at.reconstruct_b1_from_b0(row, "receiving_yards"),
            80.0,
        )
        self.assertAlmostEqual(
            at.b1_prediction(row, "receiving_yards"),
            80.0,
        )
        self.assertAlmostEqual(
            at.efficiency_identity_error(row, "receiving_yards"),
            0.0,
        )

    def test_no_team_window_change_means_no_b1_change(self):
        row = {
            "b0_features": {
                "rolling_targets": 10.0,
                "rolling_receptions": 6.0,
            },
            "features": {
                # share .25 implies the player's historical team-volume
                # average was 10 / .25 = 40; current team projection is 40.
                "projected_team_targets": 40.0,
                "prior_player_target_share": 0.25,
                "prior_player_catch_rate": 0.60,
            },
        }
        c = at.opportunity_components(row, "receptions")
        self.assertAlmostEqual(c["team_window_scale"], 1.0)
        self.assertAlmostEqual(at.b1_prediction(row, "receptions"), 6.0)
        self.assertAlmostEqual(
            at.reconstruct_b1_from_b0(row, "receptions"),
            6.0,
        )

    def test_positive_b0_opportunity_with_zero_share_fails_closed(self):
        row = {
            "b0_features": {
                "rolling_carries": 5.0,
                "rolling_rushing_yards": 20.0,
            },
            "features": {
                "projected_team_carries": 25.0,
                "prior_player_carry_share": 0.0,
                "prior_player_rush_yards_per_carry": 4.0,
            },
        }
        with self.assertRaisesRegex(ValueError, "inconsistent positive"):
            at.opportunity_components(row, "rushing_yards")


if __name__ == "__main__":
    unittest.main(verbosity=2)
