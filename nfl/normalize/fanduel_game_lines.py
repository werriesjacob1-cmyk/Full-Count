#!/usr/bin/env python3
"""Strict normalization for FanDuel NFL primary full-game markets.

This module interprets the primary pregame moneyline, spread, and game-total
markets observed in the bounded FanDuel census. Alternate spreads/totals, team
totals, period markets, models, selections, and grading are outside this
contract.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


MONEYLINE_TYPE = "MONEY_LINE"
SPREAD_TYPE = "MATCH_HANDICAP_(2-WAY)"
TOTAL_TYPE = "TOTAL_POINTS_(OVER/UNDER)"
PRIMARY_TYPES = frozenset({MONEYLINE_TYPE, SPREAD_TYPE, TOTAL_TYPE})
PRIMARY_NAMES = {
    MONEYLINE_TYPE: "Moneyline",
    SPREAD_TYPE: "Spread",
    TOTAL_TYPE: "Total Points",
}


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _event_index(events: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(events, Mapping):
        return out
    for key, event in events.items():
        if not isinstance(event, Mapping):
            continue
        copied = dict(event)
        out[str(key)] = copied
        event_id = event.get("eventId")
        if event_id not in (None, ""):
            out[str(event_id)] = copied
    return out


def _american_odds(runner: Mapping[str, Any]) -> int | None:
    value = _as_dict(
        _as_dict(runner.get("winRunnerOdds")).get("americanDisplayOdds")
    ).get("americanOddsInt")
    if value in (None, ""):
        return None
    try:
        if isinstance(value, bool):
            return None
        odds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return odds if odds else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _instant(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _selection_id(runner: Mapping[str, Any]) -> str | None:
    value = runner.get("selectionId")
    if value in (None, ""):
        return None
    return str(value)


def _utc(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: str | None) -> str | None:
    if value is None:
        return None
    digest = str(value).strip()
    if (
        len(digest) != 64
        or digest != digest.lower()
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        raise ValueError(
            "source_payload_sha256 must be a lowercase SHA-256 hex digest"
        )
    return digest


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _game(event: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    if not event:
        return None
    name = str(event.get("name") or "").strip()
    parts = name.split(" @ ")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return None
    return name, parts[0].strip(), parts[1].strip()


def _active_sides(
    market: Mapping[str, Any],
    allowed: frozenset[str],
) -> tuple[dict[str, Mapping[str, Any]] | None, str | None]:
    runners = market.get("runners")
    if not isinstance(runners, list):
        return None, "RUNNERS_NOT_LIST"
    if len(runners) != len(allowed):
        return None, "SIDE_CARDINALITY"
    sides: dict[str, Mapping[str, Any]] = {}
    for runner in runners:
        if not isinstance(runner, Mapping):
            return None, "SIDE_CARDINALITY"
        side = str(_as_dict(runner.get("result")).get("type") or "").upper()
        if side not in allowed:
            return None, "SIDE_CARDINALITY"
        if side in sides:
            return None, "SIDE_CARDINALITY"
        sides[side] = runner
    if set(sides) != set(allowed):
        return None, "SIDE_CARDINALITY"
    for side, runner in sides.items():
        if str(runner.get("runnerStatus") or "").upper() != "ACTIVE":
            return None, f"RUNNER_NOT_ACTIVE:{side}"
    return sides, None


def _base_row(
    market: Mapping[str, Any],
    event: Mapping[str, Any],
    event_name: str,
    *,
    captured_at: str | None,
    source_payload_sha256: str | None,
    source_artifact: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    return {
        "source": "fanduel_nfl",
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "event_id": str(market["eventId"]),
        "event_name": event_name,
        "event_open_date": event.get("openDate"),
        "market_id": str(market["marketId"]),
        "market_name": str(market["marketName"]),
        "market_type": str(market["marketType"]),
        "market_time": market.get("marketTime"),
        "market_status": "OPEN",
        "in_play": False,
        "captured_at": captured_at,
        "source_payload_sha256": source_payload_sha256,
        "source_artifact": source_artifact,
        "source_url": source_url,
    }


def normalize_payload(
    payload: Mapping[str, Any],
    *,
    captured_at: str | None = None,
    source_payload_sha256: str | None = None,
    source_artifact: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Normalize primary NFL full-game markets, failing closed."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    capture_clock = _utc(captured_at)
    source_digest = _digest(source_payload_sha256)
    source_name = _text_or_none(source_artifact)
    source_location = _text_or_none(source_url)

    attachments = _as_dict(payload.get("attachments"))
    markets = attachments.get("markets")
    if not isinstance(markets, Mapping):
        raise ValueError("attachments.markets must be a mapping")
    events = _event_index(attachments.get("events"))

    candidates: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    accepted_by_id: dict[str, dict[str, Any]] = {}
    conflicted_ids: set[str] = set()
    stats = {
        "markets_total": 0,
        "primary_markets_seen": 0,
        "normalized": 0,
        "rejected": 0,
        "ignored_non_primary": 0,
        "duplicates_collapsed": 0,
    }

    for raw_market in markets.values():
        if not isinstance(raw_market, Mapping):
            continue
        market = dict(raw_market)
        stats["markets_total"] += 1
        market_type = str(market.get("marketType") or "").strip()
        market_name = str(market.get("marketName") or "").strip()
        signals_primary = market_name in set(PRIMARY_NAMES.values())
        if market_type not in PRIMARY_TYPES and not signals_primary:
            stats["ignored_non_primary"] += 1
            continue
        stats["primary_markets_seen"] += 1

        if market_type not in PRIMARY_TYPES:
            _reject(rejections, market, "UNSUPPORTED_PRIMARY_TYPE")
            continue
        if market_name != PRIMARY_NAMES[market_type]:
            _reject(rejections, market, "UNSUPPORTED_PRIMARY_NAME")
            continue
        if market.get("inPlay") is not False:
            _reject(rejections, market, "IN_PLAY_OR_UNKNOWN")
            continue
        status = str(market.get("marketStatus") or "").strip().upper()
        if status != "OPEN":
            _reject(rejections, market, "MARKET_NOT_OPEN", status or None)
            continue
        if market.get("marketId") in (None, ""):
            _reject(rejections, market, "MISSING_MARKET_ID")
            continue
        event_id = market.get("eventId")
        event = events.get(str(event_id)) if event_id not in (None, "") else None
        game = _game(event)
        if game is None:
            _reject(rejections, market, "UNRESOLVED_GAME")
            continue
        event_name, away_team, home_team = game
        event_time = _instant((event or {}).get("openDate"))
        market_time = _instant(market.get("marketTime"))
        if event_time is None or market_time is None or event_time != market_time:
            _reject(rejections, market, "EVENT_TIME_MISMATCH")
            continue

        row = _base_row(
            market,
            event or {},
            event_name,
            captured_at=capture_clock,
            source_payload_sha256=source_digest,
            source_artifact=source_name,
            source_url=source_location,
        )
        if market_type in {MONEYLINE_TYPE, SPREAD_TYPE}:
            sides, error = _active_sides(
                market, frozenset({"AWAY", "HOME"})
            )
            if error:
                reason, _, detail = error.partition(":")
                _reject(rejections, market, reason, detail or None)
                continue
            assert sides is not None
            away = sides["AWAY"]
            home = sides["HOME"]
            if (
                str(away.get("runnerName") or "").strip() != away_team
                or str(home.get("runnerName") or "").strip() != home_team
            ):
                _reject(rejections, market, "TEAM_IDENTITY_MISMATCH")
                continue
            away_odds = _american_odds(away)
            home_odds = _american_odds(home)
            if away_odds is None or home_odds is None:
                _reject(rejections, market, "MISSING_ODDS")
                continue
            away_selection_id = _selection_id(away)
            home_selection_id = _selection_id(home)
            if (
                away_selection_id is None
                or home_selection_id is None
                or away_selection_id == home_selection_id
            ):
                _reject(rejections, market, "INVALID_SELECTION_ID")
                continue
            row.update({
                "market": (
                    "moneyline" if market_type == MONEYLINE_TYPE else "spread"
                ),
                "away_team": away_team,
                "home_team": home_team,
                "away_odds": away_odds,
                "home_odds": home_odds,
                "away_selection_id": away_selection_id,
                "home_selection_id": home_selection_id,
            })
            if market_type == SPREAD_TYPE:
                away_line = _finite_number(away.get("handicap"))
                home_line = _finite_number(home.get("handicap"))
                if away_line is None or home_line is None:
                    _reject(rejections, market, "MISSING_LINE")
                    continue
                if not math.isclose(
                    away_line + home_line, 0.0, rel_tol=0.0, abs_tol=1e-12
                ):
                    _reject(rejections, market, "SPREAD_NOT_OPPOSING")
                    continue
                row.update({
                    "away_line": away_line,
                    "home_line": home_line,
                })
        else:
            sides, error = _active_sides(
                market, frozenset({"OVER", "UNDER"})
            )
            if error:
                reason, _, detail = error.partition(":")
                _reject(rejections, market, reason, detail or None)
                continue
            assert sides is not None
            over = sides["OVER"]
            under = sides["UNDER"]
            if (
                str(over.get("runnerName") or "").strip().upper() != "OVER"
                or str(under.get("runnerName") or "").strip().upper() != "UNDER"
            ):
                _reject(rejections, market, "TOTAL_SIDE_IDENTITY_MISMATCH")
                continue
            over_line = _finite_number(over.get("handicap"))
            under_line = _finite_number(under.get("handicap"))
            if (
                over_line is None
                or under_line is None
                or over_line <= 0
                or under_line <= 0
            ):
                _reject(rejections, market, "MISSING_LINE")
                continue
            if not math.isclose(
                over_line, under_line, rel_tol=0.0, abs_tol=1e-12
            ):
                _reject(rejections, market, "LINE_MISMATCH")
                continue
            over_odds = _american_odds(over)
            under_odds = _american_odds(under)
            if over_odds is None or under_odds is None:
                _reject(rejections, market, "MISSING_ODDS")
                continue
            over_selection_id = _selection_id(over)
            under_selection_id = _selection_id(under)
            if (
                over_selection_id is None
                or under_selection_id is None
                or over_selection_id == under_selection_id
            ):
                _reject(rejections, market, "INVALID_SELECTION_ID")
                continue
            row.update({
                "market": "game_total",
                "away_team": away_team,
                "home_team": home_team,
                "line": over_line,
                "over_odds": over_odds,
                "under_odds": under_odds,
                "over_selection_id": over_selection_id,
                "under_selection_id": under_selection_id,
            })

        market_id = row["market_id"]
        prior = accepted_by_id.get(market_id)
        if prior is None and market_id not in conflicted_ids:
            accepted_by_id[market_id] = row
        elif prior == row:
            stats["duplicates_collapsed"] += 1
        else:
            accepted_by_id.pop(market_id, None)
            conflicted_ids.add(market_id)
            _reject(rejections, market, "DUPLICATE_MARKET_CONFLICT")

    by_family: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in accepted_by_id.values():
        by_family.setdefault((row["event_id"], row["market"]), []).append(row)
    for (event_id, market_name), rows in sorted(by_family.items()):
        if len(rows) == 1:
            candidates.append(rows[0])
            continue
        for row in rows:
            rejections.append({
                "market_id": row["market_id"],
                "event_id": event_id,
                "market_name": row["market_name"],
                "market_type": row["market_type"],
                "reason": "DUPLICATE_PRIMARY_MARKET",
                "detail": market_name,
            })
    candidates.sort(
        key=lambda row: (row["event_id"], row["market"], row["market_id"])
    )
    stats["normalized"] = len(candidates)
    stats["rejected"] = len(rejections)
    return {"candidates": candidates, "rejections": rejections, "stats": stats}
