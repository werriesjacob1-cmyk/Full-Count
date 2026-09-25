#!/usr/bin/env python3
"""F17 advanced receiving efficiency: fit once on DEV, score once (research only).

    --stage fit    k (harness DEV scale control) and w per mode on DEV target
                   seasons 2017-2022 (2016 has no prior-season position prior);
                   write f17_params.json -- commit it before --stage final
    --stage final  refit, abort unless identical to the committed params, score
                   every partition; write f17_report.json

PRE-DECLARED (committed before any fit or scoring):
* Market: receiving_yards. Primary population SNAP = REG player-games with
  PFR offense_snaps > 0 at WR/TE/RB with a harness-rule B0 (settlement-aligned:
  FanDuel voids only a player who plays no snap; eligibility never uses the
  game's own targets/receptions). Actual = weekly receiving_yards, 0 when the
  player has no weekly stat row. Sensitivity population ROLE = the harness's
  target-conditioned role rows (disclosed as postgame-conditioned).
* Constants: RECENT_GAMES 8, K_X 20, K_S 150, season weights 1.0/0.6/0.3,
  w grid 0.00..1.00 step 0.05 minimising DEV MAE on activated rows (ties ->
  smaller w). Modes: SIMPLE_EFF (control), F17_DEPTH, F17_FULL (primary).
* Primary test: F17_FULL vs SIMPLE_EFF on HOLDOUT_2023_2025, paired MAE
  difference, game-clustered bootstrap 95% CI (harness). Also reported:
  every mode vs B0 and vs the scale control, F17_FULL vs F17_DEPTH (skill
  increment), player-clustered CIs, FRESH_2026 (weeks 1-2), ROLE sensitivity.
* Decision: w(F17_FULL) = 0 -> REJECTED. SUPPORTED_HISTORICAL_EXPLORATORY only
  if (a) HOLDOUT F17_FULL - SIMPLE_EFF CI upper < 0, (b) HOLDOUT F17_FULL -
  scale control CI upper < 0, and (c) FRESH point estimate vs SIMPLE_EFF <= 0.
  Otherwise NOT_SUPPORTED. HOLDOUT outcomes were inspected by earlier
  experiments, so no historical result is prospective evidence.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from nfl.research.tier1 import harness as H
from nfl.research.tier2 import player_efficiency_f17 as M
from nfl.research.tier2 import player_efficiency_f17_data as D

HERE = Path(__file__).resolve().parent
PARAMS = HERE / "f17_params.json"
REPORT = HERE / "f17_report.json"
SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
PLAYERS = Path("/tmp/claude-0/nfl_tier1_B/players.csv")
CACHE = Path("/tmp/claude-0/nfl_f17_cache")
MARKET = "receiving_yards"
DEV_FIT = (2017, 2022)
SEASONS = range(2016, 2027)


def load():
    weekly, hprov = H.load_player_weeks(
        Path("/tmp/claude-0/nflverse_cache"),
        Path("engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"),
        first_season=2015, current_season_csv=SHARED / "stats_player_week_2026.csv")
    pos_counts = defaultdict(Counter)
    for r in weekly:
        pos_counts[r["player_id"]][r["position"]] += 1
    positions = {p: c.most_common(1)[0][0] for p, c in pos_counts.items()}
    games, pprov = D.load_game_targets(SHARED / "pbp", SEASONS, CACHE)
    xwalk = D.load_crosswalk(PLAYERS)
    snaps, sdiag = D.snap_population(SHARED / "snap_counts", SEASONS, xwalk)
    prov = {"weekly": hprov, "pbp_sha256": pprov,
            "snap_counts_sha256": {str(s): D.sha256_file(SHARED / "snap_counts" / f"snap_counts_{s}.csv") for s in SEASONS},
            "players_csv_sha256": D.sha256_file(PLAYERS), "snap_diagnostics": sdiag}
    return weekly, positions, games, snaps, prov


def build(weekly, positions, games, snaps):
    b0i, effi = M.B0Index(weekly), M.EfficiencyIndex(games, positions)
    actual = {(r["game_id"], r["player_id"]): r["receiving_yards"] for r in weekly}
    snap_rows, seen, diag = [], set(), Counter()
    for s in snaps:
        key = (s["game_id"], s["player_id"])
        if key in seen:
            diag["duplicate_snap_row"] += 1
            continue
        seen.add(key)
        pos = positions.get(s["player_id"], s["snap_position"])
        if pos not in D.POSITIONS:
            diag["non_receiver_position"] += 1
            continue
        st = b0i.state(s["player_id"], s["season"], s["week"])
        if st is None:
            diag["no_b0"] += 1
            continue
        diag["with_weekly_row" if key in actual else "no_weekly_row_actual_0"] += 1
        snap_rows.append({"season": s["season"], "week": s["week"], "game_id": s["game_id"],
                          "player_id": s["player_id"], "team": s["team"], "opponent_team": s["opponent_team"],
                          "position": pos, "actual": actual.get(key, 0.0), "b0": st["b0"], "_b0s": st,
                          "_feat": effi.features(s["player_id"], pos, s["season"], s["week"])})
    role_rows = []
    for r in weekly:
        if r["season_type"] != "REG" or max(r["targets"], r["receptions"]) <= 0 or r["season"] < 2016:
            continue
        pos = positions.get(r["player_id"])
        st = b0i.state(r["player_id"], r["season"], r["week"])
        if pos not in D.POSITIONS or st is None:
            continue
        role_rows.append({"season": r["season"], "week": r["week"], "game_id": r["game_id"],
                          "player_id": r["player_id"], "team": r["team"], "opponent_team": r["opponent_team"],
                          "position": pos, "actual": r["receiving_yards"], "b0": st["b0"], "_b0s": st,
                          "_feat": effi.features(r["player_id"], pos, r["season"], r["week"])})
    return snap_rows, role_rows, dict(diag)


def preds(rows, mode, k, w):
    out, why = {}, Counter()
    for r in rows:
        p, reason = M.predict(r["_b0s"], r["_feat"], mode, k, w)
        out[H.row_key(r)] = p
        why[reason] += 1
    return out, dict(why)


def fit(rows):
    k = H.fit_scale_control(rows, MARKET)
    params = {"k": k, "modes": {}}
    dev = [r for r in rows if DEV_FIT[0] <= r["season"] <= DEV_FIT[1]
           and M.mode_ypt(r["_feat"], "F17_FULL") is not None and r["_b0s"]["ypt5"] is not None]
    for mode in M.MODES:
        best = None
        for w in M.W_GRID:
            mae = statistics.fmean(abs(M.predict(r["_b0s"], r["_feat"], mode, k, w)[0] - r["actual"]) for r in dev)
            if best is None or mae < best[1] - 1e-12:
                best = (w, mae)
        params["modes"][mode] = {"w": best[0], "dev_mae": best[1], "n_dev_activated": len(dev)}
    return params


def persistence(games, positions, seasons=range(2016, 2023), min_tx=60):
    """DEV-only mechanism check: season-to-season correlation of per-target rates."""
    per = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])   # TX, YX, XY, T
    for g in games:
        if g["season_type"] == "REG" and g["season"] in seasons:
            c = per[(g["player_id"], g["season"])]
            c[0] += g["TX"]; c[1] += g["YX"]; c[2] += g["XY"]; c[3] += g["T"]
    out = {}
    for name, fn in (("yards_over_expected_per_target", lambda c: (c[1] - c[2]) / c[0]),
                     ("expected_yards_per_target", lambda c: c[2] / c[0]),
                     ("raw_yards_per_target", lambda c: c[1] / c[0])):
        xs, ys = [], []
        for (pid, s), c in per.items():
            n = per.get((pid, s + 1))
            if n and c[0] >= min_tx and n[0] >= min_tx and s + 1 in seasons:
                xs.append(fn(c)); ys.append(fn(n))
        out[name] = {"n_player_pairs": len(xs), "corr": statistics.correlation(xs, ys) if len(xs) > 2 else None}
    return out


def player_cluster(rows, a, b, part):
    """Paired MAE(a) - MAE(b) with a player-clustered bootstrap CI."""
    lo, hi = H.PARTITIONS[part]
    d = defaultdict(list)
    for r in rows:
        if lo <= r["season"] <= hi:
            key = H.row_key(r)
            d[r["player_id"]].append(abs(a[key] - r["actual"]) - abs(b[key] - r["actual"]))
    flat = [x for v in d.values() for x in v]
    return {"n": len(flat), "n_players": len(d), "delta": statistics.fmean(flat) if flat else None,
            "ci95_player_cluster": H._cluster_bootstrap(d)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("fit", "final"), required=True)
    a = ap.parse_args()
    weekly, positions, games, snaps, prov = load()
    snap_rows, role_rows, diag = build(weekly, positions, games, snaps)
    params = fit(snap_rows)
    if a.stage == "fit":
        PARAMS.write_text(json.dumps({"declared": __doc__, "params": params, "provenance": prov,
                                      "population_diagnostics": diag,
                                      "dev_mechanism_persistence": persistence(games, positions)},
                                     indent=2, sort_keys=True, default=str) + "\n")
        print(json.dumps({"params": params, "diag": diag}, indent=1))
        return
    frozen = json.loads(PARAMS.read_text())["params"]
    if json.dumps(frozen, sort_keys=True) != json.dumps(params, sort_keys=True):
        raise SystemExit("DEV refit differs from committed f17_params.json")
    k = params["k"]
    P, reasons = {}, {}
    for mode in M.MODES:
        P[mode], reasons[mode] = preds(snap_rows, mode, k, params["modes"][mode]["w"])
    scale = H.scale_control_predictions(snap_rows, k)
    rep = {"provenance": prov, "params": params, "population_diagnostics": diag,
           "population": {p: {"n_rows": sum(1 for r in snap_rows if lo <= r["season"] <= hi),
                              "n_players": len({r["player_id"] for r in snap_rows if lo <= r["season"] <= hi}),
                              "n_games": len({r["game_id"] for r in snap_rows if lo <= r["season"] <= hi})}
                          for p, (lo, hi) in H.PARTITIONS.items()},
           "fallback_reasons": reasons, "modes": {}, "head_to_head": {}, "player_cluster": {}}
    for mode in M.MODES:
        rep["modes"][mode] = H.evaluate_against_controls(snap_rows, P[mode], MARKET)
    for a_, b_ in (("F17_FULL", "SIMPLE_EFF"), ("F17_FULL", "F17_DEPTH"), ("F17_DEPTH", "SIMPLE_EFF")):
        shifted = [{**r, "b0": P[b_][H.row_key(r)]} for r in snap_rows]
        rep["head_to_head"][f"{a_}_vs_{b_}"] = H.evaluate(shifted, P[a_], MARKET)
    for part in ("HOLDOUT_2023_2025", "FRESH_2026"):
        rep["player_cluster"][part] = {
            "F17_FULL_vs_SIMPLE_EFF": player_cluster(snap_rows, P["F17_FULL"], P["SIMPLE_EFF"], part),
            "F17_FULL_vs_scale_control": player_cluster(snap_rows, P["F17_FULL"], scale, part),
            "SIMPLE_EFF_vs_scale_control": player_cluster(snap_rows, P["SIMPLE_EFF"], scale, part)}
    ch = [P["F17_FULL"][H.row_key(r)] - scale[H.row_key(r)] for r in snap_rows if r["season"] >= 2023]
    rep["changed_projections_2023_plus"] = {
        "n": len(ch), "abs_gt_0.5yd": sum(abs(x) > 0.5 for x in ch), "abs_gt_2yd": sum(abs(x) > 2 for x in ch),
        "abs_gt_5yd": sum(abs(x) > 5 for x in ch), "mean_abs_change": statistics.fmean(abs(x) for x in ch)}
    # ROLE sensitivity (target-conditioned population; own scale control, SNAP-fitted w)
    kr = H.fit_scale_control(role_rows, MARKET)
    rp = {m: preds(role_rows, m, kr, params["modes"][m]["w"])[0] for m in ("SIMPLE_EFF", "F17_FULL")}
    shifted = [{**r, "b0": rp["SIMPLE_EFF"][H.row_key(r)]} for r in role_rows]
    rep["role_sensitivity"] = {"k": kr, "F17_FULL": H.evaluate_against_controls(role_rows, rp["F17_FULL"], MARKET),
                               "F17_FULL_vs_SIMPLE_EFF": H.evaluate(shifted, rp["F17_FULL"], MARKET)}
    ex = []
    for r in snap_rows:
        if r["season"] == 2026 and r["_feat"].get("status") == "OK" and r["_b0s"]["ypt5"] is not None:
            key = H.row_key(r)
            f = r["_feat"]
            ex.append({"game_id": r["game_id"], "player_id": r["player_id"], "position": r["position"],
                       "b0": r["b0"], "tgt5": r["_b0s"]["tgt5"], "ypt5": r["_b0s"]["ypt5"],
                       "xypt_recent": f["xypt_recent"], "skill": f["skill"], "ypt_simple": f["ypt_simple"],
                       "catch_oe": f["catch_over_expected_per_target"], "yac_oe": f["yac_over_expected_per_target"],
                       "scale_control": scale[key], "simple_eff": P["SIMPLE_EFF"][key],
                       "f17_full": P["F17_FULL"][key], "actual": r["actual"]})
    ex.sort(key=lambda e: -abs(e["f17_full"] - e["scale_control"]))
    rep["fresh_2026_examples"] = ex[:10]
    h = rep["head_to_head"]["F17_FULL_vs_SIMPLE_EFF"]["partitions"]
    s = rep["modes"]["F17_FULL"]["vs_scale_control"]["partitions"]["HOLDOUT_2023_2025"]
    if params["modes"]["F17_FULL"]["w"] == 0:
        verdict = "REJECTED"
    elif (h["HOLDOUT_2023_2025"]["paired_delta_ci95"][1] < 0 and s["paired_delta_ci95"][1] < 0
          and h["FRESH_2026"]["paired_delta_mean"] <= 0):
        verdict = "SUPPORTED_HISTORICAL_EXPLORATORY"
    else:
        verdict = "NOT_SUPPORTED"
    rep["verdict"] = verdict
    REPORT.write_text(json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n")
    print("verdict", verdict)
    for mode, res in rep["modes"].items():
        for part in ("DEV_2016_2022", "HOLDOUT_2023_2025", "FRESH_2026"):
            e = res["vs_scale_control"]["partitions"][part]
            print(mode, "vs scale", part, e.get("n_matched"), round(e.get("activation_share") or 0, 3),
                  round(e.get("paired_delta_mean") or 0, 4), e.get("paired_delta_ci95"))
    for name, res in rep["head_to_head"].items():
        for part in ("HOLDOUT_2023_2025", "FRESH_2026"):
            e = res["partitions"][part]
            print(name, part, e.get("n_matched"), round(e.get("paired_delta_mean") or 0, 4), e.get("paired_delta_ci95"))


if __name__ == "__main__":
    main()
