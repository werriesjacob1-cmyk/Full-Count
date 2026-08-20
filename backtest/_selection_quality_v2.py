#!/usr/bin/env python3
"""_selection_quality_v2.py -- audits real production board data
(results/grades_*.json, 16 real days 2026-08-04..2026-08-19) for selection
quality: does the shipped rank/category system actually favor the props
most likely to hit, distinct from whether individual probabilities are
calibrated.

Extends the earlier _analyze_selection_quality.py pass with every
breakdown from the fuller audit checklist that only real production data
(real market odds, real reliability grades, real lineup_assumed flags) can
answer: hit rate by shipped rank, by market, by market_edge bucket, by
odds range, by reliability grade, by lineup_assumed vs confirmed, and by
probability margin above the MIN_LINE_PROB=0.60 selection cutoff.

    /tmp/mlbvenv/bin/python3 backtest/_selection_quality_v2.py
"""
import glob
import json
import os
from collections import defaultdict

GRADES_GLOB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "results", "grades_*.json")
MIN_LINE_PROB = 0.60


def load_picks():
    """Every graded pick across every real grades_{date}.json file, from
    ANY category (main board, best_of_category, moonshot) -- shadow
    entries excluded (see prior investigation: shadow = other thresholds
    of the SAME selected candidate, not a competing alternative)."""
    picks = []
    for path in sorted(glob.glob(GRADES_GLOB)):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for p in d.get("picks", []):
            if p.get("category") == "shadow":
                continue
            if p.get("grade") not in ("hit", "miss"):
                continue
            p = dict(p)
            p["_date"] = d["date"]
            p["outcome"] = 1 if p["grade"] == "hit" else 0
            picks.append(p)
    return picks


def bucket_report(picks, key_fn, buckets, label):
    print(f"\n=== {label} ===")
    for lo, hi in buckets:
        sub = [p for p in picks if (v := key_fn(p)) is not None and lo <= v < hi]
        if not sub:
            print(f"  [{lo},{hi}) n=0")
            continue
        hit = sum(p["outcome"] for p in sub) / len(sub)
        print(f"  [{lo},{hi}) n={len(sub):4d}  hit_rate={hit:.3f}")


def main():
    picks = load_picks()
    print(f"{len(picks)} real graded picks (any category, any market) across "
          f"{len({p['_date'] for p in picks})} real production days\n")

    # ---- Overall + by category -------------------------------------------
    print("=== Hit rate by category ===")
    by_cat = defaultdict(list)
    for p in picks:
        by_cat[p.get("category") or "main_board"].append(p["outcome"])
    for cat, outs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat:18s} n={len(outs):4d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- By market ----------------------------------------------------
    print("\n=== Hit rate by market (all categories combined) ===")
    by_market = defaultdict(list)
    for p in picks:
        stat = (p.get("projection") or {}).get("stat") or "unknown"
        by_market[stat].append(p["outcome"])
    for m, outs in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
        print(f"  {m:18s} n={len(outs):4d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- By shipped rank (main board only -- rank is a global counter,
    # only meaningful within the main board's own picks list) -----------
    print("\n=== Hit rate by shipped rank, main board only (category is None) ===")
    main_board = [p for p in picks if p.get("category") is None]
    by_rank = defaultdict(list)
    for p in main_board:
        r = p.get("rank")
        if r is not None:
            by_rank[r].append(p["outcome"])
    for r in sorted(by_rank):
        outs = by_rank[r]
        print(f"  rank {r:3d}  n={len(outs):3d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- By reliability grade ------------------------------------------
    print("\n=== Hit rate by reliability grade (all categories) ===")
    by_rel = defaultdict(list)
    for p in picks:
        by_rel[p.get("reliability") or "unknown"].append(p["outcome"])
    for g, outs in sorted(by_rel.items()):
        print(f"  grade {g:8s} n={len(outs):4d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- By lineup_assumed vs confirmed ---------------------------------
    print("\n=== Hit rate by lineup_assumed (batter/pitcher picks only) ===")
    by_lineup = defaultdict(list)
    for p in picks:
        la = p.get("lineup_assumed")
        if la is None:
            continue
        by_lineup["assumed" if la else "confirmed"].append(p["outcome"])
    for k, outs in sorted(by_lineup.items()):
        print(f"  {k:10s} n={len(outs):4d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- By market_edge bucket (priced picks only) ----------------------
    priced = [p for p in picks if p.get("market_edge") is not None]
    print(f"\n=== Hit rate by market_edge bucket ({len(priced)} priced picks) ===")
    bucket_report(priced, lambda p: p["market_edge"],
                  [(-1.0, -0.1), (-0.1, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)],
                  "market_edge buckets")

    # ---- By hit_probability bucket --------------------------------------
    print(f"\n=== Hit rate by hit_probability bucket ({len(picks)} picks) ===")
    bucket_report(picks, lambda p: p.get("hit_probability"),
                  [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)],
                  "hit_probability buckets")

    # ---- By probability margin above MIN_LINE_PROB cutoff ---------------
    print(f"\n=== Hit rate by margin above MIN_LINE_PROB=0.60 selection cutoff ===")
    margined = [p for p in picks if p.get("hit_probability") is not None]
    bucket_report(margined, lambda p: p["hit_probability"] - MIN_LINE_PROB,
                  [(-1.0, 0.0), (0.0, 0.03), (0.03, 0.07), (0.07, 0.12), (0.12, 1.0)],
                  "margin above cutoff")

    # ---- By odds range (American odds, priced picks) ---------------------
    odds_picks = [p for p in picks if p.get("market_odds") is not None]
    print(f"\n=== Hit rate by market odds range ({len(odds_picks)} priced picks) ===")
    def odds_bucket(p):
        o = p["market_odds"]
        return o
    for lo, hi, label in [(-100000, -300, "heavier favorite (<-300)"),
                          (-300, -180, "-300..-180"), (-180, -110, "-180..-110"),
                          (-110, 100, "-110..+100"), (100, 100000, "+100 or longer")]:
        sub = [p for p in odds_picks if lo <= p["market_odds"] < hi]
        if not sub:
            continue
        hit = sum(p["outcome"] for p in sub) / len(sub)
        print(f"  {label:28s} n={len(sub):4d}  hit_rate={hit:.3f}")

    # ---- Top pick (rank 1, main board) vs main-board rest ----------------
    top1 = [p for p in main_board if p.get("rank") == 1]
    rest = [p for p in main_board if p.get("rank") not in (None, 1)]
    if top1:
        print(f"\n=== Main-board rank 1 vs rest ===")
        print(f"  rank 1: n={len(top1)}  hit_rate={sum(p['outcome'] for p in top1)/len(top1):.3f}")
    if rest:
        print(f"  rank 2+: n={len(rest)}  hit_rate={sum(p['outcome'] for p in rest)/len(rest):.3f}")

    # ---- HIT vs MISS: what actually differs between them? ----------------
    hits = [p for p in picks if p["outcome"] == 1]
    misses = [p for p in picks if p["outcome"] == 0]
    print(f"\n=== HIT vs MISS profile (all categories, {len(hits)} hits / {len(misses)} misses) ===")
    for field, fmt in [("score", ".1f"), ("hit_probability", ".3f"), ("market_edge", ".3f")]:
        hv = [p[field] for p in hits if p.get(field) is not None]
        mv = [p[field] for p in misses if p.get(field) is not None]
        if hv and mv:
            print(f"  avg {field:16s}  HIT={sum(hv)/len(hv):{fmt}}  MISS={sum(mv)/len(mv):{fmt}}")


if __name__ == "__main__":
    main()
