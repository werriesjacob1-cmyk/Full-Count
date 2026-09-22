"""Fail-closed provenance and validation plumbing for film/charting observations.

This module does not acquire or decode video.  It accepts observations only after a
source manifest has established that the material may be used for the intended
analysis.  Synthetic fixtures are supported so the contract can be exercised before
a real source clears that gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


LABEL_FIELDS = (
    "formation",
    "personnel",
    "motion",
    "pressure",
    "coverage",
    "blocking",
    "matchup",
)
ALLOWED_SOURCE_TYPES = {"synthetic_fixture", "licensed_charting", "licensed_footage"}
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}


class ObservationValidationError(ValueError):
    """Raised when an observation cannot clear the provenance contract."""


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    source_type: str
    rights_verified: bool
    analysis_rights: str
    license_url: str | None
    cost_usd: float
    historical_coverage: str
    current_coverage: str
    accessed_at: str
    content_sha256: str
    blocker: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SourceManifest":
        required = {
            "source_id",
            "source_type",
            "rights_verified",
            "analysis_rights",
            "cost_usd",
            "historical_coverage",
            "current_coverage",
            "accessed_at",
            "content_sha256",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise ObservationValidationError(f"source manifest missing: {', '.join(missing)}")
        source_type = str(raw["source_type"])
        if type(raw["rights_verified"]) is not bool:
            raise ObservationValidationError("rights_verified must be a JSON boolean")
        if type(raw["cost_usd"]) not in {int, float}:
            raise ObservationValidationError("cost_usd must be a JSON number")
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ObservationValidationError(f"unsupported source_type: {source_type}")
        manifest = cls(
            source_id=str(raw["source_id"]),
            source_type=source_type,
            rights_verified=bool(raw["rights_verified"]),
            analysis_rights=str(raw["analysis_rights"]),
            license_url=(str(raw["license_url"]) if raw.get("license_url") else None),
            cost_usd=float(raw["cost_usd"]),
            historical_coverage=str(raw["historical_coverage"]),
            current_coverage=str(raw["current_coverage"]),
            accessed_at=str(raw["accessed_at"]),
            content_sha256=str(raw["content_sha256"]).lower(),
            blocker=(str(raw["blocker"]) if raw.get("blocker") else None),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not self.source_id or not self.analysis_rights or not self.accessed_at:
            raise ObservationValidationError("source identity, rights, and access time are required")
        _require_utc_timestamp(self.accessed_at, "accessed_at")
        if self.cost_usd < 0:
            raise ObservationValidationError("cost_usd cannot be negative")
        if len(self.content_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.content_sha256):
            raise ObservationValidationError("content_sha256 must be a lowercase SHA-256 digest")
        if self.source_type != "synthetic_fixture":
            if not self.rights_verified:
                raise ObservationValidationError("real-source observations require verified analysis rights")
            if not self.license_url:
                raise ObservationValidationError("real-source observations require a license_url")
        elif self.rights_verified:
            raise ObservationValidationError("synthetic_fixture cannot claim real-source rights")


def _require_utc_timestamp(value: str, field: str) -> str:
    if not value.endswith("Z"):
        raise ObservationValidationError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ObservationValidationError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    return value


def _require_text(raw: Mapping[str, Any], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ObservationValidationError(f"missing {key}")
    return value


def _validate_label(name: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ObservationValidationError(f"{name} must be an object")
    value = _require_text(raw, "value")
    confidence = _require_text(raw, "confidence").upper()
    if confidence not in ALLOWED_CONFIDENCE:
        raise ObservationValidationError(f"{name}.confidence must be one of {sorted(ALLOWED_CONFIDENCE)}")
    evidence_basis = _require_text(raw, "evidence_basis")
    locator = _require_text(raw, "provenance_locator")
    observed_at = _require_utc_timestamp(_require_text(raw, "observed_at"), f"{name}.observed_at")
    if confidence == "UNKNOWN" and value.upper() not in {"UNKNOWN", "NOT_OBSERVABLE"}:
        raise ObservationValidationError(f"{name}: UNKNOWN confidence requires an unknown value")
    return {
        "value": value,
        "confidence": confidence,
        "evidence_basis": evidence_basis,
        "provenance_locator": locator,
        "observed_at": observed_at,
    }


def validate_observation(raw: Mapping[str, Any], manifest: SourceManifest) -> dict[str, Any]:
    """Validate and normalize one annotation without inferring missing labels."""

    if _require_text(raw, "source_id") != manifest.source_id:
        raise ObservationValidationError("observation source_id does not match manifest")
    game = raw.get("game")
    play = raw.get("play")
    if not isinstance(game, Mapping) or not isinstance(play, Mapping):
        raise ObservationValidationError("game and play must be objects")
    normalized: dict[str, Any] = {
        "schema_version": _require_text(raw, "schema_version"),
        "source_id": manifest.source_id,
        "annotator_id": _require_text(raw, "annotator_id"),
        "annotation_created_at": _require_utc_timestamp(
            _require_text(raw, "annotation_created_at"), "annotation_created_at"
        ),
        "game": {
            "season": int(game.get("season", 0)),
            "week": int(game.get("week", 0)),
            "game_id": _require_text(game, "game_id"),
            "home_team": _require_text(game, "home_team"),
            "away_team": _require_text(game, "away_team"),
        },
        "play": {
            "play_id": _require_text(play, "play_id"),
            "quarter": int(play.get("quarter", 0)),
            "game_clock": _require_text(play, "game_clock"),
            "snap_timestamp": _require_utc_timestamp(
                _require_text(play, "snap_timestamp"), "snap_timestamp"
            ),
        },
        "labels": {},
    }
    if normalized["schema_version"] != "1.0":
        raise ObservationValidationError("unsupported schema_version")
    if normalized["game"]["season"] < 1920 or not 1 <= normalized["game"]["week"] <= 25:
        raise ObservationValidationError("invalid season/week binding")
    if not 1 <= normalized["play"]["quarter"] <= 5:
        raise ObservationValidationError("invalid quarter binding")
    if not re.fullmatch(r"(?:[0-9]|1[0-5]):[0-5][0-9]", normalized["play"]["game_clock"]):
        raise ObservationValidationError("game_clock must be M:SS within a 15-minute quarter")
    labels = raw.get("labels")
    if not isinstance(labels, Mapping):
        raise ObservationValidationError("labels must be an object")
    missing = sorted(set(LABEL_FIELDS) - labels.keys())
    if missing:
        raise ObservationValidationError(f"missing labels: {', '.join(missing)}")
    normalized["labels"] = {name: _validate_label(name, labels[name]) for name in LABEL_FIELDS}
    return normalized


def load_jsonl(path: Path, manifest: SourceManifest) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            records.append(validate_observation(raw, manifest))
        except (json.JSONDecodeError, ObservationValidationError, TypeError, ValueError) as exc:
            raise ObservationValidationError(f"{path}:{line_number}: {exc}") from exc
    if not records:
        raise ObservationValidationError(f"{path}: no observations")
    return records


def observation_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record["game"]["game_id"]),
        str(record["play"]["play_id"]),
        str(record["play"]["snap_timestamp"]),
    )


def validate_unique_bindings(records: Iterable[Mapping[str, Any]]) -> None:
    counts = Counter(observation_key(record) for record in records)
    duplicates = [key for key, count in counts.items() if count > 1]
    if duplicates:
        raise ObservationValidationError(f"duplicate game/play/time bindings: {duplicates}")


def compare_annotations(
    primary: Sequence[Mapping[str, Any]],
    independent: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return transparent exact-agreement metrics for independently keyed records."""

    validate_unique_bindings(primary)
    validate_unique_bindings(independent)
    left = {observation_key(record): record for record in primary}
    right = {observation_key(record): record for record in independent}
    common = sorted(left.keys() & right.keys())
    if not common:
        raise ObservationValidationError("no shared game/play/time bindings")
    fields: dict[str, Any] = {}
    for field in LABEL_FIELDS:
        comparable = 0
        agreements = 0
        disagreements: list[dict[str, str]] = []
        for key in common:
            a = str(left[key]["labels"][field]["value"])
            b = str(right[key]["labels"][field]["value"])
            if a.upper() in {"UNKNOWN", "NOT_OBSERVABLE"} or b.upper() in {"UNKNOWN", "NOT_OBSERVABLE"}:
                continue
            comparable += 1
            if a == b:
                agreements += 1
            else:
                disagreements.append({"binding": "|".join(key), "primary": a, "independent": b})
        fields[field] = {
            "shared_records": len(common),
            "comparable_records": comparable,
            "exact_agreements": agreements,
            "exact_agreement_rate": (agreements / comparable if comparable else None),
            "disagreements": disagreements,
        }
    return {
        "schema_version": "1.0",
        "shared_bindings": len(common),
        "primary_only_bindings": len(left.keys() - right.keys()),
        "independent_only_bindings": len(right.keys() - left.keys()),
        "fields": fields,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source_content(path: Path, manifest: SourceManifest) -> None:
    actual = sha256_file(path)
    if actual != manifest.content_sha256:
        raise ObservationValidationError(
            f"source content digest mismatch: expected {manifest.content_sha256}, got {actual}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate film/charting observation fixtures")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-content", type=Path, required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--independent", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    manifest = SourceManifest.from_dict(json.loads(args.manifest.read_text(encoding="utf-8")))
    verify_source_content(args.source_content, manifest)
    primary = load_jsonl(args.primary, manifest)
    independent = load_jsonl(args.independent, manifest)
    report = compare_annotations(primary, independent)
    report["source_id"] = manifest.source_id
    report["manifest_sha256"] = sha256_file(args.manifest)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
