"""Fail-closed identity/provenance binding for prospective NFL game markets.

This module binds a sealed FanDuel market snapshot to one nflverse schedule row
using explicit team aliases plus the exact scheduled kickoff instant. It does
NOT infer that a game is final: nflverse schedules expose scores but no explicit
final-status field, so score presence is reported separately from finality.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from nfl.prospective.game_market_snapshot import (
    GameMarketSnapshotError,
    verify_game_market_snapshot,
)

_EASTERN = ZoneInfo("America/New_York")


class GameIdentityError(ValueError):
    """Raised when sportsbook-to-schedule identity cannot be proven uniquely."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameIdentityError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_datetime(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GameIdentityError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GameIdentityError(f"{field} must be timezone-aware")
    return parsed


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise GameIdentityError(f"{field} must be 64 lowercase hex characters")
    return normalized


def _optional_int(row: Mapping[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise GameIdentityError(f"{key} must be an integer or empty") from exc
    raise GameIdentityError(f"{key} must be an integer or empty")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_team_names(snapshot: Mapping[str, Any]) -> tuple[str, str]:
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise GameIdentityError("snapshot.records must be a list")

    pairs: set[tuple[str, str]] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise GameIdentityError("snapshot record must be a mapping")
        away = row.get("away_team")
        home = row.get("home_team")
        if away is None and home is None:
            continue
        if away is None or home is None:
            raise GameIdentityError("team-bearing record must contain both away/home team names")
        pairs.add((_text(away, "away_team_name"), _text(home, "home_team_name")))

    if not pairs:
        raise GameIdentityError("snapshot has no team-bearing normalized record")
    if len(pairs) != 1:
        raise GameIdentityError("normalized records disagree on away/home team identity")
    return next(iter(pairs))


def _market_time(snapshot: Mapping[str, Any]) -> datetime:
    records = snapshot.get("records")
    times: set[str] = set()
    for row in records:
        if isinstance(row, Mapping):
            value = row.get("market_time")
            if value is not None:
                times.add(_text(value, "market_time"))
    if len(times) != 1:
        raise GameIdentityError("snapshot records must share exactly one market_time")
    return _aware_datetime(next(iter(times)), "market_time")


def _score_state(row: Mapping[str, Any]) -> tuple[str, int | None, int | None]:
    away = _optional_int(row, "away_score")
    home = _optional_int(row, "home_score")
    result = _optional_int(row, "result")
    total = _optional_int(row, "total")

    values = (away, home, result, total)
    if all(value is None for value in values):
        return "UNSETTLED", None, None
    if any(value is None for value in values):
        raise GameIdentityError("partial nflverse score state")
    if away < 0 or home < 0:
        raise GameIdentityError("scores must be non-negative")
    if result != home - away:
        raise GameIdentityError("nflverse result does not equal home_score-away_score")
    if total != home + away:
        raise GameIdentityError("nflverse total does not equal home_score+away_score")
    return "SCORES_PRESENT_CONSISTENT", away, home


def bind_nflverse_game_identity(
    snapshot: Mapping[str, Any],
    schedule_rows: Sequence[Mapping[str, Any]],
    *,
    team_aliases: Mapping[str, str],
    source_url: str,
    source_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    """Bind one sealed FanDuel event to exactly one nflverse schedule row.

    ``team_aliases`` is deliberately supplied by the caller rather than guessed.
    nflverse ``gameday``/``gametime`` are matched to the FanDuel market instant
    after converting that instant to America/New_York.
    """
    if not isinstance(snapshot, Mapping):
        raise GameIdentityError("snapshot must be a mapping")
    if snapshot.get("sport") != "NFL" or snapshot.get("research_only") is not True:
        raise GameIdentityError("snapshot must be NFL research-only evidence")
    event_id = _text(snapshot.get("event_id"), "snapshot.event_id")
    try:
        snapshot_sha = verify_game_market_snapshot(snapshot)
    except GameMarketSnapshotError as exc:
        raise GameIdentityError(f"invalid sealed snapshot: {exc}") from exc
    _sha256(snapshot.get("source_payload_sha256"), "snapshot.source_payload_sha256")

    away_name, home_name = _snapshot_team_names(snapshot)
    try:
        away_team = _text(team_aliases[away_name], f"team_aliases[{away_name!r}]").upper()
        home_team = _text(team_aliases[home_name], f"team_aliases[{home_name!r}]").upper()
    except KeyError as exc:
        raise GameIdentityError(f"unresolved sportsbook team alias: {exc.args[0]}") from exc
    if away_team == home_team:
        raise GameIdentityError("away/home aliases resolve to the same team")

    market_time = _market_time(snapshot)
    eastern = market_time.astimezone(_EASTERN)
    expected_day = eastern.date().isoformat()
    expected_time = eastern.strftime("%H:%M")

    matches: list[Mapping[str, Any]] = []
    for row in schedule_rows:
        if not isinstance(row, Mapping):
            continue
        if (
            str(row.get("away_team") or "").strip().upper() == away_team
            and str(row.get("home_team") or "").strip().upper() == home_team
            and str(row.get("gameday") or "").strip() == expected_day
            and str(row.get("gametime") or "").strip() == expected_time
        ):
            matches.append(row)

    if len(matches) != 1:
        raise GameIdentityError(f"expected exactly one nflverse game match, found {len(matches)}")
    row = deepcopy(dict(matches[0]))

    game_id = _text(row.get("game_id"), "nflverse.game_id")
    season = _optional_int(row, "season")
    week = _optional_int(row, "week")
    game_type = _text(row.get("game_type"), "nflverse.game_type").upper()
    if season is None or week is None or season < 1999 or week < 1:
        raise GameIdentityError("invalid nflverse season/week")

    score_state, away_score, home_score = _score_state(row)
    source_observed = _aware_datetime(observed_at, "observed_at")
    source_digest = _sha256(source_sha256, "source_sha256")

    body = {
        "schema_version": 1,
        "sportsbook": "FANDUEL",
        "sportsbook_event_id": event_id,
        "sportsbook_snapshot_sha256": snapshot_sha,
        "nflverse_game_id": game_id,
        "nflverse_gsis": str(row.get("gsis") or "").strip() or None,
        "nflverse_espn": str(row.get("espn") or "").strip() or None,
        "season": season,
        "week": week,
        "game_type": game_type,
        "away_team": away_team,
        "home_team": home_team,
        "away_team_name": away_name,
        "home_team_name": home_name,
        "scheduled_kickoff": market_time.isoformat(),
        "binding_basis": "AWAY_HOME_TEAMS_PLUS_EXACT_ET_KICKOFF",
        "score_state": score_state,
        "away_score": away_score,
        "home_score": home_score,
        "final_status": None,
        "finality_proven": False,
        "source": "nflverse_schedules",
        "source_url": _text(source_url, "source_url"),
        "source_sha256": source_digest,
        "source_observed_at": source_observed.isoformat(),
    }
    return {**body, "binding_sha256": _canonical_hash(body)}
