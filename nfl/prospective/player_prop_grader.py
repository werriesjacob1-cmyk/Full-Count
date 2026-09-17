"""Deterministic, outcome-only grading for normalized NFL player props.

This module grades captured market selections. It never projects a player,
selects a wager, or treats market-only settlement as a model prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


SUPPORTED_MARKETS = frozenset(
    {
        "passing_yards",
        "passing_touchdowns",
        "rushing_yards",
        "receiving_yards",
        "receptions",
        "rush_plus_rec_yards",
        "anytime_touchdown",
        "two_plus_touchdowns",
        "three_plus_touchdowns",
        "four_plus_touchdowns",
        "record_a_sack",
        "reception_yardage_threshold",
    }
)

_STAT_FIELD = {
    "passing_yards": "passing_yards",
    "passing_touchdowns": "passing_touchdowns",
    "rushing_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
    "record_a_sack": "sacks",
    "reception_yardage_threshold": "longest_reception_yards",
    "anytime_touchdown": "touchdowns",
    "two_plus_touchdowns": "touchdowns",
    "three_plus_touchdowns": "touchdowns",
    "four_plus_touchdowns": "touchdowns",
}

_FIXED_THRESHOLDS = {
    "anytime_touchdown": 1.0,
    "two_plus_touchdowns": 2.0,
    "three_plus_touchdowns": 3.0,
    "four_plus_touchdowns": 4.0,
    "record_a_sack": 1.0,
}


class PlayerPropGradeError(ValueError):
    """Raised when a player-prop selection cannot be graded safely."""


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


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerPropGradeError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise PlayerPropGradeError(f"{key} must be finite")
    return result


def _optional_number(obj: Mapping[str, Any], key: str) -> float | None:
    value = obj.get(key)
    return None if value is None else _number(value, key)


def _sha256(obj: Mapping[str, Any], key: str) -> str:
    value = _required_text(obj, key).lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise PlayerPropGradeError(f"{key} must be 64 lowercase hex characters")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _player_stat(
    stats: Mapping[str, Any], canonical_market: str
) -> float:
    if canonical_market == "rush_plus_rec_yards":
        rushing = _number(stats.get("rushing_yards"), "rushing_yards")
        receiving = _number(stats.get("receiving_yards"), "receiving_yards")
        return rushing + receiving
    field = _STAT_FIELD[canonical_market]
    return _number(stats.get(field), field)


def grade_player_prop(
    market: Mapping[str, Any], outcome: Mapping[str, Any]
) -> dict[str, Any]:
    """Grade one bound pregame selection against complete official evidence."""
    if not isinstance(market, Mapping) or not isinstance(outcome, Mapping):
        raise PlayerPropGradeError("market and outcome must be mappings")

    market_copy = deepcopy(dict(market))
    outcome_copy = deepcopy(dict(outcome))

    if str(market.get("market_status") or "").upper() != "OPEN":
        raise PlayerPropGradeError("market must be OPEN")
    if market.get("in_play") is not False:
        raise PlayerPropGradeError("market must be pregame")
    if str(market.get("binding_status") or "") != "BOUND":
        raise PlayerPropGradeError("market selection must be identity-bound")

    event_id = _required_text(market, "event_id")
    market_id = _required_text(market, "market_id")
    selection_id = _required_text(market, "selection_id")
    player_gsis_id = _required_text(market, "player_gsis_id")
    canonical_market = _required_text(market, "canonical_market").lower()
    if canonical_market not in SUPPORTED_MARKETS:
        raise PlayerPropGradeError(
            f"unsupported canonical_market: {canonical_market}"
        )
    side = _required_text(market, "side").upper()
    if side not in {"OVER", "UNDER", "YES"}:
        raise PlayerPropGradeError(f"unsupported side: {side}")

    captured_at = _aware_datetime(market, "captured_at")
    market_time = _aware_datetime(market, "market_time")
    if captured_at >= market_time:
        raise PlayerPropGradeError(
            "market must be captured strictly before market_time"
        )
    source_payload_sha256 = _sha256(market, "source_payload_sha256")

    line = _optional_number(market, "line")
    threshold = _optional_number(market, "threshold")
    if side in {"OVER", "UNDER"}:
        if line is None:
            raise PlayerPropGradeError("OVER/UNDER selection requires line")
        if threshold is not None:
            raise PlayerPropGradeError(
                "OVER/UNDER selection must not carry threshold"
            )
    else:
        if line is not None:
            raise PlayerPropGradeError("YES selection must have null line")
        if threshold is None or threshold <= 0:
            raise PlayerPropGradeError("YES selection requires positive threshold")
        fixed = _FIXED_THRESHOLDS.get(canonical_market)
        if fixed is not None and not math.isclose(
            threshold, fixed, rel_tol=0.0, abs_tol=1e-12
        ):
            raise PlayerPropGradeError(
                f"{canonical_market} threshold must equal {fixed:g}"
            )

    if _required_text(outcome, "event_id") != event_id:
        raise PlayerPropGradeError("event_id mismatch")
    if _required_text(outcome, "final_status").upper() != "FINAL":
        raise PlayerPropGradeError("outcome is not final")
    if outcome.get("participation_complete") is not True:
        raise PlayerPropGradeError("participation evidence is not complete")
    if outcome.get("player_stats_complete") is not True:
        raise PlayerPropGradeError("player-stat evidence is not complete")
    source_outcome_sha256 = _sha256(outcome, "source_outcome_sha256")

    participants = outcome.get("participants")
    if not isinstance(participants, list) or any(
        not isinstance(value, str) or not value.strip() for value in participants
    ):
        raise PlayerPropGradeError("participants must be a list of GSIS IDs")
    participant_ids = set(participants)

    if player_gsis_id not in participant_ids:
        settlement = "VOID_DNP"
        final_stat_value = None
    else:
        player_stats = outcome.get("player_stats")
        if not isinstance(player_stats, Mapping):
            raise PlayerPropGradeError("player_stats must be a mapping")
        stats = player_stats.get(player_gsis_id)
        if not isinstance(stats, Mapping):
            raise PlayerPropGradeError(
                "participating player must have an explicit stats row"
            )
        if canonical_market == "reception_yardage_threshold" and (
            outcome.get("play_by_play_complete") is not True
        ):
            raise PlayerPropGradeError(
                "longest-reception grading requires complete play-by-play"
            )
        if canonical_market in {
            "anytime_touchdown",
            "two_plus_touchdowns",
            "three_plus_touchdowns",
            "four_plus_touchdowns",
        } and outcome.get("touchdown_coverage") != "all_credited":
            raise PlayerPropGradeError(
                "touchdown grading requires all credited touchdown mechanisms"
            )

        final_stat_value = _player_stat(stats, canonical_market)
        if side == "OVER":
            if math.isclose(final_stat_value, float(line), abs_tol=1e-12):
                settlement = "PUSH"
            else:
                settlement = "HIT" if final_stat_value > float(line) else "MISS"
        elif side == "UNDER":
            if math.isclose(final_stat_value, float(line), abs_tol=1e-12):
                settlement = "PUSH"
            else:
                settlement = "HIT" if final_stat_value < float(line) else "MISS"
        else:
            settlement = (
                "HIT" if final_stat_value >= float(threshold) else "MISS"
            )

    evidence = {
        "market": market_copy,
        "outcome": outcome_copy,
        "settlement": settlement,
        "final_stat_value": final_stat_value,
    }
    return {
        "event_id": event_id,
        "market_id": market_id,
        "selection_id": selection_id,
        "canonical_market": canonical_market,
        "player_gsis_id": player_gsis_id,
        "side": side,
        "line": line,
        "threshold": threshold,
        "final_stat_value": final_stat_value,
        "settlement": settlement,
        "evidence_class": (
            "prediction_graded"
            if canonical_market == "passing_yards"
            and market.get("prediction_recorded_pregame") is True
            else "market_only_settled"
        ),
        "source_payload_sha256": source_payload_sha256,
        "source_outcome_sha256": source_outcome_sha256,
        "grade_sha256": _canonical_sha256(evidence),
    }
