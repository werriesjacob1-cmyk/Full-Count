#!/usr/bin/env python3
"""
backtest/backfill_support_bins.py — one-time metadata addition for the 3
currently-shipped calibrators, adding calibration.compute_support_bins()'s
output to each one's .meta WITHOUT touching its fitted A/B parameters.

WHY THIS IS SEPARATE FROM refit_calibrators.py: that script only ever
recomputes support_bins as a byproduct of actually re-FITTING a candidate
(see calibration.fit_calibrator, which now attaches support_bins
automatically). The 3 calibrators live in backtest/calibrators_by_market.json
today were fit before this metadata existed and are NOT being refit here --
their fitted params must stay byte-identical (verified below). This script
reconstructs each one's own recorded training row set (exact prop_type +
date_range from its own .meta) from the real backtest/rows.jsonl on disk,
and computes support_bins from THAT -- the same basis fit_calibrator now
uses for every future fit, applied retroactively to these three so the
apply-time gate in generate_picks.py has real data to check against
immediately, not only after the next scheduled recheck.

HONEST LIMITATION, stated once rather than buried: backtest/rows.jsonl is
gitignored and grows over time (new dates get appended, and per-date rows
can be regenerated). Filtering it to a calibrator's own recorded
prop_type+date_range is NOT guaranteed to reproduce the exact row set
originally used to fit that calibrator byte-for-byte -- verified directly:
strikeouts reconstructs to exactly 609 rows (matches meta.n_rows exactly),
but hits reconstructs to 3096 rows (meta says 2960, a +136 row / +4.6%
difference) and hits_runs_rbis to 3436 rows (meta says 3480, a -44 row /
-1.3% difference). This script reports every reconstruction's row count
against the recorded n_rows so the discrepancy is visible, not hidden, and
proceeds anyway: a support REGION computed from a few percent more or fewer
rows scattered across dozens of dates and ~20 probability bins is not
expected to flip any bin's supported/unsupported classification except
possibly right at a margin, and using real current data is strictly better
than not computing support at all. If a bin-level discrepancy ever matters,
the fix is a fresh refit (which recomputes support_bins from its own exact
train set automatically), not a patch to this script.

    /tmp/mlbvenv/bin/python3 backtest/backfill_support_bins.py [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration as cal  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS_PATH = os.path.join(HERE, "rows.jsonl")
CALIBRATORS_PATH = os.path.join(HERE, "calibrators_by_market.json")


def load_rows(rows_path=ROWS_PATH):
    rows = []
    with open(rows_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("predicted_prob") is None or "outcome" not in r or "date" not in r:
                continue
            rows.append(r)
    return rows


def rows_for_calibrator(all_rows, market, prop_type, date_range):
    """Reconstruct a calibrator's own recorded training window.

    fit_calibrators_by_prop_type() always pre-segments rows by REAL
    prop_type via segment_by_prop_type() before calling
    fit_calibrator(seg_rows, prop_type=None) on each segment -- so a
    recorded meta.prop_type of "all" (strikeouts' case) does NOT mean "no
    prop_type filter was applied," it means "the filter was applied by the
    caller, not by fit_calibrator itself," and the real stat is the outer
    market key. Confirmed against real data: filtering by prop_type="all"
    as a literal no-op match (every row in the date range) reconstructs
    43,928 rows against a recorded n_rows of 609 -- filtering by the market
    key "strikeouts" instead reconstructs to exactly 609, an exact match."""
    start, end = date_range
    real_prop_type = market if prop_type == "all" else prop_type
    return [r for r in all_rows if r.get("prop_type") == real_prop_type
           and start <= r["date"] <= end]


def backfill(calibrators_path=CALIBRATORS_PATH, rows_path=ROWS_PATH, dry_run=False):
    with open(calibrators_path, encoding="utf-8") as f:
        payload = json.load(f)
    all_rows = load_rows(rows_path)

    updated = copy.deepcopy(payload)
    report = []
    for market, entry in payload.items():
        meta = entry.get("meta", {})
        prop_type = meta.get("prop_type")
        date_range = meta.get("date_range")
        recorded_n = meta.get("n_rows")
        before_params = entry.get("params")

        recon_rows = rows_for_calibrator(all_rows, market, prop_type, date_range) if date_range else []
        support_bins = cal.compute_support_bins(recon_rows, bin_width=cal.SUPPORT_BIN_WIDTH,
                                                 min_count=cal.MIN_BIN_COUNT)
        n_supported = sum(1 for b in support_bins if b["supported"])

        updated[market]["meta"]["support_bin_width"] = cal.SUPPORT_BIN_WIDTH
        updated[market]["meta"]["support_min_count"] = cal.MIN_BIN_COUNT
        updated[market]["meta"]["support_rows_basis"] = "train_only_reconstructed"
        updated[market]["meta"]["support_bins"] = support_bins
        # Params must be byte-identical to what was on disk before this ran --
        # this script adds metadata only, never re-fits.
        assert updated[market]["params"] == before_params, (
            f"{market}: params changed -- this script must never refit, aborting")

        report.append({
            "market": market, "recorded_n_rows": recorded_n, "reconstructed_n_rows": len(recon_rows),
            "row_count_delta": len(recon_rows) - (recorded_n or 0),
            "n_bins": len(support_bins), "n_supported_bins": n_supported,
            "supported_range": [
                min((b["lo"] for b in support_bins if b["supported"]), default=None),
                max((b["hi"] for b in support_bins if b["supported"]), default=None),
            ],
        })

    if dry_run:
        print(json.dumps(report, indent=2))
        print("[dry-run] would write", calibrators_path)
    else:
        with open(calibrators_path, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2)
        print(json.dumps(report, indent=2))
        print(f"Wrote {calibrators_path}")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calibrators", default=CALIBRATORS_PATH)
    ap.add_argument("--rows", default=ROWS_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    backfill(args.calibrators, args.rows, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
