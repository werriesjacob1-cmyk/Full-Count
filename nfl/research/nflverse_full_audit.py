#!/usr/bin/env python3
"""Download and audit nflverse weekly player CSVs without loading them into Git."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)
NUMERIC = (
    "attempts", "completions", "passing_yards", "passing_tds", "carries",
    "rushing_yards", "rushing_tds", "targets", "receptions",
    "receiving_yards", "receiving_tds",
)
CRITICAL = (
    "player_id", "player_display_name", "position", "season", "week",
    "season_type", "team", "opponent_team",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path, expected_bytes: int) -> None:
    if destination.exists() and destination.stat().st_size == expected_bytes:
        return
    partial = destination.with_suffix(destination.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": "full-count-source-audit/1"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)
    if partial.stat().st_size != expected_bytes:
        raise ValueError(
            f"download size mismatch for {destination.name}: "
            f"{partial.stat().st_size} != {expected_bytes}"
        )
    os.replace(partial, destination)


def audit_csv(path: Path, season: int, required: set[str]) -> tuple[dict, dict]:
    row_count = 0
    duplicate_keys = 0
    blank = Counter()
    numeric_blank = Counter()
    numeric_invalid = Counter()
    season_values = Counter()
    season_types = Counter()
    weeks = Counter()
    positions = Counter()
    structural_zero_rows = 0
    sentinel_zero_id_structural_rows = 0
    missing_identity_with_offense = 0
    missing_identity_examples = []
    keys = set()
    identities: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"names": set(), "positions": set()}
    )

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing_required = sorted(required.difference(header))
        for line_number, row in enumerate(reader, 2):
            row_count += 1
            for field in CRITICAL:
                if not str(row.get(field) or "").strip():
                    blank[field] += 1
            values = []
            for field in NUMERIC:
                raw = str(row.get(field) or "").strip()
                if not raw:
                    numeric_blank[field] += 1
                    values.append(0.0)
                    continue
                try:
                    values.append(float(raw))
                except ValueError:
                    numeric_invalid[field] += 1
                    values.append(float("nan"))

            player_id = str(row.get("player_id") or "").strip()
            name = str(row.get("player_display_name") or "").strip()
            position = str(row.get("position") or "").strip()
            season_value = str(row.get("season") or "").strip()
            week = str(row.get("week") or "").strip()
            season_type = str(row.get("season_type") or "").strip()
            season_values[season_value] += 1
            season_types[season_type] += 1
            weeks[week] += 1
            positions[position] += 1
            key = (player_id, season_value, week, season_type)
            if key in keys:
                duplicate_keys += 1
            keys.add(key)

            if player_id and player_id != "0":
                if name:
                    identities[player_id]["names"].add(name)
                if position:
                    identities[player_id]["positions"].add(position)
            elif not name and not position and all(value == 0.0 for value in values):
                if player_id == "0":
                    sentinel_zero_id_structural_rows += 1
                else:
                    structural_zero_rows += 1
            elif any(value != 0.0 for value in values):
                missing_identity_with_offense += 1
                if len(missing_identity_examples) < 10:
                    missing_identity_examples.append({
                        "csv_line": line_number,
                        "game_id": str(row.get("game_id") or ""),
                        "week": str(row.get("week") or ""),
                        "season_type": season_type,
                        "team": str(row.get("team") or ""),
                        "opponent_team": str(row.get("opponent_team") or ""),
                        "player_name": str(row.get("player_name") or ""),
                        "player_display_name": name,
                        "position": position,
                        "nonzero_offense": {
                            field: str(row.get(field))
                            for field in NUMERIC
                            if str(row.get(field) or "").strip()
                            and float(str(row[field])) != 0.0
                        },
                    })

    def week_number(value: str) -> int | None:
        try:
            return int(float(value))
        except ValueError:
            return None

    parsed_weeks = [number for value in weeks if (number := week_number(value)) is not None]
    result = {
        "season": season,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "header_columns": len(header),
        "header_sha256": hashlib.sha256(
            json.dumps(header, separators=(",", ":")).encode()
        ).hexdigest(),
        "missing_required_columns": missing_required,
        "rows": row_count,
        "duplicate_player_season_week_type_keys": duplicate_keys,
        "blank_critical_fields": dict(sorted(blank.items())),
        "blank_numeric_fields": dict(sorted(numeric_blank.items())),
        "invalid_numeric_fields": dict(sorted(numeric_invalid.items())),
        "season_values": dict(sorted(season_values.items())),
        "season_types": dict(sorted(season_types.items())),
        "week_min": min(parsed_weeks) if parsed_weeks else None,
        "week_max": max(parsed_weeks) if parsed_weeks else None,
        "positions": dict(sorted(positions.items())),
        "blank_id_structural_zero_rows": structural_zero_rows,
        "sentinel_zero_id_structural_rows": sentinel_zero_id_structural_rows,
        "missing_identity_rows_with_offense": missing_identity_with_offense,
        "missing_identity_offense_examples": missing_identity_examples,
    }
    return result, identities


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    prior = None
    if args.output.exists():
        prior = json.loads(args.output.read_text(encoding="utf-8"))
    required = set(source["required_columns"])
    expected = {int(row["season"]): int(row["reported_bytes"]) for row in source["seasons"]}
    args.cache.mkdir(parents=True, exist_ok=True)
    results = []
    global_identities: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"names": set(), "positions": set()}
    )
    for season in sorted(expected):
        destination = args.cache / f"stats_player_week_{season}.csv"
        print(f"{utc_now()} season={season} download", flush=True)
        download(URL.format(season=season), destination, expected[season])
        print(f"{utc_now()} season={season} audit", flush=True)
        result, identities = audit_csv(destination, season, required)
        if prior:
            prior_season = next(
                (row for row in prior.get("seasons", []) if row.get("season") == season),
                None,
            )
            if prior_season and prior_season.get("sha256") != result["sha256"]:
                raise ValueError(
                    f"cached source drift for {season}: "
                    f"{prior_season.get('sha256')} != {result['sha256']}"
                )
        results.append(result)
        for player_id, identity in identities.items():
            global_identities[player_id]["names"].update(identity["names"])
            global_identities[player_id]["positions"].update(identity["positions"])

    output = {
        "schema_version": 1,
        "audited_at": utc_now(),
        "source_manifest_sha256": sha256_file(args.source_manifest),
        "source": {
            "repository": source["source"]["repository"],
            "canonical_asset_template": source["source"]["canonical_asset_template"],
            "declared_license": source["source"]["declared_license"],
            "license_url": source["source"]["license_url"],
        },
        "method": {
            "full_files_downloaded": True,
            "raw_cache_committed_to_git": False,
            "primary_key_checked": ["player_id", "season", "week", "season_type"],
            "numeric_fields_checked": list(NUMERIC),
        },
        "summary": {
            "seasons": len(results),
            "bytes": sum(row["bytes"] for row in results),
            "rows": sum(row["rows"] for row in results),
            "duplicate_keys": sum(
                row["duplicate_player_season_week_type_keys"] for row in results
            ),
            "blank_id_structural_zero_rows": sum(
                row["blank_id_structural_zero_rows"] for row in results
            ),
            "sentinel_zero_id_structural_rows": sum(
                row["sentinel_zero_id_structural_rows"] for row in results
            ),
            "missing_identity_rows_with_offense": sum(
                row["missing_identity_rows_with_offense"] for row in results
            ),
            "unique_player_ids": len(global_identities),
            "player_ids_with_multiple_names": sum(
                len(value["names"]) > 1 for value in global_identities.values()
            ),
            "player_ids_with_multiple_positions": sum(
                len(value["positions"]) > 1 for value in global_identities.values()
            ),
        },
        "seasons": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps(output["summary"], sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
