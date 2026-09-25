#!/usr/bin/env python3
"""Tests for nfl.research.receptions_frozen_challenger."""
from __future__ import annotations

import unittest

from nfl.research.receptions_frozen_challenger import (
    FROZEN_NB_FIT,
    FrozenChallengerError,
    compare_b0_vs_frozen_challenger,
    negative_binomial_side_probabilities,
)
from nfl.research.receptions_outcome_distribution import negative_binomial_pmf


class FrozenFitProvenanceTests(unittest.TestCase):
    def test_frozen_fit_matches_pr_159s_own_reported_training_population(self):
        # PR #159 (independently reviewed, merged) reported 85,720 scored
        # training rows on season <= 2022 and 12,095 held-out rows.
        self.assertEqual(FROZEN_NB_FIT["n_train_rows_scored_total"], 85720)
        self.assertEqual(FROZEN_NB_FIT["n_held_out_rows"], 12095)
        self.assertEqual(FROZEN_NB_FIT["train_partition"], "season <= 2022")
        self.assertEqual(FROZEN_NB_FIT["status"], "RESEARCH_ONLY_NOT_PROMOTED")

    def test_alpha_is_a_real_positive_dispersion_value(self):
        self.assertGreater(FROZEN_NB_FIT["alpha"], 0.0)
        self.assertLess(FROZEN_NB_FIT["alpha"], 1.0)


class NegativeBinomialSideProbabilitiesTests(unittest.TestCase):
    def test_over_under_push_sum_to_one(self):
        for projection, line in [(2.5, 2.5), (0.8, 0.5), (6.0, 5.5), (1.0, 1.0), (10.0, 9.5)]:
            result = negative_binomial_side_probabilities(projection=projection, line=line)
            total = result["over"] + result["under"] + result["push"]
            self.assertAlmostEqual(total, 1.0, places=9, msg=(projection, line, result))

    def test_half_integer_line_has_zero_push(self):
        result = negative_binomial_side_probabilities(projection=3.0, line=2.5)
        self.assertEqual(result["push"], 0.0)

    def test_integer_line_can_have_nonzero_push(self):
        result = negative_binomial_side_probabilities(projection=3.0, line=3.0)
        self.assertGreater(result["push"], 0.0)

    def test_matches_hand_computed_pmf_sum_using_the_real_frozen_alpha(self):
        projection, line, alpha = 4.0, 3.5, FROZEN_NB_FIT["alpha"]
        expected_under = sum(negative_binomial_pmf(k, projection, alpha) for k in range(0, 4))
        result = negative_binomial_side_probabilities(projection=projection, line=line)
        self.assertAlmostEqual(result["under"], expected_under, places=9)
        self.assertAlmostEqual(result["over"], 1.0 - expected_under, places=9)

    def test_higher_projection_gives_lower_under_probability_at_a_fixed_line(self):
        low = negative_binomial_side_probabilities(projection=1.0, line=2.5)
        high = negative_binomial_side_probabilities(projection=6.0, line=2.5)
        self.assertGreater(low["under"], high["under"])

    def test_caller_can_override_alpha_without_mutating_the_frozen_constant(self):
        frozen_alpha = FROZEN_NB_FIT["alpha"]
        negative_binomial_side_probabilities(projection=3.0, line=2.5, alpha=0.5)
        self.assertEqual(FROZEN_NB_FIT["alpha"], frozen_alpha)

    def test_rejects_nonpositive_projection(self):
        with self.assertRaises(FrozenChallengerError):
            negative_binomial_side_probabilities(projection=0.0, line=2.5)
        with self.assertRaises(FrozenChallengerError):
            negative_binomial_side_probabilities(projection=-1.0, line=2.5)

    def test_rejects_negative_line(self):
        with self.assertRaises(FrozenChallengerError):
            negative_binomial_side_probabilities(projection=3.0, line=-0.5)

    def test_rejects_nonpositive_alpha_override(self):
        with self.assertRaises(FrozenChallengerError):
            negative_binomial_side_probabilities(projection=3.0, line=2.5, alpha=0.0)


class CompareB0VsFrozenChallengerTests(unittest.TestCase):
    def test_basic_comparison_shape(self):
        result = compare_b0_vs_frozen_challenger(
            projection=4.2, line=3.5, b0_over=0.55, b0_under=0.45
        )
        self.assertEqual(result["b0"], {"over": 0.55, "under": 0.45})
        self.assertIn("challenger", result)
        self.assertAlmostEqual(
            result["absolute_over_probability_gap"],
            abs(0.55 - result["challenger"]["over"]),
            places=9,
        )
        self.assertEqual(result["status"], "RESEARCH_ONLY_NOT_PROMOTED")
        self.assertNotIn("over_ev", result["challenger"])
        self.assertNotIn("under_ev", result["challenger"])

    def test_ev_only_attached_when_a_real_price_is_supplied(self):
        result = compare_b0_vs_frozen_challenger(
            projection=4.2, line=3.5, b0_over=0.55, b0_under=0.45,
            over_price=-120, under_price=100,
        )
        self.assertIn("over_ev", result["challenger"])
        self.assertIn("under_ev", result["challenger"])
        self.assertEqual(
            result["challenger"]["over_ev"]["evidence_status"], "UNVALIDATED_RESEARCH"
        )
        self.assertTrue(result["challenger"]["over_ev"]["expected_value_is_provisional"])

    def test_rejects_out_of_range_b0_probabilities(self):
        with self.assertRaises(FrozenChallengerError):
            compare_b0_vs_frozen_challenger(projection=4.0, line=3.5, b0_over=1.5, b0_under=0.45)
        with self.assertRaises(FrozenChallengerError):
            compare_b0_vs_frozen_challenger(projection=4.0, line=3.5, b0_over=0.5, b0_under=-0.1)

    def test_never_recomputes_b0_itself_only_reuses_the_supplied_value(self):
        # Same projection/line, two very different (deliberately unrealistic)
        # b0 values -- the challenger side must be identical both times,
        # proving b0 is never re-derived from projection/line internally.
        a = compare_b0_vs_frozen_challenger(projection=4.0, line=3.5, b0_over=0.1, b0_under=0.9)
        b = compare_b0_vs_frozen_challenger(projection=4.0, line=3.5, b0_over=0.9, b0_under=0.1)
        self.assertEqual(a["challenger"]["over"], b["challenger"]["over"])
        self.assertEqual(a["challenger"]["under"], b["challenger"]["under"])
        self.assertNotEqual(a["b0"], b["b0"])


if __name__ == "__main__":
    unittest.main()
