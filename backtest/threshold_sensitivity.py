#!/usr/bin/env python3
"""threshold_sensitivity.py — Phase 3, item 9: "do not assume 60% is
permanently optimal merely because we chose it. Build the measurement
framework needed to eventually determine whether Top Picks perform better
with floors such as 55/60/65/70%, while considering price/value and
sample size. Do not optimize the threshold on the tiny recent sample. The
goal is to learn the right threshold from forward/out-of-sample evidence."

THIS SCRIPT DOES NOT CHANGE THE LIVE 60% FLOOR. It is a measurement tool:
re-runs the REAL recommendation.classify_recommendation() function (not a
reimplementation of its logic) once per candidate PER swept floor, by
temporarily overriding recommendation.TOP_PICK_MIN_PROB -- every other
requirement (A/B evidence, confirmed lineup, price/value test) stays
exactly as it is in production. This answers "how many picks would have
qualified, and how did they do" at each floor, using the real gate logic,
not an approximation of it.

HONEST LIMITATION: lineup_assumed was not persisted on saved picks before
this Phase 3 pass (see results/ANALYSIS.md) -- historical picks all read
as lineup_assumed=None (i.e. "confirmed"), which very likely overstates
how many of them would truly have cleared a real Top Pick gate. This
script prints that caveat rather than hiding it. It will stop applying to
picks made from this commit forward, since lineup_assumed is now real.

    /tmp/mlbvenv/bin/python3 backtest/threshold_sensitivity.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eval_lib as el
import recommendation as rec

FLOORS = (0.55, 0.60, 0.65, 0.70)


def simulate_floor(picks, floor):
    """Re-runs the REAL classify_recommendation() with TOP_PICK_MIN_PROB
    temporarily overridden. Restores it in a finally block regardless of
    outcome -- this must never leave the real threshold patched if
    something raises."""
    original = rec.TOP_PICK_MIN_PROB
    rec.TOP_PICK_MIN_PROB = floor
    now = datetime.now(timezone.utc)
    try:
        would_qualify = []
        for p in picks:
            cand = {
                "hit_probability": p.get("hit_probability"),
                "reliability": p.get("reliability"),
                "lineup_assumed": p.get("lineup_assumed"),
                "lift": p.get("lift"),
                "market_odds": p.get("market_odds"),
                "prob_ci": p.get("prob_ci"),
            }
            result = rec.classify_recommendation(cand, now=now, data_fresh=True,
                                                  fresh_reasons=[])
            if result["status"] == "top_pick":
                would_qualify.append(p)
    finally:
        rec.TOP_PICK_MIN_PROB = original
    return would_qualify


def report_floor(floor, picks):
    graded = el.graded_only(picks)
    priced = el.priced_only(graded)
    n = len(picks)
    print(f"\n--- floor={floor:.0%}  n_qualifying={n} ---")
    if n < el.MIN_N_DIRECTIONAL:
        print(f"    Fewer than {el.MIN_N_DIRECTIONAL} qualifying picks -- no further "
              f"numbers, not even directional.")
        return {"floor": floor, "n": n}
    hits = sum(1 for p in graded if p["grade"] == "hit")
    hit_rate = hits / len(graded) if graded else None
    print(f"    Graded: {len(graded)}/{n}   Hit rate: {hit_rate:.3f} ({el.sample_size_label(len(graded))})"
          if hit_rate is not None else "    No graded picks at this floor")
    roi = el.realized_roi([p for p in priced if p.get("grade") in ("hit", "miss")])
    if roi["n"] >= el.MIN_N_DIRECTIONAL:
        avg_odds = sum(p["market_odds"] for p in priced) / len(priced) if priced else None
        print(f"    Realized ROI: {roi['roi']:+.3f} ({roi['units']:+.2f} units over "
              f"{roi['n']} bets, {el.sample_size_label(roi['n'])})   "
              f"avg price: {avg_odds:+.0f}" if avg_odds is not None else "")
    else:
        print(f"    ROI: skipped, only {roi['n']} graded+priced picks")
    return {"floor": floor, "n": n, "n_graded": len(graded), "hit_rate": hit_rate,
           "roi": roi.get("roi"), "roi_n": roi.get("n")}


def main():
    picks = el.load_graded_picks()
    if not picks:
        print("No graded picks found — nothing to simulate.")
        return 1

    n_lineup_tracked = sum(1 for p in picks if p.get("lineup_assumed") is not None)
    print(f"Loaded {len(picks)} picks. lineup_assumed is tracked (non-None) on "
          f"{n_lineup_tracked}/{len(picks)} of them.")
    if n_lineup_tracked < len(picks):
        print("*** HONEST LIMITATION: lineup_assumed was not persisted on saved picks "
              "before this Phase 3 pass -- untracked picks are treated as "
              "lineup_assumed=None (== 'confirmed') by classify_recommendation(), which "
              "likely OVERSTATES how many would truly have cleared a real Top Pick gate. "
              "Results below should be read with that in mind until enough post-fix data "
              "accumulates. See results/ANALYSIS.md. ***")

    print("\nSimulating the REAL classify_recommendation() gate at each floor (probability "
          "requirement swept; evidence grade, lineup, and price/value tests unchanged):")

    summary = []
    for floor in FLOORS:
        qualifying = simulate_floor(picks, floor)
        summary.append(report_floor(floor, qualifying))

    print("\n" + "=" * 78)
    print("SUMMARY (do not pick a 'winner' from this alone -- see the script's own docstring "
          "and this project's Phase 3 report, item I)")
    print("=" * 78)
    print(f"{'floor':>8s}{'n':>8s}{'hit rate':>12s}{'ROI':>10s}{'roi n':>8s}")
    for row in summary:
        hr = f"{row['hit_rate']:.3f}" if row.get("hit_rate") is not None else "n/a"
        roi_s = f"{row['roi']:+.3f}" if row.get("roi") is not None else "n/a"
        print(f"{row['floor']:>7.0%} {row['n']:>7d} {hr:>11s} {roi_s:>9s} {row.get('roi_n', 0):>7d}")

    print("\nThis is a MEASUREMENT, not a decision. Do not change the live 60% floor from "
          "this sample -- re-run monthly as the current-architecture graded window grows, "
          "and only act once every floor's n clears a real confidence threshold "
          f"(>= {el.MIN_N_CONFIDENT} graded picks, ideally across >= 30 distinct days).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
