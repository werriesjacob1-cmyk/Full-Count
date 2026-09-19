#!/usr/bin/env python3
"""Contracts for the receptions alternate-line ladder research module."""
import unittest

from nfl.research.alternate_line_evaluation import AlternateLineEvaluationError
from nfl.research.receptions_alt_ladder import (
    ReceptionsAltLadderError,
    evaluate_ladder_with_prices,
    ladder_probabilities,
    require_real_thresholds,
    verify_ladder_invariants,
)
from nfl.research.receptions_outcome_distribution import EmpiricalResidualPool


def build_pool(values):
    return EmpiricalResidualPool(values)


class RequireRealThresholdsTests(unittest.TestCase):
    def test_empty_sequence_raises(self):
        with self.assertRaises(ReceptionsAltLadderError):
            require_real_thresholds([])

    def test_non_sequence_raises(self):
        with self.assertRaises(ReceptionsAltLadderError):
            require_real_thresholds(4.5)  # not a sequence at all

    def test_non_numeric_threshold_raises(self):
        with self.assertRaises(ReceptionsAltLadderError):
            require_real_thresholds([1.5, "not-a-number"])

    def test_valid_thresholds_pass_through_as_floats(self):
        self.assertEqual(require_real_thresholds([1, 2.5, 3]), [1.0, 2.5, 3.0])


class LadderProbabilitiesTests(unittest.TestCase):
    def setUp(self):
        # A symmetric-ish real-shaped residual pool around a receiver with
        # projection 3.0: outcomes span 0..6 with more mass near the center.
        self.pool_values = (
            [-3.0] * 20   # actual = 0
            + [-2.0] * 40  # actual = 1
            + [-1.0] * 80  # actual = 2
            + [0.0] * 120  # actual = 3
            + [1.0] * 80   # actual = 4
            + [2.0] * 40   # actual = 5
            + [3.0] * 20   # actual = 6
        )
        self.pool = build_pool(self.pool_values)

    def test_over_probabilities_are_monotonically_non_increasing(self):
        result = ladder_probabilities(
            projection=3.0,
            thresholds=[0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
            residual_pool=self.pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier, later)
        # And it must be a real, non-trivial decline (not all tied at Laplace
        # floor), proving the thresholds actually discriminate.
        self.assertGreater(overs[0], overs[-1])

    def test_thresholds_supplied_out_of_order_still_verify_correctly(self):
        result = ladder_probabilities(
            projection=3.0,
            thresholds=[4.5, 0.5, 2.5],
            residual_pool=self.pool,
        )
        thresholds_in_result = [rung["threshold"] for rung in result["rungs"]]
        self.assertEqual(thresholds_in_result, sorted(thresholds_in_result))

    def test_every_rung_sums_to_one_within_tolerance(self):
        result = ladder_probabilities(
            projection=3.0,
            thresholds=[0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
            residual_pool=self.pool,
        )
        for rung in result["rungs"]:
            total = rung["over"] + rung["under"] + rung["push"]
            self.assertAlmostEqual(total, 1.0, places=9)

    def test_exact_push_is_detected_at_an_integer_threshold(self):
        # Threshold 3.0 with projection 3.0 -> gap 0.0, which the pool has
        # 120 real observations exactly equal to (actual == 3). A push-
        # capable integer line must show real, non-zero push probability,
        # not force it to zero.
        result = ladder_probabilities(
            projection=3.0,
            thresholds=[3.0],
            residual_pool=self.pool,
        )
        rung = result["rungs"][0]
        self.assertEqual(rung["push_observations"], 120)
        self.assertGreater(rung["push"], 0.0)

    def test_empty_thresholds_raise(self):
        with self.assertRaises(ReceptionsAltLadderError):
            ladder_probabilities(projection=3.0, thresholds=[], residual_pool=self.pool)

    def test_zero_probability_is_explicit_and_material_for_a_low_projection_player(self):
        # A low-opportunity player pool where a meaningful share of real
        # historical outcomes were exactly zero.
        low_pool = build_pool(
            [-1.0] * 30  # actual = 0 given projection 1.0
            + [0.0] * 50  # actual = 1
            + [1.0] * 20  # actual = 2
        )
        result = ladder_probabilities(projection=1.0, thresholds=[0.5, 1.5], residual_pool=low_pool)
        self.assertTrue(result["zero_mass_is_material"])
        self.assertGreater(result["zero_probability"], 0.2)  # nowhere near silently smoothed to ~0

    def test_pooled_vs_bucket_restricted_pool_can_disagree_for_a_low_opportunity_player(self):
        """Direct demonstration (disclosed in the module docstring) that the
        full-tail over/under convention and this ladder's zero_probability
        estimator are not required to agree, and that a pool mixing
        high-opportunity historical rows into a low-opportunity player's
        ladder can materially understate a real zero-mass risk relative to a
        pool restricted to that player's own opportunity tier.
        """
        # Full pool: mostly high-opportunity players (large positive
        # residual tail mass unrelated to a real low-projection player).
        mixed_pool = build_pool([-1.0] * 10 + [0.0] * 10 + [5.0] * 500 + [8.0] * 500)
        # Bucket-restricted pool: only real low-opportunity comparables.
        low_bucket_pool = build_pool([-1.0] * 30 + [0.0] * 50 + [1.0] * 20)

        mixed_result = ladder_probabilities(projection=1.0, thresholds=[0.5], residual_pool=mixed_pool)
        bucketed_result = ladder_probabilities(projection=1.0, thresholds=[0.5], residual_pool=low_bucket_pool)

        # Both are valid, self-consistent ladders (sum-to-one, monotonic),
        # but they need not produce the same zero-mass estimate -- exactly
        # the disclosed limitation this module's docstring describes.
        self.assertNotAlmostEqual(
            mixed_result["zero_probability"], bucketed_result["zero_probability"], places=2
        )


class VerifyLadderInvariantsTests(unittest.TestCase):
    def test_detects_a_sum_violation(self):
        bad_result = {"rungs": [{"threshold": 1.0, "over": 0.5, "under": 0.4, "push": 0.0}]}
        with self.assertRaises(ReceptionsAltLadderError):
            verify_ladder_invariants(bad_result)

    def test_detects_a_monotonicity_violation(self):
        bad_result = {
            "rungs": [
                {"threshold": 1.0, "over": 0.3, "under": 0.7, "push": 0.0},
                {"threshold": 2.0, "over": 0.6, "under": 0.4, "push": 0.0},  # rose with threshold
            ]
        }
        with self.assertRaises(ReceptionsAltLadderError):
            verify_ladder_invariants(bad_result)

    def test_accepts_a_valid_result(self):
        good_result = {
            "rungs": [
                {"threshold": 1.0, "over": 0.7, "under": 0.3, "push": 0.0},
                {"threshold": 2.0, "over": 0.4, "under": 0.6, "push": 0.0},
            ]
        }
        verify_ladder_invariants(good_result)  # must not raise

    def test_raises_on_no_rungs(self):
        with self.assertRaises(ReceptionsAltLadderError):
            verify_ladder_invariants({"rungs": []})


class EvaluateLadderWithPricesTests(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool([-2.0] * 30 + [-1.0] * 40 + [0.0] * 60 + [1.0] * 40 + [2.0] * 30)

    def test_wires_through_alternate_line_evaluation_functions_exactly(self):
        from nfl.research.alternate_line_evaluation import (
            breakeven_probability,
            expected_value_from_probability,
            price_bucket,
        )

        result = evaluate_ladder_with_prices(
            projection=3.0,
            priced_rungs=[{"threshold": 2.5, "over_odds": -120, "under_odds": 105}],
            residual_pool=self.pool,
        )
        rung = result["priced_rungs"][0]
        expected_over_ev = expected_value_from_probability(
            rung["model_over_probability"], -120, evidence_status="UNVALIDATED_RESEARCH"
        )
        self.assertEqual(rung["over_price"]["price_bucket"], price_bucket(-120))
        self.assertEqual(rung["over_price"]["breakeven_probability"], breakeven_probability(-120))
        self.assertAlmostEqual(
            rung["over_price"]["expected_value_units_per_unit_staked"],
            expected_over_ev["expected_value_units_per_unit_staked"],
        )
        self.assertTrue(rung["over_price"]["expected_value_is_provisional"])
        self.assertEqual(rung["over_price"]["evidence_status"], "UNVALIDATED_RESEARCH")

    def test_never_fabricates_a_missing_price(self):
        result = evaluate_ladder_with_prices(
            projection=3.0,
            priced_rungs=[{"threshold": 2.5, "over_odds": -120}],  # no under_odds supplied
            residual_pool=self.pool,
        )
        rung = result["priced_rungs"][0]
        self.assertIsNotNone(rung["over_price"])
        self.assertIsNone(rung["under_price"])

    def test_invalid_odds_propagate_the_underlying_error_not_a_reimplementation(self):
        with self.assertRaises(AlternateLineEvaluationError):
            evaluate_ladder_with_prices(
                projection=3.0,
                priced_rungs=[{"threshold": 2.5, "over_odds": 50}],  # invalid: in (-100, 100)
                residual_pool=self.pool,
            )

    def test_empty_priced_rungs_raises(self):
        with self.assertRaises(ReceptionsAltLadderError):
            evaluate_ladder_with_prices(projection=3.0, priced_rungs=[], residual_pool=self.pool)

    def test_reports_zero_probability_alongside_priced_rungs(self):
        result = evaluate_ladder_with_prices(
            projection=3.0,
            priced_rungs=[{"threshold": 2.5, "over_odds": -110}],
            residual_pool=self.pool,
        )
        self.assertIn("zero_probability", result)
        self.assertIn("zero_mass_is_material", result)


if __name__ == "__main__":
    unittest.main()
