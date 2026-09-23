#!/usr/bin/env python3
"""Research-only alternate-line ladder for the passing-yards market.

## Why this module exists (real gap found before writing any code)

The receptions market already has BOTH a full outcome-distribution family
comparison (`receptions_outcome_distribution.py`: NORMAL vs NEGATIVE_BINOMIAL
vs EMPIRICAL_RESIDUAL, fit/evaluated out-of-sample) AND a reusable, coherent
multi-line alternate-line ladder built on top of it (`receptions_alt_ladder.py`).
Passing yards -- which already has its OWN live shadow board
(`nfl-live-passing-yards-shadow-board.yml`, real production capture, unlike
receptions_alt_ladder which this same investigation confirms below is NOT
consumed anywhere in production) -- has neither: `passing_yards_shadow.py`
only exposes a SINGLE-threshold `empirical_side_probabilities` call. A real
sportsbook posts many alternate passing-yards lines per game (e.g. 199.5,
224.5, 249.5, 274.5...), and nothing in this repository can currently score
more than one of them coherently from one shared distribution for this
market. This module closes that gap, following the exact multi-line-ladder
convention already established and reviewed for receptions, generalized to a
market with a materially different outcome shape (a large, effectively
continuous integer range instead of a small bounded count).

## Honest disclosure: `receptions_alt_ladder.py` is itself unconsumed

Before writing this module, grep for actual `import` statements (not
docstring/comment mentions, which several existing files use loosely) found
that `receptions_alt_ladder.py` -- despite being real, tested, and reviewed --
is imported ONLY by its own test files
(`test_receptions_alt_ladder.py`, `test_receptions_pmf_ladder_coherence_repair.py`,
`test_receptions_alt_ladder_coherence_audit.py`). No production research
module, live workflow, or challenger record-builder actually calls
`ladder_probabilities` or `evaluate_ladder_with_prices` for receptions. This
is disclosed here, not silently repeated: this module's own
`build_passing_yards_ladder_record` (below) is a REAL internal consumer of
`ladder_probabilities`/`compare_empirical_vs_normal`/
`evaluate_ladder_with_prices` -- calling it with different real inputs
provably changes its returned probabilities and EV fields (exercised by
`ScoreCandidateEndToEndTests` in this module's test file), not merely a
function that exists alongside an unrelated computed output.

## What is genuinely new here vs. reused unmodified

Reused, unmodified, imported directly (not reimplemented):
- `receptions_alt_ladder.require_real_thresholds` and
  `.verify_ladder_invariants` -- both are already fully generic (their own
  code never references receptions, only their docstrings do), so importing
  them is honest reuse, not a mislabeled dependency.
- `receptions_outcome_distribution.EmpiricalResidualPool` for its bisect-based
  `count_greater_than`/`count_less_than`/`count_equal` queries only. Its
  `pmf()` method (built around a small, non-negative-integer-bounded count
  support) is NOT used here -- passing yards spans a much wider effective
  range and this module needs only threshold comparisons, not a normalized
  pmf over the whole outcome space.
- `receptions_outcome_distribution.fit_normal` -- reused unmodified by this
  workstream's own evaluation script
  (`engineering/nfl_passing_yards_alt_ladder_20260923/
  passing_yards_alt_ladder_evaluation.py`) to fit the Normal control's
  `mean`/`std` from the real strictly-prior train partition; also fully
  generic (`actual`/`b0` field names, not receptions-specific).
- `alternate_line_evaluation.breakeven_probability`,
  `expected_value_from_probability`, `price_bucket` for all price-aware
  output -- no odds math is reimplemented here.

New in this module:
- A three-way (over/under/push) empirical-residual rung estimator adapted for
  a market with no small, bounded outcome space (`_rung_probabilities`, same
  additive-smoothing convention `receptions_alt_ladder._rung_probabilities`
  already established, re-derived here rather than importing a private
  symbol from another module).
- A parallel discretized-Normal-approximation ladder
  (`normal_ladder_probabilities`) as the PREDECLARED simpler control this
  module's own real evaluation compares the empirical-residual ladder
  against (see `engineering/nfl_passing_yards_alt_ladder_20260923/README.md`
  for the real, honestly-reported result). Its rung math
  (`_normal_rung_probabilities`) is a genuine reimplementation, not a call
  into `receptions_outcome_distribution.normal_discrete_pmf`: that function
  returns a single point mass at one non-negative integer `k` and folds all
  sub-zero Normal mass into `k=0`, which does not fit a three-way (push
  included) over/under/push split at a caller-supplied threshold that is
  usually a non-integer half-point line. `_normal_rung_probabilities`
  follows the SAME continuity-correction convention `normal_discrete_pmf`
  established (`[threshold-0.5, threshold+0.5)` at an integer threshold) via
  its own `_norm_cdf` helper. **Correction (independent review,
  2026-09-23): an earlier version of this docstring and the module's own
  import list claimed `normal_discrete_pmf` itself was reused unmodified by
  this module; it is not -- it was imported but never called anywhere in
  this file. The import has been removed and this note added instead of
  silently leaving the inaccurate reuse claim in place.**
- `compare_empirical_vs_normal` and `build_passing_yards_ladder_record`: the
  real composition layer that actually calls both ladders against the SAME
  real B0 projection (`passing_yards_shadow.current_b0_projection`, reused
  unmodified) and the SAME real caller-supplied thresholds/odds, proving the
  two methods are directly, coherently comparable candidate-by-candidate --
  not just two disconnected functions that happen to share a module.

## Real result (do not remove or soften this)

A real out-of-sample evaluation on 1,875 real 2023-2025 held-out QB-games
(9,334 real rung observations, five predeclared thresholds per game) found
the empirical-residual ladder technically clears its own predeclared
promotion rule against a Normal-approximation control (held-out Brier for
the "over" probability: 0.240432 empirical vs. 0.240469 Normal), but a
player-clustered bootstrap of that same gap (2,000 resamples, 104 real QBs)
gives a 95% interval of [-0.000276, +0.000195] -- comfortably straddling
zero. Honest finding, not softened: on this population, at these rungs, the
two methods are **effectively tied**, unlike receptions' own outcome-
distribution comparison (a real, if modest, log-likelihood edge for
NEGATIVE_BINOMIAL over NORMAL). See
`engineering/nfl_passing_yards_alt_ladder_20260923/README.md` for the full
reproducible evidence, including the real per-method calibration numbers.

## What this module does NOT do

- Never invents a threshold, a residual, or an odds price. `thresholds` and
  `residual_pool` must be real, caller-supplied evidence exactly like
  `receptions_alt_ladder.py`'s own two hard boundaries; `over_odds`/
  `under_odds` are optional per rung and are never defaulted or interpolated.
- Never fits or refits anything from wall-clock data or live state -- the
  Normal control's `mean`/`std` are always caller-supplied (typically from a
  real, disclosed strictly-prior train partition, matching every other
  distribution fit in this repository).
- Does not replace or modify `passing_yards_shadow.py`'s own single-threshold
  `empirical_side_probabilities`, is not wired into
  `nfl-live-passing-yards-shadow-board.yml` or any other workflow YAML (a
  disclosed, deliberate scope limit matching this repository's own
  established precedent of shipping a reviewed research module before a
  separate later live-wiring PR -- see `receptions_team_opportunity_
  challenger.py`'s own "no live workflow wiring in this pass" disclosure),
  and makes no selector/promotion decision. `RESEARCH_ONLY_NOT_PROMOTED`
  throughout.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import math

from nfl.research.alternate_line_evaluation import (
    breakeven_probability,
    expected_value_from_probability,
    price_bucket,
)
from nfl.research.passing_yards_shadow import current_b0_projection
from nfl.research.receptions_alt_ladder import (
    require_real_thresholds,
    verify_ladder_invariants,
)
from nfl.research.receptions_outcome_distribution import EmpiricalResidualPool

TOLERANCE = 1e-9


class PassingYardsAltLadderError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


def _rung_probabilities(
    pool: EmpiricalResidualPool,
    *,
    projection: float,
    threshold: float,
) -> dict[str, Any]:
    """Three-way (over/under/push) empirical probability for one real
    threshold, via the SAME additive (Laplace-style) smoothing convention
    `receptions_alt_ladder._rung_probabilities` already established: every
    rung sums to exactly 1 (n+3 in the denominator, +1 in each numerator).

    Re-derived here (not imported from that module's private function)
    because this is the one piece of real, non-trivial logic this module
    needs of its own -- everything genuinely reusable (thresholds
    validation, invariant verification, the residual-pool data structure
    itself) is imported directly instead.
    """
    gap = threshold - projection
    over_n = pool.count_greater_than(gap)
    under_n = pool.count_less_than(gap)
    push_n = pool.count_equal(gap)
    n = pool.n
    denominator = n + 3.0
    return {
        "threshold": threshold,
        "over": (over_n + 1.0) / denominator,
        "under": (under_n + 1.0) / denominator,
        "push": (push_n + 1.0) / denominator,
        "over_observations": over_n,
        "under_observations": under_n,
        "push_observations": push_n,
        "residual_n": n,
    }


def ladder_probabilities(
    *,
    projection: float,
    thresholds: Sequence[float],
    residual_pool: Sequence[float] | EmpiricalResidualPool,
) -> dict[str, Any]:
    """Empirical-residual multi-line ladder for real, caller-supplied
    passing-yards thresholds, from one shared real historical residual pool.

    `residual_pool` is real, caller-supplied `actual - projection` values
    from historical outcomes (e.g. the training-partition pool this module's
    own evaluation script builds from `passing_yards_baseline_research.
    rolling_predictions`) -- this function fits or invents nothing about the
    pool itself.

    Verifies its own invariants (`receptions_alt_ladder.
    verify_ladder_invariants`, reused unmodified) before returning: every
    rung's over+under+push sums to 1 within tolerance, and `over` is
    monotonically non-increasing as threshold increases.

    Also reports `probability_zero_or_below` -- the same additively-smoothed
    left-tail estimator as a threshold-0.5 rung's `under` -- as an explicit
    diagnostic field, matching `receptions_alt_ladder.ladder_probabilities`'s
    structural convention. Unlike receptions (where a real role player can
    plausibly record zero receptions in a meaningful share of games), a
    passing-role QB population (`b0` requires >= 3 prior appearances with
    positive attempts, matching `passing_yards_baseline_research.
    rolling_predictions`'s own eligibility gate) essentially always throws
    for a non-zero total, so this field is expected to be near-zero for a
    real starting/passing-role QB and is reported for structural symmetry
    with the receptions ladder and for any future third market that reuses
    this convention, not because it is expected to be a material risk here.
    """
    values = require_real_thresholds(thresholds)
    pool = residual_pool if isinstance(residual_pool, EmpiricalResidualPool) else EmpiricalResidualPool(residual_pool)

    sorted_thresholds = sorted(values)
    rungs = [
        _rung_probabilities(pool, projection=projection, threshold=threshold)
        for threshold in sorted_thresholds
    ]
    probability_zero_or_below = _rung_probabilities(pool, projection=projection, threshold=0.5)["under"]

    result = {
        "method": "EMPIRICAL_RESIDUAL_POOL",
        "projection": float(projection),
        "residual_pool_n": pool.n,
        "rungs": rungs,
        "probability_zero_or_below": probability_zero_or_below,
        "zero_mass_is_material": probability_zero_or_below >= 0.01,
    }
    verify_ladder_invariants(result)
    return result


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_rung_probabilities(
    *, mean: float, std: float, threshold: float,
) -> dict[str, Any]:
    """Three-way (over/under/push) probability for one real threshold under
    a discretized Normal(mean, std) -- the predeclared simpler control this
    module's real evaluation compares the empirical-residual ladder against.

    Real single-game passing yards are recorded as non-negative integers, so
    push mass is only physically possible at an (approximately) integer
    threshold; the continuity-corrected window [threshold-0.5, threshold+0.5)
    matches `receptions_outcome_distribution.normal_discrete_pmf`'s own
    convention. A non-integer threshold (the common real sportsbook
    half-point convention, e.g. 249.5) gets `push=0.0` by construction: a
    continuous Normal places exactly zero mass on a single non-integer point,
    and no real integer outcome can land there either.
    """
    std = max(float(std), 1e-9)
    is_integer_threshold = abs(threshold - round(threshold)) < 1e-9
    if is_integer_threshold:
        lower_z = (threshold - 0.5 - mean) / std
        upper_z = (threshold + 0.5 - mean) / std
        push = max(_norm_cdf(upper_z) - _norm_cdf(lower_z), 0.0)
        over = max(1.0 - _norm_cdf(upper_z), 0.0)
        under = max(_norm_cdf(lower_z), 0.0)
    else:
        push = 0.0
        z = (threshold - mean) / std
        over = max(1.0 - _norm_cdf(z), 0.0)
        under = max(_norm_cdf(z), 0.0)
    # Renormalize away float drift only (never to hide a real structural
    # problem): over+under+push must already be ~1.0 by construction above.
    total = over + under + push
    if total <= 0:
        raise PassingYardsAltLadderError("degenerate Normal control probabilities")
    return {
        "threshold": threshold,
        "over": over / total,
        "under": under / total,
        "push": push / total,
    }


def normal_ladder_probabilities(
    *,
    projection: float,
    thresholds: Sequence[float],
    mean_residual: float,
    std: float,
) -> dict[str, Any]:
    """Discretized-Normal-approximation control ladder for the SAME real
    thresholds `ladder_probabilities` scores, so the two are directly
    comparable rung-by-rung on the identical real candidate.

    `mean_residual` and `std` must come from a real, caller-fitted
    Normal(mean, std) of `actual - projection` residuals (e.g.
    `receptions_outcome_distribution.fit_normal`, reused unmodified, applied
    to a real strictly-prior training partition) -- this function fits
    nothing itself and never reads wall-clock state.
    """
    values = require_real_thresholds(thresholds)
    if std <= 0:
        raise PassingYardsAltLadderError(f"std must be positive: {std!r}")
    mean = float(projection) + float(mean_residual)

    sorted_thresholds = sorted(values)
    rungs = [
        _normal_rung_probabilities(mean=mean, std=std, threshold=threshold)
        for threshold in sorted_thresholds
    ]
    probability_zero_or_below = _normal_rung_probabilities(mean=mean, std=std, threshold=0.5)["under"]

    result = {
        "method": "NORMAL_APPROXIMATION_CONTROL",
        "projection": float(projection),
        "mean": mean,
        "std": float(std),
        "rungs": rungs,
        "probability_zero_or_below": probability_zero_or_below,
        "zero_mass_is_material": probability_zero_or_below >= 0.01,
    }
    verify_ladder_invariants(result)
    return result


def compare_empirical_vs_normal(
    *,
    projection: float,
    thresholds: Sequence[float],
    residual_pool: Sequence[float] | EmpiricalResidualPool,
    mean_residual: float,
    std: float,
) -> dict[str, Any]:
    """Score the SAME real candidate (projection + thresholds) with BOTH
    ladder methods and return them side by side, including a per-rung
    absolute probability gap -- the real, direct consumer this module needed
    to avoid repeating `receptions_alt_ladder.py`'s own disclosed
    unconsumed-module gap (see module docstring).
    """
    empirical = ladder_probabilities(projection=projection, thresholds=thresholds, residual_pool=residual_pool)
    normal = normal_ladder_probabilities(
        projection=projection, thresholds=thresholds, mean_residual=mean_residual, std=std,
    )
    empirical_by_threshold = {rung["threshold"]: rung for rung in empirical["rungs"]}
    normal_by_threshold = {rung["threshold"]: rung for rung in normal["rungs"]}
    gaps = [
        {
            "threshold": threshold,
            "absolute_over_probability_gap": abs(
                empirical_by_threshold[threshold]["over"] - normal_by_threshold[threshold]["over"]
            ),
        }
        for threshold in sorted(empirical_by_threshold)
    ]
    return {
        "projection": float(projection),
        "empirical": empirical,
        "normal_control": normal,
        "rung_gaps": gaps,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


def evaluate_ladder_with_prices(
    *,
    projection: float,
    priced_rungs: Sequence[Mapping[str, Any]],
    residual_pool: Sequence[float] | EmpiricalResidualPool,
    evidence_status: str = "UNVALIDATED_RESEARCH",
) -> dict[str, Any]:
    """Empirical-residual ladder probabilities plus break-even/EV/price-bucket
    for real, caller-supplied odds only, wired entirely through
    `alternate_line_evaluation.py` (imported, never reimplemented here) --
    the same structural convention `receptions_alt_ladder.
    evaluate_ladder_with_prices` already established for receptions.

    `priced_rungs` is a sequence of mappings, each with a real `threshold`
    and optionally real `over_odds`/`under_odds` (American odds from an
    actual captured quote). A rung's price may be omitted; this function
    never fabricates a missing price -- the corresponding EV/breakeven
    fields are `None` for that side.

    `evidence_status` defaults to `"UNVALIDATED_RESEARCH"` (the only other
    allowed value is `"PROSPECTIVELY_VALIDATED"`, which this module has no
    basis to claim); every EV result therefore carries
    `expected_value_is_provisional=True` unless the caller explicitly
    overrides it with real prospective evidence backing it.
    """
    if not priced_rungs:
        raise PassingYardsAltLadderError("priced_rungs must be non-empty (never invented by this function)")
    thresholds = [float(rung["threshold"]) for rung in priced_rungs]
    ladder = ladder_probabilities(projection=projection, thresholds=thresholds, residual_pool=residual_pool)
    rungs_by_threshold = {rung["threshold"]: rung for rung in ladder["rungs"]}

    priced_results = []
    for priced_rung in priced_rungs:
        threshold = float(priced_rung["threshold"])
        probabilities = rungs_by_threshold[threshold]
        entry: dict[str, Any] = {
            "threshold": threshold,
            "model_over_probability": probabilities["over"],
            "model_under_probability": probabilities["under"],
            "model_push_probability": probabilities["push"],
            "over_price": None,
            "under_price": None,
        }
        over_odds = priced_rung.get("over_odds")
        under_odds = priced_rung.get("under_odds")
        if over_odds is not None:
            entry["over_price"] = {
                "odds": over_odds,
                "price_bucket": price_bucket(over_odds),
                "breakeven_probability": breakeven_probability(over_odds),
                **expected_value_from_probability(
                    probabilities["over"], over_odds, evidence_status=evidence_status
                ),
            }
        if under_odds is not None:
            entry["under_price"] = {
                "odds": under_odds,
                "price_bucket": price_bucket(under_odds),
                "breakeven_probability": breakeven_probability(under_odds),
                **expected_value_from_probability(
                    probabilities["under"], under_odds, evidence_status=evidence_status
                ),
            }
        priced_results.append(entry)

    return {
        "projection": ladder["projection"],
        "residual_pool_n": ladder["residual_pool_n"],
        "probability_zero_or_below": ladder["probability_zero_or_below"],
        "zero_mass_is_material": ladder["zero_mass_is_material"],
        "priced_rungs": priced_results,
        "evidence_status": evidence_status,
    }


def build_passing_yards_ladder_record(
    *,
    player_id: str,
    prior_appearances: Sequence[dict],
    thresholds: Sequence[float],
    residual_pool: Sequence[float] | EmpiricalResidualPool,
    mean_residual: float,
    std: float,
    priced_rungs: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble one real, end-to-end passing-yards ladder record: real prior
    appearances -> real B0 projection (`passing_yards_shadow.
    current_b0_projection`, reused unmodified) -> BOTH real ladder methods
    at the SAME real thresholds (`compare_empirical_vs_normal`) -> optional
    real priced EV (`evaluate_ladder_with_prices`) for the empirical method.

    Explicit abstention: if `current_b0_projection` cannot compute a
    projection from the real supplied history (fewer than three prior
    appearances, or no positive prior passing-attempt role -- the exact two
    real conditions that function itself raises on), this returns a
    labeled abstention record rather than propagating the exception or
    fabricating a projection. Any OTHER unexpected failure (a malformed
    `thresholds`/`residual_pool`/price argument) still raises, since that is
    a real caller bug, not an expected insufficient-evidence case.
    """
    try:
        b0 = current_b0_projection(prior_appearances)
    except ValueError as exc:
        return {
            "player_id": player_id,
            "status": "ABSTAIN_INSUFFICIENT_B0_HISTORY",
            "reason": str(exc),
        }

    projection = b0["projection"]
    comparison = compare_empirical_vs_normal(
        projection=projection, thresholds=thresholds, residual_pool=residual_pool,
        mean_residual=mean_residual, std=std,
    )
    record: dict[str, Any] = {
        "player_id": player_id,
        "b0": b0,
        "comparison": comparison,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }
    if priced_rungs:
        record["priced_empirical"] = evaluate_ladder_with_prices(
            projection=projection, priced_rungs=priced_rungs, residual_pool=residual_pool,
        )
    return record


__all__ = [
    "PassingYardsAltLadderError",
    "ladder_probabilities",
    "normal_ladder_probabilities",
    "compare_empirical_vs_normal",
    "evaluate_ladder_with_prices",
    "build_passing_yards_ladder_record",
]
