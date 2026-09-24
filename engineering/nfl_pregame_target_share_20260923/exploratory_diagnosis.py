#!/usr/bin/env python3
"""EXPLORATORY mechanism diagnosis (already-inspected 2024+2025 weeks 8+).

Nothing here is confirmatory. Its only job is to find WHICH mechanism makes
the existing pregame target-share stage add error, so a single challenger
can be locked before any untouched data is looked at.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation_core import build_rows, load_context  # noqa: E402

t0 = time.time()
ctx = load_context(range(2022, 2026))
rows = []
for season in (2024, 2025):
    r, abstain = build_rows(ctx, season, 8)
    rows.extend(r)
    print(f"{season}: {len(r)} rows, abstain={abstain}", flush=True)
n = len(rows)


def mae(xs):
    return statistics.fmean(abs(x) for x in xs)


def bias(xs):
    return statistics.fmean(xs)


report = {"population": "EXPLORATORY_ALREADY_INSPECTED_2024_2025_WK8PLUS", "n": n}

# 1. Headline bias check.
b0_err = [x["b0_projection"] - x["realized_receptions"] for x in rows]
eng_err = [x["existing_projection"] - x["realized_receptions"] for x in rows]
report["b0"] = {"mae": mae(b0_err), "bias": bias(b0_err)}
report["existing_engine"] = {"mae": mae(eng_err), "bias": bias(eng_err)}
print("B0      MAE %.4f bias %+.4f" % (mae(b0_err), bias(b0_err)))
print("engine  MAE %.4f bias %+.4f" % (mae(eng_err), bias(eng_err)))

# 2. Unit consistency: the engine multiplies a share OF TEAM TARGETS by a
#    prediction of team DROPBACKS (attempts + sacks). Measure the real ratio.
ratios = []
for x in rows:
    games = x["prior_team_games"]
    if games:
        ratios.append(sum(g[3] for g in games) / sum(g[2] for g in games))
report["team_targets_per_dropback_prior8"] = {
    "mean": statistics.fmean(ratios), "p10": sorted(ratios)[len(ratios) // 10],
    "p90": sorted(ratios)[9 * len(ratios) // 10],
}
print("team targets per dropback (prior 8): mean %.3f p10 %.3f p90 %.3f" % (
    statistics.fmean(ratios), sorted(ratios)[len(ratios) // 10], sorted(ratios)[9 * len(ratios) // 10]))


# 3. Share estimators vs realized share (pregame only).
def ros(history_rows, team_targets, k, same_team=None):
    hs = [h for h in history_rows if same_team is None or h["team"] == same_team][-k:]
    num = sum(h["targets"] for h in hs)
    den = sum(team_targets.get((h["season"], h["week"], h["team"]), 0.0) for h in hs)
    return (num / den) if den > 0 else None


def mean_share(share_history, k):
    s = [v for (_, _, v) in share_history][-k:]
    return statistics.fmean(s) if s else None


estimators = {
    "existing": lambda x: x["existing_share"],
    "mean_last6": lambda x: mean_share(x["share_history"], 6),
    "ros_last6": lambda x: ros(x["history"], ctx["team_targets"], 6),
    "ros_last6_same_team": lambda x: ros(x["history"], ctx["team_targets"], 6, same_team=x["team"]),
    "ros_last10": lambda x: ros(x["history"], ctx["team_targets"], 10),
    "ros_last4": lambda x: ros(x["history"], ctx["team_targets"], 4),
}
share_table = {}
for name, fn in estimators.items():
    errs = []
    for x in rows:
        v = fn(x)
        if v is not None:
            errs.append(v - x["realized_share"])
    share_table[name] = {"n": len(errs), "mae": mae(errs), "bias": bias(errs)}
    print(f"share {name:24s} n={len(errs)} MAE {mae(errs):.4f} bias {bias(errs):+.4f}")
report["share_estimators_vs_realized_share"] = share_table


# 4. Stage substitution: hold existing team volume and catch rate fixed,
#    change ONLY the share stage (and, separately, the unit conversion).
def tpd(x):
    games = x["prior_team_games"]
    return sum(g[3] for g in games) / sum(g[2] for g in games) if games else None


variants = {}
for name, fn in estimators.items():
    for unit in ("dropbacks_as_is", "unit_consistent_targets"):
        errs = []
        for x in rows:
            s = fn(x)
            if s is None:
                s = x["existing_share"]
            factor = 1.0 if unit == "dropbacks_as_is" else (tpd(x) or 1.0)
            proj = x["predicted_dropbacks"] * factor * s * x["catch_rate"]
            errs.append(proj - x["realized_receptions"])
        variants[f"{name}|{unit}"] = {"mae": mae(errs), "bias": bias(errs)}
        print(f"chain share={name:24s} unit={unit:24s} MAE {mae(errs):.4f} bias {bias(errs):+.4f}")
report["stage_substitution_receptions"] = variants

# 5. Where does the existing share go wrong? Team changes and history depth.
by_group = defaultdict(list)
for x in rows:
    last_team = x["history"][-1]["team"] if x["history"] else None
    changed = last_team is not None and last_team != x["team"]
    same_team_games = sum(1 for h in x["history"] if h["team"] == x["team"] and h["season"] == x["season"])
    by_group["team_changed_since_last_game" if changed else "same_team"].append(x)
    by_group["season_games_lt4" if same_team_games < 4 else "season_games_ge4"].append(x)
groups = {}
for g, xs in by_group.items():
    e = [x["existing_share"] - x["realized_share"] for x in xs]
    groups[g] = {"n": len(xs), "existing_share_mae": mae(e), "existing_share_bias": bias(e)}
    print(f"group {g:32s} n={len(xs)} existing share MAE {mae(e):.4f} bias {bias(e):+.4f}")
report["existing_share_error_by_group"] = groups

report["runtime_s"] = round(time.time() - t0, 1)
out = Path(__file__).resolve().parent / "exploratory_diagnosis_report.json"
out.write_text(json.dumps(report, indent=2))
print("wrote", out, f"{time.time()-t0:.1f}s")
