#!/usr/bin/env python3
"""Fail-closed normalization for supported FanDuel NFL player props.

The output is selection-level evidence. Primary numeric markets emit one row
for OVER and one for UNDER. Alternate ladders and threshold markets emit one
YES row per priced runner. This module does not project, select, or grade.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


PRIMARY_TYPES = {
    "PLAYER_X_PASSING_YARDS_HIGH": ("passing_yards", " - Passing Yds"),
    "PLAYER_X_PASSING_YARDS_MEDIUM": ("passing_yards", " - Passing Yds"),
    "PLAYER_X_PASSING_YARDS_LOW": ("passing_yards", " - Passing Yds"),
    "PLAYER_X_PASSING_TOUCHDOWNS_HIGH": (
        "passing_touchdowns",
        " - Passing TDs",
    ),
    "PLAYER_X_PASSING_TOUCHDOWNS_MEDIUM": (
        "passing_touchdowns",
        " - Passing TDs",
    ),
    "PLAYER_X_PASSING_TOUCHDOWNS_LOW": (
        "passing_touchdowns",
        " - Passing TDs",
    ),
    "PLAYER_X_RUSHING_YARDS_HIGH": ("rushing_yards", " - Rushing Yds"),
    "PLAYER_X_RUSHING_YARDS_LOW": ("rushing_yards", " - Rushing Yds"),
    "PLAYER_X_RECEIVING_YARDS_HIGH": (
        "receiving_yards",
        " - Receiving Yds",
    ),
    "PLAYER_X_RECEIVING_YARDS_LOW": (
        "receiving_yards",
        " - Receiving Yds",
    ),
    "PLAYER_X_RECEPTIONS_HIGH": ("receptions", " - Total Receptions"),
    "PLAYER_X_RECEPTIONS_LOW": ("receptions", " - Total Receptions"),
    "PLAYER_X_RUSHING_+_RECEIVING_YARDS": (
        "rush_plus_rec_yards",
        " - Rushing + Receiving Yds",
    ),
}

ALT_TYPES = {
    "PLAYER_X_ALT_PASSING_YARDS_HIGH": (
        "passing_yards",
        " - Alt Passing Yds",
        "Yards",
    ),
    "PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH": (
        "passing_touchdowns",
        " - Alt Passing TDs",
        "Passing Touchdowns",
    ),
    "PLAYER_X_ALT_RUSHING_YARDS_HIGH": (
        "rushing_yards",
        " - Alt Rushing Yds",
        "Yards",
    ),
    "PLAYER_X_ALT_RUSHING_YARDS_LOW": (
        "rushing_yards",
        " - Alt Rushing Yds",
        "Yards",
    ),
    "PLAYER_X_ALT_RECEIVING_YARDS_HIGH": (
        "receiving_yards",
        " - Alt Receiving Yds",
        "Yards",
    ),
    "PLAYER_X_ALT_RECEIVING_YARDS_LOW": (
        "receiving_yards",
        " - Alt Receiving Yds",
        "Yards",
    ),
    "PLAYER_X_ALT_RECEPTIONS_HIGH": (
        "receptions",
        " - Alt Receptions",
        "Receptions",
    ),
    "PLAYER_X_ALT_RECEPTIONS_LOW": (
        "receptions",
        " - Alt Receptions",
        "Receptions",
    ),
}

THRESHOLD_TYPES = {
    "ANY_TIME_TOUCHDOWN_SCORER": ("anytime_touchdown", 1.0),
    "TO_SCORE_2+_TOUCHDOWNS": ("two_plus_touchdowns", 2.0),
    "TO_SCORE_3+_TOUCHDOWNS": ("three_plus_touchdowns", 3.0),
    "TO_SCORE_4+_TOUCHDOWNS": ("four_plus_touchdowns", 4.0),
    "TO_RECORD_1+_SACK": ("record_a_sack", 1.0),
    "PLAYERS_WITH_10+_YARDS_RECEPTION": (
        "reception_yardage_threshold",
        10.0,
    ),
    "PLAYERS_WITH_15+_YARDS_RECEPTION": (
        "reception_yardage_threshold",
        15.0,
    ),
    "PLAYERS_WITH_20+_YARDS_RECEPTION": (
        "reception_yardage_threshold",
        20.0,
    ),
    "PLAYERS_WITH_30+_YARDS_RECEPTION": (
        "reception_yardage_threshold",
        30.0,
    ),
}

SUPPORTED_TYPES = frozenset(PRIMARY_TYPES) | frozenset(ALT_TYPES) | frozenset(
    THRESHOLD_TYPES
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _event_index(events: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(events, dict):
        return out
    for key, event in events.items():
        if not isinstance(event, dict):
            continue
        out[str(key)] = event
        if event.get("eventId") not in (None, ""):
            out[str(event["eventId"])] = event
    return out


def _american_odds(runner: Mapping[str, Any]) -> int | None:
    value = _as_dict(
        _as_dict(runner.get("winRunnerOdds")).get("americanDisplayOdds")
    ).get("americanOddsInt")
    try:
        odds = int(value)
    except (TypeError, ValueError):
        return None
    return odds if odds != 0 else None


def _finite_number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _player_from_market_name(name: Any, suffix: str) -> str | None:
    text = str(name or "").strip()
    if not text.endswith(suffix):
        return None
    player = text[: -len(suffix)].strip()
    return player or None


def _player_from_side_runner(name: Any, side: str) -> str | None:
    text = str(name or "").strip()
    suffix = f" {side.title()}"
    if not text.endswith(suffix):
        return None
    player = text[: -len(suffix)].strip()
    return player or None


def _alt_runner(name: Any, player: str, unit: str) -> float | None:
    pattern = rf"^{re.escape(player)} (\d+(?:\.\d+)?)\+ {re.escape(unit)}$"
    match = re.fullmatch(pattern, str(name or "").strip())
    return _finite_number(match.group(1), positive=True) if match else None


def _valid_sha256(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return text


def _reject(
    rejections: list[dict],
    market: Mapping[str, Any],
    reason: str,
    detail: str | None = None,
    *,
    runner: Mapping[str, Any] | None = None,
) -> None:
    rejections.append(
        {
            "market_id": str(market.get("marketId") or "") or None,
            "event_id": str(market.get("eventId") or "") or None,
            "market_name": str(market.get("marketName") or ""),
            "market_type": str(market.get("marketType") or ""),
            "selection_id": (
                str(runner.get("selectionId"))
                if runner and runner.get("selectionId") not in (None, "")
                else None
            ),
            "reason": reason,
            "detail": detail,
        }
    )


def _base_row(
    *,
    market: Mapping[str, Any],
    event: Mapping[str, Any],
    canonical_market: str,
    player: str,
    runner: Mapping[str, Any],
    captured_at: str,
    source_payload_sha256: str,
) -> dict[str, Any]:
    return {
        "source": "fanduel_nfl",
        "sport": "NFL",
        "canonical_market": canonical_market,
        "source_market_type": str(market.get("marketType") or ""),
        "event_id": str(market.get("eventId")),
        "event_name": str(event.get("name") or "").strip(),
        "event_open_date": event.get("openDate"),
        "market_id": str(market.get("marketId")),
        "market_name": str(market.get("marketName") or "").strip(),
        "market_time": market.get("marketTime"),
        "market_status": "OPEN",
        "in_play": False,
        "player_name": player,
        "selection_id": str(runner.get("selectionId")),
        "price_american": _american_odds(runner),
        "captured_at": captured_at,
        "source_payload_sha256": source_payload_sha256,
    }


def normalize_payload(
    payload: Mapping[str, Any],
    *,
    captured_at: str,
    source_payload_sha256: str,
) -> dict[str, Any]:
    """Normalize supported player props from one raw FanDuel tab payload."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    if not isinstance(captured_at, str) or not captured_at.strip():
        raise ValueError("captured_at must be a non-empty string")
    digest = _valid_sha256(source_payload_sha256)
    if digest is None:
        raise ValueError("source_payload_sha256 must be 64 lowercase hex")

    attachments = _as_dict(payload.get("attachments"))
    markets = attachments.get("markets")
    if not isinstance(markets, dict):
        raise ValueError("attachments.markets must be a mapping")
    events = _event_index(attachments.get("events"))

    selections: list[dict] = []
    rejections: list[dict] = []
    stats = {
        "markets_total": 0,
        "supported_markets_seen": 0,
        "normalized_selections": 0,
        "rejected": 0,
        "ignored_unsupported": 0,
    }

    for raw_market in markets.values():
        if not isinstance(raw_market, Mapping):
            continue
        market = dict(raw_market)
        stats["markets_total"] += 1
        market_type = str(market.get("marketType") or "").strip()
        if market_type not in SUPPORTED_TYPES:
            stats["ignored_unsupported"] += 1
            continue
        stats["supported_markets_seen"] += 1

        if bool(market.get("inPlay")):
            _reject(rejections, market, "IN_PLAY")
            continue
        status = str(market.get("marketStatus") or "").upper()
        if status != "OPEN":
            _reject(rejections, market, "MARKET_NOT_OPEN", status or None)
            continue
        if market.get("marketId") in (None, ""):
            _reject(rejections, market, "MISSING_MARKET_ID")
            continue
        if market.get("marketTime") in (None, ""):
            _reject(rejections, market, "MISSING_MARKET_TIME")
            continue

        event_id = str(market.get("eventId") or "")
        event = events.get(event_id)
        if not event or str(event.get("name") or "").count(" @ ") != 1:
            _reject(rejections, market, "UNRESOLVED_GAME")
            continue
        runners = market.get("runners")
        if not isinstance(runners, list) or not runners:
            _reject(rejections, market, "RUNNERS_NOT_LIST")
            continue

        if market_type in PRIMARY_TYPES:
            canonical_market, suffix = PRIMARY_TYPES[market_type]
            player = _player_from_market_name(market.get("marketName"), suffix)
            if not player:
                _reject(rejections, market, "MALFORMED_MARKET_NAME")
                continue
            sides: dict[str, Mapping[str, Any]] = {}
            duplicate = False
            for runner in runners:
                if not isinstance(runner, Mapping):
                    continue
                side = str(_as_dict(runner.get("result")).get("type") or "").upper()
                if side not in {"OVER", "UNDER"}:
                    continue
                duplicate = duplicate or side in sides
                sides[side] = runner
            if duplicate or set(sides) != {"OVER", "UNDER"}:
                _reject(rejections, market, "SIDE_CARDINALITY")
                continue

            over_line = _finite_number(sides["OVER"].get("handicap"), positive=True)
            under_line = _finite_number(sides["UNDER"].get("handicap"), positive=True)
            if over_line is None or under_line is None:
                _reject(rejections, market, "MISSING_LINE")
                continue
            if not math.isclose(over_line, under_line, rel_tol=0.0, abs_tol=1e-12):
                _reject(rejections, market, "LINE_MISMATCH")
                continue
            if any(
                _player_from_side_runner(sides[side].get("runnerName"), side)
                != player
                for side in ("OVER", "UNDER")
            ):
                _reject(rejections, market, "PLAYER_IDENTITY_MISMATCH")
                continue

            bad_runner = next(
                (
                    side
                    for side in ("OVER", "UNDER")
                    if str(sides[side].get("runnerStatus") or "").upper()
                    != "ACTIVE"
                    or sides[side].get("selectionId") in (None, "")
                    or _american_odds(sides[side]) is None
                ),
                None,
            )
            if bad_runner:
                _reject(rejections, market, "UNUSABLE_RUNNER", bad_runner)
                continue

            for side in ("OVER", "UNDER"):
                runner = sides[side]
                row = _base_row(
                    market=market,
                    event=event,
                    canonical_market=canonical_market,
                    player=player,
                    runner=runner,
                    captured_at=captured_at.strip(),
                    source_payload_sha256=digest,
                )
                row.update(
                    {
                        "side": side,
                        "line": over_line,
                        "threshold": None,
                        "price_shape": "TWO_SIDED",
                        "paired_selection_id": str(
                            sides["UNDER" if side == "OVER" else "OVER"].get(
                                "selectionId"
                            )
                        ),
                        "paired_price_american": _american_odds(
                            sides["UNDER" if side == "OVER" else "OVER"]
                        ),
                    }
                )
                selections.append(row)
            continue

        if market_type in ALT_TYPES:
            canonical_market, suffix, unit = ALT_TYPES[market_type]
            player = _player_from_market_name(market.get("marketName"), suffix)
            if not player:
                _reject(rejections, market, "MALFORMED_MARKET_NAME")
                continue
            for runner in runners:
                if not isinstance(runner, Mapping):
                    continue
                threshold = _alt_runner(runner.get("runnerName"), player, unit)
                if threshold is None:
                    _reject(
                        rejections,
                        market,
                        "MALFORMED_ALT_RUNNER",
                        runner=runner,
                    )
                    continue
                if (
                    str(runner.get("runnerStatus") or "").upper() != "ACTIVE"
                    or runner.get("selectionId") in (None, "")
                    or _american_odds(runner) is None
                ):
                    _reject(rejections, market, "UNUSABLE_RUNNER", runner=runner)
                    continue
                row = _base_row(
                    market=market,
                    event=event,
                    canonical_market=canonical_market,
                    player=player,
                    runner=runner,
                    captured_at=captured_at.strip(),
                    source_payload_sha256=digest,
                )
                row.update(
                    {
                        "side": "YES",
                        "line": None,
                        "threshold": threshold,
                        "price_shape": "ONE_SIDED_THRESHOLD",
                        "paired_selection_id": None,
                        "paired_price_american": None,
                    }
                )
                selections.append(row)
            continue

        canonical_market, threshold = THRESHOLD_TYPES[market_type]
        for runner in runners:
            if not isinstance(runner, Mapping):
                continue
            player = str(runner.get("runnerName") or "").strip()
            if not player:
                _reject(rejections, market, "MISSING_PLAYER_NAME", runner=runner)
                continue
            if player.endswith(" Defense"):
                _reject(
                    rejections,
                    market,
                    "NON_PLAYER_SELECTION",
                    "team defense is not a player identity",
                    runner=runner,
                )
                continue
            if (
                str(runner.get("runnerStatus") or "").upper() != "ACTIVE"
                or runner.get("selectionId") in (None, "")
                or _american_odds(runner) is None
            ):
                _reject(rejections, market, "UNUSABLE_RUNNER", runner=runner)
                continue
            row = _base_row(
                market=market,
                event=event,
                canonical_market=canonical_market,
                player=player,
                runner=runner,
                captured_at=captured_at.strip(),
                source_payload_sha256=digest,
            )
            row.update(
                {
                    "side": "YES",
                    "line": None,
                    "threshold": threshold,
                    "price_shape": "ONE_SIDED_THRESHOLD",
                    "paired_selection_id": None,
                    "paired_price_american": None,
                }
            )
            selections.append(row)

    stats["normalized_selections"] = len(selections)
    stats["rejected"] = len(rejections)
    return {"selections": selections, "rejections": rejections, "stats": stats}
