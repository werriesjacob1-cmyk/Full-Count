#!/usr/bin/env python3
"""reliability_bands.py — builds real, historically-measured confidence
intervals for the probability bases that today withhold prob_ci entirely
(modelled_shrunk, league_only, and any calibrated-but-unsupported line),
per the 2026-08-19 audit in generate_picks.py's attach_reliability().

WHY THIS EXISTS. classify_recommendation()'s require_robust=True gate
correctly fails closed when prob_lo is absent -- that is not the bug. The
bug is that a real, defensible prob_lo has never been built for 10 of 14
markets, so those markets can NEVER produce a Top Pick or Value bet no
matter how strong the read, even when real historical evidence exists to
support a genuine interval. This script builds that evidence.

METHOD. Rather than propagate per-player sampling uncertainty through the
DP convolution (a real but much larger undertaking, and one this project's
own standing rule already declines to approximate -- see attach_reliability's
"no defensible calibrated-interval method exists yet" comment), this
measures the ACTUAL historical reliability of predictions like this one,
directly against real graded outcomes:

  1. Take every row backtest/engine.py has produced (backtest/rows_backfill
     .jsonl -- point-in-time-safe by construction, see verify_no_lookahead).
  2. For the three markets with a fitted Platt curve (hits, hits_runs_rbis,
     strikeouts), reproduce EXACTLY what apply_calibration() would have
     shown at the time -- by calling the real generate_picks._calibrate_one()
     against the real fitted curve, including its support-boundary decline
     -- so a bucket is keyed on the number that was actually ever displayed,
     never a number nobody saw.
  3. Bucket every (stat, needs) pair's rows into 5-point probability bins.
  4. Within each bin with enough real rows, compute the ACTUAL hit rate and
     a Wilson interval on it (the same interval function generate_picks.py
     already uses for empirical per-player CIs, applied here to a market-
     level cell instead of a player-level one).

This produces, per (stat, needs, probability bucket), a REAL empirical
answer to "when this pipeline said a number in this range for this market,
how often did it actually hit, and how sure are we of that rate" -- grounded
in graded outcomes, not a parametric assumption about a convolution's own
uncertainty. A bucket with too few rows to trust honestly has no answer,
which is the same fail-closed behavior the current code already has, just
now backed by an attempt to earn coverage rather than a permanent blanket
refusal.

Re-run this periodically as backtest/rows_backfill.jsonl grows (it is a
live, resumable, still-running backfill as of 2026-08-24) -- coverage will
only improve, never invalidate what's already measured, since a wider
sample can only narrow a Wilson interval or leave a thin bucket thin.

    /tmp/mlbvenv/bin/python3 backtest/reliability_bands.py
"""
import json
import os
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_picks as gp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS_PATH = os.path.join(ROOT, "backtest", "rows_backfill.jsonl")
OUT_PATH = os.path.join(ROOT, "backtest", "reliability_bands.json")

# Markets whose displayed probability passes through Platt calibration --
# everything else's rows_backfill.jsonl predicted_prob IS the number that
# was (or would be) displayed, since generate_picks.py never calibrates
# any other market (see apply_calibration's own scope comment).
CALIBRATED_MARKETS = {"hits", "hits_runs_rbis", "strikeouts"}

BUCKET_WIDTH = 0.05

# A cell needs at least this many real graded rows before its Wilson
# interval is trusted as a genuine market-level answer. Chosen well above
# eval_lib.MIN_N_REPORTABLE (20, calibrated for the much smaller live-
# graded-picks sample) because this dataset is two orders of magnitude
# larger for the markets that matter most (hits/total_bases/home_runs each
# already have 40k+ rows) -- a higher floor costs nothing there and is
# exactly what keeps thin markets (strikeouts, pitcher_outs, nrfi_combined)
# honestly uncovered rather than reporting a false-precision interval off
# a handful of rows.
MIN_BAND_N = 150


def _bucket(p):
    b = int(p // BUCKET_WIDTH) * BUCKET_WIDTH
    return round(min(max(b, 0.0), 0.95), 2)


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              check=False, text=True, capture_output=True).stdout.strip()
    except Exception:
        return None


def build(rows_path=ROWS_PATH, min_n=MIN_BAND_N):
    calibrator = gp.load_calibrator()
    per_market, glob = calibrator if calibrator else ({}, None)

    # (stat, needs, bucket) -> {"n": int, "hits": int, "prob_sum": float}
    cells = defaultdict(lambda: {"n": 0, "hits": 0, "prob_sum": 0.0})
    dates = set()
    n_rows = 0
    n_skipped = 0

    with open(rows_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                # Last line of a file another process is actively
                # appending to can be truncated mid-write -- skip it
                # rather than fail the whole build.
                n_skipped += 1
                continue
            if row.get("fair_test") is not True:
                n_skipped += 1
                continue
            stat = row.get("prop_type")
            needs = row.get("needs")
            raw_prob = row.get("predicted_prob")
            outcome = row.get("outcome")
            if stat is None or needs is None or raw_prob is None or outcome is None:
                n_skipped += 1
                continue

            display_prob = raw_prob
            if stat in CALIBRATED_MARKETS and calibrator is not None:
                cp, _by = gp._calibrate_one(raw_prob, stat, per_market, glob)
                if cp is not None:
                    display_prob = cp

            key = (stat, int(needs), _bucket(display_prob))
            cell = cells[key]
            cell["n"] += 1
            cell["hits"] += int(outcome)
            cell["prob_sum"] += display_prob
            n_rows += 1
            dates.add(row.get("date"))

    bands = defaultdict(dict)
    n_reportable_cells = 0
    for (stat, needs, bucket), cell in cells.items():
        n = cell["n"]
        if n < min_n:
            continue
        hits = cell["hits"]
        actual_rate = hits / n
        predicted_mean = cell["prob_sum"] / n
        lo, hi = gp._wilson_interval(hits, n)
        bands[f"{stat}_{needs}"][f"{bucket:.2f}"] = {
            "n": n,
            "actual_rate": round(actual_rate, 4),
            "predicted_mean": round(predicted_mean, 4),
            "bias": round(actual_rate - predicted_mean, 4),
            "wilson_lo": round(lo, 4),
            "wilson_hi": round(hi, 4),
        }
        n_reportable_cells += 1

    out = {
        "_meta": {
            "built_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "source_rows_path": os.path.relpath(rows_path, ROOT),
            "n_rows_considered": n_rows,
            "n_rows_skipped": n_skipped,
            "n_dates": len(dates),
            "date_range": [min(dates), max(dates)] if dates else None,
            "bucket_width": BUCKET_WIDTH,
            "min_band_n": min_n,
            "n_reportable_cells": n_reportable_cells,
            "n_cells_seen": len(cells),
        },
        "bands": dict(bands),
    }
    return out


def main():
    out = build()
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    meta = out["_meta"]
    print(f"Built {OUT_PATH}")
    print(f"  {meta['n_rows_considered']} rows considered ({meta['n_rows_skipped']} skipped), "
          f"{meta['n_dates']} dates {meta['date_range']}")
    print(f"  {meta['n_reportable_cells']} of {meta['n_cells_seen']} (stat,needs,bucket) cells "
          f"reach the n>={meta['min_band_n']} floor")
    by_stat = defaultdict(int)
    for key in out["bands"]:
        stat = key.rsplit("_", 1)[0]
        by_stat[stat] += len(out["bands"][key])
    for stat, n in sorted(by_stat.items(), key=lambda kv: -kv[1]):
        print(f"    {stat:20s} {n:3d} reportable buckets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
