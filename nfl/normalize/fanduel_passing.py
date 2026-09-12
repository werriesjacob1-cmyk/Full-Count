#!/usr/bin/env python3
"""Strict normalization for FanDuel NFL primary passing-yards markets.

This module is deliberately narrow for the first prospective NFL shadow board:
- primary passing-yards line only,
- two-sided OVER/UNDER only,
- pregame only,
- exact player-name agreement across market and runners,
- exact same handicap on both sides,
- both American prices required,
- game event must be resolvable from attachments.events.

Alternate ladders, touchdowns, moneyline/spread/total, and unknown market types
are not normalized here.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


PRIMARY_TYPES = frozenset({
    "PLAYER_X_PASSING_YARDS_HIGH",
    "PLAYER_X_PASSING_YARDS_MEDIUM",
    "PLAYER_X_PASSING_YARDS_LOW",
})
PRIMARY_NAME_SUFFIX = " - Passing Yds"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _event_index(events: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(events, dict):
        return out
    for key, event in events.items():
        if not isinstance(event, dict):
            continue
        event_id = event.get("eventId")
        out[str(key)] = event
        if event_id not in (None, ""):
            out[str(event_id)] = event
    return out


def _american_odds(runner: Mapping[str, Any]) -> int | None:
    odds = (
        _as_dict(
            _as_dict(runner.get("winRunnerOdds")).get(
                "americanDisplayOdds"
            )
        ).get("americanOddsInt")
    )
    if odds in (None, ""):
        return None
    try:
        value = int(odds)
    except (TypeError, ValueError):
        return None
    return value if value != 0 else None


def _finite_line(value: Any) -> float | None:
    try:
        line = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(line) or line <= 0:
        return None
    return line


def _player_from_market_name(name: Any) -> str | None:
    text = str(name or "").strip()
    if not text.endswith(PRIMARY_NAME_SUFFIX):
        return None
    player = text[: -len(PRIMARY_NAME_SUFFIX)].strip()
    return player or None


def _player_from_runner_name(name: Any, side: str) -> str | None:
    text = str(name or "").strip()
    suffix = f" {side.title()}"
    if not text.endswith(suffix):
        return None
    player = text[: -len(suffix)].strip()
    return player or None


def _reject(
    rejections: list[dict],
    market: Mapping[str, Any],
    reason: str,
    detail: str | None = None,
) -> None:
    rejections.append({
        "market_id": (
            str(market.get("marketId"))
            if market.get("marketId") not in (None, "")
            else None
        ),
        "event_id": (
            str(market.get("eventId"))
            if market.get("eventId") not in (None, "")
            else None
        ),
        "market_name": str(market.get("marketName") or ""),
        "market_type": str(market.get("marketType") or ""),
        "reason": reason,
        "detail": detail,
    })


def normalize_payload(
    payload: Mapping[str, Any],
    *,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Normalize supported primary passing-yards markets in one payload.

    Unsupported non-primary market families are ignored. A market that presents
    itself as a primary passing-yards line but violates the contract is recorded
    in rejections and never converted into a candidate.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")

    attachments = _as_dict(payload.get("attachments"))
    markets = attachments.get("markets")
    if not isinstance(markets, dict):
        raise ValueError("attachments.markets must be a mapping")
    events = _event_index(attachments.get("events"))

    candidates: list[dict] = []
    rejections: list[dict] = []
    stats = {
        "markets_total": 0,
        "primary_markets_seen": 0,
        "normalized": 0,
        "rejected": 0,
        "ignored_non_primary": 0,
    }

    for dict_key, raw_market in markets.items():
        if not isinstance(raw_market, Mapping):
            continue
        market = dict(raw_market)
        stats["markets_total"] += 1

        market_type = str(market.get("marketType") or "").strip()
        market_name = str(market.get("marketName") or "").strip()
        is_primary_name = market_name.endswith(PRIMARY_NAME_SUFFIX)
        is_alt = "_ALT_PASSING_YARDS_" in market_type

        if is_alt:
            stats["ignored_non_primary"] += 1
            continue

        if not is_primary_name and market_type not in PRIMARY_TYPES:
            stats["ignored_non_primary"] += 1
            continue

        stats["primary_markets_seen"] += 1

        if market_type not in PRIMARY_TYPES:
            _reject(rejections, market, "UNSUPPORTED_PRIMARY_TYPE")
            continue

        if bool(market.get("inPlay")):
            _reject(rejections, market, "IN_PLAY")
            continue

        status = str(market.get("marketStatus") or "").upper()
        if status in {"CLOSED", "SUSPENDED"}:
            _reject(rejections, market, "MARKET_NOT_OPEN", status)
            continue

        event_id_raw = market.get("eventId")
        event_id = (
            str(event_id_raw)
            if event_id_raw not in (None, "")
            else None
        )
        event = events.get(event_id or "")
        event_name = str((event or {}).get("name") or "").strip()
        if not event or " @ " not in event_name:
            _reject(rejections, market, "UNRESOLVED_GAME")
            continue

        player = _player_from_market_name(market_name)
        if not player:
            _reject(rejections, market, "MALFORMED_MARKET_NAME")
            continue

        runners = market.get("runners")
        if not isinstance(runners, list):
            _reject(rejections, market, "RUNNERS_NOT_LIST")
            continue

        sides: dict[str, Mapping[str, Any]] = {}
        duplicate = False
        for runner in runners:
            if not isinstance(runner, Mapping):
                continue
            side = str(
                _as_dict(runner.get("result")).get("type") or ""
            ).strip().upper()
            if side not in {"OVER", "UNDER"}:
                continue
            if side in sides:
                duplicate = True
            sides[side] = runner

        if duplicate or set(sides) != {"OVER", "UNDER"}:
            _reject(rejections, market, "SIDE_CARDINALITY")
            continue

        over = sides["OVER"]
        under = sides["UNDER"]

        over_player = _player_from_runner_name(
            over.get("runnerName"), "OVER"
        )
        under_player = _player_from_runner_name(
            under.get("runnerName"), "UNDER"
        )
        if player != over_player or player != under_player:
            _reject(rejections, market, "PLAYER_IDENTITY_MISMATCH")
            continue

        if str(over.get("runnerStatus") or "").upper() != "ACTIVE":
            _reject(rejections, market, "RUNNER_NOT_ACTIVE", "OVER")
            continue
        if str(under.get("runnerStatus") or "").upper() != "ACTIVE":
            _reject(rejections, market, "RUNNER_NOT_ACTIVE", "UNDER")
            continue

        over_line = _finite_line(over.get("handicap"))
        under_line = _finite_line(under.get("handicap"))
        if over_line is None or under_line is None:
            _reject(rejections, market, "MISSING_LINE")
            continue
        if not math.isclose(
            over_line,
            under_line,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _reject(rejections, market, "LINE_MISMATCH")
            continue

        over_odds = _american_odds(over)
        under_odds = _american_odds(under)
        if over_odds is None or under_odds is None:
            _reject(rejections, market, "MISSING_ODDS")
            continue

        market_id = market.get("marketId", dict_key)
        row = {
            "source": "fanduel_nfl",
            "market": "passing_yards",
            "event_id": str(event_id),
            "event_name": event_name,
            "event_open_date": (event or {}).get("openDate"),
            "market_id": str(market_id),
            "market_name": market_name,
            "market_type": market_type,
            "market_time": market.get("marketTime"),
            "player_name": player,
            "line": over_line,
            "over_odds": over_odds,
            "under_odds": under_odds,
            "over_selection_id": str(over.get("selectionId")),
            "under_selection_id": str(under.get("selectionId")),
            "captured_at": captured_at,
        }
        candidates.append(row)

    stats["normalized"] = len(candidates)
    stats["rejected"] = len(rejections)

    return {
        "candidates": candidates,
        "rejections": rejections,
        "stats": stats,
    }
