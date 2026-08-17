#!/usr/bin/env python3
"""model_health_report.py — Phase 3, item 11: a lightweight automated
report so the model can be monitored "without manually auditing it every
day... an early-warning system for structural problems."

Reads whatever is already on disk (today's output/picks_{date}.json,
results/history.json, results/grades_*.json) -- fetches nothing, hits no
network, safe to run as often as wanted, including on a schedule. Every
section degrades gracefully (prints "not available" / "legacy shape, no
recommendation_status") rather than crashing when a field or file this
report wants doesn't exist yet -- this must never take down whatever
pipeline runs it.

    /tmp/mlbvenv/bin/python3 model_health_report.py [YYYY-MM-DD]
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import eval_lib as el
import prop_probability as pp

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")


def _latest_picks_file(date=None):
    """The exact date's picks file if it exists, else the most recent
    picks_{date}.json on disk (never a timestamped archive copy -- those
    match picks_{date}_{timestamp}.json, one underscore-separated segment
    longer, and are explicitly excluded)."""
    if date:
        path = os.path.join(OUTPUT_DIR, f"picks_{date}.json")
        if os.path.exists(path):
            return path
    candidates = sorted(glob.glob(os.path.join(OUTPUT_DIR, "picks_*.json")))
    candidates = [c for c in candidates
                 if len(os.path.basename(c)) == len(f"picks_2026-08-16.json")]
    return candidates[-1] if candidates else None


def section_today(picks_path):
    print("=" * 78)
    print("TODAY'S BOARD")
    print("=" * 78)
    if not picks_path:
        print("  No picks file found on disk -- nothing to report.")
        return
    with open(picks_path, encoding="utf-8") as f:
        payload = json.load(f)
    picks = payload.get("picks", [])
    print(f"  File: {picks_path}  ({len(picks)} total picks/candidates)")

    has_status = any("recommendation_status" in p for p in picks)
    if not has_status:
        print("  This file predates the recommendation-layer rebuild (no "
              "recommendation_status field on any row) -- legacy shape, skipping the "
              "status breakdown below.")
        return

    by_status = defaultdict(list)
    for p in picks:
        by_status[p.get("recommendation_status") or "unclassified"].append(p)
    for status in ("top_pick", "lean", "value", "neutral", "unclassified"):
        print(f"  {status:14s} n={len(by_status.get(status, []))}")

    top_picks = by_status.get("top_pick", [])
    if top_picks:
        probs = sorted(p["hit_probability"] for p in top_picks if p.get("hit_probability") is not None)
        if probs:
            mid = len(probs) // 2
            median = probs[mid] if len(probs) % 2 else (probs[mid - 1] + probs[mid]) / 2
            print(f"  Top Pick probability distribution: min={probs[0]:.3f} "
                  f"median={median:.3f} max={probs[-1]:.3f} (n={len(probs)})")
    stale = [p for p in picks if p.get("stale")]
    print(f"  Stale-flagged recommendations: {len(stale)}")
    rejected = [p for p in picks
               if (p.get("hit_probability") or 0) >= 0.60
               and p.get("recommendation_status") != "top_pick"]
    print(f"  Picks with probability >= 60% that did NOT become a Top Pick: "
          f"{len(rejected)} (rejected on evidence/lineup/price/freshness -- see "
          f"each one's status_reasons for why)")

    meta = payload.get("recommendation_metadata") or {}
    if meta:
        print(f"  Version: model={meta.get('model_version')} "
              f"policy={meta.get('selection_policy_version')} "
              f"git_sha={meta.get('git_sha') or '(not a git checkout)'}")

    missing_prob = sum(1 for p in picks if p.get("hit_probability") is None)
    missing_price = sum(1 for p in picks if p.get("market_odds") is None)
    assumed_lineup = sum(1 for p in picks if p.get("lineup_assumed") is True)
    missing_ci_but_should = sum(1 for p in picks
                                if p.get("probability_basis") in ("empirical", "blended")
                                and p.get("prob_ci") is None)
    print(f"  Missing-data: {missing_prob} unpriced-by-model, {missing_price} "
          f"unpriced-by-market, {assumed_lineup} assumed-lineup, {missing_ci_but_should} "
          f"empirical/blended picks with NO ci (should have one -- worth investigating "
          f"if > 0)")

    disagreements = []
    for p in picks:
        prob = p.get("hit_probability")
        odds = p.get("market_odds")
        if prob is None or odds is None:
            continue
        market_prob, _exact = el.market_probability(p)
        if market_prob is None:
            continue
        if abs(prob - market_prob) >= 0.15:
            disagreements.append((abs(prob - market_prob), p.get("name"), p.get("prop"),
                                  prob, market_prob))
    disagreements.sort(reverse=True)
    print(f"  Large model/market disagreements (>=15pts): {len(disagreements)}")
    for gap, name, prop, mp, kp in disagreements[:5]:
        print(f"    {name} — {prop}: model={mp:.3f} market(no-vig)={kp:.3f} gap={gap:.3f}")


def section_current_version_record():
    print("\n" + "=" * 78)
    print("CURRENT-VERSION RECORD (results/history.json)")
    print("=" * 78)
    path = os.path.join(RESULTS_DIR, "history.json")
    if not os.path.exists(path):
        print("  No results/history.json found.")
        return
    with open(path, encoding="utf-8") as f:
        h = json.load(f)
    tp_rate = h.get("top_pick_hit_rate")
    tp_totals = (h.get("by_recommendation_status_totals") or {}).get("top_pick", {})
    print(f"  All-time Top Pick record: {tp_totals.get('hits', 0)} hits / "
          f"{tp_totals.get('misses', 0)} misses "
          f"(rate: {tp_rate if tp_rate is not None else 'n/a'})")
    print(f"  Rolling 14-day Top Pick rate: {h.get('last_14_days_top_pick_hit_rate')} "
          f"over {h.get('last_14_days_top_pick_n', 0)} graded")
    print(f"  (For comparison, NOT the same claim: blended last-14-day rate across every "
          f"category = {h.get('last_14_days_hit_rate')})")
    if tp_totals.get("hits", 0) + tp_totals.get("misses", 0) == 0:
        print("  *** ZERO Top Picks graded yet under the current architecture. This is "
              "the correct, honest starting point -- see results/ANALYSIS.md. ***")


def section_calibration_drift():
    print("\n" + "=" * 78)
    print("CALIBRATION DRIFT (recent window vs full graded window)")
    print("=" * 78)
    picks = el.graded_only(el.load_graded_picks())
    pairs_all = [(p["hit_probability"], 1.0 if p["grade"] == "hit" else 0.0)
                for p in picks if p.get("hit_probability") is not None]
    if len(pairs_all) < el.MIN_N_DIRECTIONAL:
        print("  Not enough graded, probability-carrying picks to say anything.")
        return
    dates = sorted({p.get("_date") for p in picks if p.get("_date")})
    recent_cutoff = dates[-7] if len(dates) >= 7 else dates[0]
    recent_pairs = [(p["hit_probability"], 1.0 if p["grade"] == "hit" else 0.0)
                    for p in picks
                    if p.get("hit_probability") is not None and (p.get("_date") or "") >= recent_cutoff]
    brier_all = el.brier(pairs_all)
    brier_recent = el.brier(recent_pairs) if len(recent_pairs) >= el.MIN_N_DIRECTIONAL else None
    print(f"  Full-window Brier ({len(pairs_all)} picks): {brier_all:.4f}")
    if brier_recent is not None:
        drift = brier_recent - brier_all
        flag = " <-- WORSENING" if drift > 0.03 else ""
        print(f"  Last-7-days-of-data Brier ({len(recent_pairs)} picks): {brier_recent:.4f} "
              f"(drift {drift:+.4f}){flag}")
    else:
        print(f"  Recent window has only {len(recent_pairs)} picks -- too thin to compare.")


def section_grading_completeness():
    print("\n" + "=" * 78)
    print("GRADING COMPLETENESS (last 14 grades_*.json files)")
    print("=" * 78)
    paths = sorted(glob.glob(os.path.join(RESULTS_DIR, "grades_*.json")))[-14:]
    if not paths:
        print("  No grades_*.json files found.")
        return
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"  {os.path.basename(path)}: UNREADABLE")
            continue
        picks = d.get("picks", [])
        ungraded = sum(1 for p in picks if p.get("grade") == "ungraded")
        rate = ungraded / len(picks) if picks else 0
        flag = "  <-- HIGH UNGRADED RATE" if rate > 0.25 else ""
        print(f"  {d.get('date', os.path.basename(path))}: {len(picks)} picks, "
              f"{ungraded} ungraded ({rate:.0%}){flag}")


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    picks_path = _latest_picks_file(date)
    section_today(picks_path)
    section_current_version_record()
    section_calibration_drift()
    section_grading_completeness()
    return 0


if __name__ == "__main__":
    sys.exit(main())
