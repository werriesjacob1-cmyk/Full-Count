#!/usr/bin/env python3
"""Where does the MLB model's probability lose to the posted price?
Descriptive mechanism follow-up to forward_chain.py. EXPLORATORY.

For final-run board picks (results/grades_*.json, regular season, hit/miss,
priced), it groups rows by probability basis, calibration source, sample size
and model-minus-price disagreement, and reports for each group: n, mean
stated model probability, mean posted implied probability (q), realized hit
rate, and the log loss of the model vs the price. No fitting.

    python3 engineering/mlb_accuracy_forward_chain_20261001/diagnose.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from forward_chain import DATA_SHA, FIRST, LAST, family, git_json, git_ls, implied, ll, y_of

HERE = os.path.dirname(os.path.abspath(__file__))
DISAGREE = [(-1.0, -0.10), (-0.10, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)]
SAMPLE = [(0, 20), (20, 50), (50, 100), (100, 10 ** 9)]


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
            y, p, odds = y_of(x.get("grade")), x.get("hit_probability"), x.get("market_odds")
            stat = (x.get("projection") or {}).get("stat")
            if x.get("recommendation_status") is None or y is None or p is None or odds is None:
                continue
            out.append({"date": d, "family": family(stat), "stat": stat, "p0": p, "q": implied(odds), "y": y,
                        "basis": x.get("probability_basis"), "calibrated_by": x.get("calibrated_by"),
                        "sample_n": x.get("sample_n"), "status": x.get("recommendation_status")})
    return out


def summarize(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    return {"n": n, "stated_model": sum(r["p0"] for r in rows) / n, "price_q": sum(r["q"] for r in rows) / n,
            "realized": sum(r["y"] for r in rows) / n,
            "ll_model": sum(ll(r["p0"], r["y"]) for r in rows) / n,
            "ll_price": sum(ll(r["q"], r["y"]) for r in rows) / n,
            "ll_model_minus_price": sum(ll(r["p0"], r["y"]) - ll(r["q"], r["y"]) for r in rows) / n}


def bucket(v, edges):
    for lo, hi in edges:
        if v is not None and lo <= v < hi:
            return f"[{lo},{hi})"
    return "none"


def main():
    rows = load()
    groups = {
        "family_x_basis": lambda r: f"{r['family']}|{r['basis']}",
        "family_x_calibrated": lambda r: f"{r['family']}|{'calibrated' if r['calibrated_by'] else 'raw'}",
        "family_x_sample_n": lambda r: f"{r['family']}|{bucket(r['sample_n'], SAMPLE)}",
        "disagreement_p0_minus_q": lambda r: bucket(r["p0"] - r["q"], DISAGREE),
        "family_x_disagreement": lambda r: f"{r['family']}|{bucket(r['p0'] - r['q'], DISAGREE)}",
        "status": lambda r: str(r["status"]),
    }
    report = {"evidence_class": "EXPLORATORY_DESCRIPTIVE", "data_sha": DATA_SHA, "date_range": [FIRST, LAST],
              "n_rows": len(rows), "all": summarize(rows)}
    for name, key in groups.items():
        g = defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        report[name] = {k: summarize(v) for k, v in sorted(g.items())}
    with open(os.path.join(HERE, "diagnose_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    for name in groups:
        print("==", name)
        for k, s in report[name].items():
            if s["n"] >= 15:
                print(f"  {k:45s} n={s['n']:4d} model={s['stated_model']:.3f} price={s['price_q']:.3f} "
                      f"real={s['realized']:.3f} LLdiff={s['ll_model_minus_price']:+.4f}")


if __name__ == "__main__":
    main()
