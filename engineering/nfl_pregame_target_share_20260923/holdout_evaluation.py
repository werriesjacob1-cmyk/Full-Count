#!/usr/bin/env python3
"""LOCKED holdout evaluation (see PREREGISTRATION.md). Implements the
pre-registered primary rule and secondary measurements exactly; it must not
be edited after its first run."""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation_core import build_rows, load_context  # noqa: E402
from stats_lib import player_clustered_diff_ci, poisson_brier, poisson_log_score  # noqa: E402

HOLDOUT_SEASONS = (2019, 2020, 2021, 2022)
MIN_WEEK = 8
MODELS = {
    "b0": "b0_projection",
    "existing_unadjusted": "existing_projection",
    "existing_full_engine": "full_engine_projection",
    "c1_unit_consistent": "challenger_projection",
}

t0 = time.time()
ctx = load_context(range(2018, 2023))
rows, abstain_by_season = [], {}
for season in HOLDOUT_SEASONS:
    r, a = build_rows(ctx, season, MIN_WEEK)
    rows.extend(r)
    abstain_by_season[season] = a
    print(f"{season}: {len(r)} paired rows, abstain={a}", flush=True)


def abs_err(key):
    return lambda x: abs(x[key] - x["realized_receptions"])


def mae(xs, key):
    return statistics.fmean(abs(x[key] - x["realized_receptions"]) for x in xs)


def bias(xs, key):
    return statistics.fmean(x[key] - x["realized_receptions"] for x in xs)


primary = player_clustered_diff_ci(rows, abs_err("challenger_projection"), abs_err("b0_projection"))
if primary["ci95"][1] < 0:
    verdict = "CONFIRMED_IMPROVEMENT_OVER_B0"
elif primary["ci95"][0] > 0:
    verdict = "CONFIRMED_WORSE_THAN_B0"
else:
    verdict = "NO_DEMONSTRATED_DIFFERENCE_FROM_B0"
print(f"PRIMARY C1-B0 MAE diff {primary['point']:+.4f} CI {primary['ci95']} -> {verdict}", flush=True)

secondary = {
    "c1_minus_existing_unadjusted": player_clustered_diff_ci(
        rows, abs_err("challenger_projection"), abs_err("existing_projection")),
    "c1_minus_existing_full_engine": player_clustered_diff_ci(
        rows, abs_err("challenger_projection"), abs_err("full_engine_projection")),
}

models = {}
for name, key in MODELS.items():
    models[name] = {
        "mae": mae(rows, key), "bias": bias(rows, key),
        "poisson_brier_lines_1p5_to_6p5": statistics.fmean(poisson_brier(x[key], x["realized_receptions"]) for x in rows),
        "poisson_log_score": statistics.fmean(poisson_log_score(x[key], x["realized_receptions"]) for x in rows),
    }
    print(f"{name:22s} MAE {models[name]['mae']:.4f} bias {models[name]['bias']:+.4f} "
          f"brier {models[name]['poisson_brier_lines_1p5_to_6p5']:.4f} logscore {models[name]['poisson_log_score']:.4f}")


def tier(x):
    s = x["existing_share"]
    return "share_lt_0.10" if s < 0.10 else ("share_0.10_to_0.20" if s < 0.20 else "share_ge_0.20")


groups = defaultdict(list)
for x in rows:
    groups[f"season_{x['season']}"].append(x)
    groups[f"position_{x['position']}"].append(x)
    groups[tier(x)].append(x)
by_group = {
    g: {"n": len(xs), "b0_mae": mae(xs, "b0_projection"), "c1_mae": mae(xs, "challenger_projection"),
        "c1_minus_b0": mae(xs, "challenger_projection") - mae(xs, "b0_projection")}
    for g, xs in sorted(groups.items())
}

ratios = sorted(x["challenger_projection"] / x["existing_projection"] for x in rows)
change = {
    "n": len(ratios), "mean_ratio_c1_over_unadjusted": statistics.fmean(ratios),
    "p05": ratios[len(ratios) // 20], "p50": ratios[len(ratios) // 2], "p95": ratios[19 * len(ratios) // 20],
}

report = {
    "status": "LOCKED_HOLDOUT_PER_PREREGISTRATION",
    "holdout_seasons": list(HOLDOUT_SEASONS), "min_week": MIN_WEEK,
    "n_paired_rows": len(rows), "n_players": primary["n_clusters"],
    "abstain_by_season": abstain_by_season,
    "primary": {"metric": "receptions MAE, C1 minus B0", **primary, "verdict": verdict},
    "secondary_differences": secondary,
    "models": models,
    "by_group": by_group,
    "prediction_change_c1_vs_unadjusted": change,
    "provenance": ctx["provenance"],
    "generated_in_seconds": round(time.time() - t0, 1),
}
out = Path(__file__).resolve().parent / "holdout_evaluation_report.json"
out.write_text(json.dumps(report, indent=2))
print("wrote", out, f"{time.time()-t0:.1f}s")
