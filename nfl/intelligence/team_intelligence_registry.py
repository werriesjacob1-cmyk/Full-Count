"""Validation for 32-team NFL live intelligence coverage."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping

EXPECTED_TEAMS = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WAS",
}
REQUIRED_CHANNELS = {
    "OFFICIAL_PRESS_CONFERENCES","OFFICIAL_TEAM_NEWS","OFFICIAL_INJURY_PRACTICE",
    "OFFICIAL_TRANSACTIONS","HEAD_COACH_MEDIA","OC_MEDIA","DC_MEDIA","QB_MEDIA",
    "POSITION_PLAYER_MEDIA","ACCREDITED_BEAT_REPORTERS","LOCAL_NEWSPAPER",
    "LOCAL_RADIO_TV","TEAM_FOCUSED_ANALYSIS","PRACTICE_OBSERVATIONS",
}

class TeamIntelligenceRegistryError(ValueError):
    pass

def validate_team_intelligence_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TeamIntelligenceRegistryError("registry must be mapping")
    if payload.get("schema_version") != 1 or payload.get("sport") != "NFL":
        raise TeamIntelligenceRegistryError("unsupported registry identity")
    required=set(payload.get("required_channels") or [])
    if required != REQUIRED_CHANNELS:
        raise TeamIntelligenceRegistryError("required channel set drift")
    rows=payload.get("teams")
    if not isinstance(rows,list) or len(rows)!=32:
        raise TeamIntelligenceRegistryError("registry must contain exactly 32 teams")
    seen=set()
    for row in rows:
        if not isinstance(row,Mapping):
            raise TeamIntelligenceRegistryError("team row must be mapping")
        team=row.get("team")
        if team in seen:
            raise TeamIntelligenceRegistryError(f"duplicate team: {team}")
        seen.add(team)
        if not isinstance(row.get("missing_channels"),list):
            raise TeamIntelligenceRegistryError(f"{team}: missing_channels must be list")
        if not set(row["missing_channels"]).issubset(REQUIRED_CHANNELS):
            raise TeamIntelligenceRegistryError(f"{team}: unknown missing channel")
        for key in ("official_sources","beat_reporters","local_outlets","press_conference_sources","practice_observation_sources"):
            if not isinstance(row.get(key),list):
                raise TeamIntelligenceRegistryError(f"{team}: {key} must be list")
    if seen != EXPECTED_TEAMS:
        raise TeamIntelligenceRegistryError(
            f"team universe mismatch missing={sorted(EXPECTED_TEAMS-seen)} extra={sorted(seen-EXPECTED_TEAMS)}"
        )
    return {
        "team_count":len(rows),
        "fully_populated_count":sum(1 for r in rows if not r["missing_channels"]),
        "teams_with_gaps":sorted(r["team"] for r in rows if r["missing_channels"]),
    }

def load_and_validate_team_intelligence_registry(path: str | Path):
    path=Path(path)
    with path.open("r",encoding="utf-8") as handle:
        payload=json.load(handle)
    return payload, validate_team_intelligence_registry(payload)
