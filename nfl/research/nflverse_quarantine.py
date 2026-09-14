#!/usr/bin/env python3
"""Build a deterministic fail-closed quarantine ledger from audited nflverse CSVs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path

from nfl.research.nflverse_full_audit import NUMERIC, sha256_file


STRUCTURAL_ZERO = "STRUCTURAL_ZERO_MISSING_IDENTITY"
MISSING_IDENTITY_OFFENSE = "MISSING_IDENTITY_WITH_OFFENSE"
MISSING_STABLE_IDENTITY = "MISSING_STABLE_IDENTITY"
MISSING_DISPLAY_POSITION = "IDENTIFIED_ROW_MISSING_DISPLAY_AND_POSITION"
MISSING_OPPONENT = "MISSING_OPPONENT"
MISSING_TEAM = "MISSING_TEAM"


def canonical_row_sha256(row: dict[str, str]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def offense_values(row: dict[str, str]) -> dict[str, float]:
    values = {}
    for field in NUMERIC:
        raw = str(row.get(field) or "").strip()
        if not raw:
            raise ValueError(f"blank audited numeric field: {field}")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"invalid audited numeric field {field}: {raw!r}") from exc
        if not math.isfinite(value):
            raise ValueError(f"non-finite audited numeric field {field}: {raw!r}")
        values[field] = value
    return values


def classify_row(row: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    player_id = str(row.get("player_id") or "").strip()
    name = str(row.get("player_display_name") or "").strip()
    position = str(row.get("position") or "").strip()
    values = offense_values(row)
    nonzero = {
        field: str(row.get(field) or "").strip()
        for field, value in values.items()
        if value != 0.0
    }
    reasons = []
    if player_id in {"", "0"}:
        if not name and not position and not nonzero:
            reasons.append(STRUCTURAL_ZERO)
        elif nonzero:
            reasons.append(MISSING_IDENTITY_OFFENSE)
        else:
            reasons.append(MISSING_STABLE_IDENTITY)
    elif not name and not position:
        reasons.append(MISSING_DISPLAY_POSITION)
    if not str(row.get("opponent_team") or "").strip():
        reasons.append(MISSING_OPPONENT)
    if not str(row.get("team") or "").strip():
        reasons.append(MISSING_TEAM)
    return sorted(reasons), nonzero


def quarantine_id(
    source_sha256: str,
    csv_line: int,
    reasons: list[str],
    row_sha256: str,
) -> str:
    material = "\n".join(
        ["nflverse-quarantine-v1", source_sha256, str(csv_line), ",".join(reasons), row_sha256]
    )
    return "nflq_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def ledger_entry(
    row: dict[str, str], source_name: str, source_sha256: str, csv_line: int
) -> dict | None:
    reasons, nonzero = classify_row(row)
    if not reasons:
        return None
    row_digest = canonical_row_sha256(row)
    structural_only = reasons == [STRUCTURAL_ZERO]
    entry = {
        "quarantine_id": quarantine_id(source_sha256, csv_line, reasons, row_digest),
        "source_asset": source_name,
        "source_sha256": source_sha256,
        "csv_line": csv_line,
        "row_sha256": row_digest,
        "reasons": reasons,
        "disposition": (
            "EXCLUDED_STRUCTURAL_ZERO"
            if structural_only
            else "QUARANTINED_RESEARCH_BLOCKER"
        ),
        "allowed_uses": (
            ["source_completeness_accounting"] if structural_only else []
        ),
        "context": {
            "season": str(row.get("season") or ""),
            "week": str(row.get("week") or ""),
            "season_type": str(row.get("season_type") or ""),
            "game_id": str(row.get("game_id") or ""),
            "player_id": str(row.get("player_id") or ""),
            "player_name": str(row.get("player_name") or ""),
            "player_display_name": str(row.get("player_display_name") or ""),
            "position": str(row.get("position") or ""),
            "team": str(row.get("team") or ""),
            "opponent_team": str(row.get("opponent_team") or ""),
        },
    }
    if nonzero:
        entry["nonzero_offense"] = nonzero
    return entry


def build_ledger(audit_path: Path, cache: Path) -> dict:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    entries = []
    reason_counts = Counter()
    disposition_counts = Counter()
    source_rows = 0

    for source in sorted(audit["seasons"], key=lambda item: int(item["season"])):
        season = int(source["season"])
        source_name = f"stats_player_week_{season}.csv"
        path = cache / source_name
        if not path.is_file():
            raise ValueError(f"missing audited source asset: {source_name}")
        if path.stat().st_size != int(source["bytes"]):
            raise ValueError(f"source size drift: {source_name}")
        actual_sha = sha256_file(path)
        if actual_sha != source["sha256"]:
            raise ValueError(f"source digest drift: {source_name}")

        season_structural = 0
        season_identity_offense = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for csv_line, row in enumerate(reader, 2):
                source_rows += 1
                if None in row:
                    raise ValueError(f"malformed CSV row: {source_name}:{csv_line}")
                entry = ledger_entry(row, source_name, actual_sha, csv_line)
                if entry is None:
                    continue
                entries.append(entry)
                reason_counts.update(entry["reasons"])
                disposition_counts[entry["disposition"]] += 1
                season_structural += STRUCTURAL_ZERO in entry["reasons"]
                season_identity_offense += MISSING_IDENTITY_OFFENSE in entry["reasons"]

        expected_structural = int(source["blank_id_structural_zero_rows"]) + int(
            source["sentinel_zero_id_structural_rows"]
        )
        if season_structural != expected_structural:
            raise ValueError(
                f"structural-row count drift for {season}: "
                f"{season_structural} != {expected_structural}"
            )
        expected_offense = int(source["missing_identity_rows_with_offense"])
        if season_identity_offense != expected_offense:
            raise ValueError(
                f"missing-identity offense count drift for {season}: "
                f"{season_identity_offense} != {expected_offense}"
            )

    if source_rows != int(audit["summary"]["rows"]):
        raise ValueError(
            f"source row-count drift: {source_rows} != {audit['summary']['rows']}"
        )
    return {
        "schema_version": 1,
        "evidence_as_of": audit["audited_at"],
        "audit_manifest_sha256": sha256_file(audit_path),
        "source": audit["source"],
        "policy": {
            "research_blocker_allowed_uses": [],
            "structural_zero_allowed_uses": ["source_completeness_accounting"],
            "raw_rows_committed_to_git": False,
        },
        "summary": {
            "source_assets_verified": len(audit["seasons"]),
            "source_rows_scanned": source_rows,
            "ledger_entries": len(entries),
            "reason_counts": dict(sorted(reason_counts.items())),
            "disposition_counts": dict(sorted(disposition_counts.items())),
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = build_ledger(args.audit, args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    partial.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, args.output)
    print(json.dumps(ledger["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
