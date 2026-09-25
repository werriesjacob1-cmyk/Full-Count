"""Validation for NFL intelligence source contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ALLOWED_STATUSES = {
    "AUDITED",
    "PARTIAL",
    "PROSPECTIVE_ONLY",
    "REAUDIT_REQUIRED",
    "DISCOVERY",
    "RIGHTS_OR_COST_REVIEW",
}
REQUIRED_FIELDS = {
    "source_id",
    "status",
    "intended_use",
    "historical_coverage_claim",
    "current_season_policy",
    "point_in_time_class",
    "repo_evidence",
    "restrictions",
}


class NFLSourceRegistryError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NFLSourceRegistryError(f"{field} must be non-empty")
    return value.strip()


def validate_source_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise NFLSourceRegistryError("source registry must be a mapping")
    if payload.get("schema_version") != 1 or payload.get("sport") != "NFL":
        raise NFLSourceRegistryError("unsupported source registry identity")
    rows = payload.get("sources")
    if not isinstance(rows, list) or not rows:
        raise NFLSourceRegistryError("sources must be non-empty")
    seen=set()
    statuses={}
    for i,row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise NFLSourceRegistryError(f"sources[{i}] must be a mapping")
        missing=REQUIRED_FIELDS.difference(row)
        if missing:
            raise NFLSourceRegistryError(
                f"sources[{i}] missing fields: {', '.join(sorted(missing))}"
            )
        sid=_text(row["source_id"], f"sources[{i}].source_id")
        if sid in seen:
            raise NFLSourceRegistryError(f"duplicate source_id: {sid}")
        seen.add(sid)
        status=_text(row["status"], f"{sid}.status")
        if status not in ALLOWED_STATUSES:
            raise NFLSourceRegistryError(f"{sid}: invalid status {status!r}")
        statuses[status]=statuses.get(status,0)+1
        if not isinstance(row["intended_use"], list) or not row["intended_use"]:
            raise NFLSourceRegistryError(f"{sid}: intended_use missing")
        _text(row["historical_coverage_claim"], f"{sid}.historical_coverage_claim")
        _text(row["current_season_policy"], f"{sid}.current_season_policy")
        _text(row["point_in_time_class"], f"{sid}.point_in_time_class")
        if not isinstance(row["repo_evidence"], list):
            raise NFLSourceRegistryError(f"{sid}: repo_evidence must be list")
        if not isinstance(row["restrictions"], list) or not row["restrictions"]:
            raise NFLSourceRegistryError(f"{sid}: restrictions missing")

    return {"source_count":len(rows),"source_ids":sorted(seen),"status_counts":statuses}


def load_and_validate_source_registry(path: str | Path):
    path=Path(path)
    with path.open("r",encoding="utf-8") as handle:
        payload=json.load(handle)
    return payload, validate_source_registry(payload)
