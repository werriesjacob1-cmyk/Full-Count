"""Validation helpers for the permanent NFL intelligence angle registry.

The registry is a research control plane, not a model input. Its purpose is to
make omissions visible: every named hypothesis must retain a stable id, explicit
PIT rules, multi-year policy, leakage risks, and validation requirements.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ALLOWED_STATUSES = {"GREEN", "YELLOW", "BLUE", "RED", "GRAY"}
REQUIRED_ANGLE_FIELDS = {
    "angle_id",
    "category",
    "hypothesis",
    "target_markets",
    "status",
    "historical_depth_target",
    "point_in_time_required",
    "source_candidates",
    "multi_year_policy",
    "regime_sensitive",
    "leakage_risks",
    "validation_requirements",
    "implementation_state",
    "dependencies",
    "owner",
    "active_workstream",
    "last_reviewed",
}
REQUIRED_VALIDATION_GATES = {
    "walk_forward_out_of_time",
    "season_by_season_stability",
    "equal_operational_volume",
    "clustered_uncertainty",
    "prospective_confirmation_before_promotion",
}
REQUIRED_LEAKAGE_RISKS = {
    "future_outcomes",
    "future_injury_or_depth_information",
    "future_market_state",
    "post_selection_holdout_reuse",
}


class NFLIntelligenceRegistryError(ValueError):
    pass


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NFLIntelligenceRegistryError(f"{field} must be a non-empty string")
    return value.strip()


def validate_angle_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise NFLIntelligenceRegistryError("registry must be a mapping")
    if payload.get("schema_version") != 1:
        raise NFLIntelligenceRegistryError("unsupported schema_version")
    if payload.get("sport") != "NFL":
        raise NFLIntelligenceRegistryError("sport must be NFL")

    doctrine = payload.get("multi_year_doctrine")
    if not isinstance(doctrine, Mapping) or doctrine.get("required") is not True:
        raise NFLIntelligenceRegistryError("multi_year_doctrine.required must be true")
    horizons = doctrine.get("horizons_to_test")
    if not isinstance(horizons, list) or len(horizons) < 5:
        raise NFLIntelligenceRegistryError("multi-year horizons are incomplete")
    if doctrine.get("evaluation") != "rolling_origin_walk_forward":
        raise NFLIntelligenceRegistryError("multi-year evaluation must be rolling-origin walk-forward")
    if doctrine.get("era_normalization_required") is not True:
        raise NFLIntelligenceRegistryError("era normalization must be required")

    angles = payload.get("angles")
    if not isinstance(angles, list) or not angles:
        raise NFLIntelligenceRegistryError("angles must be a non-empty list")

    seen = set()
    categories = set()
    for index, angle in enumerate(angles):
        if not isinstance(angle, Mapping):
            raise NFLIntelligenceRegistryError(f"angle[{index}] must be a mapping")
        missing = REQUIRED_ANGLE_FIELDS.difference(angle)
        if missing:
            raise NFLIntelligenceRegistryError(
                f"angle[{index}] missing fields: {', '.join(sorted(missing))}"
            )
        angle_id = _nonempty_text(angle["angle_id"], f"angle[{index}].angle_id")
        if angle_id in seen:
            raise NFLIntelligenceRegistryError(f"duplicate angle_id: {angle_id}")
        seen.add(angle_id)
        categories.add(_nonempty_text(angle["category"], f"{angle_id}.category"))
        _nonempty_text(angle["hypothesis"], f"{angle_id}.hypothesis")

        if angle["status"] not in ALLOWED_STATUSES:
            raise NFLIntelligenceRegistryError(f"{angle_id}: invalid status {angle['status']!r}")
        if angle.get("point_in_time_required") is not True:
            raise NFLIntelligenceRegistryError(f"{angle_id}: PIT safety must be required")
        if angle.get("historical_depth_target") != "MAX_DEFENSIBLE_POINT_IN_TIME_HISTORY":
            raise NFLIntelligenceRegistryError(f"{angle_id}: historical depth target drift")

        targets = angle["target_markets"]
        if not isinstance(targets, list) or not targets or not all(
            isinstance(value, str) and value.strip() for value in targets
        ):
            raise NFLIntelligenceRegistryError(f"{angle_id}: target_markets invalid")

        sources = angle["source_candidates"]
        if not isinstance(sources, list) or not sources:
            raise NFLIntelligenceRegistryError(f"{angle_id}: source_candidates missing")

        _nonempty_text(angle["multi_year_policy"], f"{angle_id}.multi_year_policy")

        leakage = set(angle["leakage_risks"])
        if not REQUIRED_LEAKAGE_RISKS.issubset(leakage):
            raise NFLIntelligenceRegistryError(f"{angle_id}: leakage controls incomplete")

        gates = set(angle["validation_requirements"])
        if not REQUIRED_VALIDATION_GATES.issubset(gates):
            raise NFLIntelligenceRegistryError(f"{angle_id}: validation gates incomplete")

    return {
        "angle_count": len(angles),
        "category_count": len(categories),
        "angle_ids": sorted(seen),
        "categories": sorted(categories),
    }


def load_and_validate_angle_registry(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload, validate_angle_registry(payload)
