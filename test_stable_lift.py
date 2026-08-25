#!/usr/bin/env python3
"""test_stable_lift.py -- regression coverage for the 2026-08-25 stable-lift
reference rollout (hits_runs_rbis Lean gate only; runs/rbis shadow-only).

Two things this file exists to prove, per the explicit authorization:
  1. Switching from current (slate-scoped) lift to stable (season-to-date)
     lift for the Lean gate changes lift/Lean qualification WHERE
     APPROPRIATE, but does NOT change hit_probability, prob_ci, market_odds/
     market_edge, or Top Pick eligibility -- ever, on any candidate.
  2. The change is scoped EXACTLY to hits_runs_rbis. runs/rbis carry
     stable_lift for shadow tracking but classify_recommendation() must
     never read it for them. Every other stat is untouched.

Also covers stable_base_rate.py directly: point-in-time safety (no same-day
or future leakage), the fail-safe None on insufficient sample/unsupported
stat, and the season-to-date windowing itself against a small synthetic
ledger built by hand (not the real multi-year table, so the exact expected
number can be hand-verified).

    /tmp/mlbvenv/bin/python3 test_stable_lift.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import recommendation as rec
import stable_base_rate as sbr

NOW = datetime.now(timezone.utc)
BOARD_NOW = NOW.isoformat()


def _fresh():
    return rec.freshness_check(now=NOW, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)


def cand(prob, odds, ci=None, reliability="A", lineup_assumed=False, lift=0.10,
         stable_lift=None, stat="hits_runs_rbis"):
    return {"hit_probability": prob, "market_odds": odds, "prob_ci": ci,
            "reliability": reliability, "lineup_assumed": lineup_assumed,
            "lift": lift, "stable_lift": stable_lift,
            "projection": {"stat": stat}}


def classify(c):
    fresh, reasons = _fresh()
    return rec.classify_recommendation(c, now=NOW, data_fresh=fresh, fresh_reasons=reasons)


# ══════════════════════════════════════════════════════════════════════════
#  PART 1: recommendation.py -- stable lift changes Lean, nothing else
# ══════════════════════════════════════════════════════════════════════════

head("1. hits_runs_rbis: stable_lift ADDS a Lean that current lift alone would not give")

# Below LEAN_MIN_LIFT (0.02) on current lift, but above it on stable_lift,
# with everything else too weak to be top_pick (prob under floor) or value
# (odds chosen so it doesn't clear value either).
c = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=0.01, stable_lift=0.05,
         stat="hits_runs_rbis")
r_before = rec.classify_recommendation(
    {**c, "stable_lift": None}, now=NOW, data_fresh=True, fresh_reasons=[])
r_after = classify(c)
check(r_before["status"] == "neutral",
      f"sanity: with lift=0.01 alone (stable_lift None), this candidate is neutral, got {r_before['status']!r}")
check(r_after["status"] == "lean",
      f"with stable_lift=0.05 present, the SAME candidate becomes a lean, got {r_after['status']!r}")

head("2. hits_runs_rbis: stable_lift can also REMOVE a Lean current lift alone would have given")

c2 = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=0.05, stable_lift=0.00,
          stat="hits_runs_rbis")
r2_before = rec.classify_recommendation(
    {**c2, "stable_lift": None}, now=NOW, data_fresh=True, fresh_reasons=[])
r2_after = classify(c2)
check(r2_before["status"] == "lean",
      f"sanity: with lift=0.05 alone (stable_lift None), this candidate is a lean, got {r2_before['status']!r}")
check(r2_after["status"] == "neutral",
      f"with stable_lift=0.00 present, the SAME candidate drops to neutral, got {r2_after['status']!r}")

head("3. runs/rbis: stable_lift is SHADOW ONLY -- must NOT change status, even when present "
     "and materially different from lift")

for shadow_stat in ("runs", "rbis"):
    c3 = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=0.01, stable_lift=0.05,
              stat=shadow_stat)
    r3 = classify(c3)
    check(r3["status"] == "neutral",
          f"{shadow_stat}: stable_lift=0.05 present but must be ignored (shadow-only), "
          f"expected neutral (matching lift=0.01 alone), got {r3['status']!r}")

head("4. every other stat: unaffected, stable_lift key not even read")

for other_stat in ("hits", "total_bases", "home_runs", "strikeouts"):
    c4 = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=0.01, stable_lift=0.99,
              stat=other_stat)
    r4 = classify(c4)
    check(r4["status"] == "neutral",
          f"{other_stat}: stable_lift=0.99 must be completely ignored, got {r4['status']!r}")

head("5. FAIL-SAFE: stable_lift=None (insufficient sample / unsupported stat / key absent "
     "entirely) falls back to current lift exactly -- identical result either way")

for lift_val, stat_val in ((0.10, "hits_runs_rbis"), (-0.05, "hits_runs_rbis"),
                           (0.03, "runs"), (0.10, "hits")):
    with_none = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=lift_val,
                     stable_lift=None, stat=stat_val)
    without_key = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=lift_val,
                       stat=stat_val)
    del without_key["stable_lift"]
    r_none = classify(with_none)
    r_absent = classify(without_key)
    check(r_none["status"] == r_absent["status"],
          f"stat={stat_val} lift={lift_val}: stable_lift=None and stable_lift-key-absent must "
          f"classify identically, got {r_none['status']!r} vs {r_absent['status']!r}")

head("6. TOP PICK ELIGIBILITY: never touched by lift or stable_lift, in either direction, for "
     "hits_runs_rbis specifically (the market this change actually applies to)")

top_pick_base = dict(hit_probability=0.70, market_odds=-160, prob_ci=[0.65, 0.75],
                     reliability="A", lineup_assumed=False,
                     projection={"stat": "hits_runs_rbis"})
for lift_val, stable_val in ((0.10, 0.10), (-0.10, -0.10), (0.10, -0.99), (-0.99, 0.10),
                             (None, None), (0.02, None)):
    c6 = {**top_pick_base, "lift": lift_val, "stable_lift": stable_val}
    r6 = classify(c6)
    check(r6["status"] == "top_pick",
          f"lift={lift_val} stable_lift={stable_val}: a fully-qualifying hits_runs_rbis "
          f"candidate must stay top_pick regardless of lift/stable_lift, got {r6['status']!r}")

head("7. probability fields themselves are never mutated by classify_recommendation() -- "
     "hit_probability/prob_ci/market_odds identical before and after, across both lift paths")

for lift_val, stable_val in ((0.01, 0.05), (0.05, 0.00)):
    c7 = cand(0.45, -110, ci=[0.40, 0.50], reliability="C", lift=lift_val,
             stable_lift=stable_val, stat="hits_runs_rbis")
    snapshot = (c7["hit_probability"], tuple(c7["prob_ci"]), c7["market_odds"])
    classify(c7)
    after = (c7["hit_probability"], tuple(c7["prob_ci"]), c7["market_odds"])
    check(snapshot == after,
          f"candidate's own probability fields must be untouched by classification, "
          f"got {snapshot} -> {after}")


# ══════════════════════════════════════════════════════════════════════════
#  PART 2: stable_base_rate.py -- point-in-time safety and fail-safe behavior
# ══════════════════════════════════════════════════════════════════════════

def _write_synthetic_ledger(stat, daily):
    path = os.path.join(sbr._DATA_DIR, f"{stat}.json")
    os.makedirs(sbr._DATA_DIR, exist_ok=True)
    backup = None
    if os.path.exists(path):
        with open(path) as f:
            backup = f.read()
    with open(path, "w") as f:
        json.dump({"stat": stat, "daily": daily, "min_sample_n": 30}, f)
    return path, backup


def _restore_ledger(path, backup):
    if backup is None:
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "w") as f:
            f.write(backup)
    sbr.clear_cache()


head("8. stable_base_rate: STRICT point-in-time -- same-day and future entries never count")

TEST_STAT = "hits_runs_rbis"  # real supported stat, real ledger swapped out temporarily
daily = [
    {"date": "2026-03-25", "needs": 1, "hit": 20, "n": 40},   # 0.50
    {"date": "2026-03-26", "needs": 1, "hit": 20, "n": 40},   # 0.50, cumulative 40/80=0.50
    # SAME-DAY as asof below -- must NOT be counted
    {"date": "2026-03-27", "needs": 1, "hit": 1000, "n": 1000},
    # FUTURE relative to asof below -- must NOT be counted
    {"date": "2026-04-01", "needs": 1, "hit": 1000, "n": 1000},
]
path, backup = _write_synthetic_ledger(TEST_STAT, daily)
sbr.clear_cache()
try:
    rate, n = sbr.stable_base_rate(TEST_STAT, 1, "2026-03-27")
    check(n == 80, f"only the two strictly-prior days count toward n (expected 80), got n={n}")
    check(rate == 0.50, f"rate must be 40/80=0.50, unpolluted by the same-day/future rows, got {rate}")
finally:
    _restore_ledger(path, backup)

head("9. stable_base_rate: fail-safe None when the season-to-date sample is under "
     "MIN_STABLE_SAMPLE")

daily_thin = [{"date": "2026-03-25", "needs": 1, "hit": 5, "n": 10}]
path, backup = _write_synthetic_ledger(TEST_STAT, daily_thin)
sbr.clear_cache()
try:
    rate, n = sbr.stable_base_rate(TEST_STAT, 1, "2026-03-27")
    check(rate is None, f"n=10 is under MIN_STABLE_SAMPLE (30) -- must return None, got {rate}")
    check(n == 0, f"insufficient-sample case reports n=0, got {n}")
finally:
    _restore_ledger(path, backup)

head("10. stable_base_rate: fail-safe None for an unsupported stat -- never invents a number")

rate, n = sbr.stable_base_rate("total_bases", 2, "2026-03-27")
check(rate is None and n == 0,
      f"total_bases has no ledger (never validated for this) -- must be None, got ({rate}, {n})")

head("11. stable_base_rate: season boundary -- a date before this year's season_start looks "
     "into the PRIOR season's window, never the current (empty) one")

daily_prior = [
    {"date": "2025-08-01", "needs": 1, "hit": 30, "n": 50},  # prior season, real data
]
path, backup = _write_synthetic_ledger(TEST_STAT, daily_prior)
sbr.clear_cache()
try:
    # asof before this year's March 20 -> season_start logic should fall back
    # to the PRIOR season's window (per _season_start's own docstring).
    rate, n = sbr.stable_base_rate(TEST_STAT, 1, "2026-02-15")
    check(n == 50, f"a pre-season-start asof pulls from the prior season's window, expected n=50, got n={n}")
finally:
    _restore_ledger(path, backup)

print()
passed = sum(1 for ok, _, _ in _results if ok)
total = len(_results)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
