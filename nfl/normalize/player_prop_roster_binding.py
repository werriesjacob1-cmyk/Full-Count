#!/usr/bin/env python3
"""Strict FanDuel non-passing player-prop candidate -> roster identity binding.

Sibling to `market_roster_binding.py`'s QB-only `bind_passing_candidate`,
generalized to the markets normalized by `player_prop_markets.py`. Kept as a
separate module rather than folded into `market_roster_binding.py` so the
validated passing-yards binder stays untouched.

Binding requires, in order:
- a resolvable Away @ Home NFL event,
- an exact normalized full-name match,
- the matched roster team to be one of the two event teams,
- a roster position compatible with the specific prop market (a passing-
  touchdowns candidate must be a QB; a record-a-sack candidate must be a
  defensive player; and so on -- see MARKET_POSITION_GROUPS),
- exactly one surviving candidate,
- a non-empty GSIS ID.

There is no fuzzy fallback and no starter inference here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.normalize.inactive_roster_binding import _position_group, team_abbr
from nfl.normalize.market_roster_binding import _name_key


BINDING_CONTRACT_VERSION = 1

# canonical_market -> allowed roster position groups (see _POSITION_GROUP in
# inactive_roster_binding.py for how a raw position string maps to a group).
# Deliberately inclusive of every position that plausibly appears in FanDuel's
# own market, not just the modal one (e.g. rushing_yards includes QB and WR
# alongside RB) -- the goal is to reject genuine mismatches, not legitimate
# but less common prop types.
MARKET_POSITION_GROUPS: dict[str, frozenset[str]] = {
    "passing_touchdowns": frozenset({"QB"}),
    "passing_touchdowns_alt": frozenset({"QB"}),
    "rushing_yards": frozenset({"RB", "QB", "WR"}),
    "rushing_yards_alt": frozenset({"RB", "QB", "WR"}),
    "receiving_yards": frozenset({"RB", "WR", "TE"}),
    "receiving_yards_alt": frozenset({"RB", "WR", "TE"}),
    "receptions": frozenset({"RB", "WR", "TE"}),
    "receptions_alt": frozenset({"RB", "WR", "TE"}),
    "rush_plus_rec_yards": frozenset({"RB", "WR", "TE", "QB"}),
    "anytime_touchdown": frozenset({"QB", "RB", "WR", "TE"}),
    "two_plus_touchdowns": frozenset({"QB", "RB", "WR", "TE"}),
    "three_plus_touchdowns": frozenset({"QB", "RB", "WR", "TE"}),
    "four_plus_touchdowns": frozenset({"QB", "RB", "WR", "TE"}),
    "record_a_sack": frozenset({"DL", "LB", "DB"}),
}


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


def _position_compatible_for_market(
    allowed: frozenset[str],
    roster_position: Any,
    depth_position: Any,
) -> bool:
    candidates = {_position_group(roster_position), _position_group(depth_position)}
    candidates.discard("")
    return bool(candidates & allowed)


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


def bind_player_prop_candidate(
    candidate: Mapping[str, Any],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
) -> dict[str, Any]:
    """Bind one normalized non-passing-yards player-prop candidate to a GSIS ID."""
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be a mapping")
    market = str(candidate.get("market") or "")
    if market not in MARKET_POSITION_GROUPS:
        raise ValueError(f"unsupported market for player-prop binding: {market!r}")
    allowed_positions = MARKET_POSITION_GROUPS[market]

    teams = _event_teams(candidate.get("event_name"))
    if teams is None:
        return _base(candidate, status="UNRESOLVED_GAME_TEAMS", away=None, home=None)
    away, home = teams
    event_teams = {away, home}

    name_key = _name_key(candidate.get("player_name"))
    if not name_key:
        return _base(candidate, status="UNRESOLVED_PLAYER", away=away, home=home)

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
        if _position_compatible_for_market(
            allowed_positions, raw.get("position"), raw.get("depth_chart_position")
        ):
            exact_event.append(raw)

    if not exact_name_any_position:
        return _base(candidate, status="UNRESOLVED_PLAYER", away=away, home=home)

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

    out = _base(candidate, status="BOUND", away=away, home=home, candidate_count=1)
    out.update({
        "binding_method": "exact_name+event_team+market_position",
        "team": str(row.get("team") or "").strip().upper(),
        "gsis_id": gsis_id,
        "esb_id": str(row.get("esb_id") or "").strip() or None,
        "roster_status": str(row.get("status") or "").strip() or None,
    })
    return out
