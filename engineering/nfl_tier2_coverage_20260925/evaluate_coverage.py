#!/usr/bin/env python3
"""F11/F12 evaluation (research only).

    --stage fit    fit alpha per (market, mode) on DEV target seasons 2019-2022
                   only; write coverage_params.json (commit before --stage final)
    --stage final  refit on DEV and abort unless identical to the committed
                   params, then score HOLDOUT 2023-2025 (previously inspected;
                   exploratory) and FRESH 2026 wk1-2 with
                   harness.evaluate_against_controls; write coverage_report.json

Pre-declared (before any scoring): WINDOW S-1 (1.0) + S-2 (0.5), K_RATE 150,
K_EFF 30, K_MIX 200, HC_CHANGE_WEIGHT 0.5, MIN_PLAYER_ONFIELD 100,
MIN_DEFENSE_LABELLED 200, ratio clip [0.75, 1.333], alpha grid 0.0..2.0 step
0.1 minimizing DEV mean absolute error of k*B0*ratio**alpha on activated rows.
Pre-declared sensitivity (added after the DEV fit returned alpha=0 for
COMBINED/F11_ONLY, before any HOLDOUT/FRESH scoring): every mode is also
scored at full strength alpha=1.0, to show what forcing the adjustment does.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from nfl.research.tier1 import harness as H
from nfl.research.tier2 import coverage_challenger as C
from nfl.research.tier2 import coverage_data as D
from nfl.research.tier2 import coverage_features as F

HERE = Path(__file__).resolve().parent
PARAMS = HERE / "coverage_params.json"
REPORT = HERE / "coverage_report.json"
SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
MARKETS = ("receptions", "receiving_yards")
DEV = (2019, 2022)
ALPHAS = [i / 10 for i in range(0, 21)]


def hc_change_map(games_csv: Path) -> dict[tuple[int, str], bool | None]:
    """(season, team) -> True/False if the head coach changed between the
    last REG game of season-1 and the first game of season; None if unknown."""
    first, last = {}, {}
    with games_csv.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("game_type") != "REG":
                continue
            s, wk = int(r["season"]), int(r["week"])
            for side in ("home", "away"):
                t = D.team(r[f"{side}_team"])
                coach = (r.get(f"{side}_coach") or "").strip()
                if not coach or coach == "NA":
                    continue
                if (s, t) not in first or wk < first[(s, t)][0]:
                    first[(s, t)] = (wk, coach)
                if (s, t) not in last or wk > last[(s, t)][0]:
                    last[(s, t)] = (wk, coach)
    out = {}
    for (s, t), (_, coach) in first.items():
        prev = last.get((s - 1, t))
        out[(s, t)] = None if prev is None else (prev[1] != coach)
    return out


def load():
    rows, hprov = H.load_player_weeks(
        Path("/tmp/claude-0/nflverse_cache"),
        Path("engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"),
        first_season=2016, current_season_csv=SHARED / "stats_player_week_2026.csv")
    pos_counts = defaultdict(Counter)
    for r in rows:
        pos_counts[r["player_id"]][r["position"]] += 1
    positions = {p: c.most_common(1)[0][0] for p, c in pos_counts.items()}
    drop, cprov = D.build_all(SHARED / "pbp", SHARED / "participation", Path("/tmp/claude-0/nfl_tier2_cov"))
    tables = F.SeasonTables(drop, positions)
    hc = hc_change_map(SHARED / "schedules" / "games.csv")
    prov = {"harness": hprov, "coverage_sources": cprov,
            "games_csv_sha256": D.sha256_file(SHARED / "schedules" / "games.csv")}
    return rows, positions, tables, hc, prov


def features_for(scored, positions, tables, hc):
    avail = set(D.COVERAGE_SEASONS)
    out = {}
    cache_r, cache_d, cache_p = {}, {}, {}
    for r in scored:
        if r["b0"] is None or r["season"] < 2019:
            continue
        s, pid, pos = r["season"], r["player_id"], positions.get(r["player_id"], "OTHER")
        opp = D.team(r["opponent_team"])
        rk = (s, pid)
        if rk not in cache_r:
            cache_r[rk] = F.receiver_profile(tables, pid, pos, s, avail)
        dk = (s, opp)
        if dk not in cache_d:
            cache_d[dk] = F.defense_profile(tables, opp, s, avail, hc.get((s, opp)))
        pk = (s, pos)
        if pk not in cache_p:
            cache_p[pk] = F.position_profile(tables, pos, s, avail)
        win = F._window(s, avail)
        out[H.row_key(r)] = {"receiver": cache_r[rk], "defense": cache_d[dk],
                             "position": cache_p[pk],
                             "league_man": F.league_man_share(tables, win) if win else None,
                             "opp": opp, "pos": pos}
    return out


def ratios(scored, feats, market):
    res = {}
    for r in scored:
        f = feats.get(H.row_key(r))
        if f is None:
            continue
        res[H.row_key(r)] = {m: C.matchup_ratio(m, market, f["receiver"], f["position"],
                                                f["defense"], f["league_man"]) for m in C.MODES}
    return res


def fit_alpha(scored, rat, k, mode):
    rows = [(r, rat[H.row_key(r)][mode][0]) for r in scored
            if DEV[0] <= r["season"] <= DEV[1] and H.row_key(r) in rat
            and rat[H.row_key(r)][mode][0] is not None]
    best = None
    for a in ALPHAS:
        mae = statistics.fmean(abs(C.predict(r["b0"], k, q, a) - r["actual"]) for r, q in rows)
        if best is None or mae < best[1] - 1e-12:
            best = (a, mae)
    return {"alpha": best[0], "dev_mae": best[1], "n_activated_dev": len(rows)}


def fit_all(rows, positions, tables, hc):
    params, cache = {}, {}
    for market in MARKETS:
        scored = H.b0_rolling_mean(rows, market)
        k = H.fit_scale_control(scored, market)
        feats = features_for(scored, positions, tables, hc)
        rat = ratios(scored, feats, market)
        params[market] = {"k": k, "modes": {m: fit_alpha(scored, rat, k, m) for m in C.MODES}}
        cache[market] = (scored, feats, rat)
    return params, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("fit", "final"), required=True)
    args = ap.parse_args()
    rows, positions, tables, hc, prov = load()
    params, cache = fit_all(rows, positions, tables, hc)
    if args.stage == "fit":
        PARAMS.write_text(json.dumps({"declared": __doc__, "params": params,
                                      "provenance": prov}, indent=2, sort_keys=True) + "\n")
        print(json.dumps(params, indent=1))
        return
    frozen = json.loads(PARAMS.read_text())["params"]
    if json.dumps(frozen, sort_keys=True) != json.dumps(params, sort_keys=True):
        raise SystemExit("DEV refit differs from committed coverage_params.json")
    report = {"provenance": prov, "params": params, "markets": {}}
    for market in MARKETS:
        scored, feats, rat = cache[market]
        k = params[market]["k"]
        mk = {"modes": {}}
        reasons = Counter()
        for m in C.MODES:
            a = params[market]["modes"][m]["alpha"]
            pred = {}
            for r in scored:
                if r["b0"] is None:
                    continue
                q = rat.get(H.row_key(r), {}).get(m, (None, "NO_FEATURES_PRE_2019"))
                if m == "COMBINED":
                    reasons[q[1]] += 1
                pred[H.row_key(r)] = C.predict(r["b0"], k, q[0], a)
            mk["modes"][m] = H.evaluate_against_controls(scored, pred, market)
            full = {H.row_key(r): C.predict(r["b0"], k, rat.get(H.row_key(r), {}).get(m, (None,))[0], 1.0)
                    for r in scored if r["b0"] is not None}
            mk["modes"][m + "_ALPHA1_SENSITIVITY"] = H.evaluate_against_controls(scored, full, market)
        mk["combined_fallback_reasons"] = dict(reasons)
        # descriptive splits on HOLDOUT, at the pre-declared alpha=1 sensitivity
        # (the frozen COMBINED alpha is 0, so frozen splits are identically 0)
        a = 1.0
        mk["splits_basis"] = "COMBINED at alpha=1.0 sensitivity, delta vs scale control"
        hold = [r for r in scored if 2023 <= r["season"] <= 2025 and r["b0"] is not None
                and rat.get(H.row_key(r), {}).get("COMBINED", (None,))[0] is not None]
        def delta(rs):
            if not rs:
                return None
            d = [abs(C.predict(r["b0"], k, rat[H.row_key(r)]["COMBINED"][0], a) - r["actual"])
                 - abs(k * r["b0"] - r["actual"]) for r in rs]
            return {"n": len(d), "mean_delta_vs_scale": statistics.fmean(d)}
        man = sorted(hold, key=lambda r: feats[H.row_key(r)]["defense"]["man_share"])
        t = len(man) // 3
        mk["holdout_by_opponent_man_tercile"] = {"low": delta(man[:t]), "mid": delta(man[t:2 * t]), "high": delta(man[2 * t:])}
        n_on = sorted(hold, key=lambda r: feats[H.row_key(r)]["receiver"]["n_onfield"])
        mk["holdout_by_receiver_sample_tercile"] = {"small": delta(n_on[:t]), "mid": delta(n_on[t:2 * t]), "large": delta(n_on[2 * t:])}
        mk["holdout_by_season"] = {s: delta([r for r in hold if r["season"] == s]) for s in (2023, 2024, 2025)}
        mk["holdout_by_position"] = {p: delta([r for r in hold if feats[H.row_key(r)]["pos"] == p]) for p in ("WR", "TE", "RB")}
        # genuine examples: largest COMBINED adjustments in FRESH 2026
        ex = []
        for r in scored:
            if r["season"] == 2026 and r["b0"] is not None:
                q = rat.get(H.row_key(r), {}).get("COMBINED", (None,))[0]
                if q is not None:
                    f = feats[H.row_key(r)]
                    ex.append({"game_id": r["game_id"], "player_id": r["player_id"], "pos": f["pos"],
                               "opponent": f["opp"], "b0": r["b0"], "scale_control": k * r["b0"],
                               "challenger": C.predict(r["b0"], k, q, a), "ratio": q,
                               "receiver_man_rate": f["receiver"]["bins"]["MAN"]["target_rate"],
                               "receiver_zone_rate": f["receiver"]["bins"]["ZONE"]["target_rate"],
                               "receiver_faced_man_share": f["receiver"]["exposure_man_share"],
                               "receiver_n_onfield": f["receiver"]["n_onfield"],
                               "opponent_man_share": f["defense"]["man_share"],
                               "opponent_regime": f["defense"]["regime"], "dc_identity": "UNKNOWN",
                               "actual": r["actual"]})
        ex.sort(key=lambda e: -abs(e["ratio"] - 1))
        mk["fresh_2026_examples"] = ex[:8]
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
