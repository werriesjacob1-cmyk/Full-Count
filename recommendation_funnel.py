#!/usr/bin/env python3
"""recommendation_funnel.py — measures WHERE candidates fall out of the Top
Pick funnel, not just how many end up as top_pick/lean/value/neutral.

WHY THIS EXISTS. recommendation.classify_recommendation() is the one place
allowed to decide Top Pick / Lean / Value / Neutral (see that module's own
docstring), and this file changes NOTHING about that decision — it adds a
purely read-only introspection layer on top, for exactly one purpose: the
operating directive's "measure the recommendation rejection funnel." Knowing
the FINAL status distribution (how many top_pick vs neutral) doesn't say WHY
supply is thin; this does, by tracing the same individual gates
classify_recommendation() evaluates internally and reporting, across many
real candidates, which single gate was the first one a rejected candidate
failed.

Deliberately a SEPARATE module from recommendation.py, not a change to it —
that module's own docstring is explicit that it is the ONLY place allowed to
decide these labels, and this file must never be able to affect that
decision. gate_trace() below independently re-derives the same individual
booleans classify_recommendation() computes internally, reusing its exported
constants (TOP_PICK_MIN_PROB, TOP_PICK_MIN_RELIABILITY, TOP_PICK_MIN_ROI) and
the same pp.value_verdict()/market_agreement() calls — never inventing a new
threshold or reimplementing the value-test math. test_recommendation_funnel.py
carries a direct consistency check (all Top Pick gates pass IFF
classify_recommendation() actually returns "top_pick") specifically because
the two are not structurally coupled and could otherwise silently drift.

The funnel's gate ORDER is not arbitrary — it is recommendation.py's own
Top Pick docstring order verbatim ("probability floor, evidence quality, a
confirmed (not projected) lineup, a real price that clears the model's own
uncertainty, and fresh data"), so a candidate's "first gate failed" always
names the same requirement a human reading that docstring would name first.

SCOPE, STATED HONESTLY. The default data source (output/picks_{date}.json)
is the PUBLISHED board — already reduced by select_best_by_category() to one
candidate per prop-type per game, and already past generate_picks.py's own
MIN_QUALITY_SCORE cut. This funnel therefore answers "of the candidates that
were already good enough to be each matchup's single best read, why do most
still not reach Top Pick" — a real and useful question, but NOT "what
fraction of every raw candidate generate_picks.py ever scored becomes a Top
Pick," since the pre-selection raw pool is not persisted anywhere historical
data can be read back from. Do not read this funnel's percentages as the
latter question's answer.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import recommendation as rec
import prop_probability as pp

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PICKS_GLOB = os.path.join(ROOT, "output", "picks_*.json")

# The Top Pick funnel's gate order, verbatim from recommendation.py's own
# module docstring ("probability floor, evidence quality, a confirmed
# (not projected) lineup, a real price that clears the model's own
# uncertainty, and fresh data"). A candidate's blocking_gate is always the
# FIRST of these it fails, so the counts below always sum to n_total.
GATE_ORDER = ["has_prob", "meets_prob_floor", "evidence_ok", "lineup_ok",
             "has_odds", "clears_value", "data_fresh"]

GATE_LABELS = {
    "has_prob": "no real probability computed for this line",
    "meets_prob_floor": f"probability below the {rec.TOP_PICK_MIN_PROB*100:.0f}% Top Pick floor",
    "evidence_ok": f"evidence grade too thin (needs {'/'.join(rec.TOP_PICK_MIN_RELIABILITY)})",
    "lineup_ok": "lineup slot is still a projection, not confirmed",
    "has_odds": "no market price posted for this line",
    "clears_value": "does not clear the price/value test at the pessimistic end of its own interval",
    "data_fresh": "board or price data is stale",
}


def gate_trace(candidate, *, data_fresh=True):
    """The individual boolean gates classify_recommendation() evaluates
    internally, returned explicitly. Read-only — computing this can never
    change what classify_recommendation() itself returns for the same
    candidate, and it calls no function that mutates the candidate."""
    prob = candidate.get("hit_probability")
    reliability = candidate.get("reliability")
    lineup_assumed = bool(candidate.get("lineup_assumed"))
    odds = candidate.get("market_odds")
    ci = candidate.get("prob_ci")

    has_prob = prob is not None
    meets_prob_floor = has_prob and prob >= rec.TOP_PICK_MIN_PROB
    evidence_ok = reliability in rec.TOP_PICK_MIN_RELIABILITY
    lineup_ok = not lineup_assumed
    has_odds = odds is not None

    # Mirrors classify_recommendation's own require_robust=True call exactly
    # -- only evaluated when there's both a probability and a price to test,
    # same precondition classify_recommendation itself enforces before ever
    # calling value_verdict.
    clears_value = None
    suspect = None
    if has_prob and has_odds:
        verdict = pp.value_verdict(prob, odds, prob_lo=(ci[0] if ci else None),
                                   min_roi=rec.TOP_PICK_MIN_ROI, require_robust=True)
        agreement = pp.market_agreement(prob, odds)
        clears_value = verdict["verdict"] == "BET"
        suspect = agreement["agreement"] == "SUSPECT"

    return {
        "has_prob": has_prob,
        "meets_prob_floor": meets_prob_floor,
        "evidence_ok": evidence_ok,
        "lineup_ok": lineup_ok,
        "has_odds": has_odds,
        "clears_value": clears_value,
        "suspect": suspect,
        "data_fresh": bool(data_fresh),
    }


def blocking_gate(gates):
    """The first gate (in GATE_ORDER) this candidate fails, or None if every
    gate passes (i.e. it should be a Top Pick). A gate value of None (not yet
    evaluated -- e.g. clears_value when has_odds is False) counts as a
    failure here, matching classify_recommendation's own require_robust=True
    treatment of an absent test as a required-test failure, not a skip."""
    for gate in GATE_ORDER:
        if not gates.get(gate):
            return gate
    return None


def classify_with_trace(candidate, *, now=None, data_fresh=True, fresh_reasons=None):
    """Both the REAL status (via the one real classifier, untouched) and its
    gate trace, for one candidate."""
    result = rec.classify_recommendation(candidate, now=now, data_fresh=data_fresh,
                                         fresh_reasons=fresh_reasons)
    gates = gate_trace(candidate, data_fresh=data_fresh)
    result["gates"] = gates
    result["blocking_gate"] = blocking_gate(gates)
    return result


def funnel_report(candidates, *, now=None, odds_fetched_at=None, board_generated_at=None):
    """Runs the real board-level freshness check ONCE (matching
    attach_recommendations' own convention), classifies+traces every
    candidate, and tallies: (1) the real final status distribution, (2)
    sequential funnel retention through each Top Pick gate in order, (3) for
    every candidate that is NOT a Top Pick, which single gate blocked it
    first."""
    now = now or datetime.now(timezone.utc)
    fresh, fresh_reasons = rec.freshness_check(now=now, odds_fetched_at=odds_fetched_at,
                                               board_generated_at=board_generated_at)

    n_total = len(candidates)
    status_counts = {"top_pick": 0, "lean": 0, "value": 0, "neutral": 0}
    blocking_counts = {gate: 0 for gate in GATE_ORDER}
    funnel_retained = {gate: 0 for gate in GATE_ORDER}

    for c in candidates:
        traced = classify_with_trace(c, now=now, data_fresh=fresh, fresh_reasons=fresh_reasons)
        status_counts[traced["status"]] = status_counts.get(traced["status"], 0) + 1
        gates = traced["gates"]
        still_in = True
        for gate in GATE_ORDER:
            if still_in and gates.get(gate):
                funnel_retained[gate] += 1
            else:
                still_in = False
        bg = traced["blocking_gate"]
        if bg is not None:
            blocking_counts[bg] += 1

    return {
        "n_total": n_total,
        "status_counts": status_counts,
        "funnel_retained": funnel_retained,
        "blocking_counts": blocking_counts,
        "blocking_gate_labels": dict(GATE_LABELS),
        "gate_order": list(GATE_ORDER),
    }


def merge_reports(reports):
    """Sums several funnel_report() outputs (e.g. one per historical day)
    into one aggregate report, for a real sample size larger than any single
    board."""
    n_total = sum(r["n_total"] for r in reports)
    status_counts = {"top_pick": 0, "lean": 0, "value": 0, "neutral": 0}
    funnel_retained = {gate: 0 for gate in GATE_ORDER}
    blocking_counts = {gate: 0 for gate in GATE_ORDER}
    for r in reports:
        for status, count in r["status_counts"].items():
            status_counts[status] = status_counts.get(status, 0) + count
        for gate in GATE_ORDER:
            funnel_retained[gate] += r["funnel_retained"][gate]
            blocking_counts[gate] += r["blocking_counts"][gate]
    return {
        "n_total": n_total,
        "n_days": len(reports),
        "status_counts": status_counts,
        "funnel_retained": funnel_retained,
        "blocking_counts": blocking_counts,
        "blocking_gate_labels": dict(GATE_LABELS),
        "gate_order": list(GATE_ORDER),
    }


def report_from_picks_file(path):
    """One real output/picks_{date}.json file -> a funnel_report(), using
    that file's OWN recorded board_generated_at/odds_fetched_at as `now`
    context, so an old file is judged fresh as of when it was actually
    generated, not stale relative to today."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    candidates = doc.get("picks") or []
    meta = doc.get("recommendation_metadata") or {}
    board_generated_at = meta.get("board_generated_at") or doc.get("generated")
    odds_fetched_at = meta.get("odds_fetched_at") or board_generated_at
    # Reuses recommendation._parse_iso() rather than a second ISO-parsing
    # call -- that function's own docstring documents the exact bug (a naive
    # timestamp raising TypeError against an aware `now`) a fresh
    # reimplementation here would silently reintroduce.
    now = rec._parse_iso(board_generated_at)
    return funnel_report(candidates, now=now, odds_fetched_at=odds_fetched_at,
                         board_generated_at=board_generated_at)


def print_report(report):
    n = report["n_total"]
    n_days = report.get("n_days")
    header = f"Recommendation rejection funnel — {n} candidates"
    if n_days:
        header += f" across {n_days} real historical boards"
    print(header)
    print()
    print("Final status distribution:")
    for status, count in report["status_counts"].items():
        pct = (count / n * 100) if n else 0.0
        print(f"  {status:10s} {count:6d}  ({pct:5.1f}%)")
    print()
    print("Sequential funnel retention (each gate, in requirement order):")
    prev = n
    for gate in report["gate_order"]:
        retained = report["funnel_retained"][gate]
        pct_of_total = (retained / n * 100) if n else 0.0
        drop = prev - retained
        print(f"  {gate:18s} {retained:6d} retained ({pct_of_total:5.1f}% of total, "
             f"-{drop} this stage)")
        prev = retained
    print()
    print("First blocking gate (why each NON-top_pick candidate stopped):")
    n_non_top = n - report["status_counts"].get("top_pick", 0)
    for gate in report["gate_order"]:
        count = report["blocking_counts"][gate]
        pct = (count / n_non_top * 100) if n_non_top else 0.0
        print(f"  {gate:18s} {count:6d}  ({pct:5.1f}% of non-top-pick) — "
             f"{report['blocking_gate_labels'][gate]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--picks-glob", default=DEFAULT_PICKS_GLOB,
        help="glob of output/picks_{date}.json files to aggregate (default: all real "
             "historical boards)")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    args = parser.parse_args()

    files = sorted(glob.glob(args.picks_glob))
    if not files:
        print(f"no files matched {args.picks_glob!r}", file=sys.stderr)
        sys.exit(1)

    per_day = [report_from_picks_file(f) for f in files]
    aggregate = merge_reports(per_day)

    if args.json:
        print(json.dumps(aggregate, indent=2))
    else:
        print_report(aggregate)


if __name__ == "__main__":
    main()
