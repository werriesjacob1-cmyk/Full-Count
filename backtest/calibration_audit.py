#!/usr/bin/env python3
"""calibration_audit.py — Phase 3, item 4: "the probabilities now matter
much more because Top Picks emphasize hit probability. Audit calibration
carefully... analyze this by prop family where the sample permits. Do not
allow strong performance in one market to hide poor calibration in
another."

A group of bets predicted at ~70% should eventually win ~70% of the time.
This buckets every graded, probability-carrying pick into 50-55/55-60/
60-65/65-70/70-75/75%+ (plus a below-50% catch-all, since this pipeline
does ship some sub-50% reads on Value/Longshot bets), POOLED first, then
CROSSED with prop family -- a family with a real, sizeable calibration gap
must never be hidden inside a good pooled number.

DATA SCOPE: every graded pick with a probability, from results/
grades_*.json -- see results/ANALYSIS.md for why this is currently all
Tier 3 (legacy, pre-2026-08-15) data, and why that is reported explicitly
rather than silently presented as evidence about the CURRENT probability
engine. Re-run this script as current-architecture graded days accumulate;
segment on recommendation_status once there's enough of that data to
matter (see backtest/segment_report.py for that cut).

    /tmp/mlbvenv/bin/python3 backtest/calibration_audit.py
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eval_lib as el

BUCKETS = (0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 1.01)


def _print_table(pairs, indent="  "):
    rows = el.calibration_table(pairs, buckets=BUCKETS)
    if not rows:
        print(f"{indent}(no data)")
        return
    for row in rows:
        flag = ""
        if row["n"] >= el.MIN_N_REPORTABLE and abs(row["gap"]) >= 0.08:
            flag = "  <-- REAL GAP (n reportable, |gap| >= 8 points)"
        elif row["n"] < el.MIN_N_REPORTABLE:
            flag = "  (thin -- treat as noise)"
        print(f"{indent}{row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
              f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}  brier={row['brier']:.4f}  "
              f"logloss={row['log_loss']:.4f}{flag}")


def main():
    picks = el.graded_only(el.load_graded_picks())
    pairs_all = [(p["hit_probability"], 1.0 if p["grade"] == "hit" else 0.0)
                for p in picks if p.get("hit_probability") is not None]
    if not pairs_all:
        print("No graded, probability-carrying picks found — nothing to audit.")
        return 1

    dates = sorted({p.get("_date") for p in picks if p.get("_date")})
    n_current = sum(1 for p in picks if p.get("recommendation_status"))
    print(f"{len(pairs_all)} graded, probability-carrying picks across {len(dates)} day(s): "
          f"{dates[0]} .. {dates[-1]}")
    print(f"{n_current} of these carry a current-architecture recommendation_status "
          f"(2026-08-15+); {len(pairs_all) - n_current} are legacy (Tier 3, see "
          f"results/ANALYSIS.md) -- calibration below reflects whichever the model actually "
          f"produced across this whole window, not a claim isolated to today's system.\n")

    print("=" * 90)
    print("POOLED CALIBRATION (every graded pick, every family)")
    print("=" * 90)
    _print_table(pairs_all)
    print(f"\n  Pooled Brier: {el.brier(pairs_all):.4f}   Pooled log loss: {el.log_loss(pairs_all):.4f}")

    print("\n" + "=" * 90)
    print("CALIBRATION BY PROP FAMILY -- a family hiding inside the pooled number above "
          "would show up here")
    print("=" * 90)
    by_stat = defaultdict(list)
    for p in picks:
        if p.get("hit_probability") is None:
            continue
        stat = (p.get("projection") or {}).get("stat") or "?"
        by_stat[stat].append((p["hit_probability"], 1.0 if p["grade"] == "hit" else 0.0))

    worst_gaps = []
    for stat in sorted(by_stat, key=lambda s: -len(by_stat[s])):
        pairs = by_stat[stat]
        n = len(pairs)
        if n < el.MIN_N_DIRECTIONAL:
            print(f"\n--- {stat}  (n={n}) --- SKIPPED, fewer than {el.MIN_N_DIRECTIONAL} "
                  f"picks, not enough to say anything")
            continue
        print(f"\n--- {stat}  (n={n}, {el.sample_size_label(n)}) ---")
        _print_table(pairs, indent="    ")
        for row in el.calibration_table(pairs, buckets=BUCKETS):
            if row["n"] >= el.MIN_N_REPORTABLE:
                worst_gaps.append((abs(row["gap"]), stat, row["range"], row["n"], row["gap"]))

    print("\n" + "=" * 90)
    print("WORST REPORTABLE CALIBRATION GAPS (n >= %d), RANKED" % el.MIN_N_REPORTABLE)
    print("=" * 90)
    worst_gaps.sort(reverse=True)
    if not worst_gaps:
        print("  No bucket anywhere reached the reportable sample-size floor yet. Nothing "
              "here can be called a confirmed calibration problem or a confirmed clean bill "
              "of health -- re-run as the graded window grows.")
    for absgap, stat, rng, n, gap in worst_gaps[:10]:
        print(f"  {stat:18s} {rng:>10s}  n={n:3d}  gap={gap:+.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
