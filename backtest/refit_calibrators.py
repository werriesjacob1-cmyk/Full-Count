#!/usr/bin/env python3
"""
backtest/refit_calibrators.py — automated, safe calibration recheck.

WHY THIS EXISTS

generate_picks.py's own calibration comment block (search "REFIT 2026-08-12")
documents a discipline that was, until now, entirely manual: fit a candidate
calibrator on one time window, evaluate it on a genuinely held-out later
window via evaluate_calibration(), and only ship it if the held-out result
shows a REAL improvement -- not just "the sample got bigger" and not just
"in-sample it looks better." pitcher_outs, nrfi_combined and singles were
checked by hand under that bar and explicitly rejected; a pooled/global
fallback was checked and found to actively HARM the two largest markets.
That history is the reason this script exists: the DISCIPLINE deserved
automating, not the shortcut of skipping it.

WHAT THIS SCRIPT DOES, once per run

  1. Extends backtest/rows.jsonl to cover a fresh trailing window (default:
     the WINDOW_DAYS ending GRADING_LAG_DAYS ago, so every date has had time
     to be fully graded) by calling backtest/engine.py's own resumable
     run_backtest -- already-covered dates are skipped, so a weekly re-run
     only pays for the new week's dates. --no-extend skips this and reads
     --rows as-is.
  2. Loads that window's rows and time-splits them (time_based_split,
     date-ordered -- never random, per SCHEMA.md's no-lookahead rule) into a
     train portion and a held-out portion.
  3. Fits ONE CANDIDATE CALIBRATOR PER MARKET on the train portion
     (fit_calibrators_by_prop_type) -- never a pooled/global fit.
  4. Evaluates every candidate on the held-out portion it was NOT fit on. A
     candidate is only promoted into backtest/calibrators_by_market.json if
     the held-out result beats BOTH the raw probability (brier_improvement
     and log_loss_improvement both meaningfully positive) AND the currently
     shipped calibrator for that market, if one already exists. Anything
     short of that bar is left alone -- the existing calibrator (or raw
     probability, if the market has none yet) keeps shipping.
  5. Writes backtest/calibration_recheck_report.json recording the decision
     and the numbers for EVERY market considered, including the ones where
     nothing changed -- "checked, no change" is a real, visible outcome
     here, not a silent no-op.

WHAT THIS SCRIPT WILL NEVER DO

  - Fall back to a pooled/global calibrator. fit_calibrators_by_prop_type
    only ever produces per-market fits; there is no code path here that
    could reintroduce the pooled curve generate_picks.py's own comment
    documents as actively harmful.
  - Delete a market's existing calibrator because this run's window didn't
    have enough rows to re-evaluate it. Silence in a given week is not
    evidence the existing fit is wrong -- only a strictly better fit
    replaces it.
  - Promote a fit on noise-scale improvement. MIN_BRIER_IMPROVEMENT and
    MIN_HELDOUT_ROWS below are a deliberately conservative, machine-checkable
    proxy for the judgment calls the manual refits made by eye -- when in
    doubt, this script leaves things alone, matching this project's own
    "raw is honest about what it is; a bad correction is not" principle.

    python3 backtest/refit_calibrators.py                # full run
    python3 backtest/refit_calibrators.py --dry-run       # report only, no write
    python3 backtest/refit_calibrators.py --no-extend     # use rows.jsonl as-is
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibration as cal  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS_PATH = os.path.join(HERE, "rows.jsonl")
CALIBRATORS_PATH = os.path.join(HERE, "calibrators_by_market.json")
REPORT_PATH = os.path.join(HERE, "calibration_recheck_report.json")

# A date must be at least this far in the past before its box score is
# trusted final enough to grade off of.
GRADING_LAG_DAYS = 2
# Matches the scale of the manual 2026-08-12 refit (33 real dates).
WINDOW_DAYS = 35

# A candidate calibrator needs at least this many TRAIN rows to be fit at
# all -- well above calibration.py's own MIN_BIN_COUNT=30 floor, because
# that floor is "don't fit noise," not "this is enough to trust in
# production." 200 keeps every market the manual refits actually shipped
# (the smallest was strikeouts at 725 total rows) comfortably fittable.
MIN_FIT_ROWS = 200
# ...and at least this many HELD-OUT rows to trust the evaluation of it.
MIN_HELDOUT_ROWS = 150
# The held-out brier_improvement must clear this before it counts as a real
# improvement rather than sampling noise. Deliberately conservative: this
# script is meant to err toward leaving a market alone, not toward changing
# it.
MIN_BRIER_IMPROVEMENT = 0.0015

# Matches generate_picks.py's own documented choice: a smooth sigmoid that
# degrades gracefully outside its training range, rather than isotonic's
# step function that flatlines at the boundary -- chosen against the
# scoreboard, not by default.
DEFAULT_METHOD = "platt"
DEFAULT_TEST_FRAC = 0.3


# ══════════════════════════════════════════════════════════════════════════
#  Row / window helpers
# ══════════════════════════════════════════════════════════════════════════

def date_window(end=None, window_days=WINDOW_DAYS, grading_lag_days=GRADING_LAG_DAYS):
    end_d = _date.fromisoformat(end) if end else _date.today() - timedelta(days=grading_lag_days)
    start_d = end_d - timedelta(days=window_days)
    return start_d.isoformat(), end_d.isoformat()


def load_rows_window(rows_path, start_date, end_date):
    """Rows from rows_path with date in [start_date, end_date] inclusive.
    Rows without a usable predicted_prob/outcome (e.g. --keep-unpriced
    candidates, per SCHEMA.md) are skipped rather than crashing downstream."""
    rows = []
    if not os.path.exists(rows_path):
        return rows
    with open(rows_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = r.get("date")
            if d is None or r.get("predicted_prob") is None or "outcome" not in r:
                continue
            if start_date <= d <= end_date:
                rows.append(r)
    return rows


def load_existing_calibrators(path):
    if not os.path.exists(path):
        return {}
    return cal.load_calibrators(path)


# ══════════════════════════════════════════════════════════════════════════
#  Decision rule
# ══════════════════════════════════════════════════════════════════════════

def evaluate_candidate(prop_type, candidate, held_out_rows, existing_calibrators,
                        min_heldout_rows=MIN_HELDOUT_ROWS,
                        min_brier_improvement=MIN_BRIER_IMPROVEMENT):
    """Decide whether `candidate` (a freshly-fit Calibrator for `prop_type`)
    should be promoted, given held_out_rows it was NOT fit on. Always
    returns a decision dict, whether promoted or not, so "checked, no
    change" is a real logged fact rather than a silent no-op."""
    seg = [r for r in held_out_rows if r.get("prop_type") == prop_type]
    n = len(seg)
    decision = {
        "prop_type": prop_type,
        "n_train": candidate.meta.get("n_rows"),
        "n_heldout": n,
        "action": "skip",
        "reason": None,
        "brier_improvement": None,
        "log_loss_improvement": None,
        "candidate_brier_after": None,
        "existing_brier_after": None,
    }
    if n < min_heldout_rows:
        decision["reason"] = f"only {n} held-out rows (need >= {min_heldout_rows})"
        return decision

    result = cal.evaluate_calibration(seg, candidate)
    brier_imp = result["brier_improvement"]
    ll_imp = result["log_loss_improvement"]
    decision["brier_improvement"] = brier_imp
    decision["log_loss_improvement"] = ll_imp
    decision["candidate_brier_after"] = result["after"]["brier"]["brier_score"]

    if brier_imp is None or ll_imp is None:
        decision["reason"] = "evaluate_calibration produced no comparable score"
        return decision

    if not (brier_imp > min_brier_improvement and ll_imp > 0):
        decision["reason"] = (
            f"held-out improvement too small to trust "
            f"(brier {brier_imp:+.5f}, log_loss {ll_imp:+.5f}) -- "
            f"raw probability, or the existing calibrator, keeps shipping"
        )
        return decision

    existing = existing_calibrators.get(prop_type)
    if existing is not None:
        existing_eval = cal.evaluate_calibration(seg, existing)
        existing_after = existing_eval["after"]["brier"]["brier_score"]
        decision["existing_brier_after"] = existing_after
        cand_after = decision["candidate_brier_after"]
        if existing_after is not None and cand_after is not None and cand_after >= existing_after:
            decision["reason"] = (
                f"held-out improvement over raw is real, but the currently-shipped "
                f"calibrator is already at least as good on this window "
                f"(existing brier {existing_after:.5f} vs candidate {cand_after:.5f}) "
                f"-- keeping the existing fit"
            )
            return decision

    decision["action"] = "promote"
    tail = ""
    if existing is not None:
        tail = (f"; also beat the existing calibrator "
                f"({decision['existing_brier_after']:.5f} -> {decision['candidate_brier_after']:.5f})")
    decision["reason"] = (
        f"held-out brier improved {brier_imp:+.5f}, log_loss improved {ll_imp:+.5f} "
        f"over raw on {n} held-out rows{tail}"
    )
    return decision


def run_recheck(rows, existing_calibrators, method=DEFAULT_METHOD, test_frac=DEFAULT_TEST_FRAC,
                 min_fit_rows=MIN_FIT_ROWS, min_heldout_rows=MIN_HELDOUT_ROWS,
                 min_brier_improvement=MIN_BRIER_IMPROVEMENT):
    """Core, network-free recheck logic: split -> fit candidates -> decide.
    Returns (decisions, candidates, train, held_out)."""
    train, held_out = cal.time_based_split(rows, test_frac=test_frac)
    candidates, skipped = cal.fit_calibrators_by_prop_type(
        train, method=method, min_rows=min_fit_rows)

    decisions = []
    for prop_type, candidate in sorted(candidates.items()):
        decisions.append(evaluate_candidate(
            prop_type, candidate, held_out, existing_calibrators,
            min_heldout_rows=min_heldout_rows,
            min_brier_improvement=min_brier_improvement))
    for prop_type, n in sorted(skipped.items()):
        decisions.append({
            "prop_type": prop_type, "n_train": n, "n_heldout": None,
            "action": "skip",
            "reason": f"only {n} train rows (need >= {min_fit_rows})",
            "brier_improvement": None, "log_loss_improvement": None,
            "candidate_brier_after": None, "existing_brier_after": None,
        })
    return decisions, candidates, train, held_out


def build_report(decisions, window, method, test_frac, n_rows_total, n_train, n_heldout):
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "window": {"start": window[0], "end": window[1]},
        "method": method,
        "test_frac": test_frac,
        "n_rows_total": n_rows_total,
        "n_train": n_train,
        "n_heldout": n_heldout,
        "min_fit_rows": MIN_FIT_ROWS,
        "min_heldout_rows": MIN_HELDOUT_ROWS,
        "min_brier_improvement": MIN_BRIER_IMPROVEMENT,
        "decisions": decisions,
        "promoted": sorted(d["prop_type"] for d in decisions if d["action"] == "promote"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Automated, safe recheck of per-market probability calibration.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", default=ROWS_PATH, help="backtest rows JSONL path")
    ap.add_argument("--out", default=CALIBRATORS_PATH, help="calibrators_by_market.json path")
    ap.add_argument("--report", default=REPORT_PATH, help="where to write the recheck report")
    ap.add_argument("--start", help="override the window start date (YYYY-MM-DD)")
    ap.add_argument("--end", help="override the window end date (YYYY-MM-DD)")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--grading-lag-days", type=int, default=GRADING_LAG_DAYS)
    ap.add_argument("--test-frac", type=float, default=DEFAULT_TEST_FRAC)
    ap.add_argument("--method", default=DEFAULT_METHOD, choices=["platt", "isotonic"])
    ap.add_argument("--min-fit-rows", type=int, default=MIN_FIT_ROWS)
    ap.add_argument("--min-heldout-rows", type=int, default=MIN_HELDOUT_ROWS)
    ap.add_argument("--min-brier-improvement", type=float, default=MIN_BRIER_IMPROVEMENT)
    ap.add_argument("--no-extend", action="store_true",
                    help="don't run backtest/engine.py to fetch new rows; use --rows as-is")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report, but never write calibrators_by_market.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    verbose = not args.quiet

    if args.start and args.end:
        start, end = args.start, args.end
    else:
        start, end = date_window(end=args.end, window_days=args.window_days,
                                 grading_lag_days=args.grading_lag_days)

    if not args.no_extend:
        import engine  # local: only pulls in generate_picks/mlb_daily's network deps when actually extending
        engine.run_backtest(start, end, args.rows, verbose=verbose)

    rows = load_rows_window(args.rows, start, end)
    existing = load_existing_calibrators(args.out)

    if not rows:
        if verbose:
            print(f"No usable rows in {args.rows} for window {start}..{end} -- nothing to do.")
        report = build_report([], (start, end), args.method, args.test_frac, 0, 0, 0)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return 0

    decisions, candidates, train, held_out = run_recheck(
        rows, existing, method=args.method, test_frac=args.test_frac,
        min_fit_rows=args.min_fit_rows, min_heldout_rows=args.min_heldout_rows,
        min_brier_improvement=args.min_brier_improvement)

    promoted = {d["prop_type"]: candidates[d["prop_type"]]
               for d in decisions if d["action"] == "promote"}
    updated = dict(existing)
    updated.update(promoted)

    report = build_report(decisions, (start, end), args.method, args.test_frac,
                          len(rows), len(train), len(held_out))
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if verbose:
        print(f"\nCalibration recheck: window {start}..{end}, {len(rows)} rows "
              f"({len(train)} train / {len(held_out)} held-out), method={args.method}")
        for d in decisions:
            tag = "PROMOTE" if d["action"] == "promote" else "skip   "
            print(f"  [{tag}] {d['prop_type']:<20} {d['reason']}")
        if promoted:
            print(f"\nPromoted: {', '.join(sorted(promoted))}")
        else:
            print("\nNo market cleared the held-out bar this run -- nothing changed.")

    if promoted:
        if args.dry_run:
            if verbose:
                print(f"[dry-run] would have written {args.out}")
        else:
            cal.save_calibrators(updated, args.out)
            if verbose:
                print(f"Wrote {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
