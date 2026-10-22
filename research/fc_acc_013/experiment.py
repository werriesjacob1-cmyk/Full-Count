"""FC-ACC-013: workload-distribution strikeout experiment. Implements PREREG.md
(frozen at commit 0ace5fbb) exactly; no constant here was chosen after looking
at an outcome.

Inputs: out/starts.json from data.py. Outputs: out/report.json, out/rows.csv.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict, Counter
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backtest"))
import prop_probability as pp  # noqa: E402  (production, frozen sha in PREREG)
import calibration as cal  # noqa: E402

OUT = ROOT / "out"
THRESH = (4, 5, 6)
SEASONS = (2024, 2025, 2026)
# Production constants (PREREG §1/§3)
LEAGUE_BF, BF_N0, HALFLIFE, MIN_STARTS, MIN_RAW_BF, K_SHRINK = 23.1, 2.62, 30.0, 3, 40, 0.5
LEAGUE_WINDOW_DAYS, MIN_BF_FOR_START, LEAGUE_MIN_STARTS = 30, 15, 200
# Challenger constants (PREREG §4)
N_DEV, M_LEAGUE, CLAMP = 10, 5, (1.0, 45.0)
PROD_A, PROD_B = 1.374592801173212, -0.5084361336087595
SEED, B_REPS = 20261022, 2000


def d(s):
    return date.fromisoformat(s)


def build(starts):
    by_p = defaultdict(list)
    for s in starts:
        by_p[s["pitcher_id"]].append(s)
    for v in by_p.values():
        v.sort(key=lambda x: (x["date"], x["game_pk"]))
    # league pools, chronological
    all_sorted = sorted(starts, key=lambda x: (x["date"], x["game_pk"]))
    dates = sorted({s["date"] for s in starts})
    # per-start deviation from its pitcher's strictly-prior same-season mean BF (needs >=3 prior)
    dev_of = {}
    for v in by_p.values():
        for i, s in enumerate(v):
            hist = [x for x in v[:i] if x["season"] == s["season"] and x["date"] < s["date"]]
            if len(hist) >= 3:
                dev_of[(s["pitcher_id"], s["game_pk"])] = s["bf"] - sum(x["bf"] for x in hist) / len(hist)
    # cumulative integer-binned league deviation histogram strictly before each date
    hist_before, cum, j = {}, Counter(), 0
    for dt in dates:
        while j < len(all_sorted) and all_sorted[j]["date"] < dt:
            k = (all_sorted[j]["pitcher_id"], all_sorted[j]["game_pk"])
            if k in dev_of:
                cum[int(round(dev_of[k]))] += 1
            j += 1
        hist_before[dt] = dict(cum)
    league_rate_cache = {}

    def league_rates(dt, season):
        if dt in league_rate_cache:
            return league_rate_cache[dt]
        lo = d(dt).toordinal() - LEAGUE_WINDOW_DAYS
        pool = [s["ks"] for s in all_sorted if s["date"] < dt and d(s["date"]).toordinal() >= lo
                and s["bf"] >= MIN_BF_FOR_START]
        basis = "trailing30"
        if len(pool) < LEAGUE_MIN_STARTS:
            pool = [s["ks"] for s in all_sorted if s["date"] < dt and s["season"] == season
                    and s["bf"] >= MIN_BF_FOR_START]
            basis = "season_to_date"
        out = ({t: sum(k >= t for k in pool) / len(pool) for t in THRESH} if pool else None, basis, len(pool))
        league_rate_cache[dt] = out
        return out

    rows, excluded = [], Counter()
    for v in by_p.values():
        for i, cur in enumerate(v):
            if any(x["date"] == cur["date"] and x["game_pk"] != cur["game_pk"] for x in v):
                excluded["same_day_multiple_games"] += 1
                continue
            season_hist = [x for x in v[:i] if x["date"] < cur["date"] and x["season"] == cur["season"]]
            if len(season_hist) < 3:
                excluded["fewer_than_three_prior_season_starts"] += 1
                continue
            day = d(cur["date"])
            # k rate (production exp-weighted, strictly prior)
            num = den = 0.0
            raw_bf = 0
            for x in season_hist:
                w = 0.5 ** ((day - d(x["date"])).days / HALFLIFE)
                num += w * x["ks"]; den += w * x["bf"]; raw_bf += x["bf"]
            if len(season_hist) >= MIN_STARTS and raw_bf >= MIN_RAW_BF and den > 0:
                k, k_src = num / den, "exp_weighted"
            elif raw_bf > 0:
                k, k_src = sum(x["ks"] for x in season_hist) / raw_bf, "season_to_date"
            else:
                k, k_src = 0.225, "default"
            # expected BF (production project_pitcher_workload)
            l14 = [x for x in season_hist if 0 < (day - d(x["date"])).days <= 14]
            n = len(l14)
            if n:
                obs = sum(x["bf"] for x in l14) / n
                w = n / (n + BF_N0)
                ebf = min(30.0, max(12.0, w * obs + (1 - w) * LEAGUE_BF))
            else:
                ebf = LEAGUE_BF
            lr, lbasis, lpool = league_rates(cur["date"], cur["season"])
            if lr is None:
                excluded["no_league_rate"] += 1
                continue
            # challenger dispersion: pitcher deviations (last N) + league pool, re-centred
            last = season_hist[-N_DEV:]
            mu = sum(x["bf"] for x in last) / len(last)
            pairs = [(x["bf"] - mu, 1.0 / (len(last) + M_LEAGUE)) for x in last]
            lh = hist_before[cur["date"]]
            tot = sum(lh.values())
            if tot:
                lw = M_LEAGUE / (len(last) + M_LEAGUE)
                pairs += [(float(dv), lw * c / tot) for dv, c in lh.items()]
            else:  # no league pool yet: renormalize pitcher deviations
                s_ = sum(w for _, w in pairs)
                pairs = [(dv, w / s_) for dv, w in pairs]
            wsum = sum(w for _, w in pairs)
            cen = sum(dv * w for dv, w in pairs) / wsum
            draws = [(min(CLAMP[1], max(CLAMP[0], ebf + dv - cen)), w / wsum) for dv, w in pairs]
            clamp_hits = sum(1 for dv, _ in pairs if not (CLAMP[0] <= ebf + dv - cen <= CLAMP[1]))
            mix_mean = sum(b * w for b, w in draws)
            row = {"season": cur["season"], "date": cur["date"], "month": cur["date"][:7], "game_pk": cur["game_pk"],
                   "pitcher_id": cur["pitcher_id"], "ks": cur["ks"], "bf": cur["bf"], "outs": cur["outs"],
                   "k_rate": k, "k_src": k_src, "ebf": ebf, "l14_n": n, "prior_starts": len(season_hist),
                   "dev_n": len(last), "prior_bf_sd": float(np.std([x["bf"] for x in last])),
                   "mix_mean": mix_mean, "clamp_hits": clamp_hits, "league_basis": lbasis, "league_pool": lpool}
            for t in THRESH:
                ma = pp._binom_at_least(ebf, k, t)
                mb = sum(w * pp._binom_at_least(b, k, t) for b, w in draws)
                row[f"lg{t}"] = lr[t]
                row[f"mA{t}"], row[f"mB{t}"] = ma, mb
                row[f"A{t}"] = K_SHRINK * lr[t] + (1 - K_SHRINK) * ma
                row[f"B{t}"] = K_SHRINK * lr[t] + (1 - K_SHRINK) * mb
                row[f"y{t}"] = int(cur["ks"] >= t)
            rows.append(row)
    return rows, dict(excluded)


def prod_calibrate(p, bins):
    return 1 / (1 + math.exp(-(PROD_A * p + PROD_B))) if cal.in_support(p, bins) else p


def brier(p, y):
    return (p - y) ** 2


def logloss(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def boot(diffs, clusters, rng):
    keys = sorted(set(clusters))
    idx = {k: i for i, k in enumerate(keys)}
    s = np.zeros(len(keys)); c = np.zeros(len(keys))
    for dv, k in zip(diffs, clusters):
        s[idx[k]] += dv; c[idx[k]] += 1
    draws = rng.integers(0, len(keys), size=(B_REPS, len(keys)))
    est = s[draws].sum(1) / c[draws].sum(1)
    return [float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))]


def evaluate(rows, arms, rng):
    """arms: {"A": key-fn(row,t)->p, "B": ...}"""
    out = {}
    for t in THRESH:
        y = [r[f"y{t}"] for r in rows]
        pa = [arms["A"](r, t) for r in rows]
        pb = [arms["B"](r, t) for r in rows]
        res = {"n": len(rows), "base_rate": sum(y) / len(y) if y else None}
        for name, f in (("brier", brier), ("logloss", logloss)):
            la = [f(p, yy) for p, yy in zip(pa, y)]
            lb = [f(p, yy) for p, yy in zip(pb, y)]
            dif = [b - a for a, b in zip(la, lb)]
            res[name] = {"A": sum(la) / len(la), "B": sum(lb) / len(lb), "B_minus_A": sum(dif) / len(dif),
                         "ci_pitcher": boot(dif, [r["pitcher_id"] for r in rows], rng),
                         "ci_game": boot(dif, [r["game_pk"] for r in rows], rng)}
        res["mean_p"] = {"A": sum(pa) / len(pa), "B": sum(pb) / len(pb)}
        ca = [int(p >= 0.5) == yy for p, yy in zip(pa, y)]
        cb = [int(p >= 0.5) == yy for p, yy in zip(pb, y)]
        res["classification_p_ge_0.5"] = {
            "A_correct": sum(ca), "A_incorrect": len(ca) - sum(ca), "B_correct": sum(cb), "B_incorrect": len(cb) - sum(cb),
            "only_B_correct": sum(1 for a, b in zip(ca, cb) if b and not a),
            "only_A_correct": sum(1 for a, b in zip(ca, cb) if a and not b)}
        dec = {}
        for floor in (0.55, 0.60, 0.65):
            added = [yy for p1, p2, yy in zip(pa, pb, y) if p1 < floor <= p2]
            removed = [yy for p1, p2, yy in zip(pa, pb, y) if p2 < floor <= p1]
            dec[str(floor)] = {"A_over": sum(p >= floor for p in pa), "B_over": sum(p >= floor for p in pb),
                               "A_over_wins": sum(yy for p, yy in zip(pa, y) if p >= floor),
                               "B_over_wins": sum(yy for p, yy in zip(pb, y) if p >= floor),
                               "added": len(added), "added_wins": sum(added),
                               "removed": len(removed), "removed_wins": sum(removed)}
        res["over_decisions"] = dec
        out[t] = res
    return out


def by_group(rows, arms, keyfn):
    g = defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    out = {}
    for k in sorted(g):
        rr = g[k]
        out[str(k)] = {}
        for t in THRESH:
            y = [r[f"y{t}"] for r in rr]
            pa = [arms["A"](r, t) for r in rr]
            pb = [arms["B"](r, t) for r in rr]
            out[str(k)][t] = {"n": len(rr), "realized": sum(y) / len(y), "mean_A": sum(pa) / len(pa),
                              "mean_B": sum(pb) / len(pb),
                              "logloss_B_minus_A": sum(logloss(b, yy) - logloss(a, yy) for a, b, yy in zip(pa, pb, y)) / len(y),
                              "brier_B_minus_A": sum(brier(b, yy) - brier(a, yy) for a, b, yy in zip(pa, pb, y)) / len(y)}
    return out


def main():
    starts = json.loads((OUT / "starts.json").read_text())
    rows, excluded = build(starts)
    rng = np.random.default_rng(SEED)
    by_year = {y: [r for r in rows if r["season"] == y] for y in SEASONS}
    prod_bins = json.loads((REPO / "backtest" / "calibrators_by_market.json").read_text())["strikeouts"]["meta"]["support_bins"]
    # C2: per-arm Platt on 2024 pooled over thresholds, identical procedure, support gate from 2024 rows
    c2 = {}
    for arm in ("A", "B"):
        p = np.array([r[f"{arm}{t}"] for r in by_year[2024] for t in THRESH])
        yv = np.array([r[f"y{t}"] for r in by_year[2024] for t in THRESH])
        A_, B_ = cal._fit_platt(p, yv)
        bins = cal.compute_support_bins([{"predicted_prob": float(x)} for x in p])
        c2[arm] = (float(A_), float(B_), bins)

    def c2f(arm):
        A_, B_, bins = c2[arm]
        return lambda r, t: (1 / (1 + math.exp(-(A_ * r[f"{arm}{t}"] + B_))) if cal.in_support(r[f"{arm}{t}"], bins)
                             else r[f"{arm}{t}"])
    treatments = {
        "C0_raw_shrunk": {"A": lambda r, t: r[f"A{t}"], "B": lambda r, t: r[f"B{t}"]},
        "C1_production_platt": {"A": lambda r, t: prod_calibrate(r[f"A{t}"], prod_bins),
                                "B": lambda r, t: prod_calibrate(r[f"B{t}"], prod_bins)},
        "C2_platt_fit_2024_per_arm": {"A": c2f("A"), "B": c2f("B")},
        "M_unshrunk_model_component": {"A": lambda r, t: r[f"mA{t}"], "B": lambda r, t: r[f"mB{t}"]},
    }
    report = {"status": "RETROSPECTIVE_EXPLORATORY_OUTCOME_ONLY", "prereg_commit": "0ace5fbb23bd3adaa279b91affb39e0ab19b31d9",
              "excluded": excluded, "rows_by_year": {y: len(by_year[y]) for y in SEASONS},
              "c2_params": {a: {"A": c2[a][0], "B": c2[a][1]} for a in c2},
              "coverage": {y: {"k_src": Counter(r["k_src"] for r in by_year[y]),
                               "l14_empty": sum(r["l14_n"] == 0 for r in by_year[y]),
                               "dev_n": Counter(r["dev_n"] for r in by_year[y]),
                               "league_basis": Counter(r["league_basis"] for r in by_year[y]),
                               "rows_with_clamp_hits": sum(r["clamp_hits"] > 0 for r in by_year[y]),
                               "mean_abs_mix_mean_minus_ebf": float(np.mean([abs(r["mix_mean"] - r["ebf"]) for r in by_year[y]]))}
                         for y in SEASONS},
              "mean_workload": {y: {"mean_ebf": float(np.mean([r["ebf"] for r in by_year[y]])),
                                    "mean_actual_bf": float(np.mean([r["bf"] for r in by_year[y]])),
                                    "sd_actual_minus_ebf": float(np.std([r["bf"] - r["ebf"] for r in by_year[y]]))}
                                for y in SEASONS},
              "results": {}}
    for name, arms in treatments.items():
        report["results"][name] = {}
        for y in SEASONS:
            rr = by_year[y]
            report["results"][name][y] = {
                "overall": evaluate(rr, arms, rng),
                "by_month": by_group(rr, arms, lambda r: r["month"]),
                "by_ebf_bin": by_group(rr, arms, lambda r: "<20" if r["ebf"] < 20 else ("20-22" if r["ebf"] < 22 else ("22-24" if r["ebf"] < 24 else "24+"))),
                "by_prior_bf_sd_tercile": None,
                "posthoc_by_actual_exit": by_group(rr, arms, lambda r: "early_exit_outs_lt_12" if r["outs"] < 12 else "normal_outs_ge_12"),
            }
            sds = sorted(r["prior_bf_sd"] for r in rr)
            q1, q2 = sds[len(sds) // 3], sds[2 * len(sds) // 3]
            report["results"][name][y]["by_prior_bf_sd_tercile"] = by_group(
                rr, arms, lambda r: f"T1_sd<{q1:.2f}" if r["prior_bf_sd"] < q1 else (f"T2_sd<{q2:.2f}" if r["prior_bf_sd"] < q2 else f"T3_sd>={q2:.2f}"))
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=lambda o: dict(o) if isinstance(o, Counter) else str(o)))
    with (OUT / "rows.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(json.dumps({k: report[k] for k in ("excluded", "rows_by_year", "c2_params", "mean_workload")}, indent=1))


if __name__ == "__main__":
    main()
