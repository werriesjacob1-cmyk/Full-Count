#!/usr/bin/env python3
"""Research-only alternate-line ladder for the receptions market.

Per Issue #91 workstream `NFL-OUTCOME-DISTRIBUTION-EXPERIMENT-20260919`,
item 3-4: given a real B0 projection and a real, historical residual pool
(reusing the same pooled-residual convention
`receptions_shadow.empirical_side_probabilities` already uses -- not a new
data source), compute over/under/exact-push probability at an arbitrary set
of caller-supplied thresholds, and wire break-even/EV/price-bucket math
through the EXISTING `alternate_line_evaluation.py` functions on real,
caller-supplied odds only.

Two hard boundaries, matching `alternate_line_evaluation.py`'s own:

1. Thresholds are never invented. `ladder_probabilities` requires the caller
   to pass real, already-quoted lines; `require_real_thresholds` raises if
   the sequence is empty.
2. Odds are never invented. `evaluate_ladder_with_prices` only produces
   breakeven/EV output for a rung where the caller actually supplied a real
   `over_odds`/`under_odds` value for that side; a rung with no supplied
   price gets `None` for that side's EV fields rather than a guessed price.

Zero-opportunity mass point: unlike a market like passing yards (a starting
QB who plays almost always throws for a non-zero total), a real receptions
outcome can land on exactly zero even for a genuine role player who was
targeted -- see `receptions_outcome_distribution.py`'s module docstring for
the measured real zero rate (~8-10% of the real historical population,
rising as high as ~15-25% for the lowest-opportunity tier). This module
therefore always reports `zero_probability` as its own explicit field
(from the real residual pool, Laplace-smoothed, never silently rounded to
0), rather than only exposing the over/under probabilities at
caller-supplied thresholds. A true "player did not play at all"
(inactive/DNP) case is explicitly OUT OF SCOPE here: that is a
settlement-layer VOID, not a modeled outcome, and is already handled by
this repo's existing settlement/grading infrastructure
(`alternate_line_evaluation.SETTLEMENT_OUTCOMES` has a dedicated `VOID`
outcome) -- the population this module's residual pool is built from
(`receptions_baseline_research.load_receiver_rows`'s
`effective_targets > 0` role gate) only ever contains real games where the
player recorded receiving-relevant activity, so `zero_probability` here
specifically means "played, was a role player historically, recorded zero
receptions in the target game" -- a genuinely bettable UNDER outcome, not a
DNP.

Disclosed design note -- `zero_probability` and a rung's `under` at a
low threshold are DELIBERATELY not the same estimator, and real data shows
they can disagree by a material amount: `zero_probability` uses a narrow
window centered on the exact target outcome
(`EmpiricalResidualPool.pmf`), while a rung's `over`/`under` use the full
tail of the pool on either side of the threshold gap (the same convention
`receptions_shadow.empirical_side_probabilities` already uses). Both are
legitimate nonparametric estimators of the same real quantity, but the
full-tail convention implicitly assumes the residual distribution's shape
does not depend on the specific projection level of the historical rows
that make up the pool -- and `receptions_outcome_distribution.py`
empirically found real, confirmed heteroskedasticity that violates this
assumption (pooled residual spread roughly doubles from the lowest to the
highest opportunity tier). This function does not resolve that tension by
picking a "best" pool for the caller: it operates on whatever real
`residual_pool` is supplied, and a caller who wants a better-calibrated
ladder for a specific player should pass a pool already restricted to that
player's own opportunity/role bucket (see
`receptions_outcome_distribution.projection_bucket`) rather than the full,
unstratified historical population. This is a real, disclosed limitation
inherited from the existing pooled-residual convention this codebase
already uses, not a defect introduced here -- see
`nfl/tests/test_receptions_alt_ladder.py` for a direct demonstration on
real data of how much the pooled vs. bucket-restricted pool answers can
differ for a low-opportunity projection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.research.alternate_line_evaluation import (
    breakeven_probability,
    expected_value_from_probability,
    price_bucket,
)
from nfl.research.receptions_outcome_distribution import EmpiricalResidualPool

TOLERANCE = 1e-9


class ReceptionsAltLadderError(ValueError):
    pass


def require_real_thresholds(thresholds: Sequence[float]) -> list[float]:
    """Fail closed on an empty/invalid threshold sequence.

    The caller must supply real, already-quoted lines -- this function does
    no defaulting, interpolation, or invention of a threshold.
    """
    if not isinstance(thresholds, Sequence) or isinstance(thresholds, (str, bytes)):
        raise ReceptionsAltLadderError("thresholds must be a sequence of real quoted lines")
    if not thresholds:
        raise ReceptionsAltLadderError("thresholds must be non-empty (never invented by this function)")
    values = []
    for value in thresholds:
        try:
            values.append(float(value))
        except (TypeError, ValueError) as exc:
            raise ReceptionsAltLadderError(f"non-numeric threshold: {value!r}") from exc
    return values


def _rung_probabilities(
    pool: EmpiricalResidualPool,
    *,
    projection: float,
    threshold: float,
) -> dict[str, Any]:
    """Three-way (over/under/push) probability for one real threshold.

    Uses additive (Laplace-style) smoothing across all three outcomes so
    every rung sums to exactly 1 (n+3 in the denominator, +1 in each
    numerator) -- a direct three-category generalization of the two-way
    smoothing `receptions_shadow.empirical_side_probabilities` already uses,
    needed here because this ladder reports push as its own real category
    rather than folding it 50/50 into over/under.
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
    """Compute over/under/push probability at each real caller-supplied
    threshold from a real historical residual pool, plus the explicit
    exact-zero mass point.

    `residual_pool` is real, caller-supplied `actual - projection` values
    from historical outcomes (e.g. the training-partition pool
    `receptions_outcome_distribution.py` builds) -- this function fits or
    invents nothing about the pool itself, and never estimates a
    probability from fewer than 1 real observation (the constructor of
    `EmpiricalResidualPool` rejects an empty pool).

    Verifies its own invariants before returning: every rung's
    over+under+push sums to 1 within tolerance, and `over` probability is
    monotonically non-increasing as threshold increases (a fixed pool means
    a strictly higher threshold can never include more "over" observations
    than a lower one) -- see `verify_ladder_invariants` for the same checks
    exposed standalone for tests to exercise directly on a result this
    function did not itself produce.
    """
    values = require_real_thresholds(thresholds)
    pool = residual_pool if isinstance(residual_pool, EmpiricalResidualPool) else EmpiricalResidualPool(residual_pool)

    sorted_thresholds = sorted(values)
    rungs = [
        _rung_probabilities(pool, projection=projection, threshold=threshold)
        for threshold in sorted_thresholds
    ]
    zero_probability = pool.pmf(0, projection)

    result = {
        "projection": float(projection),
        "residual_pool_n": pool.n,
        "rungs": rungs,
        "zero_probability": zero_probability,
        "zero_mass_is_material": zero_probability >= 0.01,
    }
    verify_ladder_invariants(result)
    return result


def verify_ladder_invariants(ladder_result: Mapping[str, Any], *, tol: float = TOLERANCE) -> None:
    """Raise if a ladder result violates sum-to-one or monotonicity.

    Exposed standalone (not only called internally by `ladder_probabilities`)
    so tests can feed it a hand-built ladder result and assert it correctly
    detects a violation, not only that the real code path happens to satisfy
    it.
    """
    rungs = ladder_result.get("rungs")
    if not rungs:
        raise ReceptionsAltLadderError("ladder_result has no rungs to verify")

    for rung in rungs:
        total = rung["over"] + rung["under"] + rung["push"]
        if abs(total - 1.0) > tol:
            raise ReceptionsAltLadderError(
                f"threshold {rung['threshold']}: over+under+push = {total} (expected ~1.0)"
            )

    sorted_rungs = sorted(rungs, key=lambda rung: rung["threshold"])
    previous_over = None
    for rung in sorted_rungs:
        if previous_over is not None and rung["over"] > previous_over + tol:
            raise ReceptionsAltLadderError(
                f"over probability increased at threshold {rung['threshold']} "
                f"({rung['over']} > {previous_over}) -- not monotonically non-increasing"
            )
        previous_over = rung["over"]


def evaluate_ladder_with_prices(
    *,
    projection: float,
    priced_rungs: Sequence[Mapping[str, Any]],
    residual_pool: Sequence[float] | EmpiricalResidualPool,
    evidence_status: str = "UNVALIDATED_RESEARCH",
) -> dict[str, Any]:
    """Ladder probabilities plus break-even/EV/price-bucket for real, caller-
    supplied odds only, wired entirely through `alternate_line_evaluation.py`
    (imported, never reimplemented here).

    `priced_rungs` is a sequence of mappings, each with a real `threshold`
    and optionally real `over_odds` and/or `under_odds` (American odds from
    an actual captured quote). A rung's `over_odds`/`under_odds` may be
    omitted; this function never fabricates a missing price -- the
    corresponding EV/breakeven fields are `None` for that side.

    This is backtested historical research, not prospectively validated
    evidence, so `evidence_status` defaults to `"UNVALIDATED_RESEARCH"`
    (the only other allowed value is `"PROSPECTIVELY_VALIDATED"`, which
    this module has no basis to claim); every EV result therefore carries
    `expected_value_is_provisional=True` unless the caller explicitly
    overrides `evidence_status` with real prospective evidence backing it.
    """
    if not priced_rungs:
        raise ReceptionsAltLadderError("priced_rungs must be non-empty (never invented by this function)")
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
        "zero_probability": ladder["zero_probability"],
        "zero_mass_is_material": ladder["zero_mass_is_material"],
        "priced_rungs": priced_results,
        "evidence_status": evidence_status,
    }

