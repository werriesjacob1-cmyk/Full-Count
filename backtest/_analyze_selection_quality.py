#!/usr/bin/env python3
"""_analyze_selection_quality.py -- audits whether Full Count's real,
shipped RANKING (score-driven) actually tracks its own probability
estimate and real outcomes, using the real production recommendation/
grading history in results/grades_*.json (16 real days, 2026-08-04 ..
2026-08-19 at time of writing). Scratch tooling, not part of the shipped
pipeline. Read-only analysis -- no backtest, no new fetches.

KEY CORRECTED FINDING (see git log for the false start this superseded):
shadow_tracking is NOT "candidates the ranking passed over" -- it is
OTHER THRESHOLDS of the SAME already-selected candidate (e.g. hard_hit_110
as a shadow alternate to hard_hit_105's winner), captured from that
candidate's own `alternatives` list (see generate_picks.py's
select_shadow_tracking docstring). Comparing "selected vs shadow" hit
rates is comparing a market-priced coinflip threshold against a near-
certain lower one for pitcher_outs, or literally the same pick duplicated
for single-threshold markets like doubles/hard_hit_105 -- not a genuine
"did the model choose a worse candidate over a better one" signal. An
earlier pass over this same data drew exactly that wrong conclusion before
this correction; kept in git history rather than silently discarded.

THE REAL FINDING: correlation(score, hit_probability) over 90 real
main-board picks = 0.137 -- score, alone, is not synonymous with "most
likely to hit". 5 of 13 real days show the #1-ranked pick was NOT the
day's highest-probability candidate; 2026-08-14 is the sharpest example
(rank 1 prob=0.263 while rank 6 that same day had prob=0.688 available).
best_of_category's rank ordering (reconstructed correctly this time --
see below) is much healthier: roughly monotonic, rank 1 clearly
outperforms rank 5 (0.399 vs 0.293 hit rate, n=163/75).

CAVEAT, stated plainly: only 16 real days / 110 main-board picks. Real,
structural (correlation is sample-size-independent), but NOT enough
volume to prove the hit-rate consequences are stable across seasons --
see report for the honest limitation and the larger-sample follow-up this
motivates.
"""
import json
import glob
from collections import defaultdict


def load_picks(category_filter):
    rows = []
    for fn in sorted(glob.glob("results/grades_*.json")):
        with open(fn) as f:
            g = json.load(f)
        for p in g.get("picks", []):
            if p.get("category") != category_filter:
                continue
            if p.get("grade") not in ("hit", "miss"):
                continue
            p["_date"] = g["date"]
            rows.append(p)
    return rows


def main():
    main_rows = load_picks(None)
    boc_rows = load_picks("best_of_category")

    print(f"main-board real graded picks: {len(main_rows)}")
    print(f"best_of_category real graded picks: {len(boc_rows)}")
    print()

    print("=" * 78)
    print("MAIN BOARD: hit rate by shipped rank")
    print("=" * 78)
    by_rank = defaultdict(list)
    for p in main_rows:
        by_rank[p["rank"]].append(p)
    for r in sorted(by_rank):
        ps = by_rank[r]
        n = len(ps)
        hits = sum(1 for p in ps if p["grade"] == "hit")
        avgp = sum(p.get("hit_probability") or 0 for p in ps) / n
        print(f"  rank={r:2d}  n={n:3d}  avg_prob={avgp:.3f}  hit_rate={hits/n:.3f}")

    print()
    print("=" * 78)
    print("MAIN BOARD: does shipped rank order match probability-descending order?")
    print("=" * 78)
    scored_rows = [p for p in main_rows if p.get("hit_probability") is not None and p.get("score") is not None]
    mismatches = 0
    dates = sorted(set(p["_date"] for p in scored_rows))
    for date in dates:
        day = sorted([p for p in scored_rows if p["_date"] == date], key=lambda p: p["rank"])
        probs = [p["hit_probability"] for p in day]
        monotonic = all(probs[i] >= probs[i + 1] - 1e-9 for i in range(len(probs) - 1))
        if not monotonic:
            mismatches += 1
            print(f"  {date}: rank order diverges from probability order: {[round(x,3) for x in probs]}")
    print(f"\n  {mismatches}/{len(dates)} real days where shipped rank order != probability-descending order")

    n = len(scored_rows)
    scores = [p["score"] for p in scored_rows]
    probs = [p["hit_probability"] for p in scored_rows]
    mean_s, mean_p = sum(scores) / n, sum(probs) / n
    cov = sum((s - mean_s) * (pr - mean_p) for s, pr in zip(scores, probs)) / n
    sd_s = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5
    sd_p = (sum((pr - mean_p) ** 2 for pr in probs) / n) ** 0.5
    print(f"\n  correlation(score, hit_probability) over {n} main-board picks = {cov/(sd_s*sd_p):.3f}")

    print()
    print("=" * 78)
    print("BEST_OF_CATEGORY: hit rate by TRUE within-category rank (reconstructed)")
    print("=" * 78)
    groups = defaultdict(list)
    for p in boc_rows:
        stat = (p.get("projection") or {}).get("stat")
        groups[(p["_date"], stat)].append(p)
    by_local_rank = defaultdict(list)
    for key, ps in groups.items():
        ps_sorted = sorted(ps, key=lambda p: p["rank"])
        for i, p in enumerate(ps_sorted, 1):
            by_local_rank[i].append(p)
    for r in sorted(by_local_rank):
        ps = by_local_rank[r]
        n2 = len(ps)
        hits = sum(1 for p in ps if p["grade"] == "hit")
        avgp = sum(p.get("hit_probability") or 0 for p in ps) / n2
        print(f"  local_rank={r}  n={n2:4d}  avg_prob={avgp:.3f}  hit_rate={hits/n2:.3f}")


if __name__ == "__main__":
    main()
