#!/usr/bin/env python3
"""Unit tests for the new snap-share gating helper introduced by workstream
`NFL-OPPORTUNITY-ABLATION-2024-HOLDOUT-20260923`
(`engineering/nfl_opportunity_ablation_2024_holdout_20260923/gating.py`).

This is the one piece of genuinely new predictive logic this diagnostic
workstream introduces (everything else reuses `nfl/research/receptions_
team_opportunity_challenger.py` read-only, unmodified). The gating module
lives outside the `nfl` package (in `engineering/...`, matching this
workstream's other evidence artifacts), so it is loaded here by explicit
file path via `importlib`, not a normal package import.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATING_MODULE_PATH = (
    REPO_ROOT / "engineering" / "nfl_opportunity_ablation_2024_holdout_20260923" / "gating.py"
)
_spec = importlib.util.spec_from_file_location("opportunity_ablation_2024_gating", GATING_MODULE_PATH)
gating_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gating_mod)  # type: ignore[union-attr]

gated_snap_informed_target_share = gating_mod.gated_snap_informed_target_share

from nfl.research.receptions_team_opportunity_challenger import TeamOpportunityChallengerError


class GatedSnapInformedTargetShareTests(unittest.TestCase):
    def test_ratio_outside_band_applies_the_real_adjustment(self):
        # Real role-change scenario: snap share nearly doubled (0.40 -> 0.75),
        # ratio 1.875, outside a [0.7, 1.4] gate band.
        target_share_info = {"estimate": 0.15, "n_current_season_games": 1}
        snap_share_info = {
            "n_current_season_games": 2, "current_season_mean": 0.75, "prior_season_value": 0.40,
        }
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.7, gate_high=1.4,
        )
        self.assertTrue(result["gate_triggered"])
        self.assertAlmostEqual(result["gate_raw_ratio"], 1.875, places=6)
        self.assertEqual(result["gate_reason"], "OUTSIDE_GATE_BAND")
        self.assertTrue(result["snap_role_change_applied"])
        self.assertAlmostEqual(result["estimate"], 0.15 * 1.875, places=6)

    def test_ratio_inside_band_leaves_target_share_unadjusted(self):
        # ratio = 0.50/0.45 = 1.111, inside [0.7, 1.4] -- a small real
        # fluctuation, not evidence of a genuine role change.
        target_share_info = {"estimate": 0.20, "n_current_season_games": 3}
        snap_share_info = {
            "n_current_season_games": 2, "current_season_mean": 0.50, "prior_season_value": 0.45,
        }
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.7, gate_high=1.4,
        )
        self.assertFalse(result["gate_triggered"])
        self.assertEqual(result["gate_reason"], "WITHIN_GATE_BAND")
        self.assertAlmostEqual(result["gate_raw_ratio"], 0.50 / 0.45, places=6)
        self.assertFalse(result["snap_role_change_applied"])
        self.assertEqual(result["estimate"], 0.20)

    def test_ratio_exactly_at_band_edge_is_inside(self):
        # Boundary case: ratio exactly equal to gate_high must count as
        # "inside" (inclusive bounds), not silently rounded outward. Uses a
        # gate_high value the real division lands on exactly in floating
        # point (0.5 / 0.25 == 2.0 bit-for-bit), so this isolates the
        # boundary semantics from float-precision noise.
        target_share_info = {"estimate": 0.10, "n_current_season_games": 2}
        snap_share_info = {
            "n_current_season_games": 2, "current_season_mean": 0.5, "prior_season_value": 0.25,
        }
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.7, gate_high=2.0,
        )
        self.assertFalse(result["gate_triggered"])
        self.assertEqual(result["gate_raw_ratio"], 2.0)

    def test_no_real_snap_signal_never_fabricates_a_ratio(self):
        target_share_info = {"estimate": 0.15, "n_current_season_games": 0}
        snap_share_info = {"n_current_season_games": 0, "current_season_mean": None, "prior_season_value": 0.40}
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.7, gate_high=1.4,
        )
        self.assertFalse(result["gate_triggered"])
        self.assertIsNone(result["gate_raw_ratio"])
        self.assertEqual(result["gate_reason"], "NO_REAL_SNAP_SIGNAL")
        self.assertEqual(result["estimate"], 0.15)

    def test_missing_target_share_estimate_is_not_fabricated(self):
        target_share_info = {"estimate": None, "n_current_season_games": 0}
        snap_share_info = {"n_current_season_games": 2, "current_season_mean": 0.75, "prior_season_value": 0.40}
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.7, gate_high=1.4,
        )
        self.assertFalse(result["gate_triggered"])
        self.assertIsNone(result["estimate"])
        self.assertEqual(result["gate_reason"], "NO_TARGET_SHARE_ESTIMATE")

    def test_wider_gate_band_lets_the_same_ratio_pass_through_unadjusted(self):
        # Same real inputs as test_ratio_outside_band_applies_the_real_
        # adjustment (ratio 1.875), but the wider [0.5, 2.0] band swallows
        # it -- demonstrates the gate band, not the ratio, determines the
        # outcome for a moderate real role change.
        target_share_info = {"estimate": 0.15, "n_current_season_games": 1}
        snap_share_info = {
            "n_current_season_games": 2, "current_season_mean": 0.75, "prior_season_value": 0.40,
        }
        result = gated_snap_informed_target_share(
            target_share_info=target_share_info, snap_share_info=snap_share_info,
            gate_low=0.5, gate_high=2.0,
        )
        self.assertFalse(result["gate_triggered"])
        self.assertEqual(result["estimate"], 0.15)

    def test_invalid_gate_band_raises_rather_than_silently_swapping(self):
        target_share_info = {"estimate": 0.15, "n_current_season_games": 1}
        snap_share_info = {
            "n_current_season_games": 1, "current_season_mean": 0.75, "prior_season_value": 0.40,
        }
        with self.assertRaises(TeamOpportunityChallengerError):
            gated_snap_informed_target_share(
                target_share_info=target_share_info, snap_share_info=snap_share_info,
                gate_low=1.4, gate_high=0.7,
            )


if __name__ == "__main__":
    unittest.main()
