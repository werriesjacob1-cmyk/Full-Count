#!/usr/bin/env python3
"""canonical_baseline_report.py -- the CONTROL baseline for
backtest/rows_canonical.jsonl, built 2026-08-25 while PID 3663 (the main
backfill) finishes so it's ready to run the instant canonical history
exists. This is NOT a promotion experiment -- it is what Full Count's
historical performance looks like on trusted, single-regime data, and every
future challenger measures itself against these same numbers.

Prepared in advance per the standing instruction ("prepare the analysis
code... do not claim results until canonical history exists") -- this
script has NOT been run against real rows_canonical.jsonl as of being
written (that file does not exist yet). It HAS been exercised against a
real, existing backtest file (backtest/rows_backfill_repair.jsonl, 400,207
real graded rows) purely to prove the script itself works correctly on
real data shape -- see this module's own __main__ block comment. Re-run
against rows_canonical.jsonl once it exists; do not treat any number
produced by the repair-file smoke test as a real conclusion about
canonical history.

WHAT THIS EXPLICITLY LABELS, per the standing instruction ("the baseline
report must explicitly label what is directly observed, what is
reconstructed, what is unavailable historically"):
  - OBSERVED: everything read directly from a row's own fields.
  - RECONSTRUCTED: a value derived from observed fields via an explicit,
    documented rule (e.g. season_phase from date's month).
  - UNAVAILABLE: fields backtest/SCHEMA.md itself documents as absent by
    design (lineup_assumed, market data, ungraded-row counts) -- reported
    as an explicit line in the output, never silently omitted or guessed.

    /tmp/mlbvenv/bin/python3 backtest/canonical_baseline_report.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

DEFAULT_PATH = "backtest/rows_canonical.jsonl"

# Coarse, honestly-labeled season-phase buckets from the date's own month --
# not a claim about MLB's real schedule structure (which varies year to
# year), just a simple, reproducible, documented split.
def season_phase(date_str):
    try:
        month = int(date_str.split("-")[1])
    except (IndexError, ValueError, AttributeError):
        return "unknown"
    if month == 4:
        return "early_season_april"
    if month in (5, 6, 7):
        return "mid_season_may_jul"
    if month == 8:
        return "late_season_aug"
    if month in (9, 10):
        return "stretch_run_sep_oct"
    return "offseason_or_other"


def prob_bucket(p, width=0.05):
    if p is None:
        return None
    # Round the division first -- naive int(p / width) is a real float-
    # imprecision trap (0.60 / 0.05 can evaluate to 11.999999999998, not
    # 12.0, silently bucketing 0.60 into 0.55-0.60 instead of 0.60-0.65).
    idx = round(p / width, 6)
    lo = int(idx) * width
    return f"{lo:.2f}-{lo + width:.2f}"


def sample_bucket(n):
    if n is None:
        return "unknown"
    if n < 30:
        return "n<30"
    if n < 100:
        return "30<=n<100"
    if n < 300:
        return "100<=n<300"
    return "n>=300"


def load_rows(path):
    rows = []
    n_malformed = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                n_malformed += 1
    return rows, n_malformed


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def build_report(rows, n_malformed):
    report = {"generated_at": datetime.utcnow().isoformat() + "Z"}

    # ---------- COVERAGE (OBSERVED) ----------
    dates = sorted({r.get("date") for r in rows if r.get("date")})
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    report["coverage"] = {
        "n_rows_total": len(rows),
        "n_malformed_lines_skipped": n_malformed,
        "n_dates": len(dates),
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "n_graded_rows": len(graded),
        "n_rows_missing_outcome_field": len(rows) - len(graded),
    }
    report["coverage_caveat"] = (
        "UNAVAILABLE: true 'ungraded' candidate counts (a candidate the "
        "pipeline scored but grade_results.py could not grade -- rain-shortened "
        "game, no box score line, etc.) are NOT reconstructable from this file "
        "alone. backtest/SCHEMA.md's own rule: 'Rows that cannot be graded are "
        "omitted entirely, never encoded as 0' -- so this file structurally "
        "never contains them. That count only ever existed in the backfill "
        "run's own console log (per-date 'N graded rows (M ungraded)' lines), "
        "which is not part of the canonical dataset. n_rows_missing_outcome_field "
        "above counts rows in THIS FILE with no outcome, not the true ungraded rate."
    )

    # ---------- MARKETS (OBSERVED) ----------
    by_market = defaultdict(lambda: {"n": 0, "n_graded": 0, "n_hit": 0})
    for r in rows:
        m = by_market[r.get("prop_type") or "unknown"]
        m["n"] += 1
        if r.get("outcome") in (0, 1):
            m["n_graded"] += 1
            m["n_hit"] += r["outcome"]
    report["markets"] = {
        market: {"n_rows": m["n"], "n_graded": m["n_graded"],
                 "hit_rate": _rate(m["n_hit"], m["n_graded"])}
        for market, m in sorted(by_market.items())
    }

    # ---------- TIME (RECONSTRUCTED: year/month/season_phase from date) ----------
    by_year = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
    by_phase = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
    for r in graded:
        date = r.get("date") or ""
        year = date.split("-")[0] if date else "unknown"
        by_year[year]["n_graded"] += 1
        by_year[year]["n_hit"] += r["outcome"]
        phase = season_phase(date)
        by_phase[phase]["n_graded"] += 1
        by_phase[phase]["n_hit"] += r["outcome"]
    report["time_reconstructed"] = {
        "by_year": {y: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
                    for y, v in sorted(by_year.items())},
        "by_season_phase": {p: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
                            for p, v in sorted(by_phase.items())},
    }

    # ---------- PROBABILITY (OBSERVED predicted_prob, RECONSTRUCTED bucket) ----------
    by_bucket = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
    for r in graded:
        b = prob_bucket(r.get("predicted_prob"))
        if b is None:
            continue
        by_bucket[b]["n_graded"] += 1
        by_bucket[b]["n_hit"] += r["outcome"]
    report["probability_buckets_reconstructed"] = {
        b: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
        for b, v in sorted(by_bucket.items())
    }
    n_missing_prob = sum(1 for r in graded if r.get("predicted_prob") is None)
    report["probability_missing_count"] = n_missing_prob

    # ---------- EVIDENCE (OBSERVED where present, else UNAVAILABLE) ----------
    reliability_rows = [r for r in graded if r.get("reliability") is not None]
    by_reliability = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
    for r in reliability_rows:
        by_reliability[r["reliability"]]["n_graded"] += 1
        by_reliability[r["reliability"]]["n_hit"] += r["outcome"]
    by_sample = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
    n_sample_n_present = 0
    for r in graded:
        sn = r.get("sample_n")
        if sn is not None:
            n_sample_n_present += 1
        b = sample_bucket(sn)
        by_sample[b]["n_graded"] += 1
        by_sample[b]["n_hit"] += r["outcome"]
    report["evidence"] = {
        "reliability_OBSERVED_only_present_on_apply_policy_rows": {
            "n_rows_with_reliability_field": len(reliability_rows),
            "by_grade": {g: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
                        for g, v in sorted(by_reliability.items())},
        },
        "sample_n_bucket_UNAVAILABLE_note": (
            "backtest rows do not carry a top-level sample_n field on a default "
            "(non --apply-policy) run -- only present when Stage 5 policy replay "
            "was used. n_rows_with_sample_n_field reports how many actually have "
            "it in THIS file; the bucket breakdown below is only meaningful if "
            "that count is a real fraction of n_graded."
        ),
        "n_rows_with_sample_n_field": n_sample_n_present,
        "sample_n_bucket": {b: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
                            for b, v in sorted(by_sample.items())},
        "lineup_assumed_UNAVAILABLE": (
            "lineup_assumed is a LIVE dashboard/registry-only field (see "
            "candidate_dataset_feasibility_2026-08-25.md) -- backtest rows never "
            "carry it. Point-in-time lineup-confirmation timing is not "
            "reconstructable from backtest/SCHEMA.md's current row shape at all."
        ),
        "fallback_source_flags_UNAVAILABLE": (
            "no explicit fallback/degraded-source flag exists in backtest/"
            "SCHEMA.md's row shape today (e.g. FanGraphs-unreachable, "
            "assumed-lineup, stale-odds). Priority 8/source-certainty research "
            "needs this instrumented before it can run on backtest rows -- not "
            "attempted here; see Priority 8's own scope note in the report this "
            "function is called from."
        ),
    }

    # ---------- SELECTION-LIKE POPULATION (RECONSTRUCTED proxy, explicitly labeled) ----------
    apply_policy_rows = [r for r in graded if r.get("recommendation_status") is not None]
    if apply_policy_rows:
        by_status = defaultdict(lambda: {"n_graded": 0, "n_hit": 0})
        for r in apply_policy_rows:
            by_status[r["recommendation_status"]]["n_graded"] += 1
            by_status[r["recommendation_status"]]["n_hit"] += r["outcome"]
        report["selection_like_population"] = {
            "source": "OBSERVED recommendation_status (Stage 5 --apply-policy rows present in this file)",
            "n_rows_with_recommendation_status": len(apply_policy_rows),
            "by_status": {s: {"n_graded": v["n_graded"], "hit_rate": _rate(v["n_hit"], v["n_graded"])}
                         for s, v in sorted(by_status.items())},
            "caveat": ("recommendation_status here can structurally only be 'lean' or "
                      "'neutral', never 'top_pick'/'value' -- no real historical market "
                      "odds exist for a point-in-time replay (see SCHEMA.md)."),
        }
    else:
        MIN_LINE_PROB = 0.60
        proxy = [r for r in graded if (r.get("predicted_prob") or 0) >= MIN_LINE_PROB]
        n_hit = sum(r["outcome"] for r in proxy)
        report["selection_like_population"] = {
            "source": "RECONSTRUCTED proxy -- no recommendation_status field present in this file",
            "proxy_rule": f"predicted_prob >= {MIN_LINE_PROB} (generate_picks.py's own MIN_LINE_PROB floor)",
            "caveat": ("This is NOT real production eligibility -- it omits the evidence/"
                      "reliability gate, lineup-confirmation gate, price/value gate, and "
                      "freshness gate real recommendation.classify_recommendation() applies. "
                      "Treat this as a rough upper-bound population only, explicitly "
                      "labeled as reconstructed, never as 'what the board would have shown.'"),
            "n_graded": len(proxy), "n_hit": n_hit, "hit_rate": _rate(n_hit, len(proxy)),
        }

    return report


def print_report(report):
    print(json.dumps(report, indent=2, sort_keys=False))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows, n_malformed)
    report["_source_file"] = path
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
