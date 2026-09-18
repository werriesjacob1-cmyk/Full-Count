"""Deterministic, outcome-only grading for normalized NFL game markets.

Consumes the canonical record emitted by ``nfl.normalize.fanduel_game_lines``.
The grader never selects bets, estimates
probabilities, or mutates pregame evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

SUPPORTED_MARKETS = {"moneyline", "spread", "game_total"}
MARKET_SIDES = {
    "moneyline": {"HOME", "AWAY"},
    "spread": {"HOME", "AWAY"},
    "game_total": {"OVER", "UNDER"},
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
    if type(value) is not int or value < 0:
        raise GameMarketGradeError(f"{key} must be a non-negative integer")
    return value


def _number(obj: Mapping[str, Any], key: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameMarketGradeError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GameMarketGradeError(f"{key} must be finite")
    return result


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _line_for_side(market: Mapping[str, Any], canonical_market: str, side: str) -> float | None:
    if canonical_market == "moneyline":
        return None
    if canonical_market == "spread":
        home = _number(market, "home_line")
        away = _number(market, "away_line")
        if not math.isclose(home + away, 0.0, abs_tol=1e-9):
            raise GameMarketGradeError("spread handicaps must be opposites")
        return home if side == "HOME" else away
    return _number(market, "line")


def grade_game_market(
    market: Mapping[str, Any], outcome: Mapping[str, Any], *, side: str
) -> dict[str, Any]:
    """Grade one canonical market against one authoritative final score."""
    if not isinstance(market, Mapping) or not isinstance(outcome, Mapping):
        raise GameMarketGradeError("market and outcome must be mappings")

    market_copy = deepcopy(dict(market))
    outcome_copy = deepcopy(dict(outcome))

    if _required_text(market, "sport").upper() != "NFL":
        raise GameMarketGradeError("market sport must be NFL")
    if _required_text(market, "sportsbook").upper() != "FANDUEL":
        raise GameMarketGradeError("market sportsbook must be FANDUEL")
    if _required_text(market, "market_status").upper() != "OPEN" or market.get("in_play") is not False:
        raise GameMarketGradeError("market must be OPEN and pregame")

    event_id = _required_text(market, "event_id")
    market_id = _required_text(market, "market_id")
    if _required_text(outcome, "event_id") != event_id:
        raise GameMarketGradeError("event_id mismatch")

    canonical_market = _required_text(market, "market").lower()
    if canonical_market not in SUPPORTED_MARKETS:
        raise GameMarketGradeError(f"unsupported canonical_market: {canonical_market}")

    selected_side = str(side or "").strip().upper()
    if selected_side not in MARKET_SIDES[canonical_market]:
        raise GameMarketGradeError(
            f"unsupported side {selected_side or '<empty>'} for {canonical_market}"
        )

    prefix = selected_side.lower()
    if canonical_market == "game_total":
        odds_key = "over_odds" if selected_side == "OVER" else "under_odds"
        selection_key = (
            "over_selection_id" if selected_side == "OVER" else "under_selection_id"
        )
    else:
        odds_key = f"{prefix}_odds"
        selection_key = f"{prefix}_selection_id"
    odds = _number(market, odds_key)
    if odds == 0 or not float(odds).is_integer():
        raise GameMarketGradeError(f"{odds_key} must be a nonzero integer")
    _required_text(market, selection_key)

    captured_at = _aware_datetime(market, "captured_at")
    market_time = _aware_datetime(market, "market_time")
    if captured_at >= market_time:
        raise GameMarketGradeError("market must be captured strictly before market_time")

    source_payload_sha256 = _required_text(market, "source_payload_sha256").lower()
    if len(source_payload_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_payload_sha256):
        raise GameMarketGradeError("source_payload_sha256 must be 64 lowercase hex characters")

    if _required_text(outcome, "final_status").upper() != "FINAL":
        raise GameMarketGradeError("outcome is not final")
    home_score = _score(outcome, "home_score")
    away_score = _score(outcome, "away_score")
    line = _line_for_side(market, canonical_market, selected_side)

    if canonical_market == "moneyline":
        if home_score == away_score:
            settlement = "UNRESOLVED_TIE"
        else:
            winner = "HOME" if home_score > away_score else "AWAY"
            settlement = "HIT" if selected_side == winner else "MISS"
    elif canonical_market == "spread":
        selected_score = home_score if selected_side == "HOME" else away_score
        opponent_score = away_score if selected_side == "HOME" else home_score
        adjusted = selected_score + float(line)
        settlement = "HIT" if adjusted > opponent_score else "MISS" if adjusted < opponent_score else "PUSH"
    else:
        total_points = home_score + away_score
        if total_points == float(line):
            settlement = "PUSH"
        elif selected_side == "OVER":
            settlement = "HIT" if total_points > float(line) else "MISS"
        else:
            settlement = "HIT" if total_points < float(line) else "MISS"

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
        "side": selected_side,
        "line": line,
        "home_score": home_score,
        "away_score": away_score,
        "settlement": settlement,
        "source_payload_sha256": source_payload_sha256,
        "grade_sha256": _canonical_sha256(evidence),
    }
