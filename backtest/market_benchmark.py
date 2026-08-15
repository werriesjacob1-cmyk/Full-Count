#!/usr/bin/env python3
"""market_benchmark.py — Section E of the 2026-08-15 recommendation-layer
rebuild: "Tell me whether Full Count's probability model improves upon
sportsbook/no-vig probabilities by individual prop family."

Direct instruction: "Do NOT assume the probability engine is solved...
Benchmark the probability model by prop family against sportsbook/no-vig
market probability. Evaluate: calibration, Brier score, log loss, sample
size, whether Full Count adds predictive information beyond the market."

DATA SOURCE. results/grades_*.json is the only place hit_probability,
market_odds, and a real graded outcome (hit/miss against the actual box
score) sit together on the same record. Betting-market signals are not
available before 2026-08-05 (see backtest/SCHEMA.md's own documented
limitation), so this only ever covers the live-graded window -- currently
~10-11 days as of 2026-08-15. That is a genuinely thin sample for a
per-family split; every result below is reported with its own N and
should be read as directional, not as a settled verdict, until the window
is longer.

WHAT THIS COMPARES, PER PROP FAMILY (projection.stat):
  - MODEL:  hit_probability, the number this project actually displayed.
  - MARKET: prop_probability.devig(implied_probability(market_odds)) --
    the de-vigged (no-vig) market estimate, ASSUMED_PROP_HOLD=8% (a
    one-sided estimate, see devig()'s own docstring; this project's feed
    exposes only one side of most player-prop markets, so an exact
    devig_two_sided() is not available at this data source).

METRICS: Brier score (mean squared error of the probability against the
0/1 outcome, lower is better), log loss (lower is better, penalizes
confident wrong calls harder), and a coarse calibration table (predicted
vs actual hit rate in probability buckets) for both MODEL and MARKET,
side by side, per family and pooled.

    /tmp/mlbvenv/bin/python3 backtest/market_benchmark.py
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prop_probability as pp

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def brier(probs_outcomes):
    if not probs_outcomes:
        return None
    return sum((p - o) ** 2 for p, o in probs_outcomes) / len(probs_outcomes)


def log_loss(probs_outcomes, eps=1e-6):
    if not probs_outcomes:
        return None
    total = 0.0
    for p, o in probs_outcomes:
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(probs_outcomes)


def calibration_table(probs_outcomes, buckets=(0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)):
    rows = []
    for lo, hi in zip(buckets, buckets[1:]):
        in_bucket = [(p, o) for p, o in probs_outcomes if lo <= p < hi]
        if not in_bucket:
            continue
        n = len(in_bucket)
        pred = sum(p for p, _ in in_bucket) / n
        actual = sum(o for _, o in in_bucket) / n
        rows.append({"range": f"{lo:.2f}-{hi:.2f}", "n": n,
                     "predicted": round(pred, 3), "actual": round(actual, 3),
                     "gap": round(actual - pred, 3)})
    return rows


def load_graded_picks():
    """Every graded (hit/miss, not ungraded), market-priced pick across every
    results/grades_*.json file on disk -- pooled across categories
    (main/moonshot/best_of_category), since the question here is about the
    PROBABILITY MODEL's accuracy per family, not about which selection
    policy chose to recommend it. Category mix is reported per family so a
    reader can see if one family's sample leans heavily on, say,
    best_of_category's sub-floor picks."""
    out = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "grades_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        date = d.get("date")
        for p in d.get("picks", []):
            if p.get("grade") not in ("hit", "miss"):
                continue
            prob = p.get("hit_probability")
            odds = p.get("market_odds")
            if prob is None or odds is None:
                continue
            stat = (p.get("projection") or {}).get("stat") or "?"
            out.append({
                "date": date, "stat": stat, "category": p.get("category") or "main",
                "model_prob": float(prob), "market_odds": odds,
                "outcome": 1.0 if p["grade"] == "hit" else 0.0,
            })
    return out


def main():
    rows = load_graded_picks()
    if not rows:
        print("No graded, market-priced picks found in results/grades_*.json — nothing to benchmark.")
        return 1

    dates = sorted(set(r["date"] for r in rows))
    print(f"Loaded {len(rows)} graded, market-priced picks across {len(dates)} day(s): "
          f"{dates[0]} .. {dates[-1]}")
    print("\n*** SAMPLE SIZE WARNING: this window is genuinely thin (a matter of days, not "
          "weeks/months). Per-family splits below can have single-digit N. Treat every number "
          "as directional, re-run this script periodically as the graded window grows, and do "
          "not treat a single day's noise as a verdict on the model. ***\n")

    for r in rows:
        implied = pp.implied_probability(r["market_odds"])
        r["market_prob"] = pp.devig(implied) if implied is not None else None

    usable = [r for r in rows if r["market_prob"] is not None]
    by_stat = defaultdict(list)
    for r in usable:
        by_stat[r["stat"]].append(r)

    def _report(label, group):
        model_po = [(r["model_prob"], r["outcome"]) for r in group]
        market_po = [(r["market_prob"], r["outcome"]) for r in group]
        n = len(group)
        cats = defaultdict(int)
        for r in group:
            cats[r["category"]] += 1
        cat_s = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        print(f"\n=== {label}  (n={n}; by category: {cat_s}) ===")
        mb, mk = brier(model_po), brier(market_po)
        ml, kl = log_loss(model_po), log_loss(market_po)
        print(f"  Brier score    -- model: {mb:.4f}   market (no-vig): {mk:.4f}   "
              f"{'MODEL BETTER' if mb < mk else ('MARKET BETTER' if mk < mb else 'TIE')} "
              f"(lower is better)")
        print(f"  Log loss       -- model: {ml:.4f}   market (no-vig): {kl:.4f}   "
              f"{'MODEL BETTER' if ml < kl else ('MARKET BETTER' if kl < ml else 'TIE')} "
              f"(lower is better)")
        model_hit_rate = sum(o for _, o in model_po) / n
        print(f"  Real hit rate over this pool: {model_hit_rate:.3f}")
        print("  Model calibration (predicted range -> n, predicted avg, actual hit rate, gap):")
        for row in calibration_table(model_po):
            print(f"    {row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
                  f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}")
        print("  Market (no-vig) calibration:")
        for row in calibration_table(market_po):
            print(f"    {row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
                  f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}")
        # Does the model know something the market doesn't? A crude signal:
        # among picks where model_prob and market_prob disagree by a real
        # margin, does the SIDE of the disagreement predict the outcome
        # better than chance -- i.e. when the model is more bullish than
        # the market, does the bet hit more often than when it's less
        # bullish? This is exactly what "adds predictive information beyond
        # the market" means operationally.
        disagree = [r for r in group if abs(r["model_prob"] - r["market_prob"]) >= 0.03]
        if len(disagree) >= 5:
            more_bullish = [r for r in disagree if r["model_prob"] > r["market_prob"]]
            less_bullish = [r for r in disagree if r["model_prob"] < r["market_prob"]]
            mb_rate = (sum(r["outcome"] for r in more_bullish) / len(more_bullish)
                      if more_bullish else None)
            lb_rate = (sum(r["outcome"] for r in less_bullish) / len(less_bullish)
                      if less_bullish else None)
            print(f"  Directional info check (|model-market| >= 3pts, n={len(disagree)}): "
                  f"when model MORE bullish than market (n={len(more_bullish)}), real hit rate="
                  f"{mb_rate if mb_rate is None else round(mb_rate, 3)}; when model LESS bullish "
                  f"(n={len(less_bullish)}), real hit rate={lb_rate if lb_rate is None else round(lb_rate, 3)}")
        else:
            print(f"  Directional info check: skipped, only {len(disagree)} picks disagree with "
                  f"the market by >=3pts (need >=5 for a non-trivial read)")

    _report("POOLED (all families)", usable)
    for stat in sorted(by_stat, key=lambda s: -len(by_stat[s])):
        group = by_stat[stat]
        if len(group) < 3:
            print(f"\n=== {stat}  (n={len(group)}) === SKIPPED -- fewer than 3 graded, "
                  f"market-priced picks, not enough to say anything")
            continue
        _report(stat, group)

    return 0


if __name__ == "__main__":
    sys.exit(main())
