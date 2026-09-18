#!/usr/bin/env python3
"""Predeclared rolling-origin NFL receptions B0 baseline.

This mirrors `passing_yards_baseline_research.py`'s rolling-origin pattern
(pinned/audited nflverse weekly corpus, last-5-appearance rolling mean,
three fixed predeclared partitions) but is deliberately a B0-only research
artifact: there is no receptions challenger yet, so this script establishes
and reports B0's own real accuracy per partition rather than comparing it
against anything.

Two receptions-specific modeling decisions are made explicit in this module
because they are not a mechanical port of the passing_yards script:

1. Population/eligibility gate (`effective_targets`, see `load_receiver_rows`):
   unlike passing_yards, where `position == "QB"` is an unambiguous label,
   receptions come from WR/TE/RB/FB. This module therefore does NOT filter by
   position label; it uses a role/opportunity gate instead -- a row counts as
   a receiver-relevant appearance when the player was targeted or caught a
   pass that week, regardless of position. Investigating the pinned 1999-2025
   corpus surfaced a real, confirmed source gap: nflverse's `targets` column
   is effectively unpopulated for the 2003-2008 seasons (a stray ~0-17 rows
   per season show targets > 0, versus ~3,500-4,300 in every other season),
   while `receptions` is fully populated across the entire 1999-2025 span and
   never exceeds `targets` in any season where `targets` IS populated (zero
   `receptions > targets` rows outside 2002-2008; one boundary row in 2002;
   thousands per season inside 2003-2008). Because a completed reception is
   definitional proof of a target, `effective_targets = max(targets,
   receptions)` is a safe, monotonic opportunity signal: it is identical to
   `targets` whenever that column is trustworthy and falls back to the
   receptions floor exactly when it is not. Gating on `effective_targets > 0`
   therefore recovers the six-season blackout instead of silently erasing six
   years of real receiving history from the development partition.
2. No role-continuity/team-change quarantine analog is implemented here (see
   `receptions_shadow.py` module docstring for the reasoning).

Status stays `RESEARCH_ONLY_NOT_PROMOTED`; nothing here is wired into any
capture pipeline, selector, or public artifact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


# Pinned reproduction of the real B0 output on the 1999-2025 audited corpus,
# restricted to rows from 2023 onward (i.e. what a from-2023 live cold start
# would actually compute). This is a drift guard, not a live production
# board: no capture pipeline consumes it yet. Regenerate deliberately if the
# pinned audit manifest or its underlying CSVs are ever intentionally
# refreshed.
EXPECTED_ACTIVE_B0 = {
    2024: {"n": 3909, "mae": 1.4570009380063103},
    2025: {"n": 3987, "mae": 1.408703285678455},
}

POSITION_BUCKETS = ("WR", "TE", "RB", "FB", "OTHER")


def mean(values):
    return statistics.fmean(values)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metrics(rows, key):
    errors = [row[key] - row["actual"] for row in rows if row.get(key) is not None]
    absolute = [abs(value) for value in errors]
    return {
        "n": len(errors),
        "mae": mean(absolute) if absolute else None,
        "median_absolute_error": statistics.median(absolute) if absolute else None,
        "rmse": math.sqrt(mean(value * value for value in errors)) if errors else None,
        "bias_prediction_minus_actual": mean(errors) if errors else None,
    }


def position_bucket(position: str) -> str:
    label = str(position or "").strip().upper()
    return label if label in POSITION_BUCKETS else "OTHER"


def load_receiver_rows(cache: Path, audit: dict):
    """Load role-positive receiver-relevant rows from the pinned nflverse corpus.

    Mirrors `passing_yards_baseline_research.load_qb_rows`'s audit-manifest
    verification exactly (season count, missing-identity population, then a
    per-season byte-size + SHA-256 check against the cached CSV) before any
    row is trusted. The only structural difference is the eligibility filter:
    no position-label filter is applied; instead every row across every
    position is inspected, and a row is kept only when
    `effective_targets = max(targets, receptions) > 0` (see module docstring
    for why the max, rather than targets alone, is required).
    """
    rows = []
    invariant_failures = defaultdict(int)
    target_coverage_by_season = {}
    seasons = audit.get("seasons")
    if not isinstance(seasons, list) or len(seasons) != 27:
        raise ValueError("full audit must contain 27 seasons")
    if audit.get("summary", {}).get("missing_identity_rows_with_offense") != 7:
        raise ValueError("full audit missing-identity population drift")
    for source in seasons:
        season = int(source["season"])
        path = cache / f"stats_player_week_{season}.csv"
        if path.stat().st_size != int(source["bytes"]):
            raise ValueError(f"{season}: cached byte size drift")
        if sha256_file(path) != source["sha256"]:
            raise ValueError(f"{season}: cached SHA-256 drift")
        season_role_rows = 0
        season_fallback_rows = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                targets = float(row["targets"] or 0)
                receptions = float(row["receptions"] or 0)
                receiving_yards = float(row["receiving_yards"] or 0)
                receiving_tds = float(row["receiving_tds"] or 0)
                if receptions > targets:
                    invariant_failures["receptions_gt_targets"] += 1
                effective_targets = max(targets, receptions)
                if effective_targets == 0 and (receiving_yards != 0 or receiving_tds != 0):
                    invariant_failures["receiving_production_with_zero_role"] += 1
                if any(
                    not math.isfinite(value)
                    for value in (targets, receptions, receiving_yards, receiving_tds)
                ):
                    invariant_failures["nonfinite"] += 1
                if effective_targets <= 0:
                    continue
                season_role_rows += 1
                if targets == 0 and receptions > 0:
                    season_fallback_rows += 1
                rows.append({
                    "player_id": str(row["player_id"]).strip(),
                    "season": int(float(row["season"])),
                    "week": int(float(row["week"])),
                    "season_type": str(row["season_type"]),
                    "game_id": str(row["game_id"]),
                    "position": position_bucket(row.get("position")),
                    "targets": targets,
                    "effective_targets": effective_targets,
                    "receptions": receptions,
                })
        target_coverage_by_season[str(season)] = {
            "role_positive_rows": season_role_rows,
            "raw_targets_column_unavailable_rows": season_fallback_rows,
            "raw_targets_column_unavailable_fraction": (
                season_fallback_rows / season_role_rows if season_role_rows else None
            ),
        }
    rows.sort(key=lambda row: (row["season"], row["week"], row["game_id"], row["player_id"]))
    return rows, dict(sorted(invariant_failures.items())), target_coverage_by_season


def rolling_predictions(rows):
    all_history = defaultdict(lambda: deque(maxlen=5))
    scored = []
    for row in rows:
        appearances = all_history[row["player_id"]]
        b0 = None
        if len(appearances) >= 3 and mean(item["effective_targets"] for item in appearances) > 0:
            b0 = mean(item["receptions"] for item in appearances)
        if row["season_type"] == "REG":
            scored.append({
                **row,
                "actual": row["receptions"],
                "b0": b0,
            })
        appearances.append(row)
    return scored


def report_partition(rows):
    return {
        "native": {"b0": metrics(rows, "b0")},
        "by_position": {
            bucket: metrics([row for row in rows if row["position"] == bucket], "b0")
            for bucket in POSITION_BUCKETS
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, invariants, target_coverage_by_season = load_receiver_rows(args.cache, audit)
    long_scored = rolling_predictions(rows)
    active_scored = rolling_predictions([row for row in rows if row["season"] >= 2023])
    partitions = {
        "development_2000_2019": (2000, 2019),
        "validation_2020_2022": (2020, 2022),
        "held_2023_2025": (2023, 2025),
    }
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "source_audit_manifest_sha256": sha256_file(args.audit_manifest),
        "predeclared_models": {
            "b0": "mean receptions over the last five player appearances with positive rolling opportunity; minimum three such appearances",
        },
        "eligibility_rule": (
            "Role/opportunity gate, not a position-label filter: a row is receiver-relevant "
            "when effective_targets = max(targets, receptions) > 0 for that player-week, "
            "across every position. See module docstring for the confirmed 2003-2008 "
            "nflverse targets-column coverage gap that makes the max() necessary."
        ),
        "population_limit": "All role-positive receiving rows across every position label, not a sportsbook-listed starter population; current-game targets/receptions are not used for eligibility.",
        "receiver_source_rows": len(rows),
        "receiving_stat_invariant_failures": invariants,
        "raw_targets_column_coverage_by_season": target_coverage_by_season,
        "partitions": {},
        "by_season": {},
        "active_b0_reproduction_2024_2025": {},
    }
    for name, (start, end) in partitions.items():
        subset = [row for row in long_scored if start <= row["season"] <= end]
        output["partitions"][name] = report_partition(subset)
    for season in range(2000, 2026):
        output["by_season"][str(season)] = report_partition(
            [row for row in long_scored if row["season"] == season]
        )
    for season in (2024, 2025):
        output["active_b0_reproduction_2024_2025"][str(season)] = metrics(
            [row for row in active_scored if row["season"] == season], "b0"
        )
        observed = output["active_b0_reproduction_2024_2025"][str(season)]
        expected = EXPECTED_ACTIVE_B0[season]
        if observed["n"] != expected["n"] or not math.isclose(
            observed["mae"], expected["mae"], rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError(f"{season}: active B0 reproduction drift")
    output["findings"] = {
        "targets_column_coverage_gap": (
            "raw nflverse `targets` is effectively unpopulated for 2003-2008 "
            "(a stray handful of rows per season vs thousands in every other "
            "season); `receptions` remains fully populated throughout. "
            "effective_targets = max(targets, receptions) recovers the "
            "affected seasons instead of erasing them from the development "
            "partition. See raw_targets_column_coverage_by_season for the "
            "per-season fallback rate."
        ),
        "position_variance": (
            "by_position in each partition/season reports B0 accuracy split "
            "by WR/TE/RB/FB/OTHER as a transparency artifact only; no "
            "position-specific model was built, per task scope."
        ),
        "no_challenger_yet": (
            "This script establishes B0 itself; there is no receptions "
            "challenger to compare against in this task."
        ),
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "active": output["active_b0_reproduction_2024_2025"],
        "held": output["partitions"]["held_2023_2025"]["native"],
        "invariants": invariants,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
