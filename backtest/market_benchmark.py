#!/usr/bin/env python3
"""market_benchmark.py — Section E of the 2026-08-15 recommendation-layer
rebuild: "Tell me whether Full Count's probability model improves upon
sportsbook/no-vig probabilities by individual prop family."

Direct instruction: "Do NOT assume the probability engine is solved...
Benchmark the probability model by prop family against sportsbook/no-vig
market probability. Evaluate: calibration, Brier score, log loss, sample
size, whether Full Count adds predictive information beyond the market."

UPDATED PHASE 3 (2026-08-16), item 5: "audit whether that benchmark is
statistically fair... you documented that the current market comparison
uses an assumed 8% one-sided hold." That audit found the comparison could
already be exact for SOME markets and wasn't using it. odds_fanduel.py's
attach_market_prices() already computes an EXACT de-vigged probability for
pitcher strikeouts, pitcher_outs, and nrfi_combined (both sides of those
markets are genuinely quoted by FanDuel and devig_two_sided() used
exactly) and persists it as market_hold/market_implied -- this script
previously ignored that and re-derived a WORSE 8%-assumed approximation
from market_odds alone for every pick, exact markets included. Fixed by
routing every pick through eval_lib.market_probability(), which uses the
exact number when market_hold proves one exists and falls back to the
labelled approximation only when it doesn't -- and now reports the split
so a reader can see how much of the comparison is exact vs approximate.

For the remaining, structurally one-sided batter YES/NO markets (hits,
total_bases, home_runs, RBIs, runs, stolen_base, singles/doubles/triples,
hits_runs_rbis) -- confirmed LIVE 2026-08-16, not assumed: FanDuel's
batter-props/popular/lasers/moonshots tabs return exactly ONE runner per
PLAYER for these market types (18 players bundled into one market object,
never two runners for two sides of one player's line). There is no second
side to capture; the 8%-assumed devig is the correct, permanent approach
for these, not a data-collection gap. See results/ANALYSIS.md and this
project's Phase 3 report for the full investigation.

DATA SOURCE. results/grades_*.json is the only place hit_probability,
market_odds, and a real graded outcome (hit/miss against the actual box
score) sit together on the same record. Betting-market signals are not
available before 2026-08-05 (see backtest/SCHEMA.md's own documented
limitation), so this only ever covers the live-graded window. That is a
genuinely thin sample for a per-family split; every result below is
reported with its own N and should be read as directional, not as a
settled verdict, until the window is longer.

    /tmp/mlbvenv/bin/python3 backtest/market_benchmark.py
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eval_lib as el


def load_rows():
    """Every graded, market-priced pick, pooled across categories
    (main/moonshot/best_of_category) since the question here is about the
    PROBABILITY MODEL's accuracy per family, not which selection policy
    chose to recommend it. Category mix is reported per family so a reader
    can see if one family's sample leans heavily on, say, best_of_
    category's sub-floor picks."""
    picks = el.graded_only(el.priced_only(el.load_graded_picks()))
    out = []
    for p in picks:
        prob = p.get("hit_probability")
        if prob is None:
            continue
        market_prob, exact = el.market_probability(p)
        if market_prob is None:
            continue
        stat = (p.get("projection") or {}).get("stat") or "?"
        out.append({
            "date": p.get("_date"), "stat": stat, "category": p.get("category") or "main",
            "model_prob": float(prob), "market_prob": market_prob, "market_exact": exact,
            "outcome": 1.0 if p["grade"] == "hit" else 0.0,
        })
    return out


def main():
    rows = load_rows()
    if not rows:
        print("No graded, market-priced picks found in results/grades_*.json — nothing to benchmark.")
        return 1

    dates = sorted(set(r["date"] for r in rows))
    n_exact = sum(1 for r in rows if r["market_exact"])
    print(f"Loaded {len(rows)} graded, market-priced picks across {len(dates)} day(s): "
          f"{dates[0]} .. {dates[-1]}")
    print(f"Market probability is EXACT (both sides genuinely quoted and de-vigged, see "
          f"odds_fanduel.attach_market_prices) for {n_exact}/{len(rows)} picks -- the rest use "
          f"the labelled {os.environ.get('ASSUMED_HOLD_NOTE', '8%-assumed')} one-sided "
          f"approximation because FanDuel does not post a second side for that market type at "
          f"all (confirmed live, see this script's own docstring).")
    print("\n*** SAMPLE SIZE WARNING: this window is genuinely thin (a matter of days, not "
          "weeks/months). Per-family splits below can have single-digit N. Treat every number "
          "as directional, re-run this script periodically as the graded window grows, and do "
          "not treat a single day's noise as a verdict on the model. ***\n")

    by_stat = defaultdict(list)
    for r in rows:
        by_stat[r["stat"]].append(r)

    def _report(label, group):
        model_po = [(r["model_prob"], r["outcome"]) for r in group]
        market_po = [(r["market_prob"], r["outcome"]) for r in group]
        n = len(group)
        n_exact_g = sum(1 for r in group if r["market_exact"])
        cats = defaultdict(int)
        for r in group:
            cats[r["category"]] += 1
        cat_s = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        print(f"\n=== {label}  (n={n}; market exact for {n_exact_g}/{n}; by category: {cat_s}) ===")
        mb, mk = el.brier(model_po), el.brier(market_po)
        ml, kl = el.log_loss(model_po), el.log_loss(market_po)
        print(f"  Brier score    -- model: {mb:.4f}   market (no-vig): {mk:.4f}   "
              f"{'MODEL BETTER' if mb < mk else ('MARKET BETTER' if mk < mb else 'TIE')} "
              f"(lower is better)")
        print(f"  Log loss       -- model: {ml:.4f}   market (no-vig): {kl:.4f}   "
              f"{'MODEL BETTER' if ml < kl else ('MARKET BETTER' if kl < ml else 'TIE')} "
              f"(lower is better)")
        model_hit_rate = sum(o for _, o in model_po) / n
        print(f"  Real hit rate over this pool: {model_hit_rate:.3f}")
        print("  Model calibration (predicted range -> n, predicted avg, actual hit rate, gap):")
        for row in el.calibration_table(model_po, buckets=(0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)):
            print(f"    {row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
                  f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}")
        print("  Market (no-vig) calibration:")
        for row in el.calibration_table(market_po, buckets=(0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)):
            print(f"    {row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
                  f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}")
        # Does the model know something the market doesn't? A crude signal:
        # among picks where model_prob and market_prob disagree by a real
        # margin, does the SIDE of the disagreement predict the outcome
        # better than chance -- i.e. when the model is more bullish than
        # the market, does the bet hit more often than when it's less
        # bullish? This is exactly what "adds predictive information beyond
        # the market" means operationally. See backtest/info_beyond_market.py
        # for the fuller, statistically rigorous version of this question.
        disagree = [r for r in group if abs(r["model_prob"] - r["market_prob"]) >= 0.03]
        if len(disagree) >= el.MIN_N_DIRECTIONAL:
            more_bullish = [r for r in disagree if r["model_prob"] > r["market_prob"]]
            less_bullish = [r for r in disagree if r["model_prob"] < r["market_prob"]]
            mb_rate = (sum(r["outcome"] for r in more_bullish) / len(more_bullish)
                      if more_bullish else None)
            lb_rate = (sum(r["outcome"] for r in less_bullish) / len(less_bullish)
                      if less_bullish else None)
            print(f"  Directional info check (|model-market| >= 3pts, n={len(disagree)}, "
                  f"{el.sample_size_label(len(disagree))}): when model MORE bullish than market "
                  f"(n={len(more_bullish)}), real hit rate="
                  f"{mb_rate if mb_rate is None else round(mb_rate, 3)}; when model LESS bullish "
                  f"(n={len(less_bullish)}), real hit rate={lb_rate if lb_rate is None else round(lb_rate, 3)}")
        else:
            print(f"  Directional info check: skipped, only {len(disagree)} picks disagree with "
                  f"the market by >=3pts (need >={el.MIN_N_DIRECTIONAL} for even a directional read)")

    _report("POOLED (all families)", rows)
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
