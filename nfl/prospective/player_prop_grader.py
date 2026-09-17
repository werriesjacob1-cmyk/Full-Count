"""Deterministic, outcome-only grading for normalized NFL player-prop markets.

Sibling to ``game_market_grader.py``, same fail-closed discipline: consumes a
bound candidate record -- the output of either ``player_prop_markets.normalize_payload``
plus ``player_prop_roster_binding.bind_player_prop_candidate``, or
``fanduel_passing.normalize_payload`` plus
``market_roster_binding.bind_passing_candidate`` for ``passing_yards`` itself
-- and one authoritative box-score outcome, and never selects bets, estimates
probabilities, or mutates either input.

Settlement rules, one per market shape:

- PRIMARY (paired OVER/UNDER): HIT if stat_value is strictly better than the
  line for the selected side, MISS if strictly worse, PUSH on exact equality
  -- the same OVER/UNDER logic as ``game_market_grader``'s game_total.
- ALT_LADDER (one-sided "N+" threshold): HIT if stat_value >= threshold, else
  MISS. There is no PUSH state for a one-sided threshold bet.
- SINGLE_THRESHOLD, touchdown-count markets (anytime/2+/3+/4+ touchdowns):
  HIT if stat_value >= threshold, else MISS.
- SINGLE_THRESHOLD, record_a_sack: HIT if stat_value > 0, else MISS. This is
  a deliberate exception to the >= threshold rule above: a credited half-sack
  (shared between two defenders) still settles as a HIT on this market by
  standard sportsbook convention, so the comparison is "recorded any sack
  credit" rather than "recorded >= 1.0 whole sack".

VOID_DNP (new, beyond game_market_grader's HIT/MISS/PUSH/UNRESOLVED_TIE) is
checked before any of the above and overrides them: a player who has a market
posted but does not appear at all in the final box score (outcome["appeared"]
is False) grades VOID_DNP, not a false MISS/UNDER at an unearned stat_value
of zero. See nfl/docs/PLAYER_PROP_SETTLEMENT_SPEC.md.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

PRIMARY_MARKETS = {
    "passing_yards", "passing_touchdowns", "rushing_yards", "receiving_yards",
    "receptions", "rush_plus_rec_yards",
}
ALT_LADDER_MARKETS = {
    "passing_touchdowns_alt", "rushing_yards_alt", "receiving_yards_alt",
    "receptions_alt",
}
SINGLE_THRESHOLD_MARKETS = {
    "anytime_touchdown", "two_plus_touchdowns", "three_plus_touchdowns",
    "four_plus_touchdowns", "record_a_sack",
}
SUPPORTED_MARKETS = PRIMARY_MARKETS | ALT_LADDER_MARKETS | SINGLE_THRESHOLD_MARKETS
PRIMARY_SIDES = {"OVER", "UNDER"}


class PlayerPropGradeError(ValueError):
    """Raised when a player-prop market/outcome pair cannot be graded safely."""


def _required_text(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlayerPropGradeError(f"{key} must be a non-empty string")
    return value.strip()


def _aware_datetime(obj: Mapping[str, Any], key: str) -> datetime:
    raw = _required_text(obj, key)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PlayerPropGradeError(f"{key} must be ISO-8601") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise PlayerPropGradeError(f"{key} must be timezone-aware")
    return value


def _number(obj: Mapping[str, Any], key: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerPropGradeError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise PlayerPropGradeError(f"{key} must be finite")
    return result


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def grade_player_prop_market(
    market: Mapping[str, Any], outcome: Mapping[str, Any], *, side: str | None = None
) -> dict[str, Any]:
    """Grade one bound player-prop candidate against one authoritative outcome.

    ``side`` is required (OVER or UNDER) for PRIMARY markets and ignored for
    ALT_LADDER and SINGLE_THRESHOLD markets, which have exactly one side.
    """
    if not isinstance(market, Mapping) or not isinstance(outcome, Mapping):
        raise PlayerPropGradeError("market and outcome must be mappings")

    market_copy = deepcopy(dict(market))
    outcome_copy = deepcopy(dict(outcome))

    canonical_market = _required_text(market, "market").lower()
    if canonical_market not in SUPPORTED_MARKETS:
        raise PlayerPropGradeError(f"unsupported market: {canonical_market}")

    if _required_text(market, "binding_status") != "BOUND":
        raise PlayerPropGradeError("market must be a BOUND candidate")
    gsis_id = _required_text(market, "gsis_id")

    event_id = _required_text(market, "event_id")
    market_id = _required_text(market, "market_id")
    if _required_text(outcome, "event_id") != event_id:
        raise PlayerPropGradeError("event_id mismatch")
    if _required_text(outcome, "gsis_id") != gsis_id:
        raise PlayerPropGradeError("gsis_id mismatch")

    market_status = str(market.get("market_status") or "").upper()
    if market_status and market_status in {"CLOSED", "SUSPENDED"}:
        raise PlayerPropGradeError("market must not be closed/suspended")
    if bool(market.get("in_play")):
        raise PlayerPropGradeError("market must be pregame")

    captured_at = _aware_datetime(market, "captured_at")
    market_time = _aware_datetime(market, "market_time")
    if captured_at >= market_time:
        raise PlayerPropGradeError("market must be captured strictly before market_time")

    if _required_text(outcome, "final_status").upper() != "FINAL":
        raise PlayerPropGradeError("outcome is not final")

    selected_side: str | None = None
    if canonical_market in PRIMARY_MARKETS:
        selected_side = str(side or "").strip().upper()
        if selected_side not in PRIMARY_SIDES:
            raise PlayerPropGradeError(
                f"unsupported side {selected_side or '<empty>'} for {canonical_market}"
            )
        line = _number(market, "line")
        threshold = None
    else:
        line = None
        threshold = _number(market, "threshold")
        if threshold <= 0:
            raise PlayerPropGradeError("threshold must be positive")

    appeared = outcome.get("appeared")
    if not isinstance(appeared, bool):
        raise PlayerPropGradeError("outcome.appeared must be a boolean")

    if not appeared:
        settlement = "VOID_DNP"
        stat_value: float | None = None
    else:
        stat_value = _number(outcome, "stat_value")
        if canonical_market in PRIMARY_MARKETS:
            if math.isclose(stat_value, line, rel_tol=0.0, abs_tol=1e-9):
                settlement = "PUSH"
            elif selected_side == "OVER":
                settlement = "HIT" if stat_value > line else "MISS"
            else:
                settlement = "HIT" if stat_value < line else "MISS"
        elif canonical_market == "record_a_sack":
            settlement = "HIT" if stat_value > 0.0 else "MISS"
        else:
            settlement = "HIT" if stat_value >= threshold else "MISS"

    evidence = {
        "market": market_copy,
        "outcome": outcome_copy,
        "side": selected_side,
        "settlement": settlement,
    }
    return {
        "event_id": event_id,
        "market_id": market_id,
        "canonical_market": canonical_market,
        "gsis_id": gsis_id,
        "side": selected_side,
        "line": line,
        "threshold": threshold,
        "stat_value": stat_value,
        "settlement": settlement,
        "grade_sha256": _canonical_sha256(evidence),
    }
