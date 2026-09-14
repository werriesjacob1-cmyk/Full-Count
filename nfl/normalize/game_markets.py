#!/usr/bin/env python3
"""Normalize primary FanDuel NFL full-game moneyline, spread, and total markets.

This module performs source-shape normalization only. It does not estimate
probabilities, select wagers, grade outcomes, or make records public-eligible.

Observed FanDuel source types:
- MONEY_LINE
- MATCH_HANDICAP_(2-WAY)
- TOTAL_POINTS_(OVER/UNDER)

The normalizer is intentionally fail-closed: malformed or duplicate primary
markets are excluded and returned as explicit failures rather than guessed.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

SOURCE_TO_CANONICAL = {
    "MONEY_LINE": "moneyline",
    "MATCH_HANDICAP_(2-WAY)": "spread",
    "TOTAL_POINTS_(OVER/UNDER)": "game_total",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {field}: {value!r}")
    return out


def _american_odds(runner: Mapping[str, Any]) -> int:
    odds = runner.get("winRunnerOdds")
    if not isinstance(odds, Mapping):
        raise ValueError("runner missing winRunnerOdds")
    display = odds.get("americanDisplayOdds")
    if not isinstance(display, Mapping):
        raise ValueError("runner missing americanDisplayOdds")
    value = display.get("americanOddsInt", display.get("americanOdds"))
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid american odds: {value!r}") from exc
    if out == 0:
        raise ValueError("american odds cannot be zero")
    return out


def _active_runners(market: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    runners = market.get("runners")
    if not isinstance(runners, list):
        raise ValueError("market runners must be a list")
    active = [
        row for row in runners
        if isinstance(row, Mapping)
        and _text(row.get("runnerStatus")).upper() == "ACTIVE"
    ]
    if len(active) != 2:
        raise ValueError(f"expected exactly 2 active runners, found {len(active)}")
    return active


def _side(runner: Mapping[str, Any]) -> str:
    result = runner.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("runner missing result side")
    side = _text(result.get("type")).upper()
    if not side:
        raise ValueError("runner result side is empty")
    return side


def _base_record(
    market: Mapping[str, Any],
    *,
    canonical_market: str,
    payload_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    event_id = _text(market.get("eventId"))
    market_id = _text(market.get("marketId"))
    market_time = _text(market.get("marketTime"))
    if not event_id or not market_id or not market_time:
        raise ValueError("market missing eventId, marketId, or marketTime")
    return {
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "canonical_market": canonical_market,
        "source_market_type": _text(market.get("marketType")),
        "source_market_name": _text(market.get("marketName")),
        "event_id": event_id,
        "market_id": market_id,
        "market_time": market_time,
        "captured_at": _text(observed_at),
        "source_payload_sha256": _text(payload_sha256),
        "market_status": _text(market.get("marketStatus")).upper(),
        "in_play": bool(market.get("inPlay")),
    }


def _normalize_moneyline(market: Mapping[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    runners = _active_runners(market)
    by_side = {_side(row): row for row in runners}
    if set(by_side) != {"AWAY", "HOME"}:
        raise ValueError(f"moneyline sides must be AWAY/HOME, got {sorted(by_side)}")
    out = dict(base)
    for side in ("AWAY", "HOME"):
        row = by_side[side]
        prefix = side.lower()
        out[f"{prefix}_team_name"] = _text(row.get("runnerName"))
        out[f"{prefix}_selection_id"] = _text(row.get("selectionId"))
        out[f"{prefix}_odds"] = _american_odds(row)
    return out


def _normalize_spread(market: Mapping[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    runners = _active_runners(market)
    by_side = {_side(row): row for row in runners}
    if set(by_side) != {"AWAY", "HOME"}:
        raise ValueError(f"spread sides must be AWAY/HOME, got {sorted(by_side)}")
    away_handicap = _number(by_side["AWAY"].get("handicap"), "away handicap")
    home_handicap = _number(by_side["HOME"].get("handicap"), "home handicap")
    if not math.isclose(away_handicap + home_handicap, 0.0, abs_tol=1e-9):
        raise ValueError(
            f"spread handicaps must be opposites: {away_handicap}, {home_handicap}"
        )
    out = dict(base)
    for side, handicap in (("AWAY", away_handicap), ("HOME", home_handicap)):
        row = by_side[side]
        prefix = side.lower()
        out[f"{prefix}_team_name"] = _text(row.get("runnerName"))
        out[f"{prefix}_selection_id"] = _text(row.get("selectionId"))
        out[f"{prefix}_handicap"] = handicap
        out[f"{prefix}_odds"] = _american_odds(row)
    return out


def _normalize_total(market: Mapping[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    runners = _active_runners(market)
    by_side = {_side(row): row for row in runners}
    if set(by_side) != {"OVER", "UNDER"}:
        raise ValueError(f"total sides must be OVER/UNDER, got {sorted(by_side)}")
    over_line = _number(by_side["OVER"].get("handicap"), "over total")
    under_line = _number(by_side["UNDER"].get("handicap"), "under total")
    if not math.isclose(over_line, under_line, abs_tol=1e-9):
        raise ValueError(f"total runners disagree on line: {over_line}, {under_line}")
    out = dict(base)
    out.update({
        "total": over_line,
        "over_selection_id": _text(by_side["OVER"].get("selectionId")),
        "under_selection_id": _text(by_side["UNDER"].get("selectionId")),
        "over_odds": _american_odds(by_side["OVER"]),
        "under_odds": _american_odds(by_side["UNDER"]),
    })
    return out


def normalize_primary_game_markets(
    payload: Mapping[str, Any],
    *,
    payload_sha256: str,
    observed_at: str,
    event_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return normalized full-game records and explicit fail-closed failures."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    attachments = payload.get("attachments")
    if not isinstance(attachments, Mapping):
        raise ValueError("payload attachments must be a mapping")
    markets = attachments.get("markets")
    if not isinstance(markets, Mapping):
        raise ValueError("payload attachments.markets must be a mapping")

    requested_event = _text(event_id)
    candidates: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    failures: list[dict[str, str]] = []

    for raw in markets.values():
        if not isinstance(raw, Mapping):
            continue
        source_type = _text(raw.get("marketType"))
        canonical = SOURCE_TO_CANONICAL.get(source_type)
        if canonical is None:
            continue
        current_event = _text(raw.get("eventId"))
        if requested_event and current_event != requested_event:
            continue
        if _text(raw.get("marketStatus")).upper() != "OPEN" or bool(raw.get("inPlay")):
            continue
        candidates[(current_event, canonical)].append(raw)

    records: list[dict[str, Any]] = []
    for (current_event, canonical), rows in sorted(candidates.items()):
        if not current_event:
            failures.append({
                "event_id": "",
                "canonical_market": canonical,
                "reason": "MISSING_EVENT_ID",
            })
            continue
        if len(rows) != 1:
            failures.append({
                "event_id": current_event,
                "canonical_market": canonical,
                "reason": f"DUPLICATE_PRIMARY_MARKETS:{len(rows)}",
            })
            continue
        market = rows[0]
        try:
            base = _base_record(
                market,
                canonical_market=canonical,
                payload_sha256=payload_sha256,
                observed_at=observed_at,
            )
            if canonical == "moneyline":
                normalized = _normalize_moneyline(market, base)
            elif canonical == "spread":
                normalized = _normalize_spread(market, base)
            else:
                normalized = _normalize_total(market, base)
        except ValueError as exc:
            failures.append({
                "event_id": current_event,
                "canonical_market": canonical,
                "reason": str(exc),
            })
            continue
        records.append(normalized)

    return records, failures
