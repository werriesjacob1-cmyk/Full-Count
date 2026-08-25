#!/usr/bin/env python3
"""replay_stable_lift_change.py -- the EXACT-CHANGE replay required before
merging the hits_runs_rbis stable-lift Lean gate (2026-08-25 authorization).

Not a reconstruction or an approximation of the challenger like
backtest/stable_baseline_challenger.py was -- this calls the REAL
stable_base_rate.stable_base_rate() function against the REAL, already-built
data/stable_base_rates/hits_runs_rbis.json ledger, and reproduces
recommendation.py's REAL has_real_lean fallback logic verbatim (stable_lift
when available, else the existing slate-scoped lift -- see
classify_recommendation()'s own comment on why). Same probabilities, same
candidates, same thresholds, everything held fixed -- only which lift value
gates the Lean check changes.

ONE HONEST GAP, stated once rather than hidden: backtest/rows.jsonl carries
no market_odds/market_edge (this backtest has never captured historical
FanDuel prices -- see backtest/engine.py's own docstring), so "average
odds"/"average market edge" cannot be reported here without fabricating
numbers. Every other requested metric is real.

    /tmp/mlbvenv/bin/python3 backtest/replay_stable_lift_change.py
"""
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/home/user/PROJECT-GRIDIRON")
import stable_base_rate as sbr

ROWS_FILE = "/home/user/PROJECT-GRIDIRON/backtest/rows.jsonl"
LEAN_MIN_LIFT = 0.02
STAT = "hits_runs_rbis"


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def season_phase(dt):
    start = datetime(dt.year, 3, 20) if dt >= datetime(dt.year, 3, 20) else datetime(dt.year - 1, 3, 20)
    days_in = (dt - start).days
    if days_in < 30:
        return "early(<30d)"
    if days_in < 90:
        return "mid(30-90d)"
    return "late(90d+)"


def hit_rate(rows):
    if not rows:
        return None, 0
    return sum(r["outcome"] for r in rows) / len(rows), len(rows)


def main():
    rows = []
    with open(ROWS_FILE) as f:
        for line in f:
            r = json.loads(line)
            if r.get("prop_type") != STAT or r.get("outcome") is None or r.get("predicted_prob") is None:
                continue
            r["_dt"] = parse_date(r["date"])
            rows.append(r)
    rows.sort(key=lambda r: r["_dt"])
    print(f"{len(rows)} real {STAT} rows loaded, {rows[0]['date']}..{rows[-1]['date']}\n")

    # CURRENT lift: the closest available reconstruction of production's
    # slate-scoped league_p (same-night cross-sectional outcome rate for
    # this exact date+needs -- see stable_baseline_challenger.py's own
    # docstring for the full parity-gap disclosure; unchanged here).
    slate_rate = {}
    by_date_needs = defaultdict(list)
    for r in rows:
        by_date_needs[(r["date"], r["needs"])].append(r["outcome"])
    for key, outcomes in by_date_needs.items():
        slate_rate[key] = sum(outcomes) / len(outcomes)

    for r in rows:
        base_current = slate_rate[(r["date"], r["needs"])]
        r["_lift_current"] = r["predicted_prob"] - base_current
        r["_lean_current"] = r["_lift_current"] >= LEAN_MIN_LIFT

        # REAL production call -- exact function, exact ledger.
        stable_ref, stable_n = sbr.stable_base_rate(STAT, r["needs"], r["date"])
        r["_stable_ref"] = stable_ref
        r["_stable_n"] = stable_n
        if stable_ref is not None:
            r["_lift_effective"] = r["predicted_prob"] - stable_ref
        else:
            # Real fallback, verbatim: recommendation.py uses stable_lift
            # only when it exists, else the existing lift -- reproduced
            # here exactly, not re-derived differently.
            r["_lift_effective"] = r["_lift_current"]
        r["_lean_new"] = r["_lift_effective"] >= LEAN_MIN_LIFT
        r["_phase"] = season_phase(r["_dt"])
        r["_year"] = r["_dt"].year

    old_leans = [r for r in rows if r["_lean_current"]]
    new_leans = [r for r in rows if r["_lean_new"]]
    added = [r for r in rows if r["_lean_new"] and not r["_lean_current"]]
    removed = [r for r in rows if r["_lean_current"] and not r["_lean_new"]]
    overlap = [r for r in rows if r["_lean_current"] and r["_lean_new"]]
    fallback_n = sum(1 for r in rows if r["_stable_ref"] is None)

    print("=" * 100)
    print("TOTAL ELIGIBLE LEANS, VOLUME CHANGE, REALIZED HIT RATE")
    print("=" * 100)
    hr_old, n_old = hit_rate(old_leans)
    hr_new, n_new = hit_rate(new_leans)
    print(f"  CURRENT (slate-scoped lift)   n={n_old:6}  hit_rate={hr_old:.4f}")
    print(f"  CHALLENGER (stable lift)      n={n_new:6}  hit_rate={hr_new:.4f}   "
          f"volume change: {n_new - n_old:+d} ({100*(n_new-n_old)/n_old:+.1f}%)")
    print(f"  rows where the stable ledger had no usable sample (fell back to current lift): "
          f"{fallback_n} of {len(rows)} ({100*fallback_n/len(rows):.1f}%)")

    print()
    print("=" * 100)
    print("PROBABILITY DISTRIBUTION of the two Lean populations (predicted_prob, unaffected "
          "by this change -- shown to confirm the two populations aren't just a probability-band shift)")
    print("=" * 100)
    for label, pop in (("CURRENT leans", old_leans), ("CHALLENGER leans", new_leans)):
        probs = [r["predicted_prob"] for r in pop]
        if probs:
            print(f"  {label:20} n={len(probs):5}  mean={statistics.mean(probs):.4f}  "
                  f"median={statistics.median(probs):.4f}  "
                  f"p10={sorted(probs)[len(probs)//10]:.4f}  p90={sorted(probs)[9*len(probs)//10]:.4f}")

    print()
    print("=" * 100)
    print("OVERLAP DECOMPOSITION -- the critical question: is this a genuine swap of bad picks "
          "for better ones, or just a volume filter?")
    print("=" * 100)
    hr_overlap, n_overlap = hit_rate(overlap)
    hr_added, n_added = hit_rate(added)
    hr_removed, n_removed = hit_rate(removed)
    print(f"  OVERLAP (Lean under both)        n={n_overlap:6}  hit_rate={hr_overlap}")
    print(f"  ADDED (new-only, stable lift)    n={n_added:6}  hit_rate={hr_added}")
    print(f"  REMOVED (old-only, dropped)      n={n_removed:6}  hit_rate={hr_removed}")
    if hr_added is not None and hr_removed is not None:
        print(f"  ADDED vs REMOVED hit-rate gap: {hr_added - hr_removed:+.4f}  "
              f"({'genuinely replacing worse picks with better ones' if hr_added > hr_removed else 'WARNING: added picks are not clearly better than removed ones'})")

    print()
    print("=" * 100)
    print("YEARLY AND SEASON-PHASE HIT RATE (both populations)")
    print("=" * 100)
    years = sorted(set(r["_year"] for r in rows))
    for y in years:
        yr_old = [r for r in old_leans if r["_year"] == y]
        yr_new = [r for r in new_leans if r["_year"] == y]
        hro, no = hit_rate(yr_old)
        hrn, nn = hit_rate(yr_new)
        print(f"  {y}: CURRENT n={no:5} hr={hro}   CHALLENGER n={nn:5} hr={hrn}")
    for ph in ("early(<30d)", "mid(30-90d)", "late(90d+)"):
        ph_old = [r for r in old_leans if r["_phase"] == ph]
        ph_new = [r for r in new_leans if r["_phase"] == ph]
        hro, no = hit_rate(ph_old)
        hrn, nn = hit_rate(ph_new)
        print(f"  {ph:14}: CURRENT n={no:5} hr={hro}   CHALLENGER n={nn:5} hr={hrn}")

    print()
    print("=" * 100)
    print("AVERAGE ODDS / AVERAGE MARKET EDGE: NOT AVAILABLE")
    print("backtest/rows.jsonl carries no market_odds/market_edge field (this backtest has")
    print("never captured historical FanDuel prices -- a disclosed, pre-existing limitation,")
    print("not something this replay can honestly compute). Not fabricated.")
    print("=" * 100)

    print()
    print("=" * 100)
    print("MERGE-CONDITION CHECK")
    print("=" * 100)
    conditions = []
    conditions.append(("Challenger hit rate >= current hit rate", hr_new is not None and hr_old is not None and hr_new >= hr_old))
    conditions.append(("Added-picks hit rate > removed-picks hit rate",
                       hr_added is not None and hr_removed is not None and hr_added > hr_removed))
    for label, ok in conditions:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


if __name__ == "__main__":
    main()
