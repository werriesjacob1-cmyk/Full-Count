#!/usr/bin/env python3
"""Real evaluation runner for the NFL rushing-yards baseline + challenger.

Invokes `nfl.research.rushing_yards_baseline_research` against the real,
already-ingested, already-audited nflverse `stats_player_week_<season>.csv`
corpus (1999-2025) -- the same audited cache and manifest that
`passing_yards_baseline_research.py` and `receptions_baseline_research.py`
already consume -- and writes the real, reproducible JSON report checked
into this directory (`rushing_yards_real_evaluation_report.json`).

This script adds no new data source: `--cache` must point at a local copy of
the pinned `stats_player_week_<season>.csv` files whose byte size and
SHA-256 match `--audit-manifest` exactly (`nfl/research/nflverse_full_
audit.py`'s own output, e.g.
`engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`).
Re-download that corpus with `nfl/research/nflverse_full_audit.py` if a
local cache is not already present; both loader functions in this module
(and the two reference baseline scripts) fail closed with a raised
exception rather than silently proceeding on a byte-size or hash mismatch.

Usage:
    python3 rushing_yards_real_evaluation.py \\
        --cache /path/to/nflverse_cache \\
        --audit-manifest /path/to/full_audit.json \\
        --output rushing_yards_real_evaluation_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nfl.research.rushing_yards_baseline_research import (  # noqa: E402
    load_rusher_rows,
    report_partition,
    rolling_predictions,
    cluster_bootstrap,
    metrics,
    sha256_file,
)
from datetime import datetime, timezone  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, invariants, coverage = load_rusher_rows(args.cache, audit)
    long_scored = rolling_predictions(rows)

    partitions = {
        "development_2000_2019": (2000, 2019),
        "validation_2020_2022": (2020, 2022),
        "held_2023_2025": (2023, 2025),
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "workstream": "NFL-RUSHING-YARDS-BASELINE-20260923",
        "source_audit_manifest_sha256": sha256_file(args.audit_manifest),
        "rusher_source_rows": len(rows),
        "rushing_stat_invariant_failures": invariants,
        "carries_rushing_yards_coverage_by_season": coverage,
        "partitions": {},
    }
    for name, (start, end) in partitions.items():
        subset = [row for row in long_scored if start <= row["season"] <= end]
        report["partitions"][name] = report_partition(subset)

    held = [row for row in long_scored if 2023 <= row["season"] <= 2025]
    report["partitions"]["held_2023_2025"]["paired"]["c1_carries3_times_ypc8"][
        "player_cluster_bootstrap"
    ] = cluster_bootstrap(held, "c1_carries3_times_ypc8")

    validation = report["partitions"]["validation_2020_2022"]["paired"]["c1_carries3_times_ypc8"]
    held_paired = report["partitions"]["held_2023_2025"]["paired"]["c1_carries3_times_ypc8"]
    rejected = validation["mae_delta_vs_b0"] >= 0 and held_paired["mae_delta_vs_b0"] >= 0
    report["research_decision"] = {
        "decision": "REJECTED_RESEARCH_CHALLENGER" if rejected else "REVIEW_REQUIRED",
        "reason": (
            "Paired MAE was worse than B0 in both validation and held partitions."
            if rejected
            else "The fixed predeclared rejection rule was not satisfied; no promotion is implied."
        ),
        "validation_mae_delta_vs_b0": validation["mae_delta_vs_b0"],
        "held_mae_delta_vs_b0": held_paired["mae_delta_vs_b0"],
    }

    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "rusher_source_rows": report["rusher_source_rows"],
        "invariants": invariants,
        "held_2023_2025_native": report["partitions"]["held_2023_2025"]["native"],
        "held_2023_2025_paired": held_paired,
        "validation_2020_2022_paired": validation,
        "decision": report["research_decision"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
