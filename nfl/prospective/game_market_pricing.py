"""Price math and fail-closed research-lean gates for NFL spread/total offers.

Model probability and sportsbook implied probability are different objects:
- implied probability: 1 / decimal odds of ONE side (contains the vig);
- no-vig probability: both sides' implied probabilities normalised to sum to 1
  (requires both sides; a one-sided market has no no-vig value);
- break-even: the win probability, conditional on no push, at which the
  captured price has zero expected value (= implied probability);
- model probability: from a ``GameDistribution`` (win, push, lose);
- edge: model conditional win probability minus break-even;
- EV: expected profit per 1 unit staked = win * (decimal - 1) - lose
  (a push returns the stake).

Gates are fixed by FC-NFL-004 PREREG and are never loosened after outcomes. A
``RESEARCH_LEAN`` is a measurement label, never a pick and never public.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

LEAN_CONFIG = "C1"
LEAN_MARKETS = frozenset({"spread", "game_total"})
MAX_QUOTE_AGE_MINUTES = 90.0
MIN_EV = 0.03
MAX_MODEL_MARKET_GAP = {"spread": 6.0, "game_total": 8.0}

RESEARCH_LEAN = "RESEARCH_LEAN"
NO_LEAN = "NO_LEAN"
QUARANTINED = "QUARANTINED"
MEASUREMENT_ONLY = "MEASUREMENT_ONLY"

# Reasons that make a selection QUARANTINED (evidence is unsafe or incomplete),
# as opposed to NO_LEAN (evidence is sound but the price is not good enough).
QUARANTINE_REASONS = frozenset({
    "IDENTITY_UNBOUND",
    "MARKET_NOT_NORMALIZED",
    "MISSING_PRICE",
    "ONE_SIDED_MARKET",
    "STALE_QUOTE",
    "QUOTE_AFTER_FREEZE",
    "POST_KICKOFF_FREEZE",
    "MODEL_INELIGIBLE",
    "QB_CHANGE_UNMODELLED",
    "QB_STATE_UNKNOWN",
    "MODEL_MARKET_GAP_IMPLAUSIBLE",
})


class GameMarketPricingError(ValueError):
    """Raised when a price cannot be interpreted safely."""


def american_to_decimal(odds: Any) -> float:
    if isinstance(odds, bool) or not isinstance(odds, (int, float)):
        raise GameMarketPricingError("american odds must be numeric")
    value = float(odds)
    if not math.isfinite(value) or not value.is_integer() or -100 < value < 100:
        raise GameMarketPricingError(f"invalid american odds: {odds!r}")
    return 1.0 + (value / 100.0 if value > 0 else 100.0 / -value)


def implied_probability(odds: Any) -> float:
    return 1.0 / american_to_decimal(odds)


def no_vig_pair(odds_a: Any, odds_b: Any) -> tuple[float, float, float]:
    """(p_a, p_b, overround) by multiplicative normalisation. Both sides required."""
    if odds_a is None or odds_b is None:
        raise GameMarketPricingError("no-vig requires both sides")
    ia, ib = implied_probability(odds_a), implied_probability(odds_b)
    total = ia + ib
    if total < 1.0 - 1e-9:
        # An arbitrage-shaped pair from one book is a capture defect, not value.
        raise GameMarketPricingError("two-sided implied probability below 1")
    return ia / total, ib / total, total - 1.0


def evaluate_price(outcome: tuple[float, float, float], odds: Any) -> dict[str, float]:
    win, push, lose = (float(x) for x in outcome)
    if min(win, push, lose) < -1e-12 or not math.isclose(win + push + lose, 1.0, abs_tol=1e-9):
        raise GameMarketPricingError("outcome must be a probability triple")
    if win + lose <= 0:
        raise GameMarketPricingError("outcome has no decisive mass")
    decimal = american_to_decimal(odds)
    break_even = 1.0 / decimal
    cond_win = win / (win + lose)
    return {
        "p_win": win,
        "p_push": push,
        "p_lose": lose,
        "p_win_conditional": cond_win,
        "break_even": break_even,
        "edge": cond_win - break_even,
        "ev_per_unit": win * (decimal - 1.0) - lose,
    }


def lean_decision(
    *,
    market: str,
    gate_reasons: Iterable[str],
    lean_eval: Mapping[str, float] | None,
    model_market_gap: float | None,
) -> tuple[str, list[str]]:
    """Return (decision, reasons). Reasons are sorted, explicit and complete."""
    reasons = set(gate_reasons)
    if market not in LEAN_MARKETS:
        return MEASUREMENT_ONLY, sorted(reasons | {"MARKET_NOT_IN_LEAN_SCOPE"})
    if model_market_gap is not None and model_market_gap > MAX_MODEL_MARKET_GAP[market]:
        reasons.add("MODEL_MARKET_GAP_IMPLAUSIBLE")
    if lean_eval is None:
        reasons.add("MODEL_INELIGIBLE")
    if reasons & QUARANTINE_REASONS:
        return QUARANTINED, sorted(reasons)
    if lean_eval["ev_per_unit"] < MIN_EV:
        return NO_LEAN, sorted(reasons | {"EV_BELOW_THRESHOLD"})
    return RESEARCH_LEAN, sorted(reasons)
