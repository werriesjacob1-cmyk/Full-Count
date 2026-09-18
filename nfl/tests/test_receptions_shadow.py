#!/usr/bin/env python3
"""Contracts for conservative B0 receptions shadow scoring."""
import unittest

from nfl.research import passing_yards_shadow
from nfl.research import receptions_shadow as shadow


class MarketMathReuseTests(unittest.TestCase):
    def test_devig_and_implied_probability_are_imported_not_duplicated(self):
        # Locks in the deliberate reuse decision: these are pure American-odds
        # math with zero receptions-specific logic, so receptions_shadow
        # imports the exact same functions passing_yards_shadow defines
        # rather than duplicating them.
        self.assertIs(
            shadow.american_implied_probability,
            passing_yards_shadow.american_implied_probability,
        )
        self.assertIs(shadow.devig_two_sided, passing_yards_shadow.devig_two_sided)


class ReceptionsShadowTests(unittest.TestCase):
    def test_american_implied_probability(self):
        self.assertAlmostEqual(shadow.american_implied_probability(+150), 0.4)
        self.assertAlmostEqual(shadow.american_implied_probability(-200), 2 / 3)

    def test_two_sided_devig_sums_to_one(self):
        fair = shadow.devig_two_sided(-114, -114)
        self.assertAlmostEqual(fair["over"], 0.5)
        self.assertAlmostEqual(fair["under"], 0.5)
        self.assertAlmostEqual(fair["over"] + fair["under"], 1.0)

    def test_current_b0_uses_last_five_prior_appearances(self):
        history = [
            {"season": 2025, "week": 1, "receptions": 12, "targets": 15},
            {"season": 2025, "week": 2, "receptions": 4, "targets": 6},
            {"season": 2025, "week": 3, "receptions": 5, "targets": 7},
            {"season": 2025, "week": 4, "receptions": 6, "targets": 8},
            {"season": 2025, "week": 5, "receptions": 7, "targets": 9},
            {"season": 2025, "week": 6, "receptions": 8, "targets": 10},
        ]
        out = shadow.current_b0_projection(history)
        self.assertEqual(out["history_n_used"], 5)
        self.assertAlmostEqual(out["projection"], 6.0)
        self.assertAlmostEqual(out["rolling_effective_targets"], 8.0)

    def test_b0_requires_three_prior_appearances(self):
        with self.assertRaisesRegex(ValueError, "three"):
            shadow.current_b0_projection([
                {"receptions": 4, "targets": 6},
                {"receptions": 5, "targets": 7},
            ])

    def test_b0_requires_positive_prior_receiving_role(self):
        with self.assertRaisesRegex(ValueError, "opportunity role"):
            shadow.current_b0_projection([
                {"receptions": 0, "targets": 0},
                {"receptions": 0, "targets": 0},
                {"receptions": 0, "targets": 0},
            ])

    def test_b0_role_gate_falls_back_to_receptions_when_targets_column_is_broken(self):
        # Simulates the confirmed 2003-2008 nflverse gap: targets reads 0 for
        # every prior appearance even though real receptions were recorded.
        # effective_targets = max(targets, receptions) must still clear the
        # positive-role gate instead of failing closed on broken metadata.
        out = shadow.current_b0_projection([
            {"receptions": 3, "targets": 0},
            {"receptions": 4, "targets": 0},
            {"receptions": 5, "targets": 0},
        ])
        self.assertAlmostEqual(out["rolling_effective_targets"], 4.0)
        self.assertAlmostEqual(out["projection"], 4.0)

    def test_empirical_probability_uses_laplace_smoothing(self):
        out = shadow.empirical_side_probabilities(
            projection=4.0,
            line=4.5,
            residuals=[-1, 0, 1],
        )
        self.assertAlmostEqual(out["over"], 0.4)
        self.assertAlmostEqual(out["under"], 0.6)
        self.assertEqual(out["residual_n"], 3)

    def test_shadow_score_never_labels_public_play(self):
        out = shadow.score_shadow_candidate(
            projection=5.5,
            line=4.5,
            over_odds=-110,
            under_odds=-110,
            residuals=[-2, -1, 0, 1, 2],
        )
        self.assertEqual(out["decision_status"], "SHADOW_ONLY")
        self.assertIn(out["research_direction"], {"OVER", "UNDER", "NEUTRAL"})
        self.assertNotIn("PLAY", out.values())

    def test_shadow_score_direction_follows_larger_edge(self):
        out = shadow.score_shadow_candidate(
            projection=8.0,
            line=4.5,
            over_odds=-110,
            under_odds=-110,
            residuals=[-1, 0, 1, 2, 3],
        )
        self.assertEqual(out["decision_status"], "SHADOW_ONLY")
        self.assertEqual(out["research_direction"], "OVER")
        self.assertGreater(out["research_edge"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
