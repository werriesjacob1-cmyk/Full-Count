#!/usr/bin/env python3
"""Scientific-integrity coherence audit of PR #148's `receptions_alt_ladder.py`.

Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-AUDIT-20260919`. This is an
AUDIT of already-merged-into-this-branch research code (`receptions_alt_ladder.py`
and `receptions_outcome_distribution.py`, both authored on draft PR #148's
branch and pulled in here unmodified so the real code under audit is
importable) -- it does not repeat PR #148's 27-season historical comparison
and does not edit either module.

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

    def test_hand_computable_heterogeneous_pool_shows_a_material_gap(self):
        # A pool that mixes two real-shaped subpopulations, exactly as the
        # module's own docstring warns can happen: 10 "low-opportunity role
        # player" residuals clustered near a projection of ~0.3-0.5, and 10
        # "high-opportunity bust game" residuals from players whose OWN
        # historical projection was much higher (~8), all pooled together
        # (the codebase's existing pooled-residual convention).
        low_opportunity_residuals = [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
        high_opportunity_bust_residuals = [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        pool_values = low_opportunity_residuals + high_opportunity_bust_residuals
        pool = EmpiricalResidualPool(pool_values)
        projection = 0.3
        n = len(pool_values)

        # --- Hand computation of zero_probability = pool.pmf(0, projection) ---
        # gap = 0 - 0.3 = -0.3; window = [gap-0.5, gap+0.5) = [-0.8, 0.2).
        # Values in [-0.8, 0.2) from the pool: -0.5, -0.5, -0.4, -0.3, -0.2,
        # -0.1, 0.0, 0.1 (8 values; 0.2 and 0.3 are excluded, high-opportunity
        # group is entirely <= -3.5 so none qualify). count/n = 8/20 = 0.4,
        # which exceeds the Laplace floor 1/(n+2) = 1/22, so pmf = 0.4 exactly.
        expected_zero_probability = 8 / 20
        zero_probability = pool.pmf(0, projection)
        self.assertAlmostEqual(zero_probability, expected_zero_probability, places=12)
        self.assertAlmostEqual(zero_probability, 0.4, places=12)

        # --- Hand computation of ladder_probabilities(threshold=0.5)["under"] ---
        # gap = 0.5 - 0.3 = 0.2; under_n = count of ALL pool values < 0.2
        # (a full left-tail cumulative count, not a narrow window). The 8
        # low-opportunity values below 0.2 qualify AND all 10 high-opportunity
        # bust residuals (-7.0 .. -3.5) also qualify, because they are all
        # far below 0.2 too -- even though they come from an entirely
        # different, high-projection historical population and do not
        # actually represent "this low-opportunity player scored near zero".
        # under_n = 8 + 10 = 18. denominator = n + 3 = 23.
        # under = (18 + 1) / 23 = 19/23.
        expected_under = 19 / 23
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        rung = result["rungs"][0]
        self.assertEqual(rung["under_observations"], 18)
        self.assertAlmostEqual(rung["under"], expected_under, places=12)

        gap = abs(rung["under"] - zero_probability)
        # The two "same real quantity" estimators disagree by ~0.426 absolute
        # (zero_probability=0.400 vs. under(0.5)=0.826) -- more than 100%
        # relative to the smaller value. This is the disclosed coherence
        # tension, quantified and confirmed real, not merely asserted as
        # "can differ" (as PR #148's own
        # `test_pooled_vs_bucket_restricted_pool_can_disagree_...` test
        # already does across two DIFFERENT pools). Here it is the SAME pool,
        # SAME projection, SAME function call's own output.
        self.assertGreater(gap, 0.35)
        self.assertAlmostEqual(gap, 19 / 23 - 8 / 20, places=12)

    def test_homogeneous_pool_keeps_the_two_estimators_close(self):
        # Control case: when the pool is NOT heterogeneous (all residuals
        # come from comparable-opportunity historical rows, as in PR #148's
        # own test fixtures), the two estimators stay close -- confirming
        # the gap above is a real property of pool heterogeneity, not a
        # universal bug that fires on every input.
        pool = EmpiricalResidualPool([-1.0] * 30 + [0.0] * 50 + [1.0] * 20)
        projection = 1.0
        zero_probability = pool.pmf(0, projection)
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        under = result["rungs"][0]["under"]
        self.assertLess(abs(under - zero_probability), 0.02)

    def test_recommended_fix_would_make_them_identical_by_construction(self):
        """Demonstrates (without editing `receptions_alt_ladder.py`) that
        redefining `zero_probability` as the ladder's OWN `under` value at
        threshold=0.5 -- i.e. `_rung_probabilities(pool, projection=p,
        threshold=0.5)["under"]` instead of `pool.pmf(0, p)` -- makes the two
        quantities mathematically IDENTICAL (not merely close), because they
        would then literally be the same function call. This is the
        recommended patch described in the audit report; it is proven here
        to work, but deliberately NOT applied to the source file (see report
        for why).
        """
        pool = EmpiricalResidualPool(
            [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
            + [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        )
        projection = 0.3

        def proposed_zero_probability(pool, projection):
            return _rung_probabilities(pool, projection=projection, threshold=0.5)["under"]

        fixed_zero_probability = proposed_zero_probability(pool, projection)
        result = ladder_probabilities(projection=projection, thresholds=[0.5], residual_pool=pool)
        under_at_half = result["rungs"][0]["under"]
        # Bit-for-bit identical (same function, same arguments), unlike the
        # current pmf()-based zero_probability computed above.
        self.assertEqual(fixed_zero_probability, under_at_half)
        current_zero_probability = pool.pmf(0, projection)
        self.assertNotEqual(fixed_zero_probability, current_zero_probability)


class FullPmfNormalizationTests(unittest.TestCase):
    """Item 2: does the pmf sum to exactly 1 across ALL outcomes (not just
    one rung's over+under+push, which PR #148 already verifies)?
    """

    def test_empirical_residual_pool_pmf_does_not_sum_to_one_across_outcomes(self):
        # Real, confirmed finding: EmpiricalResidualPool.pmf applies a
        # Laplace floor of 1/(n+2) independently to EVERY queried k. For a
        # pool with a long stretch of k values with zero real observations
        # nearby, each of those k's still contributes the floor, so summing
        # across enough outcomes exceeds 1. This does not corrupt PR #148's
        # own log-likelihood comparison (which only ever queries pmf() at
        # the single observed k per row, never sums across k), but it means
        # pmf() is NOT a valid, normalized full outcome distribution on its
        # own -- a real coherence property worth disclosing, separate from
        # the zero_probability/under gap above.
        pool = EmpiricalResidualPool(
            [-0.5, -0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
            + [-7.0, -7.0, -6.5, -6.0, -6.0, -5.5, -5.0, -4.5, -4.0, -3.5]
        )
        projection = 0.3
        total = sum(pool.pmf(k, projection) for k in range(0, 21))
        self.assertGreater(total, 1.3)
        self.assertLess(total, 1.4)
        self.assertNotAlmostEqual(total, 1.0, places=2)

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
