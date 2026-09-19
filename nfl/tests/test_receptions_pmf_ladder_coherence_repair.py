#!/usr/bin/env python3
"""Regression suite for the PMF-normalization and zero_probability/`under`
coherence REPAIR.

Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-REPAIR-20260919` (Agent B).
This file does not re-audit or re-describe the two defects -- PR #149's
`test_receptions_alt_ladder_coherence_audit.py` already did that, and its
four affected tests were updated in place to assert the corrected behavior
(see that file's REPAIR NOTE). This file adds the mission-required, more
exhaustive regression coverage for the applied fix itself: real normalization
across multiple pool shapes, nonnegativity, exact zero_probability/under
identity, rung sum-to-one at integer and half-integer lines, monotonicity,
sparse-pool stability, small-sample behavior, DNP/VOID out-of-scope
confirmation, and determinism.

No model/selector/public-pick promotion. No fabricated price/odds anywhere
(neither module accepts or emits one on its own).
"""
from __future__ import annotations

import math
import random
import unittest

from nfl.research.alternate_line_evaluation import SETTLEMENT_OUTCOMES
from nfl.research.receptions_alt_ladder import _rung_probabilities, ladder_probabilities
from nfl.research.receptions_outcome_distribution import (
    MAX_EMPIRICAL_SUPPORT,
    EmpiricalResidualPool,
    ReceptionsOutcomeDistributionError,
)

# The exact 20-value hand-computable heterogeneous pool PR #149's audit used
# to prove the PMF-normalization defect was real (sum ~1.36 over k=0..20
# before this repair). Reused verbatim, never re-derived, so this suite
# proves the SAME input that broke before now sums to 1.
ADVERSARIAL_HETEROGENEOUS_POOL = (
    [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
    + [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
)


def _full_support_sum(pool: EmpiricalResidualPool, projection: float) -> float:
    return sum(pool.pmf(k, projection) for k in range(0, MAX_EMPIRICAL_SUPPORT + 1))


class PmfNormalizationAcrossRealShapedPoolsTests(unittest.TestCase):
    """Item: pmf sums to 1 (within float tolerance) across the full declared
    support, on at least 3 different real-shaped pools, including the exact
    adversarial pool PR #149 used."""

    def test_adversarial_heterogeneous_pool_from_pr149_now_sums_to_one(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        total = _full_support_sum(pool, projection=0.3)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_large_homogeneous_pool_sums_to_one(self):
        # Shape: a real-looking symmetric residual spread around a
        # mid-opportunity projection (~3 targets/game role player).
        pool = EmpiricalResidualPool([-2.0] * 40 + [-1.0] * 120 + [0.0] * 200 + [1.0] * 130 + [2.0] * 50)
        for projection in (0.0, 1.5, 3.0, 5.0):
            with self.subTest(projection=projection):
                total = _full_support_sum(pool, projection)
                self.assertAlmostEqual(total, 1.0, places=9)

    def test_right_skewed_pool_sums_to_one(self):
        # Shape: a low-opportunity player pool where most residuals cluster
        # near zero but a real "boom game" tail exists (right-skewed).
        pool = EmpiricalResidualPool(
            [-0.5] * 30 + [0.0] * 40 + [0.5] * 20 + [1.5] * 8 + [3.5] * 4 + [7.0] * 1
        )
        total = _full_support_sum(pool, projection=0.4)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_single_observation_pool_sums_to_one(self):
        pool = EmpiricalResidualPool([2.0])
        total = _full_support_sum(pool, projection=1.0)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_two_observation_pool_sums_to_one(self):
        pool = EmpiricalResidualPool([-3.0, 4.0])
        total = _full_support_sum(pool, projection=2.0)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_normalization_holds_across_a_spread_of_projections(self):
        # The normalization property must not be an artifact of one
        # convenient projection value -- sweep several, including negative,
        # zero, integer, and fractional projections.
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        for projection in (-5.0, -0.5, 0.0, 0.3, 2.75, 10.0, 100.0):
            with self.subTest(projection=projection):
                total = _full_support_sum(pool, projection)
                self.assertAlmostEqual(total, 1.0, places=9)


class PmfNonnegativeAtEveryQueriedKTests(unittest.TestCase):
    def test_every_k_in_support_is_nonnegative_on_multiple_pools(self):
        pools = [
            EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL),
            EmpiricalResidualPool([0.0] * 5),
            EmpiricalResidualPool([-100.0, 100.0]),
            EmpiricalResidualPool([1.0]),
        ]
        for pool in pools:
            for projection in (0.0, 1.0, -3.5, 8.25):
                for k in range(0, MAX_EMPIRICAL_SUPPORT + 1):
                    with self.subTest(pool_n=pool.n, projection=projection, k=k):
                        self.assertGreaterEqual(pool.pmf(k, projection), 0.0)

    def test_pmf_never_exceeds_one(self):
        pool = EmpiricalResidualPool([0.0] * 500)
        for k in range(0, MAX_EMPIRICAL_SUPPORT + 1):
            self.assertLessEqual(pool.pmf(k, projection=0.0), 1.0)


class ZeroProbabilityUnderIdentityTests(unittest.TestCase):
    """Item: zero_probability and ladder_probabilities(threshold=0.5)["under"]
    are now bit-for-bit identical -- a real assertion, not a design note."""

    def test_identical_on_the_adversarial_pool(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        result = ladder_probabilities(projection=0.3, thresholds=[0.5], residual_pool=pool)
        self.assertEqual(result["zero_probability"], result["rungs"][0]["under"])

    def test_identical_when_0_5_is_not_the_only_or_first_threshold(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        result = ladder_probabilities(
            projection=0.3, thresholds=[10.5, -3.5, 0.5, 2.5], residual_pool=pool
        )
        half_rung = next(rung for rung in result["rungs"] if rung["threshold"] == 0.5)
        self.assertEqual(result["zero_probability"], half_rung["under"])

    def test_identical_even_when_0_5_is_not_supplied_at_all(self):
        # zero_probability must still equal what a threshold=0.5 rung WOULD
        # report, even if the caller never asked for that rung explicitly.
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        result = ladder_probabilities(projection=0.3, thresholds=[10.5, 20.5], residual_pool=pool)
        independently_computed_under_half = _rung_probabilities(pool, projection=0.3, threshold=0.5)["under"]
        self.assertEqual(result["zero_probability"], independently_computed_under_half)

    def test_identical_on_a_tiny_sparse_pool(self):
        pool = EmpiricalResidualPool([0.0])
        result = ladder_probabilities(projection=2.0, thresholds=[0.5], residual_pool=pool)
        self.assertEqual(result["zero_probability"], result["rungs"][0]["under"])

    def test_identical_across_many_random_pools_and_projections(self):
        rng = random.Random(20260919)
        for trial in range(25):
            n = rng.randint(1, 200)
            pool_values = [rng.uniform(-10.0, 10.0) for _ in range(n)]
            projection = rng.uniform(-5.0, 15.0)
            pool = EmpiricalResidualPool(pool_values)
            result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
            with self.subTest(trial=trial, n=n, projection=projection):
                self.assertEqual(result["zero_probability"], result["rungs"][0]["under"])


class RungSumToOneTests(unittest.TestCase):
    """Item: over/under/push sum to exactly 1 at every rung, including at
    least one integer-line and one half-integer-line case."""

    def test_integer_line_sums_to_one(self):
        pool = EmpiricalResidualPool([-3.0, -1.0, 0.0, 0.0, 2.0, 5.0])
        result = ladder_probabilities(projection=3.0, thresholds=[3.0], residual_pool=pool)
        rung = result["rungs"][0]
        total = rung["over"] + rung["under"] + rung["push"]
        self.assertAlmostEqual(total, 1.0, places=12)

    def test_half_integer_line_sums_to_one(self):
        pool = EmpiricalResidualPool([-3.0, -1.0, 0.0, 0.0, 2.0, 5.0])
        result = ladder_probabilities(projection=3.0, thresholds=[3.5], residual_pool=pool)
        rung = result["rungs"][0]
        total = rung["over"] + rung["under"] + rung["push"]
        self.assertAlmostEqual(total, 1.0, places=12)

    def test_every_rung_in_a_multi_threshold_ladder_sums_to_one(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        thresholds = [-6.5, -4.5, -0.5, 0.5, 1.5, 3.5, 8.5]
        result = ladder_probabilities(projection=0.3, thresholds=thresholds, residual_pool=pool)
        for rung in result["rungs"]:
            with self.subTest(threshold=rung["threshold"]):
                total = rung["over"] + rung["under"] + rung["push"]
                self.assertAlmostEqual(total, 1.0, places=12)


class MonotonicityReverificationTests(unittest.TestCase):
    """Item: monotonic threshold probabilities re-verified after the fix."""

    def test_over_is_non_increasing_after_the_fix_on_pr149s_pool_shape(self):
        pool = EmpiricalResidualPool(
            [-2.0] * 5 + [-1.0] * 5 + [0.0] * 5 + [1.0] * 3 + [4.0] * 2 + [9.0] * 1
        )
        result = ladder_probabilities(
            projection=1.0,
            thresholds=[-1.5, -0.5, 0.5, 1.5, 2.5, 5.5, 10.5],
            residual_pool=pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier + 1e-12, later)
        self.assertGreater(overs[0], overs[-1])

    def test_over_is_non_increasing_on_the_adversarial_pool(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        result = ladder_probabilities(
            projection=0.3,
            thresholds=[-8.5, -6.5, -4.5, -0.5, 0.5, 2.5, 6.5],
            residual_pool=pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier + 1e-12, later)


class SparseTailStabilityTests(unittest.TestCase):
    """Item: sparse-tail stability (n=1 or very small pool) -- no NaN, no
    exception, sum still ~= 1."""

    def test_n_equals_1_pool_full_support_sum_is_still_one(self):
        pool = EmpiricalResidualPool([0.0])
        total = _full_support_sum(pool, projection=2.0)
        self.assertTrue(math.isfinite(total))
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_n_equals_1_pool_ladder_does_not_crash_at_extreme_thresholds(self):
        pool = EmpiricalResidualPool([0.0])
        for threshold in (-500.5, 500.5):
            with self.subTest(threshold=threshold):
                result = ladder_probabilities(projection=2.0, thresholds=[threshold], residual_pool=pool)
                rung = result["rungs"][0]
                for key in ("over", "under", "push"):
                    self.assertTrue(math.isfinite(rung[key]))
                self.assertAlmostEqual(rung["over"] + rung["under"] + rung["push"], 1.0, places=12)
                self.assertTrue(math.isfinite(result["zero_probability"]))

    def test_n_equals_2_pool_no_nan_across_full_support(self):
        pool = EmpiricalResidualPool([-1.0, 6.0])
        for k in range(0, MAX_EMPIRICAL_SUPPORT + 1):
            value = pool.pmf(k, projection=1.0)
            self.assertTrue(math.isfinite(value))
            self.assertGreaterEqual(value, 0.0)


class SmallSampleBehaviorTests(unittest.TestCase):
    """Item: small-sample behavior generally -- the additive smoothing must
    remain a real, visible floor (never collapsing to 0) without ever
    breaking normalization, even as n shrinks toward 1."""

    def test_smoothing_floor_shrinks_but_stays_positive_as_n_grows(self):
        # With more real observations, the Laplace floor's share of the
        # total shrinks (less smoothing dominance) -- but never to exactly
        # zero, and the sum stays exactly 1 regardless of n.
        floors = []
        for n in (1, 5, 50, 500):
            pool = EmpiricalResidualPool([100.0] * n)  # no mass anywhere near k=0
            p_zero = pool.pmf(0, projection=0.0)
            floors.append(p_zero)
            self.assertGreater(p_zero, 0.0)
            self.assertAlmostEqual(_full_support_sum(pool, projection=0.0), 1.0, places=9)
        for earlier, later in zip(floors, floors[1:]):
            self.assertGreater(earlier, later)

    def test_rejects_empty_pool_even_under_the_new_pmf(self):
        with self.assertRaises(ReceptionsOutcomeDistributionError):
            EmpiricalResidualPool([])


class DnpVoidStaysOutOfScopeTests(unittest.TestCase):
    """Item: confirm the fix does not accidentally start modeling a DNP
    outcome -- that remains a VOID settlement concern, untouched here."""

    def test_settlement_outcomes_vocabulary_is_unmodified_by_this_repair(self):
        # This repair touches only receptions_outcome_distribution.py and
        # receptions_alt_ladder.py; alternate_line_evaluation.py's own
        # settlement vocabulary must remain exactly what it was.
        self.assertEqual(SETTLEMENT_OUTCOMES, {"HIT", "MISS", "PUSH", "VOID"})

    def test_ladder_result_never_introduces_a_dnp_or_void_field(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        result = ladder_probabilities(projection=0.3, thresholds=[0.5, 2.5], residual_pool=pool)
        self.assertNotIn("dnp_probability", result)
        self.assertNotIn("void_probability", result)
        self.assertNotIn("VOID", result)
        for rung in result["rungs"]:
            self.assertEqual(set(rung) & {"dnp", "void", "DNP", "VOID"}, set())

    def test_pmf_support_bound_is_a_normalization_truncation_not_a_dnp_model(self):
        # MAX_EMPIRICAL_SUPPORT folds an extreme upper tail into one bin for
        # normalization purposes only -- it must not be confused with, or
        # coincide with, any DNP/inactive semantics. k=0 in this pmf always
        # means "zero real receptions recorded", never "did not play".
        pool = EmpiricalResidualPool([0.0] * 10)
        # A pool built entirely from zero-residual (i.e. actual == b0)
        # observations still reports k=0 with real, nonzero, non-unity mass
        # -- it is a genuine outcome probability, not a DNP flag.
        p_zero = pool.pmf(0, projection=0.0)
        self.assertGreater(p_zero, 0.0)
        self.assertLess(p_zero, 1.0)


class DeterminismTests(unittest.TestCase):
    """Item: determinism under different input ordering and repeated
    execution -- same inputs -> same outputs, every time, no hidden
    randomness."""

    def test_pmf_is_order_independent(self):
        values = ADVERSARIAL_HETEROGENEOUS_POOL
        pool_a = EmpiricalResidualPool(values)
        pool_b = EmpiricalResidualPool(list(reversed(values)))
        rng = random.Random(7)
        shuffled = list(values)
        rng.shuffle(shuffled)
        pool_c = EmpiricalResidualPool(shuffled)

        for k in range(0, MAX_EMPIRICAL_SUPPORT + 1):
            with self.subTest(k=k):
                a = pool_a.pmf(k, projection=0.3)
                b = pool_b.pmf(k, projection=0.3)
                c = pool_c.pmf(k, projection=0.3)
                self.assertEqual(a, b)
                self.assertEqual(a, c)

    def test_ladder_probabilities_is_order_independent_in_pool_and_thresholds(self):
        values = ADVERSARIAL_HETEROGENEOUS_POOL
        shuffled_values = list(reversed(values))
        thresholds = [0.5, -4.5, 2.5]
        shuffled_thresholds = [2.5, 0.5, -4.5]

        result_a = ladder_probabilities(
            projection=0.3, thresholds=thresholds, residual_pool=EmpiricalResidualPool(values)
        )
        result_b = ladder_probabilities(
            projection=0.3,
            thresholds=shuffled_thresholds,
            residual_pool=EmpiricalResidualPool(shuffled_values),
        )
        self.assertEqual(result_a["zero_probability"], result_b["zero_probability"])
        rungs_a = {rung["threshold"]: rung for rung in result_a["rungs"]}
        rungs_b = {rung["threshold"]: rung for rung in result_b["rungs"]}
        self.assertEqual(set(rungs_a), set(rungs_b))
        for threshold, rung_a in rungs_a.items():
            rung_b = rungs_b[threshold]
            self.assertEqual(rung_a["over"], rung_b["over"])
            self.assertEqual(rung_a["under"], rung_b["under"])
            self.assertEqual(rung_a["push"], rung_b["push"])

    def test_repeated_execution_produces_identical_results(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        first = ladder_probabilities(projection=0.3, thresholds=[0.5, 5.5], residual_pool=pool)
        for _ in range(10):
            again = ladder_probabilities(projection=0.3, thresholds=[0.5, 5.5], residual_pool=pool)
            self.assertEqual(first, again)

    def test_repeated_pmf_calls_are_identical(self):
        pool = EmpiricalResidualPool(ADVERSARIAL_HETEROGENEOUS_POOL)
        values = [pool.pmf(5, projection=0.3) for _ in range(20)]
        self.assertEqual(len(set(values)), 1)


if __name__ == "__main__":
    unittest.main()
