#!/usr/bin/env python3
"""INDEPENDENT ADVERSARIAL REVIEW of PR #193 -- EXPLORATORY ONLY.

This is NOT a re-test of the locked rule and does not change its verdict.
It runs on the same locked holdout rows (built by the unchanged
`evaluation_core.build_rows`) to ask questions the pre-registration did not:

Attack 4: constant-scale alternatives vs the per-team ratio (C1). The
          constant is measured on the EARLIER exploratory 2024-2025 wk8+ rows,
          never on the holdout.
Attack 6: expand the population with players who logged offensive snaps in
          the target game but have no nflverse stats row (realized 0 rec).
Attack 8: C1 invariance to player-derived vs PBP team-offense rows (2025).
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import evaluation_core as ec  # noqa: E402
from stats_lib import player_clustered_diff_ci, poisson_brier  # noqa: E402
import data_cache  # noqa: E402

OUT = {}


def mae(xs, f):
    return statistics.fmean(abs(f(x) - x["realized_receptions"]) for x in xs)


def bias(xs, f):
    return statistics.fmean(f(x) - x["realized_receptions"] for x in xs)


def ci(rows, fa, fb):
    r = player_clustered_diff_ci(rows, lambda x: abs(fa(x) - x["realized_receptions"]),
                                 lambda x: abs(fb(x) - x["realized_receptions"]))
    return {"point": round(r["point"], 4), "ci95": [round(v, 4) for v in r["ci95"]]}


# ---------------- exploratory period (2024-2025 wk8+) constants -------------
ctx_e = ec.load_context(range(2022, 2026))
rows_e = []
for s in (2024, 2025):
    rows_e.extend(ec.build_rows(ctx_e, s, 8)[0])
ratios_e = [x["targets_per_dropback"] for x in rows_e]
k_mean_ratio = statistics.fmean(ratios_e)
# mean-matching scale (sum realized / sum projection) and MAE-optimal scale by grid
grid = [0.60 + 0.002 * i for i in range(301)]


def best_scale(xs, key):
    return min(grid, key=lambda k: statistics.fmean(abs(k * x[key] - x["realized_receptions"]) for x in xs))


consts = {
    "chain_x_mean_ratio_expl": k_mean_ratio,
    "chain_mae_opt_scale_expl": best_scale(rows_e, "existing_projection"),
    "chain_mean_match_scale_expl": sum(x["realized_receptions"] for x in rows_e) / sum(x["existing_projection"] for x in rows_e),
    "b0_mae_opt_scale_expl": best_scale(rows_e, "b0_projection"),
    "b0_mean_match_scale_expl": sum(x["realized_receptions"] for x in rows_e) / sum(x["b0_projection"] for x in rows_e),
    "c1_mae_opt_scale_expl": best_scale(rows_e, "challenger_projection"),
}
OUT["exploratory_constants"] = {k: round(v, 4) for k, v in consts.items()}
OUT["exploratory_n"] = len(rows_e)
print("constants", OUT["exploratory_constants"], flush=True)

# ---------------- holdout rows (identical to the locked evaluation) --------
ctx = ec.load_context(range(2018, 2023))
rows = []
for s in (2019, 2020, 2021, 2022):
    rows.extend(ec.build_rows(ctx, s, 8)[0])
print("holdout rows", len(rows), flush=True)
assert len(rows) == 10961

models = {
    "b0": lambda x: x["b0_projection"],
    "c1": lambda x: x["challenger_projection"],
    "chain_unadjusted": lambda x: x["existing_projection"],
    "chain_x_0.828_mean_ratio_expl": lambda x: consts["chain_x_mean_ratio_expl"] * x["existing_projection"],
    "chain_x_mae_opt_scale_expl": lambda x: consts["chain_mae_opt_scale_expl"] * x["existing_projection"],
    "chain_x_mean_match_scale_expl": lambda x: consts["chain_mean_match_scale_expl"] * x["existing_projection"],
    "b0_x_mae_opt_scale_expl": lambda x: consts["b0_mae_opt_scale_expl"] * x["b0_projection"],
    "b0_x_mean_match_scale_expl": lambda x: consts["b0_mean_match_scale_expl"] * x["b0_projection"],
    "c1_x_mae_opt_scale_expl": lambda x: consts["c1_mae_opt_scale_expl"] * x["challenger_projection"],
}
OUT["holdout_models"] = {n: {"mae": round(mae(rows, f), 4), "bias": round(bias(rows, f), 4)} for n, f in models.items()}
OUT["holdout_diffs"] = {
    "c1_minus_chain_x_0.828": ci(rows, models["c1"], models["chain_x_0.828_mean_ratio_expl"]),
    "chain_x_0.828_minus_b0": ci(rows, models["chain_x_0.828_mean_ratio_expl"], models["b0"]),
    "c1_minus_chain_x_mae_opt": ci(rows, models["c1"], models["chain_x_mae_opt_scale_expl"]),
    "c1_minus_b0_x_mae_opt": ci(rows, models["c1"], models["b0_x_mae_opt_scale_expl"]),
    "b0_x_mae_opt_minus_b0": ci(rows, models["b0_x_mae_opt_scale_expl"], models["b0"]),
}
# how much of C1's gain over B0 does a constant recover?
d_c1 = OUT["holdout_diffs"]["c1_minus_chain_x_0.828"]["point"]
# per-team ratio spread on the holdout and whether its deviation is informative
hr = [x["targets_per_dropback"] for x in rows]
OUT["holdout_ratio_distribution"] = {"mean": round(statistics.fmean(hr), 4),
                                     "p10": round(sorted(hr)[len(hr) // 10], 4),
                                     "p90": round(sorted(hr)[9 * len(hr) // 10], 4)}
# realized team targets/dropback in the target game vs prior-8 ratio: correlation of deviations
tg = []
for x in rows:
    key = (x["season"], x["week"], x["team"])
    games = [g for g in ctx["team_games"].get(x["team"], []) if (g[0], g[1]) == (x["season"], x["week"])]
    if games:
        tg.append((x["targets_per_dropback"], games[0][3] / games[0][2]))
if tg:
    a = [p for p, _ in tg]
    b = [r for _, r in tg]
    OUT["prior8_vs_realized_team_ratio_corr"] = round(statistics.correlation(a, b), 4)
    OUT["prior8_vs_realized_team_ratio_n_rows"] = len(tg)
by_season = defaultdict(list)
for x in rows:
    by_season[x["season"]].append(x)
OUT["by_season_c1_minus_chain_x_0.828"] = {
    s: round(mae(xs, models["c1"]) - mae(xs, models["chain_x_0.828_mean_ratio_expl"]), 4) for s, xs in sorted(by_season.items())
}

# proper scores (MAE rewards median-like shading; MSE/Brier do not)


def ci_loss(rows_, fa, fb, loss):
    r = player_clustered_diff_ci(rows_, lambda x: loss(fa(x), x["realized_receptions"]),
                                 lambda x: loss(fb(x), x["realized_receptions"]))
    return {"point": round(r["point"], 4), "ci95": [round(v, 4) for v in r["ci95"]]}


def sq(p, y):
    return (p - y) ** 2


k828 = models["chain_x_0.828_mean_ratio_expl"]
OUT["holdout_proper_scores"] = {
    "mse": {n: round(statistics.fmean(sq(models[n](x), x["realized_receptions"]) for x in rows), 4)
            for n in ("b0", "c1", "chain_x_0.828_mean_ratio_expl")},
    "poisson_brier": {n: round(statistics.fmean(poisson_brier(models[n](x), x["realized_receptions"]) for x in rows), 4)
                      for n in ("b0", "c1", "chain_x_0.828_mean_ratio_expl")},
    "mse_c1_minus_b0": ci_loss(rows, models["c1"], models["b0"], sq),
    "mse_c1_minus_chain_x_0.828": ci_loss(rows, models["c1"], k828, sq),
    "brier_c1_minus_b0": ci_loss(rows, models["c1"], models["b0"], poisson_brier),
    "brier_c1_minus_chain_x_0.828": ci_loss(rows, models["c1"], k828, poisson_brier),
}
print(json.dumps(OUT, indent=1), flush=True)

# ---------------- attack 6: snap-only (no stats row) players ---------------
stat_keys = {(r["player_id"], r["season"], r["week"]) for r in ctx["player_rows"]}
snap_rows = []
for s in range(2019, 2023):
    snap_rows.extend(data_cache.load_snap_rows(s)["rows"])
synthetic = []
for r in snap_rows:
    if r["week"] < 8 or r["position"] not in ("WR", "TE", "RB") or not r["player_id"] or r["offense_snaps"] <= 0:
        continue
    if (r["player_id"], r["season"], r["week"]) in stat_keys:
        continue
    synthetic.append({"player_id": r["player_id"], "player_name": "", "position": r["position"],
                      "season": r["season"], "week": r["week"], "game_id": r["game_id"], "team": r["team"],
                      "opponent_team": r.get("opponent", ""), "targets": 0.0, "receptions": 0.0})
ctx2 = dict(ctx, player_rows=synthetic)
extra = []
ab_total = defaultdict(int)
for s in (2019, 2020, 2021, 2022):
    rr, ab = ec.build_rows(ctx2, s, 8)
    extra.extend(rr)
    for k, v in ab.items():
        ab_total[k] += v
full = rows + extra
OUT["attack6"] = {
    "snap_only_candidates": len(synthetic), "snap_only_scored": len(extra), "snap_only_abstain": dict(ab_total),
    "snap_only_mae": {"b0": round(mae(extra, models["b0"]), 4), "c1": round(mae(extra, models["c1"]), 4)} if extra else None,
    "expanded_population_c1_minus_b0": ci(full, models["c1"], models["b0"]),
    "expanded_n": len(full),
}
print(json.dumps(OUT["attack6"], indent=1), flush=True)

# ---------------- attack 8: source substitution invariance (2025) ----------
from forward_sources import player_derived_team_offense  # noqa: E402

orig = ec.load_team_offense


def derived(season):
    d = player_derived_team_offense(season)
    return {"rows": d["rows"], "source": "PLAYER_DERIVED", "rows_sha256": d["raw_sha256"]}


ctx_p = ec.load_context(range(2024, 2026))
rows_p = {(x["player_id"], x["season"], x["week"]): x for x in ec.build_rows(ctx_p, 2025, 8)[0]}
ec.load_team_offense = derived
try:
    ctx_d = ec.load_context(range(2024, 2026))
    rows_d = {(x["player_id"], x["season"], x["week"]): x for x in ec.build_rows(ctx_d, 2025, 8)[0]}
finally:
    ec.load_team_offense = orig
common = sorted(set(rows_p) & set(rows_d))
rel = lambda key: [rows_d[k][key] / rows_p[k][key] for k in common]  # noqa: E731
c1r, unr, dbr = rel("challenger_projection"), rel("existing_projection"), rel("predicted_dropbacks")
tpdr = rel("targets_per_dropback")


def summ(v):
    v = sorted(v)
    return {"mean": round(statistics.fmean(v), 4), "p05": round(v[len(v) // 20], 4), "p50": round(v[len(v) // 2], 4),
            "p95": round(v[19 * len(v) // 20], 4), "mean_abs_pct_change": round(100 * statistics.fmean(abs(x - 1) for x in v), 3)}


cp = [rows_p[k] for k in common]
cd = [rows_d[k] for k in common]
OUT["attack8_2025_wk8plus"] = {
    "n_pbp": len(rows_p), "n_derived": len(rows_d), "n_common": len(common),
    "ratio_derived_over_pbp": {"c1": summ(c1r), "unadjusted": summ(unr), "predicted_dropbacks": summ(dbr), "targets_per_dropback": summ(tpdr)},
    "mae_pbp": {"c1": round(mae(cp, models["c1"]), 4), "unadjusted": round(mae(cp, models["chain_unadjusted"]), 4), "b0": round(mae(cp, models["b0"]), 4)},
    "mae_derived": {"c1": round(mae(cd, models["c1"]), 4), "unadjusted": round(mae(cd, models["chain_unadjusted"]), 4), "b0": round(mae(cd, models["b0"]), 4)},
}
print(json.dumps(OUT["attack8_2025_wk8plus"], indent=1), flush=True)
OUT["status"] = "EXPLORATORY_INDEPENDENT_REVIEW_NOT_CONFIRMATORY"
(HERE / "independent_review_report.json").write_text(json.dumps(OUT, indent=2))
print("wrote independent_review_report.json")
