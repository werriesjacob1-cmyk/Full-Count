#!/usr/bin/env python3
"""Scientific-integrity coherence audit of PR #148's `receptions_alt_ladder.py`.

Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-AUDIT-20260919`. This is an
AUDIT of already-merged-into-this-branch research code (`receptions_alt_ladder.py`
and `receptions_outcome_distribution.py`, both authored on draft PR #148's
branch and pulled in here unmodified so the real code under audit is
importable) -- it does not repeat PR #148's 27-season historical comparison
and did not, at audit time, edit either module.

REPAIR NOTE (Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-REPAIR-20260919`,
applied after this audit): the two real defects this file confirmed --
`EmpiricalResidualPool.pmf` not summing to 1, and `zero_probability`/`under`
disagreeing on a heterogeneous pool -- have since been fixed directly in
`receptions_outcome_distribution.py`/`receptions_alt_ladder.py`. The four
tests below that specifically asserted the OLD, broken numeric behavior
(`test_hand_computable_heterogeneous_pool_shows_a_material_gap`,
`test_homogeneous_pool_keeps_the_two_estimators_close`,
`test_recommended_fix_would_make_them_identical_by_construction`,
`test_empirical_residual_pool_pmf_does_not_sum_to_one_across_outcomes`)
were updated in place to assert the corrected behavior instead, with the
original hand-computed numbers preserved in comments as the historical
negative-result evidence per repository convention. Every other test in
this file is unchanged and still independently re-verifies its own item.

Scope, matching the audit's exact five verification items:

1. `zero_probability` vs. `ladder_probabilities(...)["rungs"][...]["under"]`
   at threshold 0.5 coherence: quantifies, on a hand-computable pool, exactly
   how much these two "same real quantity" nonparametric estimators can
   disagree, and shows the arithmetic is exactly reproducible by hand.
2. Full pmf normalization across ALL outcomes (not just one rung's
   over+under+push), independently re-verified for both
   `EmpiricalResidualPool.pmf` (the estimator used for `zero_probability`)
   and the two parametric candidates it is compared against elsewhere in
   PR #148 (`normal_discrete_pmf`, `negative_binomial_pmf`).
3. `over` monotonicity, independently re-verified on a pool shape the
   existing PR #148 test suite does not already use (a skewed, non-uniform
   pool), not merely re-running the existing symmetric-pool test.
4. Discrete exact-line push handling, independently re-verified on a
   different pool/threshold pairing than PR #148's own test.
5. The DNP/inactive exclusion design ("a true did-not-play case is a
   settlement-layer VOID, never folded into the modeled zero-reception
   mass") is verified as an ENFORCED code path in
   `receptions_baseline_research.load_receiver_rows` (the `effective_targets
   <= 0: continue` gate), not merely trusted from its docstring -- using a
   fully synthetic, clearly-labeled 27-file corpus built only to exercise
   that one code path, never real historical data.
6. Graceful degradation at a sparse pool / extreme threshold: confirms no
   NaN/exception, and separately documents (without treating it as a crash)
   that additive Laplace smoothing with n=1 does not converge probabilities
   toward 0/1 at an absurd threshold -- a real, disclosed edge property.
7. No hardcoded/fabricated odds or price literal anywhere in either module's
   actual code (AST-based, so docstring prose cannot hide or fake a pass).

No model/selector/public-pick promotion. No plus-money profitability claim.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import math
import unittest
from pathlib import Path

from nfl.research.receptions_alt_ladder import _rung_probabilities, ladder_probabilities
from nfl.research.receptions_outcome_distribution import (
    EmpiricalResidualPool,
    MAX_EMPIRICAL_SUPPORT,
    negative_binomial_pmf,
    normal_discrete_pmf,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ZeroProbabilityVsLowThresholdUnderCoherenceTests(unittest.TestCase):
    """Item 1: is `zero_probability` the same real-world quantity as
    `ladder_probabilities(threshold=0.5)["under"]`, and if not, exactly how
    far apart are they on a pool where both sides can be hand-computed?
    """

    def test_hand_computable_heterogeneous_pool_shows_the_prior_gap_is_now_closed(self):
        # UPDATED (post-repair): this test originally demonstrated that
        # `zero_probability = pool.pmf(0, projection)` (a narrow-window
        # estimate, hand-computed as exactly 8/20 = 0.4 under the OLD
        # per-query-floored pmf) disagreed with `under(0.5)` (a full-tail
        # count, 19/23 ~= 0.826) by ~0.426 absolute on this same
        # hand-computable heterogeneous pool. `ladder_probabilities` no
        # longer computes `zero_probability` from `pool.pmf` at all -- it is
        # now literally `_rung_probabilities(..., threshold=0.5)["under"]`,
        # the SAME call that produces the rung's own `under` -- so the two
        # are bit-for-bit identical by construction on this exact pool that
        # broke before, not merely close.
        low_opportunity_residuals = [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
        high_opportunity_bust_residuals = [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        pool_values = low_opportunity_residuals + high_opportunity_bust_residuals
        pool = EmpiricalResidualPool(pool_values)
        projection = 0.3
        n = len(pool_values)

        # --- Hand computation of ladder_probabilities(threshold=0.5)["under"] ---
        # gap = 0.5 - 0.3 = 0.2; under_n = count of ALL pool values < 0.2
        # (a full left-tail cumulative count). The 8 low-opportunity values
        # below 0.2 qualify AND all 10 high-opportunity bust residuals
        # (-7.0 .. -3.5) also qualify (all far below 0.2 too).
        # under_n = 8 + 10 = 18. denominator = n + 3 = 23.
        # under = (18 + 1) / 23 = 19/23.
        expected_under = 19 / 23
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["under_observations"], 18)
        self.assertAlmostEqual(rung["under"], expected_under, places=12)

        # The repaired coherence property: no gap at all, exact equality.
        self.assertEqual(result["zero_probability"], rung["under"])
        self.assertAlmostEqual(result["zero_probability"], 19 / 23, places=12)

        # Historical negative-result evidence, preserved rather than deleted:
        # the OLD narrow-window `pool.pmf(0, projection)` estimator (still a
        # real, independently-useful function post-repair, just no longer
        # wired into `zero_probability`) is now a properly NORMALIZED
        # estimate of a different real quantity (0/1 mass on the FULL
        # declared 0..MAX_EMPIRICAL_SUPPORT outcome support, not a bare
        # windowed fraction) and, by design, is no longer expected to match
        # `under(0.5)` -- they answer different statistical questions
        # (a single-outcome pmf vs. a three-way over/under/push rung) with
        # different smoothing scales. This is disclosed, not hidden:
        pmf_zero = pool.pmf(0, projection)
        self.assertNotEqual(pmf_zero, result["zero_probability"])
        self.assertGreater(abs(pmf_zero - result["zero_probability"]), 0.1)

    def test_homogeneous_pool_zero_probability_still_matches_under_exactly(self):
        # UPDATED (post-repair): this was originally a "control" case
        # showing the OLD `pool.pmf`-based `zero_probability` happened to
        # stay close to `under(0.5)` when the pool was homogeneous (unlike
        # the heterogeneous case above). That was a coincidental property of
        # the old per-query floor, not a designed guarantee -- with the
        # repaired, properly-normalized `pmf` (different smoothing scale
        # from the 3-way rung split), the two no longer stay close even on a
        # homogeneous pool. The property that now actually holds, BY
        # CONSTRUCTION, for every pool regardless of homogeneity is that
        # `ladder_probabilities(...)["zero_probability"]` exactly equals
        # that same call's `rungs[...]["under"]` at threshold 0.5 -- proven
        # here on this homogeneous pool too, not just the heterogeneous one.
        pool = EmpiricalResidualPool([-1.0] * 30 + [0.0] * 50 + [1.0] * 20)
        projection = 1.0
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        under = result["rungs"][0]["under"]
        self.assertEqual(result["zero_probability"], under)

    def test_recommended_fix_is_now_applied_and_verified_bit_for_bit_identical(self):
        """UPDATED (post-repair): PR #149's recommended patch -- redefine
        `zero_probability` as `_rung_probabilities(pool, projection=p,
        threshold=0.5)["under"]` instead of `pool.pmf(0, p)` -- has now been
        applied directly in `receptions_alt_ladder.ladder_probabilities`.
        This test proves the applied fix behaves exactly as PR #149
        predicted: bit-for-bit identical to the rung's own `under`, and
        numerically different from the (still-standalone, still valid,
        just no longer used for this purpose) `pool.pmf(0, p)` estimator.
        """
        pool = EmpiricalResidualPool(
            [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
            + [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        )
        projection = 0.3

        expected_zero_probability = _rung_probabilities(pool, projection=projection, threshold=0.5)["under"]
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        under_at_half = result["rungs"][0]["under"]
        actual_zero_probability = result["zero_probability"]

        # Bit-for-bit identical (same function, same arguments) -- the
        # applied fix, not merely a demonstration of a proposed one.
        self.assertEqual(actual_zero_probability, under_at_half)
        self.assertEqual(actual_zero_probability, expected_zero_probability)
        standalone_pmf_zero = pool.pmf(0, projection)
        self.assertNotEqual(actual_zero_probability, standalone_pmf_zero)


class FullPmfNormalizationTests(unittest.TestCase):
    """Item 2: does the pmf sum to exactly 1 across ALL outcomes (not just
    one rung's over+under+push, which PR #148 already verifies)?
    """

    def test_empirical_residual_pool_pmf_now_sums_to_one_across_the_full_support(self):
        # UPDATED (post-repair): this test originally documented the real,
        # confirmed defect that `EmpiricalResidualPool.pmf` applied a
        # Laplace floor of `1/(n+2)` independently to EVERY queried k, so
        # summing over k=0..20 on this exact adversarial 20-value
        # heterogeneous pool totaled ~1.36 -- not corrupting PR #148's own
        # log-likelihood comparison (which only ever queries `pmf()` once
        # per row, never sums across k), but meaning `pmf()` was NOT a
        # valid, normalized distribution on its own. `pmf` now applies
        # additive smoothing ONCE across the whole declared
        # 0..MAX_EMPIRICAL_SUPPORT support instead, so this EXACT pool
        # (the one PR #149 used to prove the defect was real) now sums to
        # exactly 1 -- proving the same input that broke before is fixed,
        # not just a freshly chosen easy case.
        pool = EmpiricalResidualPool(
            [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
            + [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        )
        projection = 0.3
        total = sum(pool.pmf(k, projection) for k in range(0, MAX_EMPIRICAL_SUPPORT + 1))
        self.assertAlmostEqual(total, 1.0, places=9)
        # The old (now-fixed) partial sum over just k=0..20 is preserved
        # here as historical negative-result evidence: it is no longer >1.3
        # because the per-query floor that caused that inflation is gone,
        # but a PARTIAL sum over less than the full declared support is not
        # expected to equal 1 either (some real mass legitimately lives
        # above k=20 on this pool/projection) -- only the FULL-support sum
        # above is the actual normalization guarantee.
        partial_total = sum(pool.pmf(k, projection) for k in range(0, 21))
        self.assertLess(partial_total, total)

    def test_normal_and_negative_binomial_pmfs_remain_properly_normalized(self):
        # Control/contrast: the two parametric candidates PR #148 compares
        # the empirical approach against ARE proper normalized distributions
        # (within floating point tolerance over a wide enough outcome range)
        # -- the non-normalization above is specific to
        # EmpiricalResidualPool.pmf's per-bin floor, not a defect shared by
        # every candidate in PR #148's comparison table.
        normal_total = sum(normal_discrete_pmf(k, 3.0, 2.0) for k in range(0, 60))
        nb_total = sum(negative_binomial_pmf(k, 3.0, 0.5) for k in range(0, 200))
        self.assertAlmostEqual(normal_total, 1.0, places=6)
        self.assertAlmostEqual(nb_total, 1.0, places=6)


class OverMonotonicityReverificationTests(unittest.TestCase):
    """Item 3: independently re-verify `over` monotonicity on a pool shape
    PR #148's own test suite does not use (skewed/heterogeneous, not the
    existing symmetric fixture), rather than trusting the existing test.
    """

    def test_over_is_monotonically_non_increasing_on_a_skewed_heterogeneous_pool(self):
        skewed_pool = EmpiricalResidualPool(
            [-2.0] * 5 + [-1.0] * 5 + [0.0] * 5 + [1.0] * 3 + [4.0] * 2 + [9.0] * 1
        )
        result = ladder_probabilities(
            projection=1.0,
            thresholds=[-1.5, -0.5, 0.5, 1.5, 2.5, 5.5, 10.5],
            residual_pool=skewed_pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier + 1e-12, later)
        self.assertGreater(overs[0], overs[-1])

    def test_over_is_monotonically_non_increasing_on_a_tiny_asymmetric_pool(self):
        tiny_pool = EmpiricalResidualPool([-3.0, 0.0, 0.0, 2.0, 7.0])
        result = ladder_probabilities(
            projection=2.0,
            thresholds=[0.5, 1.5, 8.5, 20.5],
            residual_pool=tiny_pool,
        )
        overs = [rung["over"] for rung in result["rungs"]]
        for earlier, later in zip(overs, overs[1:]):
            self.assertGreaterEqual(earlier + 1e-12, later)


class DiscretePushReverificationTests(unittest.TestCase):
    """Item 4: independently re-verify exact-line push handling on a
    different pool/threshold than PR #148's own test.
    """

    def test_push_detected_on_a_different_pool_and_threshold(self):
        pool = EmpiricalResidualPool([-4.0] * 6 + [-2.0] * 9 + [0.0] * 15 + [3.0] * 9 + [6.0] * 6)
        # projection 2.0, threshold 2.0 -> gap 0.0 -> the 15 zero-residual
        # observations are an exact push.
        result = ladder_probabilities(projection=2.0, thresholds=[2.0], residual_pool=pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["push_observations"], 15)
        self.assertGreater(rung["push"], 0.0)
        total = rung["over"] + rung["under"] + rung["push"]
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_no_false_push_when_no_residual_lands_exactly_on_the_gap(self):
        pool = EmpiricalResidualPool([-4.0] * 6 + [-2.0] * 9 + [0.5] * 15 + [3.0] * 9 + [6.0] * 6)
        result = ladder_probabilities(projection=2.0, thresholds=[2.0], residual_pool=pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["push_observations"], 0)
        # Push is still non-zero via the +1 Laplace term, never silently
        # collapsed to exactly 0 -- consistent with PR #148's own additive-
        # smoothing design, re-confirmed here rather than assumed.
        self.assertGreater(rung["push"], 0.0)


class DnpVoidExclusionEnforcementTests(unittest.TestCase):
    """Item 5: is the "a true DNP/inactive case is a settlement-layer VOID,
    never folded into the modeled zero-reception mass" design choice
    actually ENFORCED in code (`receptions_baseline_research.load_receiver_
    rows`'s `effective_targets <= 0: continue` gate), not merely documented
    in a module docstring?

    Builds a fully synthetic, clearly-labeled 27-season corpus (never real
    historical data) solely to exercise this one eligibility-gate code path
    end to end, rather than trusting the docstring's description of it.
    """

    def test_effective_targets_gate_excludes_a_true_dnp_row_but_keeps_role_positive_rows(self):
        from nfl.research.receptions_baseline_research import load_receiver_rows

        tmp_dir = Path(__file__).resolve().parent / "_scratch_dnp_gate_audit"
        tmp_dir.mkdir(exist_ok=True)
        try:
            header = [
                "player_id", "season", "week", "season_type", "game_id",
                "position", "targets", "receptions", "receiving_yards", "receiving_tds",
            ]
            seasons_meta = []
            # 27 synthetic seasons (1999-2025) so `load_receiver_rows`'s own
            # `len(seasons) != 27` invariant check is satisfied. All but one
            # season are header-only (zero rows) -- they exist purely to
            # satisfy the corpus-shape contract, not as fabricated history.
            for season in range(1999, 2026):
                path = tmp_dir / f"stats_player_week_{season}.csv"
                if season == 2025:
                    rows = [
                        # True DNP / inactive: no targets, no receptions --
                        # effective_targets == 0 -- must be EXCLUDED.
                        ["SYNTH_DNP", "2025", "1", "REG", "SYNTH_G1", "WR", "0", "0", "0", "0"],
                        # Genuine role-positive appearance -- must be KEPT.
                        ["SYNTH_ACTIVE", "2025", "1", "REG", "SYNTH_G1", "WR", "5", "3", "40", "0"],
                        # Fallback case: targets column unpopulated but a
                        # real reception proves a real target existed --
                        # effective_targets = max(0, 2) = 2 -- must be KEPT.
                        ["SYNTH_FALLBACK", "2025", "1", "REG", "SYNTH_G1", "WR", "0", "2", "20", "0"],
                    ]
                else:
                    rows = []
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(header)
                    writer.writerows(rows)
                data = path.read_bytes()
                seasons_meta.append({
                    "season": season,
                    "bytes": len(data),
                    "sha256": _sha256_bytes(data),
                })

            audit = {
                "seasons": seasons_meta,
                # Synthetic value only to satisfy this fully-synthetic
                # corpus's own internal invariant check -- not a claim about
                # any real corpus.
                "summary": {"missing_identity_rows_with_offense": 7},
            }

            rows, invariant_failures, _coverage = load_receiver_rows(tmp_dir, audit)

            player_ids = {row["player_id"] for row in rows}
            self.assertNotIn(
                "SYNTH_DNP", player_ids,
                "a true DNP/inactive row (effective_targets == 0) leaked into the "
                "modeled role-positive population instead of being excluded as a "
                "settlement-layer VOID",
            )
            self.assertIn("SYNTH_ACTIVE", player_ids)
            self.assertIn("SYNTH_FALLBACK", player_ids)
            self.assertEqual(len(rows), 2)
            by_id = {row["player_id"]: row for row in rows}
            self.assertEqual(by_id["SYNTH_ACTIVE"]["effective_targets"], 5)
            self.assertEqual(by_id["SYNTH_FALLBACK"]["effective_targets"], 2)
        finally:
            for path in tmp_dir.glob("*.csv"):
                path.unlink()
            tmp_dir.rmdir()


class SparsePoolExtremeThresholdTests(unittest.TestCase):
    """Item 6: does the ladder degrade gracefully (no NaN/exception) at a
    very sparse pool and an extreme threshold, and what does "gracefully"
    actually look like numerically?
    """

    def test_single_observation_pool_at_an_absurdly_high_threshold_does_not_crash_or_nan(self):
        pool = EmpiricalResidualPool([0.0])
        result = ladder_probabilities(projection=2.0, thresholds=[500.5], residual_pool=pool)
        rung = result["rungs"][0]
        for key in ("over", "under", "push"):
            self.assertTrue(math.isfinite(rung[key]))
            self.assertGreaterEqual(rung[key], 0.0)
            self.assertLessEqual(rung[key], 1.0)
        self.assertAlmostEqual(rung["over"] + rung["under"] + rung["push"], 1.0, places=12)
        # Real, disclosed edge property: with n=1, the +1 additive-smoothing
        # term dominates the single real observation, so `over` at a
        # threshold no real receiver could ever reach (500.5 receptions)
        # is 0.25 -- not the ~0 an infinite-sample estimator would give.
        # This is not a crash or NaN, but it IS a real calibration
        # degradation this audit surfaces rather than silently accepting as
        # "graceful" without quantifying it.
        self.assertAlmostEqual(rung["over"], 0.25, places=12)

    def test_single_observation_pool_at_an_absurdly_low_threshold_does_not_crash_or_nan(self):
        pool = EmpiricalResidualPool([0.0])
        result = ladder_probabilities(projection=2.0, thresholds=[-500.5], residual_pool=pool)
        rung = result["rungs"][0]
        for key in ("over", "under", "push"):
            self.assertTrue(math.isfinite(rung[key]))
        self.assertAlmostEqual(rung["over"] + rung["under"] + rung["push"], 1.0, places=12)
        zero_probability = pool.pmf(0, 2.0)
        self.assertTrue(math.isfinite(zero_probability))
        self.assertGreaterEqual(zero_probability, 0.0)
        self.assertLessEqual(zero_probability, 1.0)


class NoFabricatedPriceTests(unittest.TestCase):
    """Item 7: no historical or offered sportsbook price is hardcoded/
    fabricated anywhere in either module, verified by AST (so docstring
    prose describing the wiring cannot hide or fake a pass)."""

    def _numeric_literals_at_least(self, path: Path, minimum_abs_value: float) -> list[tuple[int, object]]:
        tree = ast.parse(path.read_text())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                if abs(node.value) >= minimum_abs_value:
                    found.append((node.lineno, node.value))
        return found

    def test_alt_ladder_module_has_no_odds_shaped_numeric_literal(self):
        module_path = Path(__file__).resolve().parents[1] / "research" / "receptions_alt_ladder.py"
        # Real American odds are always |value| >= 100. Every literal in
        # this module's actual code (smoothing constants, tolerances) is
        # far below that, so any literal >= 100 would be suspicious.
        suspicious = self._numeric_literals_at_least(module_path, 100)
        self.assertEqual(suspicious, [], f"unexpected large numeric literal(s) in receptions_alt_ladder.py: {suspicious}")

    def test_outcome_distribution_module_has_no_odds_shaped_numeric_literal_outside_known_constants(self):
        module_path = Path(__file__).resolve().parents[1] / "research" / "receptions_outcome_distribution.py"
        suspicious = self._numeric_literals_at_least(module_path, 100)
        # The only large literals in this module are a bucket boundary (200)
        # and CLI season-split years (2022/2023/2025) -- neither is a price.
        # This module's own docstring states no odds appear in it at all;
        # this test independently confirms that via source inspection.
        allowed = {200, 2022, 2023, 2025}
        unexpected = [(line, value) for line, value in suspicious if value not in allowed]
        self.assertEqual(unexpected, [], f"unexpected numeric literal(s): {unexpected}")


if __name__ == "__main__":
    unittest.main()
