#!/usr/bin/env python3
"""Strict FanDuel passing candidate -> nflverse roster identity binding.

A normalized sportsbook player name is not yet a canonical player identity.
This binder requires:
- a resolvable Away @ Home NFL event,
- an exact normalized full-name match,
- the matched roster team to be one of the two event teams,
- QB-compatible roster position,
- exactly one surviving candidate,
- a non-empty GSIS ID.

There is no fuzzy fallback and no starter inference here.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from nfl.normalize.inactive_roster_binding import team_abbr


BINDING_CONTRACT_VERSION = 1


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _event_teams(event_name: Any) -> tuple[str, str] | None:
    text = str(event_name or "").strip()
    if text.count(" @ ") != 1:
        return None
    away_label, home_label = [part.strip() for part in text.split(" @ ", 1)]
    away = team_abbr(away_label)
    home = team_abbr(home_label)
    if not away or not home or away == home:
        return None
    return away, home


def _qb_compatible(row: Mapping[str, Any]) -> bool:
    position = re.sub(r"[^A-Z0-9]", "", str(row.get("position") or "").upper())
    depth = re.sub(
        r"[^A-Z0-9]",
        "",
        str(row.get("depth_chart_position") or "").upper(),
    )
    return "QB" in {position, depth}


def _base(
    candidate: Mapping[str, Any],
    *,
    status: str,
    away: str | None,
    home: str | None,
    candidate_count: int = 0,
) -> dict[str, Any]:
    return {
        **dict(candidate),
        "binding_contract_version": BINDING_CONTRACT_VERSION,
        "binding_status": status,
        "binding_method": None,
        "candidate_count": int(candidate_count),
        "event_away_team": away,
        "event_home_team": home,
        "team": None,
        "gsis_id": None,
        "esb_id": None,
        "roster_status": None,
    }


def bind_passing_candidate(
    candidate: Mapping[str, Any],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
) -> dict[str, Any]:
    """Bind one normalized primary passing-yards candidate to a durable QB ID."""
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be a mapping")
    if str(candidate.get("market") or "") != "passing_yards":
        raise ValueError("market must be passing_yards")

    teams = _event_teams(candidate.get("event_name"))
    if teams is None:
        return _base(
            candidate,
            status="UNRESOLVED_GAME_TEAMS",
            away=None,
            home=None,
        )
    away, home = teams
    event_teams = {away, home}

    name_key = _name_key(candidate.get("player_name"))
    if not name_key:
        return _base(
            candidate,
            status="UNRESOLVED_PLAYER",
            away=away,
            home=home,
        )

    exact_event = []
    exact_name_any_position = []
    for raw in roster_rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            row_season = int(raw.get("season"))
        except (TypeError, ValueError):
            continue
        if row_season != int(season):
            continue
        if _name_key(raw.get("full_name")) != name_key:
            continue

        team = str(raw.get("team") or "").strip().upper()
        if team not in event_teams:
            continue

        exact_name_any_position.append(raw)
        if _qb_compatible(raw):
            exact_event.append(raw)

    if not exact_name_any_position:
        return _base(
            candidate,
            status="UNRESOLVED_PLAYER",
            away=away,
            home=home,
        )

    if not exact_event:
        return _base(
            candidate,
            status="POSITION_MISMATCH",
            away=away,
            home=home,
            candidate_count=len(exact_name_any_position),
        )

    if len(exact_event) != 1:
        return _base(
            candidate,
            status="AMBIGUOUS_PLAYER",
            away=away,
            home=home,
            candidate_count=len(exact_event),
        )

    row = exact_event[0]
    gsis_id = str(row.get("gsis_id") or "").strip()
    if not gsis_id:
        return _base(
            candidate,
            status="MISSING_DURABLE_ID",
            away=away,
            home=home,
            candidate_count=1,
        )

    out = _base(
        candidate,
        status="BOUND",
        away=away,
        home=home,
        candidate_count=1,
    )
    out.update({
        "binding_method": "exact_name+event_team+qb_position",
        "team": str(row.get("team") or "").strip().upper(),
        "gsis_id": gsis_id,
        "esb_id": str(row.get("esb_id") or "").strip() or None,
        "roster_status": str(row.get("status") or "").strip() or None,
    })
    return out
