"""Deterministic, outcome-only grading for prospective NFL game markets.

This module intentionally does not select bets, estimate probabilities, or modify
pregame evidence.  It grades one immutable, pre-kickoff market observation
against one authoritative final-score record and fails closed on ambiguous input.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


SUPPORTED_MARKETS = {"MONEYLINE", "SPREAD", "TOTAL"}
MARKET_SIDES = {
    "MONEYLINE": {"HOME", "AWAY"},
    "SPREAD": {"HOME", "AWAY"},
    "TOTAL": {"OVER", "UNDER"},
}


class GameMarketGradeError(ValueError):
    """Raised when a market/outcome pair cannot be graded safely."""


def _required_text(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GameMarketGradeError(f"{key} must be a non-empty string")
    return value.strip()


def _aware_datetime(obj: Mapping[str, Any], key: str) -> datetime:
    raw = _required_text(obj, key)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GameMarketGradeError(f"{key} must be ISO-8601") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise GameMarketGradeError(f"{key} must be timezone-aware")
    return value


def _score(obj: Mapping[str, Any], key: str) -> int:
    value = obj.get(key)
    if type(value) is not int or value < 0:  # bool is intentionally rejected.
        raise GameMarketGradeError(f"{key} must be a non-negative integer")
    return value


def _line(market: Mapping[str, Any], market_type: str) -> float | None:
    value = market.get("line")
    if market_type == "MONEYLINE":
        if value is not None:
            raise GameMarketGradeError("moneyline line must be null")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameMarketGradeError("line must be numeric for spread/total")
    return float(value)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def grade_game_market(
    market: Mapping[str, Any], outcome: Mapping[str, Any]
) -> dict[str, Any]:
    """Grade one prospective market against one final score.

    Required market keys: event_id, market_type, side, line, captured_at,
    kickoff_at, source_payload_sha256. Required outcome keys: event_id,
    home_score, away_score, final_status.

    ``final_status`` must be exactly ``FINAL``.  Pregame observations captured at
    or after kickoff are rejected.  Moneyline ties are returned as
    ``UNRESOLVED_TIE`` rather than assuming a sportsbook settlement rule.
    """
    if not isinstance(market, Mapping) or not isinstance(outcome, Mapping):
        raise GameMarketGradeError("market and outcome must be mappings")

    market_copy = deepcopy(dict(market))
    outcome_copy = deepcopy(dict(outcome))

    event_id = _required_text(market, "event_id")
    if _required_text(outcome, "event_id") != event_id:
        raise GameMarketGradeError("event_id mismatch")

    market_type = _required_text(market, "market_type").upper()
    if market_type not in SUPPORTED_MARKETS:
        raise GameMarketGradeError(f"unsupported market_type: {market_type}")

    side = _required_text(market, "side").upper()
    if side not in MARKET_SIDES[market_type]:
        raise GameMarketGradeError(f"unsupported side {side} for {market_type}")

    line = _line(market, market_type)
    captured_at = _aware_datetime(market, "captured_at")
    kickoff_at = _aware_datetime(market, "kickoff_at")
    if captured_at >= kickoff_at:
        raise GameMarketGradeError("market must be captured strictly before kickoff")

    source_payload_sha256 = _required_text(market, "source_payload_sha256").lower()
    if len(source_payload_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_payload_sha256):
        raise GameMarketGradeError("source_payload_sha256 must be 64 lowercase hex characters")

    if _required_text(outcome, "final_status").upper() != "FINAL":
        raise GameMarketGradeError("outcome is not final")
    home_score = _score(outcome, "home_score")
    away_score = _score(outcome, "away_score")

    if market_type == "MONEYLINE":
        if home_score == away_score:
            settlement = "UNRESOLVED_TIE"
        else:
            winner = "HOME" if home_score > away_score else "AWAY"
            settlement = "HIT" if side == winner else "MISS"
    elif market_type == "SPREAD":
        selected_score = home_score if side == "HOME" else away_score
        opponent_score = away_score if side == "HOME" else home_score
        adjusted = selected_score + float(line)
        settlement = "HIT" if adjusted > opponent_score else "MISS" if adjusted < opponent_score else "PUSH"
    else:
        total = home_score + away_score
        if total == float(line):
            settlement = "PUSH"
        elif side == "OVER":
            settlement = "HIT" if total > float(line) else "MISS"
        else:
            settlement = "HIT" if total < float(line) else "MISS"

    evidence = {
        "market": market_copy,
        "outcome": outcome_copy,
        "settlement": settlement,
    }
    return {
        "event_id": event_id,
        "market_type": market_type,
        "side": side,
        "line": line,
        "home_score": home_score,
        "away_score": away_score,
        "settlement": settlement,
        "source_payload_sha256": source_payload_sha256,
        "grade_sha256": _canonical_sha256(evidence),
    }
