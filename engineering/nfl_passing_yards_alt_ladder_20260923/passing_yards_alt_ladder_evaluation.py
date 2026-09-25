#!/usr/bin/env python3
"""Real, out-of-sample evaluation: empirical-residual passing-yards ladder
vs. a predeclared Normal-approximation control.

## Predeclared BEFORE any held-out result was computed (do not retune)

1. Data: the exact pinned 1999-2025 nflverse `stats_player_week_<season>.csv`
   corpus already verified byte-size/SHA-256-identical against
   `engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json` --
   the same manifest `passing_yards_baseline_research.py` and
   `receptions_outcome_distribution.py` already use. No new source is
   pinned or fetched by this script.
2. Projection model: B0 (`passing_yards_baseline_research.rolling_predictions`,
   reused unmodified) -- mean passing yards over the last five prior
   appearances, minimum three prior appearances with positive rolling
   attempts. This script does not build a new point-projection model.
3. Partition: `season <= 2022` trains both the residual pool AND the Normal
   fit; `2023 <= season <= 2025` is held out and scored ONLY -- the exact
   train/held boundary `receptions_outcome_distribution.py` already
   established for this repository.
4. Rungs (thresholds): for EACH held-out row, five thresholds at real
   sportsbook-style half-point offsets from that row's OWN B0 projection:
   projection - 30, -15, 0, +15, +30 yards, each rounded to the nearest 0.5
   and floored at a minimum of 0.5 (passing yards cannot be negative). This
   rule uses only the row's own B0 projection, decided before any actual
   outcome is inspected -- it is not tuned to the real results below.
5. Promotion rule (decided before running the comparison below): the
   empirical-residual ladder is preferred over the Normal-approximation
   control ONLY IF its aggregate held-out Brier score for the "over"
   probability, macro-averaged across the five predeclared rungs and all
   held-out rows, is STRICTLY LOWER (better) than the Normal control's on
   the IDENTICAL held-out population. A tie or a Normal win is reported
   verbatim as a negative/neutral finding for the empirical method, never
   softened, and neither outcome promotes anything -- both remain
   RESEARCH_ONLY_NOT_PROMOTED regardless of which one wins this comparison.

## Real result (see this script's own printed/written output; do not remove)

Filled in after running this script against the real pinned corpus -- see
`README.md` in this directory for the reported numbers.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from nfl.research.passing_yards_alt_ladder import (
    ladder_probabilities,
    normal_ladder_probabilities,
)
from nfl.research.passing_yards_baseline_research import (
    load_qb_rows,
    rolling_predictions,
    sha256_file,
)
from nfl.research.receptions_outcome_distribution import EmpiricalResidualPool, fit_normal

RUNG_OFFSETS = (-30.0, -15.0, 0.0, 15.0, 30.0)


def build_rungs_for_row(projection: float) -> list[float]:
    """Predeclared rung construction: real sportsbook-style half-point
    offsets from THIS row's own B0 projection, decided before any actual
    outcome is inspected. Never uses `actual` in any way.
    """
    rungs = []
    for offset in RUNG_OFFSETS:
        raw = projection + offset
        rounded = round(raw * 2.0) / 2.0
        rungs.append(max(rounded, 0.5))
    # De-duplicate while preserving order (a very low projection can collapse
    # multiple offsets onto the same floored 0.5 rung -- report the real
    # collapsed set rather than silently padding back to five).
    seen = []
    for value in rungs:
        if value not in seen:
            seen.append(value)
    return seen


def actual_over_indicator(actual: float, threshold: float) -> float:
    """Proper push-neutral scoring convention: exact push counts as half a
    win for both sides rather than being silently excluded or assigned
    entirely to one side.
    """
    if actual > threshold:
        return 1.0
    if actual < threshold:
        return 0.0
    return 0.5


def cluster_bootstrap_brier_gap(
    per_player_gaps: dict[str, list[float]], *, iterations: int = 2000, seed: int = 20260923,
) -> dict[str, Any]:
    """Player-clustered bootstrap on the per-rung Brier gap (empirical minus
    normal; negative means empirical is better), matching the exact
    `player_id`-cluster resampling convention
    `passing_yards_baseline_research.cluster_bootstrap` already established
    for this same QB population -- resampling individual rung observations
    would understate variance since a single QB's own rungs are correlated
    with each other.
    """
    players = sorted(per_player_gaps)
    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        sampled_players = [rng.choice(players) for _ in players]
        pooled = []
        for player_id in sampled_players:
            pooled.extend(per_player_gaps[player_id])
        deltas.append(statistics.fmean(pooled))
    deltas.sort()
    return {
        "cluster": "player_id",
        "players": len(players),
        "iterations": iterations,
        "seed": seed,
        "brier_gap_p2_5": deltas[int(iterations * 0.025)],
        "brier_gap_p50": statistics.median(deltas),
        "brier_gap_p97_5": deltas[int(iterations * 0.975)],
    }


def evaluate(train_rows: list[dict[str, Any]], held_rows: list[dict[str, Any]]) -> dict[str, Any]:
    residual_pool_values = [float(row["actual"]) - float(row["b0"]) for row in train_rows]
    pool = EmpiricalResidualPool(residual_pool_values)
    normal_fit = fit_normal(train_rows)

    per_rung_squared_error = {"EMPIRICAL_RESIDUAL_POOL": [], "NORMAL_APPROXIMATION_CONTROL": []}
    per_rung_predicted_over = {"EMPIRICAL_RESIDUAL_POOL": [], "NORMAL_APPROXIMATION_CONTROL": []}
    per_rung_actual_over = []
    per_player_brier_gap: dict[str, list[float]] = defaultdict(list)
    rows_scored = 0
    rows_by_rung_count: dict[int, int] = {}

    for row in held_rows:
        projection = float(row["b0"])
        actual = float(row["actual"])
        player_id = row["player_id"]
        rungs = build_rungs_for_row(projection)
        rows_by_rung_count[len(rungs)] = rows_by_rung_count.get(len(rungs), 0) + 1

        empirical = ladder_probabilities(projection=projection, thresholds=rungs, residual_pool=pool)
        normal = normal_ladder_probabilities(
            projection=projection, thresholds=rungs,
            mean_residual=normal_fit["mean"], std=normal_fit["std"],
        )
        empirical_by_threshold = {r["threshold"]: r for r in empirical["rungs"]}
        normal_by_threshold = {r["threshold"]: r for r in normal["rungs"]}

        for threshold in rungs:
            actual_over = actual_over_indicator(actual, threshold)
            per_rung_actual_over.append(actual_over)
            emp_over = empirical_by_threshold[threshold]["over"]
            norm_over = normal_by_threshold[threshold]["over"]
            per_rung_predicted_over["EMPIRICAL_RESIDUAL_POOL"].append(emp_over)
            per_rung_predicted_over["NORMAL_APPROXIMATION_CONTROL"].append(norm_over)
            emp_sq_error = (emp_over - actual_over) ** 2
            norm_sq_error = (norm_over - actual_over) ** 2
            per_rung_squared_error["EMPIRICAL_RESIDUAL_POOL"].append(emp_sq_error)
            per_rung_squared_error["NORMAL_APPROXIMATION_CONTROL"].append(norm_sq_error)
            per_player_brier_gap[player_id].append(emp_sq_error - norm_sq_error)
        rows_scored += 1

    n_rung_observations = len(per_rung_actual_over)
    brier = {
        method: statistics.fmean(values) for method, values in per_rung_squared_error.items()
    }
    mean_predicted_over = {
        method: statistics.fmean(values) for method, values in per_rung_predicted_over.items()
    }
    actual_over_rate = statistics.fmean(per_rung_actual_over)

    empirical_wins = brier["EMPIRICAL_RESIDUAL_POOL"] < brier["NORMAL_APPROXIMATION_CONTROL"]
    bootstrap = cluster_bootstrap_brier_gap(per_player_brier_gap)

    return {
        "schema_version": 1,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "train_n_rows": len(train_rows),
        "held_n_rows": rows_scored,
        "n_rung_observations": n_rung_observations,
        "rows_by_rung_count": rows_by_rung_count,
        "normal_fit_train": normal_fit,
        "residual_pool_n": pool.n,
        "held_out_brier_over_probability": brier,
        "held_out_mean_predicted_over_probability": mean_predicted_over,
        "held_out_actual_over_rate": actual_over_rate,
        "player_clustered_bootstrap_brier_gap": bootstrap,
        "predeclared_promotion_rule": (
            "Empirical-residual ladder preferred over Normal-approximation control "
            "iff its held-out Brier score (macro-averaged over the five predeclared "
            "rungs) is strictly lower on the identical held-out population."
        ),
        "promotion_decision": {
            "empirical_beats_normal_on_brier": empirical_wins,
            "brier_gap_empirical_minus_normal": (
                brier["EMPIRICAL_RESIDUAL_POOL"] - brier["NORMAL_APPROXIMATION_CONTROL"]
            ),
            "decision": (
                "EMPIRICAL_PREFERRED_ON_PREDECLARED_RULE" if empirical_wins
                else "NORMAL_CONTROL_NOT_BEATEN_NEGATIVE_FINDING"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    audit = json.loads(args.audit_manifest.read_text(encoding="utf-8"))
    rows, invariants = load_qb_rows(args.cache, audit)
    scored = [row for row in rolling_predictions(rows) if row.get("b0") is not None]
    train_rows = [row for row in scored if row["season"] <= 2022]
    held_rows = [row for row in scored if 2023 <= row["season"] <= 2025]

    evaluation = evaluate(train_rows, held_rows)
    output = {
        "schema_version": 1,
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "source_audit_manifest_sha256": sha256_file(args.audit_manifest),
        "reused_projection_model": "passing_yards_baseline_research.rolling_predictions (B0)",
        "train_partition": "season <= 2022",
        "held_partition": "2023 <= season <= 2025",
        "qb_source_rows": len(rows),
        "passing_stat_invariant_failures": invariants,
        "rung_offsets_yards": list(RUNG_OFFSETS),
        "evaluation": evaluation,
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evaluation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
