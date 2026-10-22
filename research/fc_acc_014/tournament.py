"""FC-ACC-014 tournament runner: implements PREREG.md (frozen at d2185cb4) exactly.
Reads out/features_{hits,tb,k}.jsonl; writes out/report.json. Deterministic.

Implementation detail fixed before evaluation: where an M3 (distribution model)
ingredient is unavailable for a row (no slot PA distribution yet / no batter rate),
M3 falls back to that fold's M1 prediction for the row; coverage is reported.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
SEED, REPS, LAM = 20261022, 2000, 1.0
FOLDS = (("fold1_2024_to_2025", (2024,), 2025), ("fold2_2024_25_to_2026", (2024, 2025), 2026))
BATTER_GROUPS = {
    "SKILL": lambda k: k.startswith("skill_"),
    "OPP": lambda k: k in ("opp_slot", "opp_home", "opp_team_rpg"),
    "MATCH": lambda k: k.startswith("opp_sp_") or k.startswith("match_") or k in ("sig_platoon", "sig_sp_era_weak"),
    "CONTACT": lambda k: k in ("sig_l7_avg_ev", "sig_l7_barrel_pct", "sig_season_barrel_pct", "sig_iso", "sig_platoon_xwoba",
                               "sig_platoon_barrel_pct", "sig_bat_speed_trend", "sig_pitch_exploit"),
    "LINEUP": lambda k: k in ("sig_woba_behind", "sig_woba_ahead", "lin_team_hit"),
    "PARK_WX": lambda k: k.startswith("park_") or k.startswith("wx_") or k in ("sig_park_hand_index", "sig_pull_park_synergy"),
    "RECENCY": lambda k: k.startswith("rec_"),
    "CONTEXT": lambda k: k in ("sig_days_rest", "sig_series_game", "sig_getaway_day", "sig_bullpen_fatigue",
                               "sig_opp_catcher_framing", "sig_bullpen_era_diff"),
}
K_GROUPS = {
    "SKILL": lambda k: k.startswith("skill_"),
    "WORKLOAD": lambda k: k.startswith("work_"),
    "MATCH": lambda k: k.startswith("match_"),
    "PARK_WX": lambda k: k.startswith("park_") or k.startswith("wx_"),
    "RECENCY": lambda k: k.startswith("rec_"),
}


def sig(z):
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_logistic(X, y, lam=0.0, iters=60):
    """Newton-IRLS; X includes a leading intercept column (unpenalised)."""
    w = np.zeros(X.shape[1])
    P = np.eye(X.shape[1]) * lam
    P[0, 0] = 0.0
    for _ in range(iters):
        p = sig(X @ w)
        g = X.T @ (p - y) + P @ w
        H = (X * (p * (1 - p))[:, None]).T @ X + P + np.eye(X.shape[1]) * 1e-9
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


class Design:
    """Train-only median imputation + __missing indicators + standardization."""

    def __init__(self, rows, keys):
        self.keys = keys
        M = np.array([[np.nan if r["f"].get(k) is None else float(r["f"][k]) for k in keys] for r in rows])
        self.med = np.nanmedian(np.where(np.isfinite(M), M, np.nan), axis=0)
        self.med = np.where(np.isfinite(self.med), self.med, 0.0)
        self.miss = [j for j in range(len(keys)) if np.isnan(M[:, j]).any()]
        Z = self._raw(M)
        self.mu, self.sd = Z.mean(0), Z.std(0)
        self.sd[self.sd < 1e-12] = 1.0

    @staticmethod
    def matrix(rows, keys):
        return np.array([[np.nan if r["f"].get(k) is None else float(r["f"][k]) for k in keys] for r in rows])

    def _raw(self, M):
        filled = np.where(np.isnan(M), self.med, M)
        ind = np.isnan(M[:, self.miss]).astype(float) if self.miss else np.zeros((len(M), 0))
        return np.hstack([filled, ind])

    def X(self, rows):
        Z = (self._raw(self.matrix(rows, self.keys)) - self.mu) / self.sd
        return np.hstack([np.ones((len(rows), 1)), Z])


def losses(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return (p - y) ** 2, -(y * np.log(p) + (1 - y) * np.log(1 - p))


def cluster_ci(diff, clusters, rng):
    keys, inv = np.unique(clusters, return_inverse=True)
    s = np.bincount(inv, weights=diff, minlength=len(keys))
    c = np.bincount(inv, minlength=len(keys)).astype(float)
    draws = rng.integers(0, len(keys), size=(REPS, len(keys)))
    est = s[draws].sum(1) / c[draws].sum(1)
    return [float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))]


def cluster_sum_ci(per_cluster, rng):
    v = np.asarray(per_cluster, dtype=float)
    draws = rng.integers(0, len(v), size=(REPS, len(v)))
    tot = v[draws].sum(1)
    return [float(np.percentile(tot, 2.5)), float(np.percentile(tot, 97.5))]


def calib(p, y):
    lp = logit(p)
    w = fit_logistic(np.column_stack([np.ones_like(lp), lp]), y)
    bins = np.minimum((p * 10).astype(int), 9)
    ece = sum(abs(y[bins == b].mean() - p[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any())
    dec = np.quantile(p, np.linspace(0, 1, 11))
    rel = []
    for i in range(10):
        m = (p >= dec[i]) & (p <= dec[i + 1]) if i == 9 else (p >= dec[i]) & (p < dec[i + 1])
        if m.any():
            rel.append([float(p[m].mean()), float(y[m].mean()), int(m.sum())])
    return {"citl": float(y.mean() - p.mean()), "slope": float(w[1]), "ece": float(ece), "reliability_deciles": rel}


def summarize(p, y):
    b, l = losses(p, y)
    out = {"n": int(len(y)), "base_rate": float(y.mean()), "mean_p": float(p.mean()), "brier": float(b.mean()),
           "logloss": float(l.mean()), "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
           "p_quantiles": [float(x) for x in np.quantile(p, [0.05, 0.25, 0.5, 0.75, 0.95])]}
    out.update(calib(p, y))
    return out


def contrast(pa, pb, y, rows, rng):
    """b minus a; negative is better for b."""
    ba, la = losses(pa, y)
    bb, lb = losses(pb, y)
    dates = np.array([r["date"] for r in rows])
    players = np.array([r["player_id"] for r in rows])
    return {"brier": float((bb - ba).mean()), "brier_ci_date": cluster_ci(bb - ba, dates, rng),
            "logloss": float((lb - la).mean()), "logloss_ci_date": cluster_ci(lb - la, dates, rng),
            "logloss_ci_player": cluster_ci(lb - la, players, rng)}


def equal_n(p_base, p_ch, y, rows, frac, rng):
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r["date"]].append(i)
    added = removed = aw = rw = 0
    per_date = []
    for dt in sorted(by):
        idx = by[dt]
        n = max(1, round(frac * len(idx)))
        base = set(sorted(idx, key=lambda i: (-p_base[i], rows[i]["player_id"]))[:n])
        ch = set(sorted(idx, key=lambda i: (-p_ch[i], rows[i]["player_id"]))[:n])
        a, rm = ch - base, base - ch
        added += len(a); removed += len(rm)
        aw += int(sum(y[i] for i in a)); rw += int(sum(y[i] for i in rm))
        per_date.append(sum(y[i] for i in a) - sum(y[i] for i in rm))
    sel_n = sum(max(1, round(frac * len(v))) for v in by.values())
    base_w = sum(int(sum(y[i] for i in sorted(v, key=lambda i: (-p_base[i], rows[i]["player_id"]))[:max(1, round(frac * len(v)))])) for v in by.values())
    return {"frac": frac, "selected_per_arm": sel_n, "baseline_wins": base_w, "challenger_wins": base_w + aw - rw,
            "added": added, "added_wins": aw, "removed": removed, "removed_wins": rw, "net_wins": aw - rw,
            "net_ci_date": cluster_sum_ci(per_date, rng)}


def m3_prob_batter(rows, market):
    out = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        m = r["m3"]
        pd_ = m.get("pa_dist")
        if not pd_:
            continue
        if market == "hits" and m.get("q_hit") is not None:
            q = m["q_hit"]
            out[i] = sum(w * (1 - (1 - q) ** n) for n, w in enumerate(pd_))
        elif market == "total_bases" and m.get("comp"):
            c = m["comp"]
            pv = [0.0, c["s1"], c["d2"], c["d3"], c["hr"]]
            s = sum(pv[1:])
            if s >= 0.95:
                pv = [0.0] + [x * 0.95 / s for x in pv[1:]]
            pv[0] = 1 - sum(pv[1:])
            dist = {0: 1.0}
            p2 = 0.0
            for n in range(len(pd_)):
                if n > 0:
                    nxt = defaultdict(float)
                    for tb, ptb in dist.items():
                        for b, pb in enumerate(pv):
                            nxt[min(tb + b, 2)] += ptb * pb
                    dist = nxt
                p2 += pd_[n] * dist.get(2, 0.0)
            out[i] = p2
    return out


def run_market(name, rows, groups, target, base_key, frac, rng, m3_raw):
    keys = sorted({k for r in rows for k in r["f"]} if not isinstance(base_key, str) else
                  {k for r in rows for k in r["f"] if not k.startswith("base_logit")} | {base_key})
    if name.startswith("strikeouts"):
        keys = sorted([k for k in keys if not k.startswith("base_logit")] + [base_key])
    feat_keys = [k for k in keys if k != base_key]
    res = {"folds": {}}
    for fold, train_seasons, test_season in FOLDS:
        tr = [i for i, r in enumerate(rows) if r["season"] in train_seasons]
        te = [i for i, r in enumerate(rows) if r["season"] == test_season]
        R_tr, R_te = [rows[i] for i in tr], [rows[i] for i in te]
        y_tr = np.array([target(r) for r in R_tr], float)
        y_te = np.array([target(r) for r in R_te], float)
        preds = {}
        p0 = np.array([sig(r["f"][base_key]) for r in R_te])
        preds["M0"] = p0
        # M1
        x1 = lambda R: np.column_stack([np.ones(len(R)), [r["f"][base_key] for r in R]])  # noqa: E731
        w1 = fit_logistic(x1(R_tr), y_tr)
        preds["M1"] = sig(x1(R_te) @ w1)
        m1_tr = sig(x1(R_tr) @ w1)
        # M2
        D = Design(R_tr, [base_key] + feat_keys)
        w2 = fit_logistic(D.X(R_tr), y_tr, LAM)
        preds["M2"] = sig(D.X(R_te) @ w2)
        coefs = dict(zip(["intercept"] + D.keys + [f"{D.keys[j]}__missing" for j in D.miss], w2.tolist()))
        # M3
        raw_tr, raw_te = m3_raw(R_tr), m3_raw(R_te)
        ok_tr = np.isfinite(raw_tr)
        a3 = fit_logistic(np.column_stack([np.ones(ok_tr.sum()), logit(raw_tr[ok_tr])]), y_tr[ok_tr])
        p3 = sig(a3[0] + a3[1] * logit(np.where(np.isfinite(raw_te), raw_te, 0.5)))
        preds["M3"] = np.where(np.isfinite(raw_te), p3, preds["M1"])
        m3_cov = {"train_coverage": float(ok_tr.mean()), "test_coverage": float(np.isfinite(raw_te).mean()), "platt": a3.tolist()}
        # M4
        Mtr, Mte = Design.matrix(R_tr, [base_key] + feat_keys), Design.matrix(R_te, [base_key] + feat_keys)
        gb = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, min_samples_leaf=200,
                                            l2_regularization=1.0, early_stopping=False, random_state=SEED)
        gb.fit(Mtr, y_tr)
        preds["M4"] = gb.predict_proba(Mte)[:, 1]
        f = {"n_train": len(R_tr), "n_test": len(R_te), "m3": m3_cov,
             "models": {m: summarize(p, y_te) for m, p in preds.items()},
             "vs_M1": {m: contrast(preds["M1"], preds[m], y_te, R_te, rng) for m in ("M0", "M2", "M3", "M4")},
             "equal_n_vs_production_order": {m: equal_n(preds["M1"], preds[m], y_te, R_te, frac, rng) for m in ("M2", "M3", "M4")},
             "m2_standardized_coefficients": coefs}
        # ablations (GLM family)
        abl = {}
        for g, fn in groups.items():
            gk = [k for k in feat_keys if fn(k)]
            if not gk:
                continue
            Da = Design(R_tr, [base_key] + gk)
            pa_ = sig(Da.X(R_te) @ fit_logistic(Da.X(R_tr), y_tr, LAM))
            rest = [k for k in feat_keys if not fn(k)]
            Dd = Design(R_tr, [base_key] + rest)
            pd_ = sig(Dd.X(R_te) @ fit_logistic(Dd.X(R_tr), y_tr, LAM))
            abl[g] = {"features": gk, "add_to_M1": contrast(preds["M1"], pa_, y_te, R_te, rng),
                      "drop_from_M2": contrast(preds["M2"], pd_, y_te, R_te, rng)}
        Db = Design(R_tr, feat_keys)
        pb_ = sig(Db.X(R_te) @ fit_logistic(Db.X(R_tr), y_tr, LAM))
        abl["BASE"] = {"features": [base_key], "drop_from_M2": contrast(preds["M2"], pb_, y_te, R_te, rng)}
        f["ablation"] = abl
        # month stability: best-of {M2,M3,M4} vs M1 computed later from stored preds
        f["_preds"] = {m: p.tolist() for m, p in preds.items()}
        f["_y"] = y_te.tolist()
        f["_D"] = D
        f["_w2"] = w2
        res["folds"][fold] = f
    return res


def month_table(res, rows_by_fold, cand):
    out = {}
    for fold, f in res["folds"].items():
        R = rows_by_fold[fold]
        y = np.array(f["_y"])
        pa, pb = np.array(f["_preds"]["M1"]), np.array(f["_preds"][cand])
        _, la = losses(pa, y)
        _, lb = losses(pb, y)
        mm = defaultdict(list)
        for i, r in enumerate(R):
            mm[r["month"]].append(lb[i] - la[i])
        out[fold] = {m: [float(np.mean(v)), len(v)] for m, v in sorted(mm.items())}
    return out


def load(p):
    with open(p) as fh:
        return [json.loads(x) for x in fh]


def strip(res):
    for f in res["folds"].values():
        for k in ("_preds", "_y", "_D", "_w2"):
            f.pop(k, None)
    return res


def main():
    rng = np.random.default_rng(SEED)
    report = {"prereg_commit": "d2185cb4160e02b3e2feb9a626121c871103cc72", "markets": {}}
    batter = {"hits": load(OUT / "features_hits.jsonl"), "total_bases": load(OUT / "features_tb.jsonl")}
    fitted = {}
    for mk, rows in batter.items():
        rows.sort(key=lambda r: (r["date"], r["game_pk"], r["player_id"]))
        res = run_market(mk, rows, BATTER_GROUPS, lambda r: r["y"], "base_logit", 0.05, rng,
                         lambda R, mk=mk: m3_prob_batter(R, mk))
        fitted[mk] = res
        rbf = {fold: [r for r in rows if r["season"] == ts] for fold, _, ts in FOLDS}
        cand = min(("M2", "M3", "M4"), key=lambda m: res["folds"]["fold1_2024_to_2025"]["models"][m]["logloss"])
        res["candidate"] = cand
        res["month_delta_logloss_candidate_vs_M1"] = month_table(res, rbf, cand)
        # fair_test subset and fixed floor (hits only, NOT equal-volume)
        for fold, f in res["folds"].items():
            R = rbf[fold]
            y = np.array(f["_y"])
            ft = np.array([r["fair_test"] for r in R])
            f["fair_test_subset"] = {m: summarize(np.array(f["_preds"][m])[ft], y[ft]) for m in ("M1", cand)}
            if mk == "hits":
                fl = {}
                for m in ("M1", cand):
                    p = np.array(f["_preds"][m])
                    sel = p >= 0.60
                    fl[m] = {"n": int(sel.sum()), "wins": int(y[sel].sum()), "hit_rate": float(y[sel].mean()) if sel.any() else None}
                f["fixed_floor_0.60_NOT_equal_volume"] = fl
        report["markets"][mk] = res
    # cross-market direction test (hits vs total bases)
    cross = {}
    rows_h, rows_t = batter["hits"], batter["total_bases"]
    for own, other in (("hits", "total_bases"), ("total_bases", "hits")):
        cross[own] = {}
        R_own = batter[own]
        for fold, trs, ts in FOLDS:
            fo = fitted[other]["folds"][fold]
            Do, wo = fo["_D"], fo["_w2"]
            tr = [r for r in R_own if r["season"] in trs]
            te = [r for r in R_own if r["season"] == ts]
            eta_tr, eta_te = Do.X(tr) @ wo, Do.X(te) @ wo
            y_tr = np.array([r["y"] for r in tr], float)
            y_te = np.array([r["y"] for r in te], float)
            a = fit_logistic(np.column_stack([np.ones(len(tr)), eta_tr]), y_tr)
            p_cross = sig(a[0] + a[1] * eta_te)
            p_own = np.array(fitted[own]["folds"][fold]["_preds"]["M2"])
            cross[own][fold] = {"own_M2_minus_cross_direction": contrast(p_cross, p_own, y_te, te, rng),
                                "cross_logloss": summarize(p_cross, y_te)["logloss"],
                                "own_logloss": summarize(p_own, y_te)["logloss"]}
    report["hits_vs_total_bases_direction_test"] = cross
    # strikeouts
    krows = load(OUT / "features_k.jsonl")
    krows.sort(key=lambda r: (r["date"], r["game_pk"], r["player_id"]))
    kres = {}
    for t in (4, 5, 6):
        res = run_market(f"strikeouts_{t}", krows, K_GROUPS, lambda r, t=t: r["y"][str(t)], f"base_logit{t}", 0.10, rng,
                         lambda R, t=t: np.array([r["m3"][str(t)] for r in R], float))
        kres[t] = res
    cand_k = min(("M2", "M3", "M4"), key=lambda m: np.mean([kres[t]["folds"]["fold1_2024_to_2025"]["models"][m]["logloss"] for t in (4, 5, 6)]))
    rbf_k = {fold: [r for r in krows if r["season"] == ts] for fold, _, ts in FOLDS}
    for t in (4, 5, 6):
        kres[t]["candidate"] = cand_k
        kres[t]["month_delta_logloss_candidate_vs_M1"] = month_table(kres[t], rbf_k, cand_k)
        # post-hoc explanation only: early vs normal exits
        for fold, f in kres[t]["folds"].items():
            R = rbf_k[fold]
            y = np.array(f["_y"])
            early = np.array([r["outs"] < 12 for r in R])
            f["posthoc_exit_strata"] = {lab: {m: summarize(np.array(f["_preds"][m])[msk], y[msk]) for m in ("M1", cand_k)}
                                        for lab, msk in (("early_outs_lt_12", early), ("normal", ~early))}
        report["markets"][f"strikeouts_{t}"] = kres[t]
    # verdicts (PREREG §7)
    verdicts = {}

    def crit(res, cand):
        ll = [res["folds"][f]["vs_M1"][cand]["logloss_ci_date"][1] < 0 for f, _, _ in FOLDS]
        en = res["folds"]
        win = (en["fold1_2024_to_2025"]["equal_n_vs_production_order"][cand]["net_wins"] > 0 and
               en["fold2_2024_25_to_2026"]["equal_n_vs_production_order"][cand]["net_wins"] > 0 and
               en["fold2_2024_25_to_2026"]["equal_n_vs_production_order"][cand]["net_ci_date"][0] > 0)
        return all(ll), win
    for mk in ("hits", "total_bases"):
        ll, win = crit(report["markets"][mk], report["markets"][mk]["candidate"])
        verdicts[mk] = {"candidate": report["markets"][mk]["candidate"], "logloss_both_folds": ll, "equal_n_wins": win,
                        "verdict": "GO" if (ll and win) else ("MODIFY" if ll else "STOP")}
    kl = [crit(kres[t], cand_k) for t in (4, 5, 6)]
    n_ll, n_go = sum(a for a, _ in kl), sum(a and b for a, b in kl)
    verdicts["strikeouts"] = {"candidate": cand_k, "thresholds_logloss_both_folds": n_ll, "thresholds_go": n_go,
                              "verdict": "GO" if n_go >= 2 else ("MODIFY" if n_ll >= 2 else "STOP")}
    report["verdicts"] = verdicts
    for mk in report["markets"]:
        strip(report["markets"][mk])
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(verdicts, indent=1))


if __name__ == "__main__":
    main()
