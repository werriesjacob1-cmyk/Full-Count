#!/usr/bin/env python3
"""FC-ACC-015 audit runner: implements PLAN.md (frozen at 1650dadf) exactly.

Imports the frozen FC-ACC-014 runner READ-ONLY (research/fc_acc_014/tournament.py
@ c3f3362d) and reuses its model, Design, contrast and equal-N code unchanged.
Reads the reproduced FC-ACC-014 feature files (research/fc_acc_014/out/*.jsonl,
gitignored; rebuilt by the reproduction step) and schedule.json.
Writes research/fc_acc_015/out/audit_report.json and out/frozen_models.json.
Never writes into research/fc_acc_014.

Usage: python3 audit.py [<fc_acc_014 dir>]
"""
from __future__ import annotations

import bisect
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FC14 = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "fc_acc_014"
sys.path.insert(0, str(FC14))
import tournament as T  # noqa: E402  (frozen FC-ACC-014 runner, read-only)

SEED, TIE_REPS = 20261022, 200
FOLDS = T.FOLDS
F1, F2 = FOLDS[0][0], FOLDS[1][0]

WX = ("wx_temp", "wx_wind_mph", "wx_wind_out", "wx_wind_in", "wx_roof")
SP_ID = ("match_same_hand", "sig_platoon", "sig_sp_era_weak", "sig_pitch_exploit")
SLOT = ("opp_slot", "sig_woba_behind", "sig_woba_ahead")
RUN_ENV = ("opp_team_rpg", "lin_team_hit", "opp_home")


def drop_wx(k):
    return k in WX


def drop_sp(k):
    return k.startswith("opp_sp_") or k in SP_ID


def drop_slot(k):
    return k in SLOT


def drop_fid(k):
    return k in RUN_ENV or k.startswith("park_") or k.startswith("wx_")


BATTER_VARIANTS = {
    "V0_ORIGINAL": (lambda k: False, True, False),
    "V1_NO_WX": (drop_wx, True, False),
    "V2_NO_SP_IDENTITY": (drop_sp, True, False),
    "V3_NO_SLOT": (drop_slot, True, False),
    "V4_PRIOR_SLOT": (drop_slot, True, True),
    "V5_SAMEDAY_STRICT": (lambda k: drop_wx(k) or drop_sp(k) or drop_slot(k), True, False),
    "V6_SAMEDAY_STRICT_NO_BASE": (lambda k: drop_wx(k) or drop_sp(k) or drop_slot(k), False, False),
    "V7_FIDELITY": (drop_fid, True, False),
}
K_WX = ("wx_temp", "wx_roof")
K_LINEUP = ("match_lineup_k", "match_lineup_n", "match_same_hand")
K_VARIANTS = {
    "K0_ORIGINAL": (lambda k: False, True),
    "K1_NO_WX": (lambda k: k in K_WX, True),
    "K2_NO_OPP_LINEUP": (lambda k: k in K_LINEUP, True),
    "K3_SAMEDAY_STRICT": (lambda k: k in K_WX or k in K_LINEUP, True),
    "K4_SAMEDAY_STRICT_NO_BASE": (lambda k: k in K_WX or k in K_LINEUP, False),
}


def od(s):
    return date.fromisoformat(s).toordinal()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- prior slot (V4)
def add_prior_slot(rows, sched):
    hist = defaultdict(list)
    for g in sched.values():
        for side in ("home_lineup", "away_lineup"):
            for i, pid in enumerate(g[side][:9]):
                hist[pid].append((od(g["date"]), i + 1))
    for v in hist.values():
        v.sort()
    for r in rows:
        h = hist.get(r["player_id"], [])
        j = bisect.bisect_left([x[0] for x in h], od(r["date"]))
        last = [s for _, s in h[max(0, j - 10):j]]
        r["f"]["prior_slot"] = float(np.mean(last)) if last else None


# ---------------------------------------------------------------- selection helpers
def per_date_index(rows):
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r["date"]].append(i)
    return {d: np.array(v) for d, v in sorted(by.items())}


def top_set(idx, p, pid, n):
    order = sorted(idx, key=lambda i: (-p[i], pid[i]))
    return set(order[:n])


def tie_avg_wins(idx, p, y, n):
    """Expected wins of the top-n by p when the tie block at the boundary is
    allocated fractionally (no arbitrary tie-break)."""
    if n <= 0:
        return 0.0, False
    vals = p[idx]
    v = np.sort(vals)[::-1][n - 1]
    above = idx[vals > v]
    block = idx[vals == v]
    slots = n - len(above)
    return float(y[above].sum() + slots / len(block) * y[block].sum()), len(block) > slots


def selection(p_base, p_ch, y, rows, count_fn, rng, tie_reps=0):
    """Equal per-date counts for both arms. count_fn(date, idx, p_base) -> n_d."""
    by = per_date_index(rows)
    pid = np.array([r["player_id"] for r in rows])
    added = removed = aw = rw = sel = base_w = 0
    per_date, contrib = [], defaultdict(lambda: [0, 0, 0, 0])   # player -> added n, added wins, removed n, removed wins
    tie_base = tie_ch = 0.0
    boundary_tie_dates = 0
    keep = []
    for d, idx in by.items():
        n = min(int(count_fn(d, idx, p_base)), len(idx))
        if n <= 0:
            continue
        keep.append((idx, n))
        b, c = top_set(idx, p_base, pid, n), top_set(idx, p_ch, pid, n)
        assert len(b) == len(c) == n
        a, rm = c - b, b - c
        added += len(a); removed += len(rm); sel += n
        aw += int(y[list(a)].sum()) if a else 0
        rw += int(y[list(rm)].sum()) if rm else 0
        base_w += int(y[list(b)].sum())
        per_date.append((y[list(a)].sum() if a else 0) - (y[list(rm)].sum() if rm else 0))
        for i in a:
            contrib[pid[i]][0] += 1; contrib[pid[i]][1] += y[i]
        for i in rm:
            contrib[pid[i]][2] += 1; contrib[pid[i]][3] += y[i]
        tb, tied = tie_avg_wins(idx, p_base, y, n)
        tc, _ = tie_avg_wins(idx, p_ch, y, n)
        tie_base += tb; tie_ch += tc; boundary_tie_dates += int(tied)
    out = {"selected_per_arm": sel, "dates": len(keep), "baseline_wins": base_w, "challenger_wins": base_w + aw - rw,
           "added": added, "added_wins": aw, "removed": removed, "removed_wins": rw, "net_wins": aw - rw,
           "net_ci_date": T.cluster_sum_ci(per_date, rng) if per_date else None,
           "net_ci_player": player_ci(contrib, added, rng) if added else None,
           "tie_averaged": {"baseline_wins": tie_base, "challenger_wins": tie_ch, "net": tie_ch - tie_base,
                            "dates_with_boundary_tie_in_baseline": boundary_tie_dates}}
    if tie_reps:
        nets = []
        for _ in range(tie_reps):
            u = rng.random(len(rows))
            tot = 0
            for idx, n in keep:
                ob = idx[np.lexsort((u[idx], -p_base[idx]))][:n]
                oc = idx[np.lexsort((u[idx], -p_ch[idx]))][:n]
                tot += int(y[oc].sum()) - int(y[ob].sum())
            nets.append(tot)
        out["random_tiebreak_net"] = {"reps": tie_reps, "mean": float(np.mean(nets)), "min": int(min(nets)),
                                      "p2_5": float(np.percentile(nets, 2.5)), "p97_5": float(np.percentile(nets, 97.5))}
    return out


def player_ci(contrib, n_added, rng):
    """Player-clustered CI for the equal-N net that PRESERVES equal counts: resample
    players, recompute the added and removed hit rates, net* = n_added * (rate_a* - rate_r*).
    (A plain bootstrap of per-player net sums would let the added/removed counts drift
    apart and mostly measure that imbalance, not the selection difference.)"""
    v = np.array(list(contrib.values()), float)
    draws = rng.integers(0, len(v), size=(T.REPS, len(v)))
    s = v[draws].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        net = n_added * (s[:, 1] / s[:, 0] - s[:, 3] / s[:, 2])
    net = net[np.isfinite(net)]
    return [float(np.percentile(net, 2.5)), float(np.percentile(net, 97.5))]


def frac_count(frac):
    return lambda d, idx, pb: max(1, round(frac * len(idx)))


def k_count(k):
    return lambda d, idx, pb: k


def floor_count(thr):
    return lambda d, idx, pb: int((pb[idx] >= thr).sum())


# ---------------------------------------------------------------- variant fit
def fit_variant(rows, base_key, all_feat, drop, use_base, target, extra=()):
    feat = [k for k in all_feat if not drop(k)] + list(extra)
    keys = ([base_key] if use_base else []) + feat
    res = {}
    for fold, trs, ts in FOLDS:
        R_tr = [r for r in rows if r["season"] in trs]
        R_te = [r for r in rows if r["season"] == ts]
        y_tr = np.array([target(r) for r in R_tr], float)
        y_te = np.array([target(r) for r in R_te], float)
        x1 = lambda R: np.column_stack([np.ones(len(R)), [r["f"][base_key] for r in R]])  # noqa: E731
        w1 = T.fit_logistic(x1(R_tr), y_tr)
        p1 = T.sig(x1(R_te) @ w1)
        D = T.Design(R_tr, keys)
        w2 = T.fit_logistic(D.X(R_tr), y_tr, T.LAM)
        p2 = T.sig(D.X(R_te) @ w2)
        res[fold] = {"R_te": R_te, "y": y_te, "M1": p1, "M2": p2, "D": D, "w": w2, "keys": keys}
    return res


def frozen_model(D, w):
    return {"keys": D.keys, "median": D.med.tolist(), "missing_indicator_for": [D.keys[j] for j in D.miss],
            "mu": D.mu.tolist(), "sd": D.sd.tolist(), "coef": w.tolist(),
            "coef_order": ["intercept"] + D.keys + [f"{D.keys[j]}__missing" for j in D.miss],
            "transform": "z = (([x with NaN->median], 1[x missing] for listed keys) - mu) / sd; p = sigmoid(coef . [1, z])"}


def summarize_variant(fitres, frac, rng, depth, floor, tie_reps):
    out = {}
    for fold, f in fitres.items():
        y, R = f["y"], f["R_te"]
        s1, s2 = T.summarize(f["M1"], y), T.summarize(f["M2"], y)
        e = {"frac": selection(f["M1"], f["M2"], y, R, frac_count(frac), rng, tie_reps)}
        for k in depth:
            e[f"top{k}_per_date"] = selection(f["M1"], f["M2"], y, R, k_count(k), rng)
        if floor is not None:
            e[f"floor_matched_M1_ge_{floor}"] = selection(f["M1"], f["M2"], y, R, floor_count(floor), rng)
        out[fold] = {"n_test": len(y), "n_features": len(f["keys"]), "M1": {k: s1[k] for k in ("logloss", "brier", "auc", "slope", "ece")},
                     "M2": {k: s2[k] for k in ("logloss", "brier", "auc", "slope", "ece")},
                     "vs_M1": T.contrast(f["M1"], f["M2"], y, R, rng), "equal_n": e}
    return out


def go_rule(v):
    ll = all(v[f]["vs_M1"]["logloss_ci_date"][1] < 0 for f in (F1, F2))
    en1, en2 = v[F1]["equal_n"]["frac"], v[F2]["equal_n"]["frac"]
    win = en1["net_wins"] > 0 and en2["net_wins"] > 0 and en2["net_ci_date"][0] > 0
    return ll, win


# ---------------------------------------------------------------- population audit
def population(rows, name, man_cov):
    dup_dp = Counter((r["date"], r["player_id"]) for r in rows)
    dup_gp = Counter((r["game_pk"], r["player_id"]) for r in rows)
    per_date = Counter(r["date"] for r in rows)
    v = np.array(list(per_date.values()))
    return {"rows": len(rows), "dates": len(per_date), "per_date_min_median_max": [int(v.min()), float(np.median(v)), int(v.max())],
            "duplicate_game_player": sum(c - 1 for c in dup_gp.values() if c > 1),
            "same_date_player_multiple_games": sum(c - 1 for c in dup_dp.values() if c > 1),
            "fair_test_share": float(np.mean([bool(r.get("fair_test")) for r in rows])) if "fair_test" in rows[0] else None,
            "ungraded_skipped_by_builder": (man_cov.get(name) or {}).get("ungraded_skipped"),
            "p0_distinct_values": len({r["p0"] for r in rows}) if not isinstance(rows[0]["p0"], dict) else None}


def slot_provenance(rows, sched):
    c = Counter()
    for r in rows:
        g = sched.get(str(r["game_pk"]))
        s = r["f"].get("opp_slot")
        lin = None
        if g:
            for side in ("home_lineup", "away_lineup"):
                if r["player_id"] in g[side][:9]:
                    lin = g[side].index(r["player_id"]) + 1
        c["slot_missing" if s is None else "slot_present"] += 1
        c["in_schedule_starting_lineup" if lin else "not_in_schedule_starting_lineup"] += 1
        if s is not None and lin:
            c["slot_equals_schedule_lineup_index" if s == lin else "slot_differs_from_schedule_lineup_index"] += 1
    return dict(c)


def main():
    rng = np.random.default_rng(SEED)
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    fman = json.loads((FC14 / "out" / "features_manifest.json").read_text())
    ident = {"fc014_runner_sha256": sha(FC14 / "tournament.py"), "fc014_features_sha256": sha(FC14 / "features.py"),
             "fc014_data_sha256": sha(FC14 / "data.py"), "fc014_fc013_build_sha256": sha(FC14 / "fc013_build.py"),
             "features": {n: sha(FC14 / "out" / n) for n in ("features_hits.jsonl", "features_tb.jsonl", "features_k.jsonl")},
             "features_manifest_expected": fman["sha256"], "audit_plan_commit": "1650dadfa29e62d06ceada998e63d775eef915ea"}
    ident["features_match_manifest"] = ident["features"] == fman["sha256"]
    sched = json.loads((FC14 / "out" / "schedule.json").read_text())
    report = {"identity": ident, "markets": {}, "population": {}}
    frozen = {}
    for mk, fname in (("hits", "features_hits.jsonl"), ("total_bases", "features_tb.jsonl")):
        rows = T.load(FC14 / "out" / fname)
        rows.sort(key=lambda r: (r["date"], r["game_pk"], r["player_id"]))
        report["population"][mk] = population(rows, mk, fman["coverage"])
        report["population"][mk]["slot_provenance"] = slot_provenance(rows, sched)
        add_prior_slot(rows, sched)
        all_feat = sorted({k for r in rows for k in r["f"] if not k.startswith("base_logit") and k != "prior_slot"})
        mres = {}
        for vn, (drop, use_base, prior) in BATTER_VARIANTS.items():
            fr = fit_variant(rows, "base_logit", all_feat, drop, use_base, lambda r: r["y"],
                             extra=("prior_slot",) if prior else ())
            full = vn in ("V0_ORIGINAL", "V1_NO_WX")
            mres[vn] = summarize_variant(fr, 0.05, rng, depth=(1, 2, 3) if full else (),
                                         floor=0.60 if (full and mk == "hits") else None, tie_reps=TIE_REPS if full else 0)
            mres[vn]["dropped_features"] = [k for k in all_feat if drop(k)] + ([] if use_base else ["base_logit"])
            if vn == "V1_NO_WX":
                frozen[mk] = frozen_model(fr[F2]["D"], fr[F2]["w"])
                frozen[mk]["trained_on"] = "seasons 2024-2025 (FC-ACC-014 fold-2 training set), lambda=1"
            print(mk, vn, json.dumps({f: [round(mres[vn][f]["vs_M1"]["logloss"], 5), mres[vn][f]["equal_n"]["frac"]["net_wins"]]
                                     for f in (F1, F2)}), flush=True)
        report["markets"][mk] = mres
    krows = T.load(FC14 / "out" / "features_k.jsonl")
    krows.sort(key=lambda r: (r["date"], r["game_pk"], r["player_id"]))
    report["population"]["strikeouts"] = population(krows, "strikeouts", fman["coverage"])
    k_feat = sorted({k for r in krows for k in r["f"] if not k.startswith("base_logit")})
    for t in (4, 5, 6):
        mres = {}
        for vn, (drop, use_base) in K_VARIANTS.items():
            fr = fit_variant(krows, f"base_logit{t}", k_feat, drop, use_base, lambda r, t=t: r["y"][str(t)])
            full = vn in ("K0_ORIGINAL", "K3_SAMEDAY_STRICT")
            mres[vn] = summarize_variant(fr, 0.10, rng, depth=(1, 2) if full else (), floor=0.60 if full else None,
                                         tie_reps=TIE_REPS if full else 0)
            mres[vn]["dropped_features"] = [k for k in k_feat if drop(k)] + ([] if use_base else [f"base_logit{t}"])
            if vn == "K3_SAMEDAY_STRICT":
                frozen[f"strikeouts_{t}_RESEARCH_ONLY"] = frozen_model(fr[F2]["D"], fr[F2]["w"])
            print(f"K{t}", vn, json.dumps({f: [round(mres[vn][f]["vs_M1"]["logloss"], 5), mres[vn][f]["equal_n"]["frac"]["net_wins"]]
                                          for f in (F1, F2)}), flush=True)
        report["markets"][f"strikeouts_{t}"] = mres
    # ---- retained share and frozen verdicts (PLAN §7)
    for mk, m in report["markets"].items():
        v0 = m.get("V0_ORIGINAL") or m.get("K0_ORIGINAL")
        for vn, v in m.items():
            v["retained_net_share"] = {f: (v[f]["equal_n"]["frac"]["net_wins"] / v0[f]["equal_n"]["frac"]["net_wins"]
                                           if v0[f]["equal_n"]["frac"]["net_wins"] else None) for f in (F1, F2)}
            v["logloss_rule"], v["equal_n_rule"] = go_rule(v)
    verdicts = {}
    for mk in ("hits", "total_bases"):
        m = report["markets"][mk]
        v1, v5 = m["V1_NO_WX"], m["V5_SAMEDAY_STRICT"]
        v1_go = v1["logloss_rule"] and v1["equal_n_rule"]
        v5_ok = v5["logloss_rule"] and all(v5[f]["equal_n"]["frac"]["net_wins"] > 0 for f in (F1, F2))
        tie_ok = all(v1[f]["equal_n"]["frac"]["tie_averaged"]["net"] > 0 for f in (F1, F2))
        verdict = ("GO" if (v1_go and v5_ok and tie_ok) else
                   "STOP" if not v1["logloss_rule"] else "MODIFY")
        verdicts[mk] = {"V1_go_rule": v1_go, "V5_survives": v5_ok, "V1_tie_averaged_positive": tie_ok,
                        "verdict_before_leakage_gate": verdict}
    k3 = [report["markets"][f"strikeouts_{t}"]["K3_SAMEDAY_STRICT"] for t in (4, 5, 6)]
    n_go = sum(v["logloss_rule"] and v["equal_n_rule"] for v in k3)
    n_ll = sum(v["logloss_rule"] for v in k3)
    verdicts["strikeouts"] = {"K3_thresholds_go": n_go, "K3_thresholds_logloss": n_ll,
                              "verdict_before_leakage_gate": "GO" if n_go >= 2 else ("STOP" if n_ll < 2 else "MODIFY")}
    report["verdicts"] = verdicts
    (out / "audit_report.json").write_text(json.dumps(report, indent=1, default=float))
    (out / "frozen_models.json").write_text(json.dumps(frozen, indent=1))
    print(json.dumps(verdicts, indent=1))


if __name__ == "__main__":
    main()
