#!/usr/bin/env python3
"""Predeclared rolling-origin NFL passing-yard baseline comparison."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_ACTIVE_B0 = {
    2024: {"n": 611, "mae": 70.83543371522084},
    2025: {"n": 617, "mae": 72.41990815775256},
}


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


def paired_delta(rows, challenger):
    paired = [row for row in rows if row.get("b0") is not None and row.get(challenger) is not None]
    if not paired:
        return {"n": 0, "mae_delta_vs_b0": None}
    b0 = mean(abs(row["b0"] - row["actual"]) for row in paired)
    alt = mean(abs(row[challenger] - row["actual"]) for row in paired)
    return {"n": len(paired), "b0_mae": b0, "challenger_mae": alt, "mae_delta_vs_b0": alt - b0}


def cluster_bootstrap(rows, challenger, iterations=2000, seed=20260914):
    paired = [row for row in rows if row.get("b0") is not None and row.get(challenger) is not None]
    by_player = defaultdict(list)
    for row in paired:
        by_player[row["player_id"]].append(row)
    players = sorted(by_player)
    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        sampled = [rng.choice(players) for _ in players]
        b0_error = []
        challenger_error = []
        for player_id in sampled:
            for row in by_player[player_id]:
                b0_error.append(abs(row["b0"] - row["actual"]))
                challenger_error.append(abs(row[challenger] - row["actual"]))
        deltas.append(mean(challenger_error) - mean(b0_error))
    deltas.sort()
    return {
        "cluster": "player_id",
        "players": len(players),
        "iterations": iterations,
        "seed": seed,
        "mae_delta_vs_b0_p2_5": deltas[int(iterations * 0.025)],
        "mae_delta_vs_b0_p50": statistics.median(deltas),
        "mae_delta_vs_b0_p97_5": deltas[int(iterations * 0.975)],
    }


def load_qb_rows(cache: Path, audit: dict):
    rows = []
    invariant_failures = defaultdict(int)
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
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("position") or "").strip().upper() != "QB":
                    continue
                attempts = float(row["attempts"])
                completions = float(row["completions"])
                yards = float(row["passing_yards"])
                touchdowns = float(row["passing_tds"])
                if completions > attempts:
                    invariant_failures["completions_gt_attempts"] += 1
                if attempts == 0 and (completions != 0 or yards != 0 or touchdowns != 0):
                    invariant_failures["production_with_zero_attempts"] += 1
                if any(not math.isfinite(value) for value in (attempts, completions, yards, touchdowns)):
                    invariant_failures["nonfinite"] += 1
                rows.append({
                    "player_id": str(row["player_id"]).strip(),
                    "season": int(float(row["season"])),
                    "week": int(float(row["week"])),
                    "season_type": str(row["season_type"]),
                    "game_id": str(row["game_id"]),
                    "attempts": attempts,
                    "passing_yards": yards,
                })
    rows.sort(key=lambda row: (row["season"], row["week"], row["game_id"], row["player_id"]))
    return rows, dict(sorted(invariant_failures.items()))


def rolling_predictions(rows):
    all_history = defaultdict(lambda: deque(maxlen=5))
    passing_history = defaultdict(lambda: deque(maxlen=8))
    scored = []
    for row in rows:
        appearances = all_history[row["player_id"]]
        passing = passing_history[row["player_id"]]
        b0 = None
        if len(appearances) >= 3 and mean(item["attempts"] for item in appearances) > 0:
            b0 = mean(item["passing_yards"] for item in appearances)
        c1 = None
        c2 = None
        if len(passing) >= 3:
            last_five = list(passing)[-5:]
            c1 = mean(item["passing_yards"] for item in last_five)
            recent_three = list(passing)[-3:]
            attempts_3 = mean(item["attempts"] for item in recent_three)
            attempts_8 = sum(item["attempts"] for item in passing)
            if attempts_8 > 0:
                c2 = attempts_3 * sum(item["passing_yards"] for item in passing) / attempts_8
        if row["season_type"] == "REG":
            scored.append({
                **row,
                "actual": row["passing_yards"],
                "b0": b0,
                "c1_passing_role_last5": c1,
                "c2_attempts3_times_ypa8": c2,
            })
        appearances.append(row)
        if row["attempts"] > 0:
            passing.append(row)
    return scored


def report_partition(rows):
    models = ("b0", "c1_passing_role_last5", "c2_attempts3_times_ypa8")
    return {
        "native": {model: metrics(rows, model) for model in models},
        "paired": {model: paired_delta(rows, model) for model in models[1:]},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, invariants = load_qb_rows(args.cache, audit)
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
            "b0": "mean passing yards over the last five player appearances; minimum three appearances and positive rolling attempts",
            "c1_passing_role_last5": "mean passing yards over the last five prior appearances with attempts > 0; minimum three passing-role appearances",
            "c2_attempts3_times_ypa8": "mean attempts over three prior passing-role appearances multiplied by aggregate yards per attempt over up to eight prior passing-role appearances",
        },
        "population_limit": "All regular-season QB rows, not a sportsbook-listed starter population; current-game attempts are not used for eligibility.",
        "qb_source_rows": len(rows),
        "passing_stat_invariant_failures": invariants,
        "partitions": {},
        "by_season": {},
        "active_b0_reproduction_2024_2025": {},
    }
    for name, (start, end) in partitions.items():
        subset = [row for row in long_scored if start <= row["season"] <= end]
        output["partitions"][name] = report_partition(subset)
    held = [row for row in long_scored if 2023 <= row["season"] <= 2025]
    for challenger in ("c1_passing_role_last5", "c2_attempts3_times_ypa8"):
        output["partitions"]["held_2023_2025"]["paired"][challenger]["player_cluster_bootstrap"] = cluster_bootstrap(held, challenger)
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
    validation = output["partitions"]["validation_2020_2022"]["paired"]
    held = output["partitions"]["held_2023_2025"]["paired"]
    output["research_decisions"] = {}
    for challenger in ("c1_passing_role_last5", "c2_attempts3_times_ypa8"):
        validation_delta = validation[challenger]["mae_delta_vs_b0"]
        held_delta = held[challenger]["mae_delta_vs_b0"]
        rejected = validation_delta >= 0 and held_delta >= 0
        output["research_decisions"][challenger] = {
            "decision": (
                "REJECTED_RESEARCH_CHALLENGER"
                if rejected else "REVIEW_REQUIRED"
            ),
            "reason": (
                "Paired MAE was worse than B0 in both validation and held partitions."
                if rejected
                else "The fixed rejection rule was not satisfied; no promotion is implied."
            ),
            "validation_mae_delta_vs_b0": validation_delta,
            "held_mae_delta_vs_b0": held_delta,
        }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "active": output["active_b0_reproduction_2024_2025"],
        "held": output["partitions"]["held_2023_2025"],
        "invariants": invariants,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
