#!/usr/bin/env python3
"""_score_component_deep_dive.py -- for each of the five raw score
components (cat_matchup/cat_recent_form/cat_environment/cat_baseline_skill/
cat_context), quantify:
  1. raw relationship with outcomes
  2. incremental relationship after controlling for predicted_prob
  3. performance within narrow predicted-prob bands (already folded into 2,
     reported per-band here for visibility)
  4. performance within each major market
  5. stability across 2024/2025/2026
  6. usefulness for ranking candidates against OTHERS on the SAME date +
     SAME market (within-group demeaned correlation -- the cleanest
     operationalization of "does this help pick the winner from tonight's
     real alternatives")
Plus a decile/extreme check per component (linear correlation can hide a
component that only works at its tails).

Source: backtest/rows.jsonl, batter/pitcher rows only (cat_* only exists
for score_batter/score_pitcher, not the other prop-specific scorers -- see
SCHEMA.md). No new fetches -- pure post-processing of already-collected,
point-in-time-safe backtest rows.

    /tmp/mlbvenv/bin/python3 backtest/_score_component_deep_dive.py
"""
import json
import os
from collections import defaultdict

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
CAT_FIELDS = ("cat_matchup", "cat_recent_form", "cat_environment",
              "cat_baseline_skill", "cat_context")
CAT_WEIGHTS = {"cat_matchup": 0.35, "cat_recent_form": 0.25, "cat_environment": 0.15,
               "cat_baseline_skill": 0.15, "cat_context": 0.10}
PROB_BUCKETS = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
MAJOR_MARKETS = ("hits", "hits_runs_rbis", "total_bases", "home_run", "runs", "rbis", "doubles")


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx and vy else None


def within_prob_bucket_corr(rows, field, min_n=50):
    weighted_sum, weighted_n = 0.0, 0
    for lo, hi in PROB_BUCKETS:
        sub = [r for r in rows if lo <= r["predicted_prob"] < hi]
        if len(sub) < min_n:
            continue
        corr = pearson([r[field] for r in sub], [r["outcome"] for r in sub])
        if corr is None:
            continue
        weighted_sum += corr * len(sub)
        weighted_n += len(sub)
    return (weighted_sum / weighted_n, weighted_n) if weighted_n else (None, 0)


def load():
    rows = []
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if (d.get("outcome") is None or d.get("fair_test") is not True
                    or d.get("cat_matchup") is None or d.get("predicted_prob") is None):
                continue
            rows.append(d)
    return rows


def main():
    rows = load()
    print(f"{len(rows)} batter/pitcher rows with category breakdown + predicted_prob\n")

    # ---- 1 & 2: raw + incremental (bucket-controlled), already known, ----
    # restated here as the anchor for everything else in this script
    print("=== 1+2. Raw vs incremental (predicted_prob-controlled) correlation ===")
    outs = [r["outcome"] for r in rows]
    for field in CAT_FIELDS:
        raw = pearson([r[field] for r in rows], outs)
        inc, n = within_prob_bucket_corr(rows, field)
        print(f"  {field:20s} weight={CAT_WEIGHTS[field]:.2f}  raw={raw:+.4f}  "
             f"incremental={inc:+.4f} (n={n})" if inc is not None else
             f"  {field:20s} weight={CAT_WEIGHTS[field]:.2f}  raw={raw:+.4f}  incremental=n/a")

    # ---- 3: incremental correlation by individual probability band -------
    print("\n=== 3. Incremental correlation, band by band ===")
    for lo, hi in PROB_BUCKETS:
        sub = [r for r in rows if lo <= r["predicted_prob"] < hi]
        if len(sub) < 50:
            continue
        print(f"  -- prob [{lo:.1f},{hi:.1f}) n={len(sub)} --")
        for field in CAT_FIELDS:
            corr = pearson([r[field] for r in sub], [r["outcome"] for r in sub])
            print(f"     {field:20s} corr={corr:+.4f}" if corr is not None else f"     {field:20s} n/a")

    # ---- 4: incremental correlation by major market -----------------------
    print("\n=== 4. Incremental (prob-controlled) correlation by market ===")
    by_market = defaultdict(list)
    for r in rows:
        by_market[r["prop_type"]].append(r)
    for market in MAJOR_MARKETS:
        sub = by_market.get(market, [])
        if len(sub) < 100:
            continue
        print(f"  -- {market} (n={len(sub)}) --")
        for field in CAT_FIELDS:
            inc, n = within_prob_bucket_corr(sub, field, min_n=20)
            print(f"     {field:20s} incremental={inc:+.4f} (n={n})" if inc is not None
                 else f"     {field:20s} insufficient data")

    # ---- 5: stability across seasons ---------------------------------------
    print("\n=== 5. Incremental (prob-controlled) correlation by season ===")
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["date"][:4]].append(r)
    for year in sorted(by_year):
        sub = by_year[year]
        print(f"  -- {year} (n={len(sub)}) --")
        for field in CAT_FIELDS:
            inc, n = within_prob_bucket_corr(sub, field, min_n=30)
            print(f"     {field:20s} incremental={inc:+.4f} (n={n})" if inc is not None
                 else f"     {field:20s} insufficient data")

    # ---- 6: within (date,market) group -- ranking candidates against each
    # other on the SAME slate. Demean every field (including outcome... no,
    # outcome stays binary) by the group's own mean, so a component's
    # correlation here measures "does deviating above/below tonight's
    # average on this component predict deviating toward a hit," fully
    # controlling for date+market baseline levels (which predicted_prob's
    # bucket approach only partially does).
    print("\n=== 6. Within-(date,market)-group demeaned correlation ===")
    print("    (does this component distinguish the winner from tonight's real alternatives?)")
    groups = defaultdict(list)
    for r in rows:
        groups[(r["date"], r["prop_type"])].append(r)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"  {len(multi)} groups with 2+ candidates")

    demeaned_outcome = []
    demeaned_fields = defaultdict(list)
    demeaned_prob = []
    for v in multi.values():
        mean_outcome = sum(r["outcome"] for r in v) / len(v)
        mean_prob = sum(r["predicted_prob"] for r in v) / len(v)
        for r in v:
            demeaned_outcome.append(r["outcome"] - mean_outcome)
            demeaned_prob.append(r["predicted_prob"] - mean_prob)
            for field in CAT_FIELDS:
                mean_field = sum(x[field] for x in v) / len(v)
                demeaned_fields[field].append(r[field] - mean_field)

    prob_within_corr = pearson(demeaned_prob, demeaned_outcome)
    print(f"  predicted_prob (within-group)  corr={prob_within_corr:+.4f}")
    for field in CAT_FIELDS:
        corr = pearson(demeaned_fields[field], demeaned_outcome)
        print(f"  {field:20s} (within-group)  corr={corr:+.4f}  weight={CAT_WEIGHTS[field]:.2f}")

    # ---- extreme/decile check for nonlinearity -----------------------------
    print("\n=== Decile check: is any component's relationship nonlinear (useful only at extremes)? ===")
    for field in CAT_FIELDS:
        vals = sorted(r[field] for r in rows)
        n = len(vals)
        deciles = [vals[min(int(n * p / 10), n - 1)] for p in range(11)]
        deciles[-1] = vals[-1] + 1  # inclusive top edge
        print(f"  -- {field} --")
        for d in range(10):
            lo, hi = deciles[d], deciles[d + 1]
            sub = [r for r in rows if lo <= r[field] < hi]
            if not sub:
                continue
            hit = sum(r["outcome"] for r in sub) / len(sub)
            print(f"     decile {d+1:2d} [{lo:6.1f},{hi:6.1f})  n={len(sub):6d}  hit_rate={hit:.3f}")


if __name__ == "__main__":
    main()
