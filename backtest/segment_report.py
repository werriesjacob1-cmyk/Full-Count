#!/usr/bin/env python3
"""segment_report.py — Phase 3, items 1 + 2: "test whether the new Top
Pick rules are actually selecting better bets" and "build a clean
current-version track record."

Evaluates recommendation_status segments (top_pick / lean / value /
neutral / unclassified-legacy) INDEPENDENTLY of each other -- never
blended into one headline number, per direct instruction: "do not blend
performance generated under materially different selection rules into the
headline performance of the current system."

Per segment, where sample size allows: hit rate, average predicted
probability, calibration, Brier score, log loss, average sportsbook price,
ROI/units, average market edge, and closing-line value (CLV) where a
closing price was actually captured for that pick (see prop_snapshot.py's
hourly archive in data/props/ -- CLV is only computable for picks made on
a date with real snapshot coverage, and is reported as unavailable rather
than guessed otherwise).

"unclassified" is the pre-2026-08-15 legacy system (see
results/ANALYSIS.md's Tier 3) -- reported for reference, never presented
as evidence about the current architecture.

    /tmp/mlbvenv/bin/python3 backtest/segment_report.py
"""
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eval_lib as el
import prop_probability as pp

PROPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "props")

SEGMENTS = ["top_pick", "lean", "value", "neutral", "unclassified"]


def _closing_prices(date):
    """Reuses grade_value.py's own closing-price reconstruction rather than
    a second implementation -- same reasoning as everywhere else in this
    project: one proven implementation, not two that can drift."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import grade_value as gv
    old = gv.PROPS_DIR
    gv.PROPS_DIR = PROPS_DIR
    try:
        return gv.closing_prices(date)
    finally:
        gv.PROPS_DIR = old


def attach_clv(picks):
    """CLV in probability points: closing_implied - bet_implied. Positive
    means the market's own probability estimate moved UP after the bet was
    made -- i.e. the price paid was cheap relative to where the market
    eventually settled, the standard "beat the closing line" signal.
    Computed only where BOTH a real bet price and a real captured closing
    price exist for that exact (player, stat, needs, date); every other
    pick gets clv=None, never a guess."""
    by_date = defaultdict(list)
    for p in picks:
        by_date[p.get("_date")].append(p)
    for date, group in by_date.items():
        if not date:
            continue
        try:
            closes = _closing_prices(date)
        except Exception:
            closes = {}
        if not closes:
            for p in group:
                p["clv"] = None
            continue
        import odds_fanduel as fd
        for p in group:
            odds = p.get("market_odds")
            if odds is None:
                p["clv"] = None
                continue
            proj = p.get("projection") or {}
            key = (fd.normalize_name(p.get("name") or ""), proj.get("stat"), proj.get("needs"))
            close = closes.get(key)
            if not close:
                p["clv"] = None
                continue
            bet_implied = pp.implied_probability(odds)
            close_implied = pp.implied_probability(close["american"])
            if bet_implied is None or close_implied is None:
                p["clv"] = None
                continue
            p["clv"] = round(close_implied - bet_implied, 4)
    return picks


def report_segment(label, picks):
    n = len(picks)
    print(f"\n=== {label}  (n={n}, {el.sample_size_label(n)}) ===")
    if n < el.MIN_N_DIRECTIONAL:
        print(f"  Fewer than {el.MIN_N_DIRECTIONAL} picks -- not even a directional read. "
              f"No further numbers printed for this segment.")
        return

    graded = el.graded_only(picks)
    priced = el.priced_only(graded)
    print(f"  {len(graded)}/{n} graded (game final); {len(priced)}/{len(graded)} of those "
          f"carry a real market price")

    if graded:
        hits = sum(1 for p in graded if p["grade"] == "hit")
        print(f"  Hit rate: {hits}/{len(graded)} = {hits/len(graded):.3f}")
        avg_prob = sum(p.get("hit_probability") or 0 for p in graded) / len(graded)
        print(f"  Average predicted (displayed) probability: {avg_prob:.3f}")

    pairs = [(p["hit_probability"], 1.0 if p["grade"] == "hit" else 0.0)
            for p in graded if p.get("hit_probability") is not None]
    if len(pairs) >= el.MIN_N_DIRECTIONAL:
        print(f"  Brier score: {el.brier(pairs):.4f}   Log loss: {el.log_loss(pairs):.4f}")
        print("  Calibration (predicted range -> n, predicted avg, actual, gap):")
        for row in el.calibration_table(pairs):
            print(f"    {row['range']:>10s}  n={row['n']:3d}  pred={row['predicted']:.3f}  "
                  f"actual={row['actual']:.3f}  gap={row['gap']:+.3f}")
    else:
        print(f"  Brier/log loss/calibration: skipped, only {len(pairs)} priced+graded "
              f"picks (need >={el.MIN_N_DIRECTIONAL})")

    if priced:
        avg_odds = sum(p["market_odds"] for p in priced) / len(priced)
        print(f"  Average sportsbook price: {avg_odds:+.0f} "
              f"(mean American odds across {len(priced)} priced picks)")
        edges = [p.get("market_edge") for p in priced if p.get("market_edge") is not None]
        if edges:
            print(f"  Average market edge (model prob - market implied): "
                  f"{sum(edges)/len(edges):+.4f} (n={len(edges)})")
        roi = el.realized_roi([p for p in priced if p.get("grade") in ("hit", "miss")])
        if roi["n"] >= el.MIN_N_DIRECTIONAL:
            print(f"  Realized ROI (flat 1-unit stake): {roi['roi']:+.3f} "
                  f"({roi['units']:+.2f} units over {roi['n']} bets, {el.sample_size_label(roi['n'])})")
        else:
            print(f"  Realized ROI: skipped, only {roi['n']} graded+priced picks")

    clv_vals = [p["clv"] for p in priced if p.get("clv") is not None]
    if len(clv_vals) >= el.MIN_N_DIRECTIONAL:
        avg_clv = sum(clv_vals) / len(clv_vals)
        beat = sum(1 for v in clv_vals if v > 0)
        print(f"  Closing-line value: avg {avg_clv:+.4f} probability points, beat closing "
              f"{beat}/{len(clv_vals)} times ({el.sample_size_label(len(clv_vals))}) -- "
              f"only computable on dates with real hourly snapshot coverage in data/props/")
    else:
        print(f"  Closing-line value: unavailable for {n - len(clv_vals)}/{n} picks (no "
              f"captured closing price for that date/player/market — see data/props/ "
              f"coverage) -- NOT fabricated, reported as unavailable")


def main():
    picks = el.load_graded_picks()
    if not picks:
        print("No graded picks found in results/grades_*.json.")
        return 1
    picks = attach_clv(picks)

    dates = sorted({p.get("_date") for p in picks if p.get("_date")})
    print(f"Loaded {len(picks)} picks across {len(dates)} graded day(s): "
          f"{dates[0] if dates else '?'} .. {dates[-1] if dates else '?'}")

    by_status = defaultdict(list)
    for p in picks:
        status = p.get("recommendation_status") or "unclassified"
        by_status[status].append(p)

    n_current = sum(len(by_status.get(s, [])) for s in ("top_pick", "lean", "value", "neutral"))
    print(f"\n{n_current} picks carry a real recommendation_status from the current "
          f"(2026-08-15+) architecture; {len(by_status.get('unclassified', []))} are "
          f"legacy (pre-rebuild, see results/ANALYSIS.md Tier 3).")
    if n_current == 0:
        print("*** The current architecture has ZERO graded days as of this run. That is "
              "the honest, correct starting point -- not a bug, not padded, not borrowed "
              "from the legacy system. See this project's Phase 3 report, item B. ***")

    for status in SEGMENTS:
        report_segment(status, by_status.get(status, []))

    return 0


if __name__ == "__main__":
    sys.exit(main())
