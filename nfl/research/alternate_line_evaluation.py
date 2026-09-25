"""Research-only foundation for plus-money / alternate-line evaluation.

Per Issue #91 comment 5742555668 (items 2-3): a first-class, price-aware
evaluation surface for genuinely quoted sportsbook prices -- positive-odds vs
negative-odds buckets, implied break-even probability, push/void handling,
and unit profit/ROI -- that never substitutes for FULL COUNT's equal-volume
realized-hit-rate north star and never fabricates a historical or offered
price.

Two hard boundaries enforced throughout this module:

1. Every function that touches a price takes that price as an explicit
   argument supplied by the caller from a real, captured quote. Nothing here
   invents, interpolates, or defaults a price.
2. A model-derived win probability is never presented as a validated
   forecast. `expected_value_from_probability` requires the caller to state
   the probability's `evidence_status` and always returns
   `expected_value_is_provisional=True` unless that status is exactly
   `PROSPECTIVELY_VALIDATED` -- a point projection is provisional until
   prospective evidence says otherwise.

This module does not fetch, capture, or publish anything, and is not wired
into any selector or public pick.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nfl.research.passing_yards_shadow import american_implied_probability

# Real settlement outcomes a graded selection can have. VOID and PUSH both
# return stake with zero profit/loss but are tracked separately because they
# mean different things (a canceled/ungraded market vs. a push on the exact
# line) -- collapsing them would hide a real data-quality distinction.
SETTLEMENT_OUTCOMES = {"HIT", "MISS", "PUSH", "VOID"}

# Ascending order of how far a market has actually gotten, matching
# market_registry.py's ALLOWED_STATUSES. Used only to give a single,
# consistent ordering for `require_supported_market`'s threshold check.
STATUS_ORDER = [
    "UNSUPPORTED",
    "DEFERRED",
    "BLOCKED",
    "RESEARCH",
    "IMPLEMENTED",
    "TESTED",
    "LIVE_SHADOW",
    "VALIDATED",
]

EVIDENCE_STATUSES = {"UNVALIDATED_RESEARCH", "PROSPECTIVELY_VALIDATED"}


class UnsupportedMarketError(ValueError):
    """A market's registry status does not meet the caller's required floor."""


class AlternateLineEvaluationError(ValueError):
    pass


def require_supported_market(
    market_id: str,
    market_status: str,
    *,
    minimum_status: str = "TESTED",
) -> None:
    """Fail closed unless `market_status` is at or above `minimum_status`.

    Callers must supply `market_status` themselves (e.g. read from
    `market_registry.json` via `market_registry.py`) -- this function does no
    file I/O and trusts nothing it wasn't explicitly given.
    """
    if market_status not in STATUS_ORDER:
        raise UnsupportedMarketError(f"unknown market_status: {market_status!r}")
    if minimum_status not in STATUS_ORDER:
        raise AlternateLineEvaluationError(f"unknown minimum_status: {minimum_status!r}")
    if STATUS_ORDER.index(market_status) < STATUS_ORDER.index(minimum_status):
        raise UnsupportedMarketError(
            f"{market_id}: status {market_status} is below required floor {minimum_status}"
        )


def _finite_odds(odds: Any) -> int:
    try:
        value = int(odds)
    except (TypeError, ValueError) as exc:
        raise AlternateLineEvaluationError(f"non-integer American odds: {odds!r}") from exc
    if value == 0 or -100 < value < 100:
        raise AlternateLineEvaluationError(f"American odds cannot be in (-100, 100): {value}")
    return value


def price_bucket(odds: Any) -> str:
    """Classify one real quoted price as PLUS_MONEY or MINUS_MONEY."""
    value = _finite_odds(odds)
    return "PLUS_MONEY" if value > 0 else "MINUS_MONEY"


def breakeven_probability(odds: Any) -> float:
    """Implied break-even win probability for one real quoted American price.

    Reuses `passing_yards_shadow.american_implied_probability` rather than
    duplicating this pure odds math -- see that module and
    `receptions_shadow.py` for the established precedent of importing this
    function instead of re-deriving it per market.
    """
    return american_implied_probability(_finite_odds(odds))


def decimal_payout_per_unit(odds: Any) -> float:
    """Total return per 1 staked unit on a HIT, stake included (e.g. 2.0 for +100)."""
    value = _finite_odds(odds)
    if value > 0:
        return 1.0 + value / 100.0
    return 1.0 + 100.0 / abs(value)


def settle_selection(odds: Any, outcome: str) -> float:
    """Unit profit/loss for one already-graded real selection at 1 unit staked.

    HIT: decimal payout minus the staked unit. MISS: the staked unit is lost.
    PUSH/VOID: stake is returned, net zero -- tracked as zero profit, not
    excluded, so a caller summing raw records never silently drops a row.
    """
    outcome = str(outcome or "").strip().upper()
    if outcome not in SETTLEMENT_OUTCOMES:
        raise AlternateLineEvaluationError(f"unknown settlement outcome: {outcome!r}")
    if outcome == "HIT":
        return decimal_payout_per_unit(odds) - 1.0
    if outcome == "MISS":
        return -1.0
    return 0.0


def summarize_price_aware_performance(
    records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate real, already-graded selections into price-aware performance.

    Each record must have real `odds` (American) and a real `outcome` from
    `SETTLEMENT_OUTCOMES` -- this function grades nothing itself and infers
    no missing prices. ROI is computed over `graded_units`
    (HIT + MISS count) only: a PUSH/VOID selection had no realized risk, so
    including it in the ROI denominator would understate real performance on
    settled action. This is a separate, price-aware view -- it does not
    replace FULL COUNT's equal-volume realized-hit-rate north star.
    """
    if not isinstance(records, Sequence):
        raise AlternateLineEvaluationError("records must be a sequence")

    counts = {outcome: 0 for outcome in SETTLEMENT_OUTCOMES}
    bucket_counts = {"PLUS_MONEY": 0, "MINUS_MONEY": 0}
    total_profit_units = 0.0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AlternateLineEvaluationError(f"records[{index}] must be a mapping")
        odds = record.get("odds")
        outcome = str(record.get("outcome") or "").strip().upper()
        if outcome not in SETTLEMENT_OUTCOMES:
            raise AlternateLineEvaluationError(
                f"records[{index}]: unknown settlement outcome {record.get('outcome')!r}"
            )
        counts[outcome] += 1
        bucket_counts[price_bucket(odds)] += 1
        total_profit_units += settle_selection(odds, outcome)

    graded_units = counts["HIT"] + counts["MISS"]
    hit_rate = counts["HIT"] / graded_units if graded_units else None
    roi = total_profit_units / graded_units if graded_units else None

    return {
        "n_records": len(records),
        "outcome_counts": dict(counts),
        "price_bucket_counts": bucket_counts,
        "graded_units": graded_units,
        "hit_rate_on_graded_units": hit_rate,
        "total_profit_units": total_profit_units,
        "roi_per_graded_unit": roi,
    }


def expected_value_from_probability(
    probability: float,
    odds: Any,
    *,
    evidence_status: str,
) -> dict[str, Any]:
    """Provisional expected value from a caller-supplied win probability.

    This does not estimate `probability` itself -- it is always supplied by
    the caller (a model, a baseline, a research finding) and this function
    never treats it as ground truth. The result is only ever presented as
    validated when `evidence_status == "PROSPECTIVELY_VALIDATED"`; any other
    status (including ordinary historical/backtested research) is marked
    `expected_value_is_provisional=True`, matching the mission's requirement
    to never treat a point prediction as a validated tail probability.
    """
    if evidence_status not in EVIDENCE_STATUSES:
        raise AlternateLineEvaluationError(f"unknown evidence_status: {evidence_status!r}")
    try:
        p = float(probability)
    except (TypeError, ValueError) as exc:
        raise AlternateLineEvaluationError(f"non-numeric probability: {probability!r}") from exc
    if not (0.0 <= p <= 1.0):
        raise AlternateLineEvaluationError(f"probability out of [0,1]: {p}")

    payout = decimal_payout_per_unit(odds)
    expected_value_units = p * (payout - 1.0) - (1.0 - p) * 1.0
    breakeven = breakeven_probability(odds)

    return {
        "probability": p,
        "breakeven_probability": breakeven,
        "edge_vs_breakeven": p - breakeven,
        "expected_value_units_per_unit_staked": expected_value_units,
        "evidence_status": evidence_status,
        "expected_value_is_provisional": evidence_status != "PROSPECTIVELY_VALIDATED",
    }
