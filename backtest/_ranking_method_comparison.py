#!/usr/bin/env python3
"""_ranking_method_comparison.py -- the largest trustworthy historical
comparison of ranking methods on IDENTICAL candidate pools: does a
simpler probability-first ranking select more winning props than the
current score-based selection?

Source: backtest/rows.jsonl, 242,776 rows / 401 real dates spanning
2024-04-01..2026-08-12 -- every candidate build_candidates() +
best_of_category_extras() produced for each date (not just what shipped
to the board), so within any (date, prop_type) group, "method A's top
choice" vs "method B's top choice" is a genuine comparison over the same
real alternatives, with real graded outcomes, no leakage (rows.jsonl is
built exclusively through backtest/engine.py's PointInTime machinery).

METHODS COMPARED (only 1 and 2 are testable at this scale -- see below):
  1. raw score          (current selection key for build_candidates()'s
                          own per-batter competition, and for anything
                          that reads `score` to rank)
  2. raw predicted_prob  (pre-calibration model probability)
Methods 3 (predicted_prob + reliability guardrail) and 4 (current
production rank_for_board, which needs real market_edge/market_odds) are
NOT computable from this file: reliability requires per-date empirical
support counts that were never stored per-row, and market data was never
fetched in ANY backtest run (see this module's own coverage_report).
Method 3 is instead measured directly via
_sweep_best_of_category_reliability.py, which reconstructs reliability
from a real, freshly-run point-in-time replay; method 4 can only be
measured on the 16 real production days that have real market data (see
_selection_quality_v2.py).

    /tmp/mlbvenv/bin/python3 backtest/_ranking_method_comparison.py
"""
import json
import os
from collections import defaultdict

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")


def load():
    by_date = defaultdict(list)
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("outcome") is None or d.get("fair_test") is not True:
                continue
            by_date[d["date"]].append(d)
    return dict(sorted(by_date.items()))


def season_of(date):
    return date[:4]


def month_of(date):
    return date[:7]


def top_choice_by(entries, key):
    """Highest-`key` entry in a group; ties broken by player_id for
    determinism (never by outcome -- that would leak)."""
    return max(entries, key=lambda e: (e[key], str(e.get("player_id") or "")))


def main():
    by_date = load()
    groups = defaultdict(list)
    for date, rows in by_date.items():
        for r in rows:
            groups[(date, r["prop_type"])].append(r)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"{sum(len(v) for v in by_date.values())} fair-test rows, {len(by_date)} dates, "
          f"{len(multi)} (date,market) groups with 2+ real alternatives "
          f"(of {len(groups)} total groups)\n")

    # ---- headline: top-choice hit rate, method 1 vs method 2 --------------
    score_top = [top_choice_by(v, "score") for v in multi.values()]
    prob_top = [top_choice_by(v, "predicted_prob") for v in multi.values()]
    score_hit = sum(e["outcome"] for e in score_top) / len(score_top)
    prob_hit = sum(e["outcome"] for e in prob_top) / len(prob_top)
    agree = sum(1 for a, b in zip(score_top, prob_top)
               if a.get("player_id") == b.get("player_id") and a.get("needs") == b.get("needs"))
    print("=== HEADLINE: top-choice hit rate on identical candidate pools ===")
    print(f"  n groups = {len(multi)}")
    print(f"  method 1 (raw score):         hit_rate={score_hit:.4f}  n_selections={len(score_top)}")
    print(f"  method 2 (raw predicted_prob): hit_rate={prob_hit:.4f}  n_selections={len(prob_top)}")
    lift_abs = prob_hit - score_hit
    lift_rel = lift_abs / score_hit if score_hit else float("nan")
    print(f"  absolute lift (prob - score): {lift_abs:+.4f}")
    print(f"  relative lift: {lift_rel:+.1%}")
    print(f"  agreement rate (same top choice): {agree}/{len(multi)} ({agree/len(multi):.1%})")

    # ---- by market ----------------------------------------------------
    print("\n=== By market ===")
    by_market = defaultdict(list)
    for k, v in multi.items():
        by_market[k[1]].append(v)
    for market, vlist in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
        st = [top_choice_by(v, "score") for v in vlist]
        pt = [top_choice_by(v, "predicted_prob") for v in vlist]
        sh = sum(e["outcome"] for e in st) / len(st)
        ph = sum(e["outcome"] for e in pt) / len(pt)
        print(f"  {market:16s} n={len(vlist):5d}  score={sh:.3f}  prob={ph:.3f}  lift={ph-sh:+.3f}")

    # ---- by season (year) -----------------------------------------------
    print("\n=== By season (year) ===")
    by_year = defaultdict(list)
    for k, v in multi.items():
        by_year[season_of(k[0])].append(v)
    for year, vlist in sorted(by_year.items()):
        st = [top_choice_by(v, "score") for v in vlist]
        pt = [top_choice_by(v, "predicted_prob") for v in vlist]
        sh = sum(e["outcome"] for e in st) / len(st)
        ph = sum(e["outcome"] for e in pt) / len(pt)
        print(f"  {year}  n={len(vlist):5d}  score={sh:.3f}  prob={ph:.3f}  lift={ph-sh:+.3f}")

    # ---- by month (year-month, chronological) ----------------------------
    print("\n=== By month ===")
    by_month = defaultdict(list)
    for k, v in multi.items():
        by_month[month_of(k[0])].append(v)
    for month in sorted(by_month):
        vlist = by_month[month]
        if len(vlist) < 15:
            continue  # too thin to report per-month reliably
        st = [top_choice_by(v, "score") for v in vlist]
        pt = [top_choice_by(v, "predicted_prob") for v in vlist]
        sh = sum(e["outcome"] for e in st) / len(st)
        ph = sum(e["outcome"] for e in pt) / len(pt)
        sign = "prob wins" if ph > sh else ("score wins" if sh > ph else "tie")
        print(f"  {month}  n={len(vlist):4d}  score={sh:.3f}  prob={ph:.3f}  lift={ph-sh:+.3f}  ({sign})")

    # ---- by candidate-set size --------------------------------------------
    print("\n=== By candidate-set size (alternatives available that night) ===")
    by_size = defaultdict(list)
    for k, v in multi.items():
        size_bucket = min(len(v), 6)
        by_size[size_bucket].append(v)
    for size in sorted(by_size):
        vlist = by_size[size]
        st = [top_choice_by(v, "score") for v in vlist]
        pt = [top_choice_by(v, "predicted_prob") for v in vlist]
        sh = sum(e["outcome"] for e in st) / len(st)
        ph = sum(e["outcome"] for e in pt) / len(pt)
        label = f"{size}" if size < 6 else "6+"
        print(f"  size={label:3s}  n={len(vlist):5d}  score={sh:.3f}  prob={ph:.3f}  lift={ph-sh:+.3f}")

    # ---- by predicted_prob bucket of the PROB method's top choice ---------
    print("\n=== Prob-method's own top-choice hit rate by its probability bucket ===")
    for lo, hi in [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]:
        sub = [e for e in prob_top if lo <= e["predicted_prob"] < hi]
        if not sub:
            continue
        print(f"  [{lo:.1f},{hi:.1f}) n={len(sub):5d}  hit_rate={sum(e['outcome'] for e in sub)/len(sub):.3f}")

    # ---- rank 1 vs ranks 2-5 within score AND within prob orderings -------
    print("\n=== Rank 1 vs ranks 2-5, by ordering method (multi-candidate groups only) ===")
    for key, label in [("score", "SCORE ordering"), ("predicted_prob", "PROB ordering")]:
        rank_hits = defaultdict(list)
        for v in multi.values():
            ordered = sorted(v, key=lambda e: -e[key])
            for i, e in enumerate(ordered[:5]):
                rank_hits[i + 1].append(e["outcome"])
        print(f"  -- {label} --")
        for rank in sorted(rank_hits):
            outs = rank_hits[rank]
            print(f"    rank {rank}: n={len(outs):5d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- score-component analysis ----------------------------------------
    print("\n=== Score-component analysis (batter/pitcher rows with category breakdown) ===")
    all_rows = [r for rows in by_date.values() for r in rows]
    cat_rows = [r for r in all_rows if r.get("cat_matchup") is not None
               and r.get("predicted_prob") is not None]
    print(f"  n={len(cat_rows)} rows")
    cat_fields = ("cat_matchup", "cat_recent_form", "cat_environment",
                  "cat_baseline_skill", "cat_context")
    cat_weights = {"cat_matchup": 0.35, "cat_recent_form": 0.25, "cat_environment": 0.15,
                   "cat_baseline_skill": 0.15, "cat_context": 0.10}

    def pearson(xs, ys):
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        return cov / (vx * vy) ** 0.5 if vx and vy else None

    outs = [r["outcome"] for r in cat_rows]
    print("  -- raw correlation with outcome vs hand-set weight --")
    for field in cat_fields:
        vals = [r[field] for r in cat_rows]
        corr = pearson(vals, outs)
        print(f"    {field:20s} weight={cat_weights[field]:.2f}  corr={corr:+.4f}")

    # Incremental usefulness after controlling for predicted_prob: bucket
    # by predicted_prob (so every row in a bucket has "similar probability"
    # per the literal request), then measure each component's correlation
    # with outcome WITHIN that bucket only -- a component that only tracks
    # predicted_prob will show near-zero correlation once prob is held
    # roughly fixed; a component with real incremental information will not.
    print("\n  -- incremental usefulness after controlling for predicted_prob --")
    print("     (correlation with outcome WITHIN probability-similar buckets, weighted by bucket size)")
    prob_buckets = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
    for field in cat_fields:
        weighted_sum, weighted_n = 0.0, 0
        for lo, hi in prob_buckets:
            sub = [r for r in cat_rows if lo <= r["predicted_prob"] < hi]
            if len(sub) < 50:
                continue
            vals = [r[field] for r in sub]
            outs_sub = [r["outcome"] for r in sub]
            corr = pearson(vals, outs_sub)
            if corr is None:
                continue
            weighted_sum += corr * len(sub)
            weighted_n += len(sub)
        avg_corr = weighted_sum / weighted_n if weighted_n else None
        print(f"    {field:20s} weight={cat_weights[field]:.2f}  "
             f"within-bucket corr={avg_corr:+.4f}" if avg_corr is not None else
             f"    {field:20s} insufficient data")

    # Does each component help distinguish HIT vs MISS among SIMILAR-prob candidates?
    print("\n  -- HIT vs MISS component averages, restricted to predicted_prob in [0.55, 0.65) --")
    similar = [r for r in cat_rows if 0.55 <= r["predicted_prob"] < 0.65]
    hits = [r for r in similar if r["outcome"] == 1]
    misses = [r for r in similar if r["outcome"] == 0]
    print(f"    n={len(similar)} ({len(hits)} hits / {len(misses)} misses)")
    for field in cat_fields:
        hv = [r[field] for r in hits]
        mv = [r[field] for r in misses]
        if hv and mv:
            print(f"    {field:20s} HIT_avg={sum(hv)/len(hv):6.2f}  MISS_avg={sum(mv)/len(mv):6.2f}  "
                 f"diff={sum(hv)/len(hv)-sum(mv)/len(mv):+.2f}")


if __name__ == "__main__":
    main()
