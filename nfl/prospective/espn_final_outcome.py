"""Extract a provenance-rich, explicitly final NFL outcome from ESPN scoreboard data.

This adapter consumes a previously proven FanDuel<->nflverse identity binding.
It trusts neither score presence nor team names alone: the ESPN event id must
match nflverse's retained ESPN id, both teams/roles must match, kickoff must
match, and ESPN must explicitly report completed=true plus STATUS_FINAL.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


class EspnFinalOutcomeError(ValueError):
    """Raised when ESPN cannot prove one explicit final outcome safely."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EspnFinalOutcomeError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise EspnFinalOutcomeError(f"{field} must be 64 lowercase hex characters")
    return normalized


def _aware_datetime(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EspnFinalOutcomeError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EspnFinalOutcomeError(f"{field} must be timezone-aware")
    return parsed


def _score(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise EspnFinalOutcomeError(f"{field} must be a non-negative integer string")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value.strip())
        except ValueError as exc:
            raise EspnFinalOutcomeError(f"{field} must be a non-negative integer string") from exc
    else:
        raise EspnFinalOutcomeError(f"{field} must be a non-negative integer string")
    if result < 0:
        raise EspnFinalOutcomeError(f"{field} must be non-negative")
    return result


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_espn_final_outcome(
    binding: Mapping[str, Any],
    espn_event: Mapping[str, Any],
    *,
    source_url: str,
    payload_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    """Return a grader-ready outcome only when ESPN explicitly proves FINAL."""
    if not isinstance(binding, Mapping) or not isinstance(espn_event, Mapping):
        raise EspnFinalOutcomeError("binding and espn_event must be mappings")

    sportsbook_event_id = _text(binding.get("sportsbook_event_id"), "binding.sportsbook_event_id")
    binding_sha = _sha256(binding.get("binding_sha256"), "binding.binding_sha256")
    espn_id = _text(binding.get("nflverse_espn"), "binding.nflverse_espn")
    away_team = _text(binding.get("away_team"), "binding.away_team").upper()
    home_team = _text(binding.get("home_team"), "binding.home_team").upper()
    scheduled = _aware_datetime(binding.get("scheduled_kickoff"), "binding.scheduled_kickoff")

    if _text(espn_event.get("id"), "espn.id") != espn_id:
        raise EspnFinalOutcomeError("ESPN event id does not match nflverse ESPN id")
    event_date = _aware_datetime(espn_event.get("date"), "espn.date")
    if event_date != scheduled:
        raise EspnFinalOutcomeError("ESPN kickoff does not match bound scheduled kickoff")

    status = espn_event.get("status")
    if not isinstance(status, Mapping):
        raise EspnFinalOutcomeError("espn.status must be a mapping")
    status_type = status.get("type")
    if not isinstance(status_type, Mapping):
        raise EspnFinalOutcomeError("espn.status.type must be a mapping")
    completed = status_type.get("completed")
    status_name = _text(status_type.get("name"), "espn.status.type.name").upper()
    if completed is not True or status_name != "STATUS_FINAL":
        raise EspnFinalOutcomeError(
            f"ESPN event is not explicitly final: completed={completed!r} status={status_name}"
        )

    competitions = espn_event.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        raise EspnFinalOutcomeError("ESPN event must contain exactly one competition")
    competition = competitions[0]
    if not isinstance(competition, Mapping):
        raise EspnFinalOutcomeError("ESPN competition must be a mapping")
    competition_id = _text(competition.get("id"), "espn.competition.id")
    if competition_id != espn_id:
        raise EspnFinalOutcomeError("ESPN competition id does not match event id")

    competitors = competition.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise EspnFinalOutcomeError("ESPN competition must contain exactly two competitors")

    by_role: dict[str, Mapping[str, Any]] = {}
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            raise EspnFinalOutcomeError("ESPN competitor must be a mapping")
        role = _text(competitor.get("homeAway"), "espn.competitor.homeAway").lower()
        if role not in {"home", "away"} or role in by_role:
            raise EspnFinalOutcomeError("ESPN competitors must contain unique home/away roles")
        by_role[role] = competitor
    if set(by_role) != {"home", "away"}:
        raise EspnFinalOutcomeError("ESPN competitors must contain home and away")

    def team_abbr(competitor: Mapping[str, Any], role: str) -> str:
        team = competitor.get("team")
        if not isinstance(team, Mapping):
            raise EspnFinalOutcomeError(f"ESPN {role} team must be a mapping")
        return _text(team.get("abbreviation"), f"espn.{role}.team.abbreviation").upper()

    espn_away = team_abbr(by_role["away"], "away")
    espn_home = team_abbr(by_role["home"], "home")
    if espn_away != away_team or espn_home != home_team:
        raise EspnFinalOutcomeError(
            f"ESPN team identity mismatch: expected {away_team}@{home_team}, got {espn_away}@{espn_home}"
        )

    away_score = _score(by_role["away"].get("score"), "espn.away.score")
    home_score = _score(by_role["home"].get("score"), "espn.home.score")

    source_digest = _sha256(payload_sha256, "payload_sha256")
    observed = _aware_datetime(observed_at, "observed_at")
    if observed < event_date:
        raise EspnFinalOutcomeError("observed_at cannot precede kickoff for a final outcome")

    body = {
        "schema_version": 1,
        "event_id": sportsbook_event_id,
        "home_score": home_score,
        "away_score": away_score,
        "final_status": "FINAL",
        "authoritative_event_id": espn_id,
        "competition_id": competition_id,
        "away_team": away_team,
        "home_team": home_team,
        "scheduled_kickoff": scheduled.isoformat(),
        "finality_basis": "ESPN_STATUS_FINAL_AND_COMPLETED_TRUE",
        "binding_sha256": binding_sha,
        "source": "espn_scoreboard",
        "source_url": _text(source_url, "source_url"),
        "source_payload_sha256": source_digest,
        "source_observed_at": observed.isoformat(),
    }
    return {**body, "outcome_sha256": _canonical_hash(body)}
