#!/usr/bin/env python3
"""Is the combined-strikeouts model's distribution too narrow? EXPLORATORY.

score_combined_strikeouts prices each FanDuel rung with a sum of two
independent binomials at fixed expected batters faced. For every graded
regular-season row we recover that distribution's mean and SD from its own
ladder (the posted rung plus its stored alternatives), using the normal
approximation with continuity correction:
    P(X >= t) = 1 - Phi((t - 0.5 - mu) / sigma)
fitted by least squares on the probit scale (needs >= 2 rungs). Then:
- z = (actual - mu) / sigma. A correct model gives mean(z) ~ 0, var(z) ~ 1.
- Forward-chained fix: on each ISO week, fit a bias b and a spread factor s
  on earlier weeks only (maximum likelihood on the realized counts under
  N(mu + b, (s*sigma)^2) with continuity correction), then score the posted
  rung with the adjusted distribution. Compare its log loss with the raw
  model's and with the posted price's.

    python3 engineering/mlb_accuracy_forward_chain_20261001/cs_dispersion.py
"""
from __future__ import annotations

import json
import math
import os
from statistics import NormalDist, fmean, pvariance

import numpy as np

from forward_chain import DATA_SHA, FIRST, LAST, git_json, git_ls, implied, ll, monday

HERE = os.path.dirname(os.path.abspath(__file__))
N = NormalDist()


def load():
    out = []
    for path in sorted(git_ls("results")):
        base = os.path.basename(path)
        if not (base.startswith("grades_") and base.endswith(".json")):
            continue
        d = base[len("grades_"):-5]
        if not FIRST <= d <= LAST:
            continue
        for x in git_json(path).get("picks") or []:
            proj = x.get("projection") or {}
            if proj.get("stat") != "combined_strikeouts" or x.get("actual") is None:
                continue
            if x.get("grade") not in ("hit", "miss") or x.get("market_odds") is None:
                continue
            rungs = [(proj["needs"], x["hit_probability"])]
            rungs += [(a["needs"], a["prob"]) for a in x.get("alternatives") or []
                      if a.get("stat") == "combined_strikeouts" and a.get("prob") is not None]
            out.append({"date": d, "game_pk": x.get("game_pk"), "needs": proj["needs"], "p0": x["hit_probability"],
                        "q": implied(x["market_odds"]), "odds": x["market_odds"], "y": 1 if x["grade"] == "hit" else 0,
                        "actual": float(x["actual"]), "rungs": sorted(set(rungs))})
    return out


def recover(rungs):
    """mu, sigma from P(X >= t) = 1 - Phi((t - 0.5 - mu)/sigma); probit LS."""
    pts = [(t - 0.5, N.inv_cdf(min(max(1 - p, 1e-6), 1 - 1e-6))) for t, p in rungs]
    if len({x for x, _ in pts}) < 2:
        return None
    x = np.array([p[0] for p in pts])
    zq = np.array([p[1] for p in pts])
    slope, intercept = np.polyfit(x, zq, 1)  # z = (x - mu)/sigma => slope=1/sigma, intercept=-mu/sigma
    if slope <= 0:
        return None
    sigma = 1.0 / slope
    return -intercept * sigma, sigma


def p_at_least(t, mu, sigma):
    return 1 - N.cdf((t - 0.5 - mu) / sigma)


def point_ll(actual, mu, sigma):
    lo, hi = N.cdf((actual - 0.5 - mu) / sigma), N.cdf((actual + 0.5 - mu) / sigma)
    return -math.log(max(hi - lo, 1e-9))


def fit_bias_spread(rows):
    best = None
    for b in np.arange(-3.0, 3.01, 0.1):
        for s in np.arange(0.8, 2.51, 0.05):
            nll = sum(point_ll(r["actual"], r["mu"] + b, s * r["sigma"]) for r in rows)
            if best is None or nll < best[0]:
                best = (nll, float(b), float(s))
    return best[1], best[2]


def main():
    rows = []
    dropped = 0
    for r in load():
        rec = recover(r["rungs"])
        if rec is None:
            dropped += 1
            continue
        r["mu"], r["sigma"] = rec
        r["z"] = (r["actual"] - r["mu"]) / r["sigma"]
        rows.append(r)
    z = [r["z"] for r in rows]
    weeks = sorted({monday(r["date"]) for r in rows})
    tested, folds = [], []
    for wk in weeks[2:]:
        train = [r for r in rows if monday(r["date"]) < wk]
        test = [r for r in rows if monday(r["date"]) == wk]
        if len(train) < 20 or not test:
            continue
        b, s = fit_bias_spread(train)
        folds.append({"week_of": wk.isoformat(), "n_train": len(train), "n_test": len(test), "bias": b, "spread": s})
        for r in test:
            tested.append({**r, "p_fix": p_at_least(r["needs"], r["mu"] + b, s * r["sigma"])})

    def arm(name):
        return fmean(ll(r[name], r["y"]) for r in tested) if tested else None

    report = {"evidence_class": "EXPLORATORY_DESCRIPTIVE", "data_sha": DATA_SHA, "n_rows": len(rows),
              "n_dropped_fewer_than_2_rungs": dropped,
              "z": {"mean": fmean(z), "variance": pvariance(z), "share_abs_gt_2": sum(abs(v) > 2 for v in z) / len(z)},
              "mean_model_mu": fmean(r["mu"] for r in rows), "mean_actual": fmean(r["actual"] for r in rows),
              "mean_model_sigma": fmean(r["sigma"] for r in rows),
              "realized_sd_of_actual_minus_mu": pvariance([r["actual"] - r["mu"] for r in rows]) ** 0.5,
              "full_sample_bias_spread": dict(zip(("bias", "spread"), fit_bias_spread(rows))),
              "forward_chain": {"folds": folds, "n_tested": len(tested),
                                "log_loss": {"model_p0": arm("p0"), "price_q": arm("q"), "fixed_p_fix": arm("p_fix")},
                                "mean_stated": {k: fmean(r[k] for r in tested) for k in ("p0", "q", "p_fix")} if tested else None,
                                "realized": fmean(r["y"] for r in tested) if tested else None}}
    with open(os.path.join(HERE, "cs_dispersion_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
