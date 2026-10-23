#!/usr/bin/env python3
"""FC-ACC-019 analysis (PREREG §3-6). R1 = published Top Picks; R2 = retained pregame candidates
graded by official StatsAPI logs; R3 = frozen FC-ACC-015 challengers on covered R2 rows.
The only fitted object is the preregistered 2-3 parameter diagnostic logistic control (§4.2).

Usage: python3 analysis.py <repo root> <raw StatsAPI dir> <fc_acc_014 out dir> <frozen_models.json>
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HOLD, REPS, SEED = 1.08, 2000, 20261025
BINS = (("L+ (+200 or longer)", 200, 10 ** 6), ("P (+100..+199)", 100, 199), ("E (-100..-149)", -149, -100),
        ("M (-150..-199)", -199, -150), ("F (-200..-299)", -299, -200), ("X (-300 or shorter)", -10 ** 6, -300))
SUPPORT = 300


def odds_bin(a):
    for name, lo, hi in BINS:
        if lo <= a <= hi:
            return name
    return "unbinned"


def raw_implied(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def dec(a):
    return 1 + (100 / -a if a < 0 else a / 100)


def lg(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


# ------------------------------------------------------------------ R1
def load_r1(repo):
    reg_p = Path(repo) / "data/public_top_picks/registry.json"
    reg = json.loads(reg_p.read_text())["entries"]
    grades, seen = {}, Counter()
    for f in sorted(glob.glob(str(Path(repo) / "results/grades_*.json"))):
        for p in json.loads(Path(f).read_text()).get("public_top_picks") or []:
            grades[p["id"]] = p.get("grade")
            seen[p["id"]] += 1
    hist = json.loads((Path(repo) / "results/history.json").read_text()).get("public_top_pick_totals")
    c = Counter(grades.get(k) for k in reg)
    recon = {"registry_entries": len(reg), "graded_ids": len(grades), "duplicates": sum(v > 1 for v in seen.values()),
             "ungraded_registry_ids": sum(1 for k in reg if k not in grades), "grades": dict(c), "history_totals": hist,
             "history_matches": (c.get("hit"), c.get("miss"), c.get("void"), c.get("ungraded")) ==
                                (hist.get("hits"), hist.get("misses"), hist.get("voids"), hist.get("ungraded")),
             "registry_sha256": hashlib.sha256(reg_p.read_bytes()).hexdigest()}
    rows = []
    for k, e in reg.items():
        g = grades.get(k)
        if g not in ("hit", "miss"):
            continue
        s = e["snapshot"]
        raw = raw_implied(s["market_odds"])
        m = s.get("market_implied")
        nv, basis = ((m, "recorded_fair") if m and abs(raw / m - 1) > 0.002 else (raw / HOLD, "assumed_hold"))
        rows.append({"slate": e["slate_date"], "game_pk": int(s["game_pk"]), "player_id": s.get("player_id"),
                     "stat": s["projection"]["stat"], "american": s["market_odds"], "raw_implied": raw, "no_vig": nv,
                     "no_vig_basis": basis, "model": s["hit_probability"], "y": 1.0 if g == "hit" else 0.0})
    return rows, recon


# ------------------------------------------------------------------ R2 grading
def logs(raw):
    bat, pit = {}, {}
    for kind, store in (("blogs", bat), ("plogs", pit)):
        for f in sorted(glob.glob(str(Path(raw) / f"{kind}_2026_*.json"))):
            for person in json.loads(Path(f).read_text()).get("people", []):
                for block in person.get("stats", []):
                    for s in block.get("splits", []):
                        if s.get("gameType") == "R":
                            store[(int(s["game"]["gamePk"]), int(person["id"]))] = s["stat"]
    return bat, pit


def stat_value(stat, s):
    g = lambda k: int(s.get(k) or 0)  # noqa: E731
    return {"hits": g("hits"), "hits_runs_rbis": g("hits") + g("runs") + g("rbi"), "total_bases": g("totalBases"),
            "singles": g("hits") - g("doubles") - g("triples") - g("homeRuns"), "doubles": g("doubles"),
            "triples": g("triples"), "home_runs": g("homeRuns"), "runs": g("runs"), "rbis": g("rbi"),
            "stolen_base": g("stolenBases"), "strikeouts": g("strikeOuts"), "pitcher_outs": g("outs")}[stat]


def grade(row, bat, pit):
    key = (int(row["game_pk"]), int(row["player_id"]))
    if row["stat"] in ("strikeouts", "pitcher_outs"):
        s = pit.get(key)
        if not s or int(s.get("gamesStarted") or 0) != 1:
            return None
    else:
        s = bat.get(key)
        if not s or int(s.get("plateAppearances") or 0) == 0:
            return None
    return 1.0 if stat_value(row["stat"], s) >= int(row["needs"]) else 0.0


def load_r2(raw):
    C = json.loads((HERE / "out" / "candidates.json").read_text())["candidates"]
    bat, pit = logs(raw)
    rows, void = [], 0
    for c in C:
        if c["model"] is None or c["player_id"] is None:
            continue
        y = grade(c, bat, pit)
        if y is None:
            void += 1
            continue
        rows.append({**c, "y": y})
    return rows, void


# ------------------------------------------------------------------ statistics
def fit(X, y, iters=50):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        q = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        H = (X * (q * (1 - q))[:, None]).T @ X + 1e-6 * np.eye(X.shape[1])
        w -= np.linalg.solve(H, X.T @ (q - y))
    return w


def ll(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def segment_stats(R, rng, fit_control=True):
    y = np.array([r["y"] for r in R]); m = np.array([r["model"] for r in R]); nv = np.array([r["no_vig"] for r in R])
    raw = np.array([r["raw_implied"] for r in R]); od = np.array([r["american"] for r in R], float)
    prof = np.where(y == 1, np.array([dec(a) - 1 for a in od]), -1.0)
    games = np.array([f"{r['slate']}:{r['game_pk']}" for r in R])
    gk, gi = np.unique(games, return_inverse=True)

    def gboot(v):
        s = np.bincount(gi, weights=v, minlength=len(gk)); c = np.bincount(gi, minlength=len(gk)).astype(float)
        d = rng.integers(0, len(gk), (REPS, len(gk)))
        e = s[d].sum(1) / c[d].sum(1)
        return [float(np.percentile(e, 2.5)), float(np.percentile(e, 97.5))]
    out = {"n": len(R), "games": len(gk), "players": len({r["player_id"] for r in R}), "slates": len({r["slate"] for r in R}),
           "hit_rate": float(y.mean()), "mean_model": float(m.mean()), "mean_raw_breakeven": float(raw.mean()),
           "mean_no_vig": float(nv.mean()), "no_vig_exact_share": float(np.mean([r["no_vig_basis"] != "assumed_hold" for r in R])),
           "roi_unauthenticated": float(prof.mean()), "roi_ci_game": gboot(prof),
           "logloss": {"model": float(ll(m, y).mean()), "no_vig": float(ll(nv, y).mean()), "raw": float(ll(raw, y).mean())},
           "brier": {"model": float(np.mean((m - y) ** 2)), "no_vig": float(np.mean((nv - y) ** 2))},
           "citl": {"model": float(y.mean() - m.mean()), "no_vig": float(y.mean() - nv.mean())},
           "model_minus_novig_logloss_ci_game": gboot(ll(m, y) - ll(nv, y)),
           "top_stats": dict(Counter(r["stat"] for r in R).most_common(4)),
           "max_rows_per_game": int(np.bincount(gi).max())}
    if fit_control and len(R) >= SUPPORT:
        slates = sorted({r["slate"] for r in R}); med = slates[len(slates) // 2]
        tr = np.array([r["slate"] < med for r in R])
        X0 = np.column_stack([np.ones(len(R)), lg(nv)])
        X1 = np.column_stack([X0, lg(m) - lg(nv)])
        w0, w1 = fit(X0[tr], y[tr]), fit(X1[tr], y[tr])
        p0 = 1 / (1 + np.exp(-X0[~tr] @ w0)); p1 = 1 / (1 + np.exp(-X1[~tr] @ w1))
        dl = ll(p1, y[~tr]) - ll(p0, y[~tr])
        gte = gi[~tr]; uk = np.unique(gte); remap = {g: i for i, g in enumerate(uk)}
        idx = np.array([remap[g] for g in gte])
        s = np.bincount(idx, weights=dl, minlength=len(uk)); c = np.bincount(idx, minlength=len(uk)).astype(float)
        dd = rng.integers(0, len(uk), (REPS, len(uk)))
        e = s[dd].sum(1) / c[dd].sum(1)
        wf = fit(X1, y)
        cs = []
        for _ in range(300):                                  # game-clustered bootstrap of the disagreement coefficient
            pick = rng.integers(0, len(gk), len(gk))
            sel = np.concatenate([np.where(gi == g)[0] for g in pick])
            cs.append(fit(X1[sel], y[sel], iters=25)[2])
        out["control"] = {"split_slate": med, "n_train": int(tr.sum()), "n_test": int((~tr).sum()),
                          "oos_delta_logloss": float(dl.mean()),
                          "oos_delta_logloss_ci_game": [float(np.percentile(e, 2.5)), float(np.percentile(e, 97.5))],
                          "disagreement_coef": float(wf[2]),
                          "disagreement_coef_ci_game": [float(np.percentile(cs, 2.5)), float(np.percentile(cs, 97.5))],
                          "market_coef": float(wf[1])}
        out["credible_incremental_information"] = bool(out["control"]["disagreement_coef_ci_game"][0] > 0
                                                         and out["control"]["oos_delta_logloss_ci_game"][1] < 0)
    gap = lg(m) - lg(nv)
    deciles = []
    for lo, hi in ((-9, -0.2), (-0.2, 0), (0, 0.2), (0.2, 0.4), (0.4, 9)):
        k = (gap >= lo) & (gap < hi)
        if k.sum():
            deciles.append({"logit_gap": [lo, hi], "n": int(k.sum()), "hit": float(y[k].mean()),
                            "no_vig": float(nv[k].mean()), "excess_vs_market": float(y[k].mean() - nv[k].mean()),
                            "roi": float(prof[k].mean())})
    out["disagreement_bands"] = deciles
    return out


def by_bins(R, rng, fit_control=True):
    res = {}
    for name, _, _ in BINS:
        sub = [r for r in R if odds_bin(r["american"]) == name]
        res[name] = segment_stats(sub, rng, fit_control) if sub else {"n": 0}
        if sub:
            months = defaultdict(list)
            for r in sub:
                months[r["slate"][:7]].append(r["y"])
            res[name]["by_month_hit"] = {k: [float(np.mean(v)), len(v)] for k, v in sorted(months.items())}
    return res


# ------------------------------------------------------------------ R3 + ranking diagnostics
def r3(R2, feat_dir, frozen_path):
    models = json.loads(Path(frozen_path).read_text())
    out = {}
    for mk, fn in (("hits", "features_hits.jsonl"), ("total_bases", "features_tb.jsonl")):
        need = 1 if mk == "hits" else 2
        F = {}
        with open(Path(feat_dir) / fn) as fh:
            for line in fh:
                r = json.loads(line)
                if r["date"] >= "2026-08-18":
                    r.pop("y", None)
                    F[(r["date"], int(r["game_pk"]), int(r["player_id"]))] = r["f"]
        e = models[mk]; keys = e["keys"]
        med, mu, sd, coef = (np.array(e[k]) for k in ("median", "mu", "sd", "coef"))
        miss = [keys.index(k) for k in e["missing_indicator_for"]]
        rows = []
        for r in R2:
            if r["stat"] != mk or int(r["needs"]) != need:
                continue
            f = F.get((r["slate"], int(r["game_pk"]), int(r["player_id"])))
            if f is None:
                continue
            x = np.array([np.nan if f.get(k) is None else float(f[k]) for k in keys])
            z = (np.concatenate([np.where(np.isnan(x), med, x), np.isnan(x[miss]).astype(float)]) - mu) / sd
            rows.append((r["y"], float(1 / (1 + np.exp(-(coef[0] + z @ coef[1:])))), r["no_vig"], r["model"]))
        if rows:
            a = np.array(rows)
            out[mk] = {"n": len(rows), "hit": float(a[:, 0].mean()), "logloss_challenger": float(ll(a[:, 1], a[:, 0]).mean()),
                       "logloss_no_vig": float(ll(a[:, 2], a[:, 0]).mean()), "logloss_production": float(ll(a[:, 3], a[:, 0]).mean()),
                       "mean_challenger": float(a[:, 1].mean()), "mean_no_vig": float(a[:, 2].mean())}
        else:
            out[mk] = {"n": 0}
    return out


def ranking(R2, rng, k=3):
    by = defaultdict(list)
    for r in R2:
        by[r["slate"]].append(r)
    keys = {"no_vig_prob": lambda r: -r["no_vig"], "model_prob": lambda r: -r["model"],
            "model_minus_breakeven": lambda r: -(r["model"] - r["raw_implied"])}
    out = {}
    for name, kf in keys.items():
        sel = []
        short = 0
        for d, rs in sorted(by.items()):
            pick, pl, gm = [], set(), Counter()
            for r in sorted(rs, key=lambda r: (kf(r), str(r["id"]))):
                if len(pick) == k:
                    break
                if r["player_id"] in pl or gm[r["game_pk"]] >= 2:
                    continue
                pick.append(r); pl.add(r["player_id"]); gm[r["game_pk"]] += 1
            short += k - len(pick)
            sel += pick
        st = segment_stats(sel, rng, fit_control=False)
        st["shortfall"] = short
        st["odds_quantiles"] = [float(x) for x in np.percentile([r["american"] for r in sel], [0, 25, 50, 75, 100])]
        st["market_expected_wins"] = float(sum(r["no_vig"] for r in sel))
        st["wins"] = int(sum(r["y"] for r in sel))
        st.pop("disagreement_bands", None)
        out[name] = st
    return out


def main(repo, raw, feat_dir, frozen):
    rng = np.random.default_rng(SEED)
    R1, recon = load_r1(repo)
    R2, void2 = load_r2(raw)
    res = {"R1_reconciliation": recon, "R1_overall": segment_stats(R1, rng), "R1_bins": by_bins(R1, rng),
           "R2_void_or_ungraded": void2, "R2_overall": segment_stats(R2, rng), "R2_bins": by_bins(R2, rng),
           "R2_market_by_bin": {}, "R3": r3(R2, feat_dir, frozen), "R2_ranking_k3": ranking(R2, rng)}
    for mk in sorted({r["stat"] for r in R2}):
        sub = [r for r in R2 if r["stat"] == mk]
        if len(sub) >= SUPPORT:
            res["R2_market_by_bin"][mk] = {"overall": segment_stats(sub, rng),
                                           "bins": {b: (segment_stats(s, rng) if len(s) >= SUPPORT else {"n": len(s)})
                                                    for b in [x[0] for x in BINS]
                                                    for s in [[r for r in sub if odds_bin(r["american"]) == b]]}}
        else:
            res["R2_market_by_bin"][mk] = {"overall": {"n": len(sub)}}
    supported = []
    for scope, seg in [("R2 all", res["R2_overall"])] + [(f"R2 {b}", v) for b, v in res["R2_bins"].items()] + \
            [(f"R2 {mk}", v["overall"]) for mk, v in res["R2_market_by_bin"].items()] + \
            [(f"R2 {mk} {b}", s) for mk, v in res["R2_market_by_bin"].items() for b, s in v.get("bins", {}).items()]:
        if seg.get("n", 0) >= SUPPORT and "credible_incremental_information" in seg:
            supported.append({"segment": scope, "n": seg["n"], "credible": seg["credible_incremental_information"],
                              "coef": seg["control"]["disagreement_coef"], "coef_ci": seg["control"]["disagreement_coef_ci_game"],
                              "oos_dll": seg["control"]["oos_delta_logloss"], "oos_dll_ci": seg["control"]["oos_delta_logloss_ci_game"]})
    res["supported_segments"] = supported
    res["verdict"] = ("MODIFY" if any(s["credible"] for s in supported) else "STOP — NO DEMONSTRATED EDGE")
    (HERE / "out" / "analysis.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    r = main(*sys.argv[1:5])
    print(json.dumps(r["R1_reconciliation"], indent=1))
    for s in r["supported_segments"]:
        print(s)
    print(r["verdict"])
