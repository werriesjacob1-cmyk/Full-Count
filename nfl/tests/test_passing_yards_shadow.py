#!/usr/bin/env python3
"""Contracts for conservative B0 passing-yards shadow scoring."""
import unittest

from nfl.research import passing_yards_shadow as shadow


class PassingYardsShadowTests(unittest.TestCase):
    def test_same_team_role_continuity_passes(self):
        out = shadow.role_continuity(
            "PHI",
            [
                {"team": "PHI", "passing_yards": 200, "attempts": 30},
                {"team": "PHI", "passing_yards": 220, "attempts": 32},
            ],
        )
        self.assertTrue(out["role_continuity_gate_pass"])
        self.assertEqual(
            out["role_continuity_status"],
            "ROLE_CONTINUITY_CONFIRMED",
        )
        self.assertEqual(out["prior_team"], "PHI")

    def test_team_change_role_continuity_fails_closed(self):
        out = shadow.role_continuity(
            "ATL",
            [
                {"team": "DAL", "passing_yards": 200, "attempts": 30},
                {"team": "DAL", "passing_yards": 220, "attempts": 32},
            ],
        )
        self.assertFalse(out["role_continuity_gate_pass"])
        self.assertEqual(
            out["role_continuity_status"],
            "TEAM_CHANGE_ROLE_UNCERTAINTY",
        )
        self.assertEqual(out["prior_team"], "DAL")

    def test_missing_prior_team_fails_closed(self):
        out = shadow.role_continuity(
            "ATL",
            [{"passing_yards": 200, "attempts": 30}],
        )
        self.assertFalse(out["role_continuity_gate_pass"])
        self.assertEqual(
            out["role_continuity_status"],
            "UNKNOWN_PRIOR_TEAM",
        )

    def test_american_implied_probability(self):
        self.assertAlmostEqual(
            shadow.american_implied_probability(+150),
            0.4,
        )
        self.assertAlmostEqual(
            shadow.american_implied_probability(-200),
            2 / 3,
        )

    def test_two_sided_devig_sums_to_one(self):
        fair = shadow.devig_two_sided(-114, -114)
        self.assertAlmostEqual(fair["over"], 0.5)
        self.assertAlmostEqual(fair["under"], 0.5)
        self.assertAlmostEqual(fair["over"] + fair["under"], 1.0)

    def test_current_b0_uses_last_five_prior_appearances(self):
        history = [
            {"season": 2025, "week": 1, "passing_yards": 999, "attempts": 50},
            {"season": 2025, "week": 2, "passing_yards": 200, "attempts": 30},
            {"season": 2025, "week": 3, "passing_yards": 210, "attempts": 31},
            {"season": 2025, "week": 4, "passing_yards": 220, "attempts": 32},
            {"season": 2025, "week": 5, "passing_yards": 230, "attempts": 33},
            {"season": 2025, "week": 6, "passing_yards": 240, "attempts": 34},
        ]
        out = shadow.current_b0_projection(history)
        self.assertEqual(out["history_n_used"], 5)
        self.assertAlmostEqual(out["projection"], 220.0)
        self.assertAlmostEqual(out["rolling_attempts"], 32.0)

    def test_b0_requires_three_prior_appearances(self):
        with self.assertRaisesRegex(ValueError, "three"):
            shadow.current_b0_projection([
                {"passing_yards": 200, "attempts": 30},
                {"passing_yards": 210, "attempts": 31},
            ])

    def test_b0_requires_positive_prior_attempt_role(self):
        with self.assertRaisesRegex(ValueError, "attempt"):
            shadow.current_b0_projection([
                {"passing_yards": 0, "attempts": 0},
                {"passing_yards": 0, "attempts": 0},
                {"passing_yards": 0, "attempts": 0},
            ])

    def test_empirical_probability_uses_laplace_smoothing(self):
        out = shadow.empirical_side_probabilities(
            projection=100,
            line=105,
            residuals=[-10, 0, 10],
        )
        self.assertAlmostEqual(out["over"], 0.4)
        self.assertAlmostEqual(out["under"], 0.6)
        self.assertEqual(out["residual_n"], 3)

    def test_shadow_score_never_labels_public_play(self):
        out = shadow.score_shadow_candidate(
            projection=240,
            line=230.5,
            over_odds=-110,
            under_odds=-110,
            residuals=[-20, -10, 0, 10, 20],
        )
        self.assertEqual(out["decision_status"], "SHADOW_ONLY")
        self.assertIn(out["research_direction"], {"OVER", "UNDER", "NEUTRAL"})
        self.assertNotIn("PLAY", out.values())


if __name__ == "__main__":
    unittest.main(verbosity=2)
