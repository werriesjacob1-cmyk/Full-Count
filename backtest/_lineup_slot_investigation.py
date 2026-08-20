#!/usr/bin/env python3
"""_lineup_slot_investigation.py -- does batting-order slot carry real,
walk-forward-validated incremental information beyond predicted_prob, and
is any part of its relationship with outcomes currently mispriced?

signals.lineup_slot = scale(10 - order, 1, 9) from score_batter() --
monotonic in real batting order (order=1 leadoff -> 100, order=9 -> 0).

WALK-FORWARD DISCIPLINE: split rows.jsonl's 401 dates chronologically at
the 70th percentile date. DEV = earliest 70% of dates (fit/discover
anything here). EVAL = latest 30% (touched only to confirm, never to
pick a threshold or re-cut). This is a genuine walk-forward split, not a
random one -- order effects (rule changes, roster construction trends,
league-wide approach shifts) could drift over time, and a random split
would hide that.

    /tmp/mlbvenv/bin/python3 backtest/_lineup_slot_investigation.py
"""
import json
import os
from collections import defaultdict

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx and vy else None


def load():
    rows = []
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if (d.get("outcome") is None or d.get("fair_test") is not True
                    or d.get("predicted_prob") is None):
                continue
            slot = (d.get("signals") or {}).get("lineup_slot")
            if slot is None:
                continue
            d["_slot"] = slot
            rows.append(d)
    return rows


def within_prob_bucket_corr(rows, field, min_n=50):
    buckets = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
    weighted_sum, weighted_n = 0.0, 0
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r["predicted_prob"] < hi]
        if len(sub) < min_n:
            continue
        corr = pearson([r[field] for r in sub], [r["outcome"] for r in sub])
        if corr is None:
            continue
        weighted_sum += corr * len(sub)
        weighted_n += len(sub)
    return (weighted_sum / weighted_n, weighted_n) if weighted_n else (None, 0)


def decile_report(rows, field, label):
    vals = sorted(r[field] for r in rows)
    n = len(vals)
    deciles = [vals[min(int(n * p / 10), n - 1)] for p in range(11)]
    deciles[-1] = vals[-1] + 1
    print(f"  -- {label} deciles --")
    for d in range(10):
        lo, hi = deciles[d], deciles[d + 1]
        sub = [r for r in rows if lo <= r[field] < hi]
        if not sub:
            continue
        hit = sum(r["outcome"] for r in sub) / len(sub)
        avg_prob = sum(r["predicted_prob"] for r in sub) / len(sub)
        print(f"     decile {d+1:2d} [{lo:6.1f},{hi:6.1f})  n={len(sub):6d}  "
             f"hit_rate={hit:.3f}  avg_predicted_prob={avg_prob:.3f}  gap={hit-avg_prob:+.3f}")


def main():
    rows = load()
    dates = sorted({r["date"] for r in rows})
    cut = dates[int(len(dates) * 0.7)]
    dev = [r for r in rows if r["date"] < cut]
    eval_ = [r for r in rows if r["date"] >= cut]
    print(f"{len(rows)} batter rows with lineup_slot, {len(dates)} dates "
         f"({dates[0]}..{dates[-1]})")
    print(f"DEV: {len(dev)} rows ({dates[0]}..{cut}, exclusive)  "
         f"EVAL: {len(eval_)} rows ({cut}..{dates[-1]})\n")

    print("=== DEV: raw + incremental correlation ===")
    raw = pearson([r["_slot"] for r in dev], [r["outcome"] for r in dev])
    inc, n = within_prob_bucket_corr(dev, "_slot")
    print(f"  raw corr(lineup_slot, outcome) = {raw:+.4f}")
    print(f"  incremental (predicted_prob-controlled) = {inc:+.4f} (n={n})")

    print("\n=== DEV: decile report (is the relationship linear, or is one part mispriced?) ===")
    decile_report(dev, "_slot", "lineup_slot")

    print("\n=== EVAL (holdout, touched only to confirm): raw + incremental correlation ===")
    raw_e = pearson([r["_slot"] for r in eval_], [r["outcome"] for r in eval_])
    inc_e, n_e = within_prob_bucket_corr(eval_, "_slot")
    print(f"  raw corr(lineup_slot, outcome) = {raw_e:+.4f}")
    print(f"  incremental (predicted_prob-controlled) = {inc_e:+.4f} (n={n_e})")

    print("\n=== EVAL: decile report ===")
    decile_report(eval_, "_slot", "lineup_slot")

    # By season, to check stability rather than assume it
    print("\n=== Incremental correlation by season (stability check, both dev+eval) ===")
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["date"][:4]].append(r)
    for year in sorted(by_year):
        sub = by_year[year]
        inc_y, n_y = within_prob_bucket_corr(sub, "_slot", min_n=30)
        print(f"  {year}: incremental={inc_y:+.4f} (n={n_y})" if inc_y is not None
             else f"  {year}: insufficient data")

    # By market, since lineup_slot's mechanism (more PAs earlier in order)
    # should matter more for high-volume-PA-dependent markets
    print("\n=== Incremental correlation by market (both dev+eval, mechanism check) ===")
    by_market = defaultdict(list)
    for r in rows:
        by_market[r["prop_type"]].append(r)
    for market, sub in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
        if len(sub) < 200:
            continue
        inc_m, n_m = within_prob_bucket_corr(sub, "_slot", min_n=20)
        print(f"  {market:16s} n={len(sub):6d}  incremental={inc_m:+.4f}" if inc_m is not None
             else f"  {market:16s} n={len(sub):6d}  insufficient data")


if __name__ == "__main__":
    main()
