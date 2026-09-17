#!/usr/bin/env python3
"""Strict normalization for FanDuel NFL player-prop markets beyond passing yards.

Three DISTINCT runner shapes exist on this book, verified live against event
35599552 (2026-09-17) rather than assumed -- conflating them was a real mistake
caught before code was written against it (see
nfl/docs/PLAYER_PROP_SETTLEMENT_SPEC.md's "Corrected finding"):

1. PRIMARY (paired OVER/UNDER): one line, two runners, `result.type` in
   {OVER, UNDER}, matching handicap on both sides. Same contract as
   `fanduel_passing.py`'s existing passing-yards parser, generalized to the
   other primary stat families that module deliberately does not cover.

2. ALT LADDER (one-sided, per-runner threshold in text): each runner is an
   independent rung -- e.g. "Jared Goff 175+ Yards", handicap=0, result={},
   one price -- with its threshold embedded ONLY in `runnerName`. A player can
   clear one rung and miss another in the same game; each rung is graded
   separately, never merged into one line.

3. SINGLE-THRESHOLD MARKET-LEVEL (anytime TD, 2+/3+/4+ TDs, 1+ sack): the
   THRESHOLD belongs to the market itself, not the runner. Every runner in the
   market is just a bare player name with no ladder text at all -- "Jahmyr
   Gibbs", handicap=0, one price. `ANY_TIME_TOUCHDOWN_SCORER` is threshold=1,
   `TO_SCORE_2+_TOUCHDOWNS` is threshold=2, and so on.

Dispatch is by exact `market_type` membership, never by name-suffix pattern
matching alone -- market_type is unambiguous where a suffix could theoretically
collide (e.g. "... Rushing Yds" vs "... Rushing + Receiving Yds"). The name
suffix is still checked as an integrity cross-check, matching
`fanduel_passing.py`'s existing discipline of trusting structure over text.

`passing_yards` / `passing_yards_alt` are NOT handled here. They already have
a dedicated, validated normalizer (`fanduel_passing.py`) feeding the live
shadow scorer; duplicating that logic here risks two normalizers silently
drifting apart for the one market family that already has real backtest
evidence behind it.

`reception_yardage_threshold` (PLAYERS_WITH_10+/15+/20+/30+_YARDS_RECEPTION)
is NOT handled here. Deferred: it settles against a longest-single-catch
threshold that needs per-target play-by-play, not the box-score season total,
and tonight's clock does not allow verifying that shape live before coding it.
Recorded as an explicit gap, not silently dropped -- see the settlement spec.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


# market_type -> (canonical_market, exact market-name suffix)
PRIMARY_STAT_MARKETS: dict[str, tuple[str, str]] = {
    "PLAYER_X_PASSING_TOUCHDOWNS_HIGH": ("passing_touchdowns", " - Passing TDs"),
    "PLAYER_X_RUSHING_YARDS_HIGH": ("rushing_yards", " - Rushing Yds"),
    "PLAYER_X_RUSHING_YARDS_LOW": ("rushing_yards", " - Rushing Yds"),
    "PLAYER_X_RECEIVING_YARDS_HIGH": ("receiving_yards", " - Receiving Yds"),
    "PLAYER_X_RECEIVING_YARDS_LOW": ("receiving_yards", " - Receiving Yds"),
    "PLAYER_X_RECEPTIONS_HIGH": ("receptions", " - Total Receptions"),
    "PLAYER_X_RECEPTIONS_LOW": ("receptions", " - Total Receptions"),
    "PLAYER_X_RUSHING_+_RECEIVING_YARDS": (
        "rush_plus_rec_yards", " - Rushing + Receiving Yds"),
}

# market_type -> (canonical_market, exact market-name suffix, per-runner threshold regex)
# The regex's single capture group is the integer threshold; it must match the
# WHOLE runner name after the player prefix is stripped, anchored, so a name
# containing a stray digit elsewhere can never be mistaken for the threshold.
ALT_LADDER_MARKETS: dict[str, tuple[str, str, str]] = {
    "PLAYER_X_ALT_RUSHING_YARDS_HIGH": (
        "rushing_yards_alt", " - Alt Rushing Yds", r"(\d+)\+ Yards$"),
    "PLAYER_X_ALT_RUSHING_YARDS_LOW": (
        "rushing_yards_alt", " - Alt Rushing Yds", r"(\d+)\+ Yards$"),
    "PLAYER_X_ALT_RECEIVING_YARDS_HIGH": (
        "receiving_yards_alt", " - Alt Receiving Yds", r"(\d+)\+ Yards$"),
    "PLAYER_X_ALT_RECEIVING_YARDS_LOW": (
        "receiving_yards_alt", " - Alt Receiving Yds", r"(\d+)\+ Yards$"),
    "PLAYER_X_ALT_RECEPTIONS_HIGH": (
        "receptions_alt", " - Alt Receptions", r"(\d+)\+ Receptions$"),
    "PLAYER_X_ALT_RECEPTIONS_LOW": (
        "receptions_alt", " - Alt Receptions", r"(\d+)\+ Receptions$"),
    "PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH": (
        "passing_touchdowns_alt", " - Alt Passing TDs",
        r"(\d+)\+ Passing Touchdowns$"),
}

# market_type -> (canonical_market, fixed threshold)
SINGLE_THRESHOLD_MARKETS: dict[str, tuple[str, int]] = {
    "ANY_TIME_TOUCHDOWN_SCORER": ("anytime_touchdown", 1),
    "TO_SCORE_2+_TOUCHDOWNS": ("two_plus_touchdowns", 2),
    "TO_SCORE_3+_TOUCHDOWNS": ("three_plus_touchdowns", 3),
    "TO_SCORE_4+_TOUCHDOWNS": ("four_plus_touchdowns", 4),
    "TO_RECORD_1+_SACK": ("record_a_sack", 1),
}

ALL_KNOWN_TYPES = (
    frozenset(PRIMARY_STAT_MARKETS) | frozenset(ALT_LADDER_MARKETS)
    | frozenset(SINGLE_THRESHOLD_MARKETS)
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
        event_id = event.get("eventId")
        out[str(key)] = event
        if event_id not in (None, ""):
            out[str(event_id)] = event
    return out


def _american_odds(runner: Mapping[str, Any]) -> int | None:
    odds = _as_dict(
        _as_dict(runner.get("winRunnerOdds")).get("americanDisplayOdds")
    ).get("americanOddsInt")
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


def _reject(rejections: list[dict], market: Mapping[str, Any], reason: str,
            detail: str | None = None) -> None:
    rejections.append({
        "market_id": (str(market.get("marketId"))
                     if market.get("marketId") not in (None, "") else None),
        "event_id": (str(market.get("eventId"))
                     if market.get("eventId") not in (None, "") else None),
        "market_name": str(market.get("marketName") or ""),
        "market_type": str(market.get("marketType") or ""),
        "reason": reason,
        "detail": detail,
    })


def _base_gate(market: Mapping[str, Any], events: dict, market_type: str,
                rejections: list[dict]) -> tuple[str, dict] | None:
    """Checks shared by all three shapes: in-play, status, resolvable event."""
    if bool(market.get("inPlay")):
        _reject(rejections, market, "IN_PLAY")
        return None
    status = str(market.get("marketStatus") or "").upper()
    if status in {"CLOSED", "SUSPENDED"}:
        _reject(rejections, market, "MARKET_NOT_OPEN", status)
        return None
    event_id_raw = market.get("eventId")
    event_id = str(event_id_raw) if event_id_raw not in (None, "") else None
    event = events.get(event_id or "")
    event_name = str((event or {}).get("name") or "").strip()
    if not event or " @ " not in event_name:
        _reject(rejections, market, "UNRESOLVED_GAME")
        return None
    return event_id, event


def _normalize_primary(market: Mapping[str, Any], dict_key: str, event_id: str,
                       event: dict, canonical_market: str, name_suffix: str,
                       captured_at: str | None, rejections: list[dict],
                       ) -> dict | None:
    market_name = str(market.get("marketName") or "").strip()
    if not market_name.endswith(name_suffix):
        _reject(rejections, market, "MALFORMED_MARKET_NAME")
        return None
    player = market_name[: -len(name_suffix)].strip()
    if not player:
        _reject(rejections, market, "MALFORMED_MARKET_NAME")
        return None

    runners = market.get("runners")
    if not isinstance(runners, list):
        _reject(rejections, market, "RUNNERS_NOT_LIST")
        return None

    sides: dict[str, Mapping[str, Any]] = {}
    duplicate = False
    for runner in runners:
        if not isinstance(runner, Mapping):
            continue
        side = str(_as_dict(runner.get("result")).get("type") or "").strip().upper()
        if side not in {"OVER", "UNDER"}:
            continue
        if side in sides:
            duplicate = True
        sides[side] = runner
    if duplicate or set(sides) != {"OVER", "UNDER"}:
        _reject(rejections, market, "SIDE_CARDINALITY")
        return None

    over, under = sides["OVER"], sides["UNDER"]
    for side_name, runner in (("OVER", over), ("UNDER", under)):
        runner_name = str(runner.get("runnerName") or "").strip()
        if not runner_name.startswith(player) or not runner_name[len(player):].strip().upper() == side_name:
            _reject(rejections, market, "PLAYER_IDENTITY_MISMATCH", side_name)
            return None
        if str(runner.get("runnerStatus") or "").upper() != "ACTIVE":
            _reject(rejections, market, "RUNNER_NOT_ACTIVE", side_name)
            return None

    over_line = _finite_line(over.get("handicap"))
    under_line = _finite_line(under.get("handicap"))
    if over_line is None or under_line is None:
        _reject(rejections, market, "MISSING_LINE")
        return None
    if not math.isclose(over_line, under_line, rel_tol=0.0, abs_tol=1e-12):
        _reject(rejections, market, "LINE_MISMATCH")
        return None

    over_odds, under_odds = _american_odds(over), _american_odds(under)
    if over_odds is None or under_odds is None:
        _reject(rejections, market, "MISSING_ODDS")
        return None

    return {
        "source": "fanduel_nfl",
        "shape": "primary",
        "market": canonical_market,
        "event_id": event_id,
        "event_name": str(event.get("name") or ""),
        "event_open_date": event.get("openDate"),
        "market_id": str(market.get("marketId", dict_key)),
        "market_name": market_name,
        "market_type": str(market.get("marketType") or ""),
        "market_time": market.get("marketTime"),
        "player_name": player,
        "line": over_line,
        "over_odds": over_odds,
        "under_odds": under_odds,
        "over_selection_id": str(over.get("selectionId")),
        "under_selection_id": str(under.get("selectionId")),
        "captured_at": captured_at,
    }


def _normalize_alt_ladder(market: Mapping[str, Any], dict_key: str, event_id: str,
                          event: dict, canonical_market: str, name_suffix: str,
                          threshold_pattern: str, captured_at: str | None,
                          rejections: list[dict]) -> list[dict]:
    market_name = str(market.get("marketName") or "").strip()
    if not market_name.endswith(name_suffix):
        _reject(rejections, market, "MALFORMED_MARKET_NAME")
        return []
    player = market_name[: -len(name_suffix)].strip()
    if not player:
        _reject(rejections, market, "MALFORMED_MARKET_NAME")
        return []

    runners = market.get("runners")
    if not isinstance(runners, list):
        _reject(rejections, market, "RUNNERS_NOT_LIST")
        return []

    pattern = re.compile(r"^" + re.escape(player) + r" " + threshold_pattern)
    seen_thresholds: set[int] = set()
    out: list[dict] = []
    for runner in runners:
        if not isinstance(runner, Mapping):
            continue
        runner_name = str(runner.get("runnerName") or "").strip()
        match = pattern.match(runner_name)
        if not match:
            _reject(rejections, market, "MALFORMED_RUNNER_NAME", runner_name)
            continue
        try:
            threshold = int(match.group(1))
        except (TypeError, ValueError, IndexError):
            _reject(rejections, market, "MALFORMED_RUNNER_NAME", runner_name)
            continue
        if threshold in seen_thresholds:
            _reject(rejections, market, "DUPLICATE_THRESHOLD", str(threshold))
            continue
        if str(runner.get("runnerStatus") or "").upper() != "ACTIVE":
            _reject(rejections, market, "RUNNER_NOT_ACTIVE", runner_name)
            continue
        odds = _american_odds(runner)
        if odds is None:
            _reject(rejections, market, "MISSING_ODDS", runner_name)
            continue
        seen_thresholds.add(threshold)
        out.append({
            "source": "fanduel_nfl",
            "shape": "alt_ladder",
            "market": canonical_market,
            "event_id": event_id,
            "event_name": str(event.get("name") or ""),
            "event_open_date": event.get("openDate"),
            "market_id": str(market.get("marketId", dict_key)),
            "market_name": market_name,
            "market_type": str(market.get("marketType") or ""),
            "market_time": market.get("marketTime"),
            "player_name": player,
            "threshold": threshold,
            "yes_odds": odds,
            "selection_id": str(runner.get("selectionId")),
            "captured_at": captured_at,
        })
    return out


def _normalize_single_threshold(market: Mapping[str, Any], dict_key: str,
                                event_id: str, event: dict, canonical_market: str,
                                threshold: int, captured_at: str | None,
                                rejections: list[dict]) -> list[dict]:
    runners = market.get("runners")
    if not isinstance(runners, list):
        _reject(rejections, market, "RUNNERS_NOT_LIST")
        return []
    out: list[dict] = []
    seen_players: set[str] = set()
    for runner in runners:
        if not isinstance(runner, Mapping):
            continue
        player = str(runner.get("runnerName") or "").strip()
        if not player:
            _reject(rejections, market, "MALFORMED_RUNNER_NAME")
            continue
        if player in seen_players:
            _reject(rejections, market, "DUPLICATE_PLAYER", player)
            continue
        if str(runner.get("runnerStatus") or "").upper() != "ACTIVE":
            _reject(rejections, market, "RUNNER_NOT_ACTIVE", player)
            continue
        odds = _american_odds(runner)
        if odds is None:
            _reject(rejections, market, "MISSING_ODDS", player)
            continue
        seen_players.add(player)
        out.append({
            "source": "fanduel_nfl",
            "shape": "single_threshold",
            "market": canonical_market,
            "event_id": event_id,
            "event_name": str(event.get("name") or ""),
            "event_open_date": event.get("openDate"),
            "market_id": str(market.get("marketId", dict_key)),
            "market_name": str(market.get("marketName") or ""),
            "market_type": str(market.get("marketType") or ""),
            "market_time": market.get("marketTime"),
            "player_name": player,
            "threshold": threshold,
            "yes_odds": odds,
            "selection_id": str(runner.get("selectionId")),
            "captured_at": captured_at,
        })
    return out


def normalize_payload(payload: Mapping[str, Any], *,
                      captured_at: str | None = None) -> dict[str, Any]:
    """Normalize every supported player-prop market in one raw event-page payload.

    Unknown/unsupported market types (including passing_yards, which has its
    own dedicated normalizer, and reception_yardage_threshold, deferred) are
    ignored, not rejected -- rejection is reserved for a market that IS one of
    the supported types but violates its contract.
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
    stats = {"markets_total": 0, "supported_markets_seen": 0, "normalized": 0,
             "rejected": 0, "ignored_unsupported": 0}

    for dict_key, raw_market in markets.items():
        if not isinstance(raw_market, Mapping):
            continue
        market = dict(raw_market)
        stats["markets_total"] += 1
        market_type = str(market.get("marketType") or "").strip()

        if market_type not in ALL_KNOWN_TYPES:
            stats["ignored_unsupported"] += 1
            continue
        stats["supported_markets_seen"] += 1

        gated = _base_gate(market, events, market_type, rejections)
        if gated is None:
            continue
        event_id, event = gated

        if market_type in PRIMARY_STAT_MARKETS:
            canonical_market, name_suffix = PRIMARY_STAT_MARKETS[market_type]
            row = _normalize_primary(market, dict_key, event_id, event,
                                     canonical_market, name_suffix,
                                     captured_at, rejections)
            if row is not None:
                candidates.append(row)
        elif market_type in ALT_LADDER_MARKETS:
            canonical_market, name_suffix, pattern = ALT_LADDER_MARKETS[market_type]
            candidates.extend(_normalize_alt_ladder(
                market, dict_key, event_id, event, canonical_market,
                name_suffix, pattern, captured_at, rejections))
        else:
            canonical_market, threshold = SINGLE_THRESHOLD_MARKETS[market_type]
            candidates.extend(_normalize_single_threshold(
                market, dict_key, event_id, event, canonical_market,
                threshold, captured_at, rejections))

    stats["normalized"] = len(candidates)
    stats["rejected"] = len(rejections)
    return {"candidates": candidates, "rejections": rejections, "stats": stats}
