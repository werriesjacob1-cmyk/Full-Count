#!/usr/bin/env python3
"""_selection_quality_rows_scale.py -- large-scale (242,776 rows, 401 real
dates, 2024-04-01..2026-08-12) selection-quality audit using the candidate
pool already captured in backtest/rows.jsonl. Distinct from calibration:
this asks "does the model correctly identify the BETTER of two available
alternatives," not "is its stated probability numerically honest."

WHY rows.jsonl IS THE RIGHT SOURCE FOR THIS, NOT A NEW BACKTEST RUN:
engine.py's simulate_date() writes one row per candidate (main pick) PLUS
best_of_category_extras() for every date -- i.e. every real prop the model
scored that could be graded, not just what would have shipped to the top10
board. That means, for any given date, this file already contains the full
set of "alternatives that existed that night" -- exactly what's needed to
ask "did the model favor the wrong one" without any new fetch/compute.

WHAT THIS CANNOT ANSWER FROM rows.jsonl ALONE: market_edge, market_odds,
odds-range hit rate, and probability-margin-above-MIN_LINE_PROB all require
real FanDuel prices, which backtest explicitly never fetches (see this
module's own coverage_report). Those are answered separately in
_selection_quality_v2.py against the real production record
(results/grades_*.json), which does have real prices -- just far fewer
days (16 vs 401). rows.jsonl's job here is scale: is a candidate-pool
ranking problem visible over two full seasons, not just three weeks.

    /tmp/mlbvenv/bin/python3 backtest/_selection_quality_rows_scale.py
"""
import json
import os
from collections import defaultdict

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")

CAT_FIELDS = ("cat_matchup", "cat_recent_form", "cat_environment",
              "cat_baseline_skill", "cat_context")
CAT_WEIGHTS = {"cat_matchup": 0.35, "cat_recent_form": 0.25, "cat_environment": 0.15,
               "cat_baseline_skill": 0.15, "cat_context": 0.10}


def load():
    by_date = defaultdict(list)
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("outcome") is None or d.get("fair_test") is not True:
                continue
            by_date[d["date"]].append(d)
    return dict(sorted(by_date.items()))


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


def season_slice(date):
    y, m, _ = date.split("-")
    return f"{y}"


def main():
    by_date = load()
    all_rows = [r for rows in by_date.values() for r in rows]
    print(f"{len(all_rows)} fair-test graded rows across {len(by_date)} real dates "
          f"({min(by_date)} .. {max(by_date)})\n")

    # ---- 1. Hit rate by market (prop_type) --------------------------------
    print("=== Hit rate by market ===")
    by_market = defaultdict(list)
    for r in all_rows:
        by_market[r["prop_type"]].append(r["outcome"])
    for market, outs in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
        n = len(outs)
        print(f"  {market:16s} n={n:6d}  hit_rate={sum(outs)/n:.3f}")

    # ---- 2. Hit rate by predicted_prob bucket (pre-calibration) -----------
    print("\n=== Hit rate by predicted_prob bucket (PRE-calibration, all markets) ===")
    buckets = [(0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        sub = [r for r in all_rows if r.get("predicted_prob") is not None and lo <= r["predicted_prob"] < hi]
        if not sub:
            continue
        avg_p = sum(r["predicted_prob"] for r in sub) / len(sub)
        hit = sum(r["outcome"] for r in sub) / len(sub)
        print(f"  [{lo:.1f},{hi:.1f}) n={len(sub):6d}  avg_pred={avg_p:.3f}  hit_rate={hit:.3f}  gap={hit-avg_p:+.3f}")

    # ---- 3. Hit rate by score bucket ---------------------------------------
    print("\n=== Hit rate by score bucket (all markets) ===")
    for lo, hi in [(0, 40), (40, 55), (55, 65), (65, 75), (75, 85), (85, 101)]:
        sub = [r for r in all_rows if lo <= r["score"] < hi]
        if not sub:
            continue
        hit = sum(r["outcome"] for r in sub) / len(sub)
        print(f"  score [{lo:3d},{hi:3d}) n={len(sub):6d}  hit_rate={hit:.3f}")

    # ---- 4. Score-component correlation with real outcome vs. weight ------
    print("\n=== Score-component correlation with real outcome (batter/pitcher rows only) ===")
    print("    (compares each category's ACTUAL predictive power to its hand-set weight)")
    cat_rows = [r for r in all_rows if r.get("cat_matchup") is not None]
    print(f"  n={len(cat_rows)} rows with category breakdown")
    outs = [r["outcome"] for r in cat_rows]
    for field in CAT_FIELDS:
        vals = [r[field] for r in cat_rows]
        corr = pearson(vals, outs)
        w = CAT_WEIGHTS[field]
        corr_s = f"{corr:+.4f}" if corr is not None else "n/a"
        print(f"  {field:20s} weight={w:.2f}  corr_with_outcome={corr_s}")
    score_corr = pearson([r["score"] for r in cat_rows], outs)
    prob_corr = pearson([r["predicted_prob"] for r in cat_rows if r.get("predicted_prob") is not None],
                        [r["outcome"] for r in cat_rows if r.get("predicted_prob") is not None])
    print(f"  {'TOTAL score':20s}          corr_with_outcome={score_corr:+.4f}")
    print(f"  {'predicted_prob':20s}          corr_with_outcome={prob_corr:+.4f}")

    # ---- 5. Within (date, market): does higher score => higher real hit  --
    # For every date+market with 2+ graded candidates, rank by score desc
    # and by predicted_prob desc independently, then check: does the
    # model's own #1 choice (by score) actually have >= outcome rate vs the
    # rest of that same group, and does the #1-by-score coincide with the
    # #1-by-probability?
    print("\n=== Within (date, market) groups with 2+ candidates: does score correctly ===")
    print("    identify the alternative that (a) has higher predicted_prob and (b) actually hit? ===")
    groups = defaultdict(list)
    for r in all_rows:
        groups[(r["date"], r["prop_type"])].append(r)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"  {len(multi)} (date, market) groups with 2+ candidates "
          f"(out of {len(groups)} total groups)")

    n_groups = 0
    n_score_top_also_prob_top = 0
    n_score_top_hit = 0
    n_score_top_beats_group_rate = 0
    n_prob_top_hit = 0
    hit_by_score_rank = defaultdict(list)
    hit_by_prob_rank = defaultdict(list)
    for (date, market), rows in multi.items():
        n_groups += 1
        by_score = sorted(rows, key=lambda r: -r["score"])
        priced = [r for r in rows if r.get("predicted_prob") is not None]
        by_prob = sorted(priced, key=lambda r: -r["predicted_prob"]) if priced else None
        group_rate = sum(r["outcome"] for r in rows) / len(rows)

        for i, r in enumerate(by_score[:5]):
            hit_by_score_rank[i + 1].append(r["outcome"])
        if by_prob:
            for i, r in enumerate(by_prob[:5]):
                hit_by_prob_rank[i + 1].append(r["outcome"])

        score_top = by_score[0]
        n_score_top_hit += score_top["outcome"]
        if score_top["outcome"] >= group_rate:
            n_score_top_beats_group_rate += 1
        if by_prob:
            prob_top = by_prob[0]
            n_prob_top_hit += prob_top["outcome"]
            if score_top.get("player_id") == prob_top.get("player_id"):
                n_score_top_also_prob_top += 1

    print(f"  score-#1 also prob-#1 in same group: {n_score_top_also_prob_top}/{n_groups} "
          f"({n_score_top_also_prob_top/n_groups:.1%})")
    print(f"  score-#1 real hit rate: {n_score_top_hit/n_groups:.3f}")
    print(f"  prob-#1 real hit rate:  {n_prob_top_hit/n_groups:.3f}")

    print("\n  Hit rate by within-group SCORE rank (1=highest score in that date+market):")
    for rank in sorted(hit_by_score_rank):
        outs = hit_by_score_rank[rank]
        print(f"    rank {rank}: n={len(outs):6d}  hit_rate={sum(outs)/len(outs):.3f}")

    print("\n  Hit rate by within-group PREDICTED_PROB rank (1=highest predicted_prob):")
    for rank in sorted(hit_by_prob_rank):
        outs = hit_by_prob_rank[rank]
        print(f"    rank {rank}: n={len(outs):6d}  hit_rate={sum(outs)/len(outs):.3f}")

    # ---- 6. Stability across time: repeat the score-bucket check by year --
    print("\n=== Stability check: score-bucket hit rate by season/year ===")
    by_year = defaultdict(list)
    for r in all_rows:
        by_year[season_slice(r["date"])].append(r)
    for year in sorted(by_year):
        rows = by_year[year]
        print(f"  -- {year} (n={len(rows)}) --")
        for lo, hi in [(55, 65), (65, 75), (75, 101)]:
            sub = [r for r in rows if lo <= r["score"] < hi]
            if not sub:
                continue
            hit = sum(r["outcome"] for r in sub) / len(sub)
            print(f"    score [{lo:3d},{hi:3d}) n={len(sub):6d}  hit_rate={hit:.3f}")

    # ---- 7. Monotonicity check: is score-bucket hit rate actually increasing?
    print("\n=== Monotonicity: hit rate must rise with score bucket for score to be a valid rank key ===")
    prev = None
    monotonic = True
    for lo, hi in [(0, 40), (40, 55), (55, 65), (65, 75), (75, 85), (85, 101)]:
        sub = [r for r in all_rows if lo <= r["score"] < hi]
        if not sub:
            continue
        hit = sum(r["outcome"] for r in sub) / len(sub)
        if prev is not None and hit < prev - 0.005:
            monotonic = False
            print(f"  NON-MONOTONIC at [{lo},{hi}): hit_rate={hit:.3f} < previous bucket {prev:.3f}")
        prev = hit
    print(f"  Overall score-bucket monotonic (within 0.5pt noise tolerance): {monotonic}")


if __name__ == "__main__":
    main()
