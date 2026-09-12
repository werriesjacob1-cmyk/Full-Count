#!/usr/bin/env python3
"""Strict offline binding from official NFL inactive rows to roster IDs.

This module is deliberately narrow:
- input 1: parsed source-local inactive rows,
- input 2: a caller-pinned nflverse roster snapshot,
- output: durable roster IDs only when one exact safe candidate exists.

It never fetches data, never guesses a game ID, never infers a starter, and
never turns an unresolved source row into evidence of availability.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


BINDING_CONTRACT_VERSION = 1


_TEAM_NICKNAMES = {
    "CARDINALS": "ARI",
    "FALCONS": "ATL",
    "RAVENS": "BAL",
    "BILLS": "BUF",
    "PANTHERS": "CAR",
    "BEARS": "CHI",
    "BENGALS": "CIN",
    "BROWNS": "CLE",
    "COWBOYS": "DAL",
    "BRONCOS": "DEN",
    "LIONS": "DET",
    "PACKERS": "GB",
    "TEXANS": "HOU",
    "COLTS": "IND",
    "JAGUARS": "JAX",
    "CHIEFS": "KC",
    "RAIDERS": "LV",
    "CHARGERS": "LAC",
    "RAMS": "LAR",
    "DOLPHINS": "MIA",
    "VIKINGS": "MIN",
    "PATRIOTS": "NE",
    "SAINTS": "NO",
    "GIANTS": "NYG",
    "JETS": "NYJ",
    "EAGLES": "PHI",
    "STEELERS": "PIT",
    "49ERS": "SF",
    "SEAHAWKS": "SEA",
    "BUCCANEERS": "TB",
    "TITANS": "TEN",
    "COMMANDERS": "WAS",
}

_TEAM_FULL_NAMES = {
    "ARIZONA CARDINALS": "ARI",
    "ATLANTA FALCONS": "ATL",
    "BALTIMORE RAVENS": "BAL",
    "BUFFALO BILLS": "BUF",
    "CAROLINA PANTHERS": "CAR",
    "CHICAGO BEARS": "CHI",
    "CINCINNATI BENGALS": "CIN",
    "CLEVELAND BROWNS": "CLE",
    "DALLAS COWBOYS": "DAL",
    "DENVER BRONCOS": "DEN",
    "DETROIT LIONS": "DET",
    "GREEN BAY PACKERS": "GB",
    "HOUSTON TEXANS": "HOU",
    "INDIANAPOLIS COLTS": "IND",
    "JACKSONVILLE JAGUARS": "JAX",
    "KANSAS CITY CHIEFS": "KC",
    "LAS VEGAS RAIDERS": "LV",
    "LOS ANGELES CHARGERS": "LAC",
    "LOS ANGELES RAMS": "LAR",
    "MIAMI DOLPHINS": "MIA",
    "MINNESOTA VIKINGS": "MIN",
    "NEW ENGLAND PATRIOTS": "NE",
    "NEW ORLEANS SAINTS": "NO",
    "NEW YORK GIANTS": "NYG",
    "NEW YORK JETS": "NYJ",
    "PHILADELPHIA EAGLES": "PHI",
    "PITTSBURGH STEELERS": "PIT",
    "SAN FRANCISCO 49ERS": "SF",
    "SEATTLE SEAHAWKS": "SEA",
    "TAMPA BAY BUCCANEERS": "TB",
    "TENNESSEE TITANS": "TEN",
    "WASHINGTON COMMANDERS": "WAS",
}

_TEAM_ALIASES = {}
_TEAM_ALIASES.update(_TEAM_NICKNAMES)
_TEAM_ALIASES.update(_TEAM_FULL_NAMES)
for _abbr in set(_TEAM_NICKNAMES.values()):
    _TEAM_ALIASES[_abbr] = _abbr


_POSITION_GROUP = {
    "QB": "QB",
    "RB": "RB", "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "C": "OL", "G": "OL", "OG": "OL", "T": "OL", "OT": "OL",
    "LT": "OL", "RT": "OL", "LG": "OL", "RG": "OL", "OL": "OL",
    "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
    "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
    "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB", "DB": "DB",
    "K": "K", "P": "P", "LS": "LS",
}


def _team_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    tokens = re.findall(r"[A-Za-z0-9]+", text.upper())
    return " ".join(tokens)


def team_abbr(source_team_label: Any) -> str | None:
    """Map an official report team label to nflverse abbreviation."""
    return _TEAM_ALIASES.get(_team_key(source_team_label))


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _position_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _position_group(value: Any) -> str:
    key = _position_key(value)
    return _POSITION_GROUP.get(key, key)


def _position_compatible(
    listed_position: Any,
    roster_position: Any,
    depth_position: Any,
) -> bool:
    listed = _position_group(listed_position)
    if not listed:
        return False
    candidates = {
        _position_group(roster_position),
        _position_group(depth_position),
    }
    candidates.discard("")
    return listed in candidates


def _base_result(
    source_team_label: Any,
    player: Mapping[str, Any],
    *,
    team: str | None,
    status: str,
    candidate_count: int = 0,
) -> dict[str, Any]:
    return {
        "binding_contract_version": BINDING_CONTRACT_VERSION,
        "binding_status": status,
        "binding_method": None,
        "source_team_label": str(source_team_label or "").strip(),
        "team": team,
        "player_name": str(player.get("player_name") or "").strip(),
        "listed_position": str(player.get("listed_position") or "").strip(),
        "source_player_href": player.get("source_player_href"),
        "source_player_slug": player.get("source_player_slug"),
        "candidate_count": int(candidate_count),
        "gsis_id": None,
        "esb_id": None,
        "roster_position": None,
        "roster_depth_chart_position": None,
    }


def bind_player(
    source_team_label: Any,
    player: Mapping[str, Any],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
) -> dict[str, Any]:
    """Bind one parsed inactive player using exact safe roster semantics.

    Matching order is intentionally strict:
    1. known source team label -> nflverse team abbreviation,
    2. exact normalized full_name inside that team and season,
    3. compatible listed/roster position group,
    4. exactly one surviving row,
    5. non-empty GSIS durable ID.

    There is no name-only or fuzzy fallback.
    """
    if not isinstance(player, Mapping):
        raise ValueError("player must be a mapping")

    team = team_abbr(source_team_label)
    if team is None:
        return _base_result(
            source_team_label, player, team=None, status="UNRESOLVED_TEAM"
        )

    name_key = _name_key(player.get("player_name"))
    if not name_key:
        return _base_result(
            source_team_label, player, team=team, status="UNRESOLVED_PLAYER"
        )

    exact_name_team = []
    for row in roster_rows:
        try:
            row_season = int(row.get("season"))
        except (TypeError, ValueError):
            continue
        if row_season != int(season):
            continue
        if str(row.get("team") or "").strip().upper() != team:
            continue
        if _name_key(row.get("full_name")) != name_key:
            continue
        exact_name_team.append(row)

    if not exact_name_team:
        return _base_result(
            source_team_label, player, team=team, status="UNRESOLVED_PLAYER"
        )

    position_matches = [
        row for row in exact_name_team
        if _position_compatible(
            player.get("listed_position"),
            row.get("position"),
            row.get("depth_chart_position"),
        )
    ]
    if not position_matches:
        return _base_result(
            source_team_label,
            player,
            team=team,
            status="POSITION_MISMATCH",
            candidate_count=len(exact_name_team),
        )
    if len(position_matches) != 1:
        return _base_result(
            source_team_label,
            player,
            team=team,
            status="AMBIGUOUS_PLAYER",
            candidate_count=len(position_matches),
        )

    row = position_matches[0]
    gsis_id = str(row.get("gsis_id") or "").strip()
    if not gsis_id:
        return _base_result(
            source_team_label,
            player,
            team=team,
            status="MISSING_DURABLE_ID",
            candidate_count=1,
        )

    result = _base_result(
        source_team_label,
        player,
        team=team,
        status="BOUND",
        candidate_count=1,
    )
    result.update({
        "binding_method": "exact_name+team+position_group",
        "gsis_id": gsis_id,
        "esb_id": str(row.get("esb_id") or "").strip() or None,
        "roster_position": str(row.get("position") or "").strip() or None,
        "roster_depth_chart_position":
            str(row.get("depth_chart_position") or "").strip() or None,
    })
    return result


def bind_report(
    parsed_report: Mapping[str, Any],
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
) -> dict[str, Any]:
    """Bind every source row while preserving unresolved rows explicitly."""
    bound_teams = []
    status_counts: dict[str, int] = {}
    total = 0
    bound = 0

    for team_block in parsed_report.get("teams") or []:
        label = team_block.get("source_team_label")
        players = []
        for player in team_block.get("players") or []:
            result = bind_player(
                label, player, roster_rows, season=season
            )
            players.append(result)
            total += 1
            status = result["binding_status"]
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "BOUND":
                bound += 1
        bound_teams.append({
            "source_team_label": label,
            "team": team_abbr(label),
            "players": players,
        })

    return {
        "binding_contract_version": BINDING_CONTRACT_VERSION,
        "report_title": parsed_report.get("report_title"),
        "report_published_at": parsed_report.get("report_published_at"),
        "season": int(season),
        "player_count": total,
        "bound_player_count": bound,
        "unresolved_player_count": total - bound,
        "all_players_bound": total > 0 and bound == total,
        "status_counts": status_counts,
        "teams": bound_teams,
        "canonical_game_id": None,
        "game_identity_bound": False,
    }
