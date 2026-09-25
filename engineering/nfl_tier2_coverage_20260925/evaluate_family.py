#!/usr/bin/env python3
"""F11/F12 by coverage FAMILY (research only). Companion to evaluate_coverage.py.

Pre-declared 2026-09-25, committed BEFORE any family fit or scoring. The
man/zone results (coverage_params.json / coverage_report.json) are frozen
and untouched; this is one additional pre-declared formulation, fitted once
and scored once. It is not a retune of the rejected man/zone consumer.

    --stage fit    alpha per (market, mode) on DEV target seasons 2019-2022;
                   write coverage_family_params.json (commit before final)
    --stage final  refit on DEV, abort unless identical to the committed params,
                   score all partitions; write coverage_family_report.json

Declared: families COVER_0, COVER_1, 2_MAN, COVER_2, COVER_3, COVER_4, COVER_6,
OTHER (UNKNOWN excluded); K_FAM 60 (family target rate shrinks toward the
receiver's shrunk man/zone rate for the family's structure; OTHER toward his
exposure-pooled rate); catch rate / yds per target K_EFF 30 toward the same
parent; opponent family mix K_MIX 200 toward the league mix of the same window
with the F12 head-coach rule; the same windows, sample gates, ratio clip
[0.75, 1.333], alpha grid 0.0..2.0 step 0.1 on DEV MAE and alpha=1.0
sensitivity as evaluate_coverage.py. Modes: FAMILY_COMBINED (receiver family
rates, opponent mix vs faced mix), FAMILY_F11_ONLY (league mix vs faced mix),
FAMILY_F12_ONLY (position family rates, opponent mix vs league mix).
Rows without a family profile fall back to exactly k*B0 (reason recorded).
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import evaluate_coverage as EC
from nfl.research.tier1 import harness as H
from nfl.research.tier2 import coverage_challenger as C
from nfl.research.tier2 import coverage_data as D
from nfl.research.tier2 import coverage_features as F

HERE = Path(__file__).resolve().parent
PARAMS = HERE / "coverage_family_params.json"
REPORT = HERE / "coverage_family_report.json"


def family_ratios(scored, feats, tables, hc, market):
    avail = set(D.COVERAGE_SEASONS)
    cr, cd, cp = {}, {}, {}
    out = {}
    for r in scored:
        f = feats.get(H.row_key(r))
        if f is None:
            continue
        s, pid, pos, opp = r["season"], r["player_id"], f["pos"], f["opp"]
        if (s, pid) not in cr:
            cr[(s, pid)] = F.receiver_family_profile(tables, pid, f["receiver"], s, avail)
        if (s, opp) not in cd:
            cd[(s, opp)] = F.defense_family_mix(tables, opp, s, avail, hc.get((s, opp)))
        if (s, pos) not in cp:
            cp[(s, pos)] = F.position_family_profile(tables, pos, s, avail)
        league = F.league_family_mix(tables, F._window(s, avail))
        out[H.row_key(r)] = {m: C.family_ratio(m, market, cr[(s, pid)], cp[(s, pos)], cd[(s, opp)], league)
                             for m in C.FAMILY_MODES}
    return out


def fit_all(rows, positions, tables, hc):
    params, cache = {}, {}
    for market in EC.MARKETS:
        scored = H.b0_rolling_mean(rows, market)
        k = H.fit_scale_control(scored, market)
        feats = EC.features_for(scored, positions, tables, hc)
        rat = family_ratios(scored, feats, tables, hc, market)
        params[market] = {"k": k, "modes": {m: EC.fit_alpha(scored, rat, k, m) for m in C.FAMILY_MODES}}
        cache[market] = (scored, feats, rat)
    return params, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("fit", "final"), required=True)
    args = ap.parse_args()
    rows, positions, tables, hc, prov = EC.load()
    params, cache = fit_all(rows, positions, tables, hc)
    if args.stage == "fit":
        PARAMS.write_text(json.dumps({"declared": __doc__, "params": params, "provenance": prov},
                                     indent=2, sort_keys=True) + "\n")
        print(json.dumps(params, indent=1))
        return
    frozen = json.loads(PARAMS.read_text())["params"]
    if json.dumps(frozen, sort_keys=True) != json.dumps(params, sort_keys=True):
        raise SystemExit("DEV refit differs from committed coverage_family_params.json")
    report = {"provenance": prov, "params": params, "markets": {}}
    for market in EC.MARKETS:
        scored, feats, rat = cache[market]
        k = params[market]["k"]
        mk = {"modes": {}, "fallback_reasons": {}, "ratio_sd_activated": {}}
        for m in C.FAMILY_MODES:
            a = params[market]["modes"][m]["alpha"]
            reasons, pred, full, act = Counter(), {}, {}, []
            for r in scored:
                if r["b0"] is None:
                    continue
                q, why = rat.get(H.row_key(r), {}).get(m, (None, "NO_FEATURES_PRE_2019"))
                reasons[why] += 1
                pred[H.row_key(r)] = C.predict(r["b0"], k, q, a)
                full[H.row_key(r)] = C.predict(r["b0"], k, q, 1.0)
                if q is not None and r["season"] >= 2023:
                    act.append(q)
            mk["modes"][m] = H.evaluate_against_controls(scored, pred, market)
            mk["modes"][m + "_ALPHA1_SENSITIVITY"] = H.evaluate_against_controls(scored, full, market)
            mk["fallback_reasons"][m] = dict(reasons)
            mk["ratio_sd_activated"][m] = statistics.pstdev(act) if len(act) > 1 else None
        ex = []
        for r in scored:
            if r["season"] == 2026 and r["b0"] is not None:
                q = rat.get(H.row_key(r), {}).get("FAMILY_COMBINED", (None,))[0]
                if q is not None:
                    ex.append({"game_id": r["game_id"], "player_id": r["player_id"], "opponent": feats[H.row_key(r)]["opp"],
                               "b0": r["b0"], "scale_control": k * r["b0"], "ratio": q,
                               "challenger_alpha1": C.predict(r["b0"], k, q, 1.0), "actual": r["actual"]})
        ex.sort(key=lambda e: -abs(e["ratio"] - 1))
        mk["fresh_2026_examples_alpha1"] = ex[:8]
        report["markets"][market] = mk
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    for market, mk in report["markets"].items():
        for m, res in mk["modes"].items():
            for part in ("HOLDOUT_2023_2025", "FRESH_2026", "DEV_2016_2022"):
                e = res["vs_scale_control"]["partitions"][part]
                print(market, m, part, e.get("n_matched"), round(e.get("activation_share") or 0, 3),
                      e.get("paired_delta_mean"), e.get("paired_delta_ci95"))


if __name__ == "__main__":
    main()
