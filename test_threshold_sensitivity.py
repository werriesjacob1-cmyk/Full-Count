#!/usr/bin/env python3
"""test_threshold_sensitivity.py — coverage for
backtest/threshold_sensitivity.py, Phase 3 item 9: measuring what the Top
Pick record would look like at alternate probability floors (55/60/65/70%)
using the REAL classify_recommendation() function with only
TOP_PICK_MIN_PROB swept, never a reimplementation of its gate logic.

Real historical data currently produces ZERO qualifying picks at every
floor -- verified deliberately, not a bug: every legacy pick that clears
the probability+evidence bar fails the price/value test (see this
project's Phase 3 report). This test proves the SIMULATION MECHANICS work
correctly using a synthetic candidate built to actually pass, since real
data alone can't currently prove the qualifying path works.

    /tmp/mlbvenv/bin/python3 test_threshold_sensitivity.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, "backtest")

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


import threshold_sensitivity as ts
import recommendation as rec

head("1. simulate_floor() restores recommendation.TOP_PICK_MIN_PROB to its original value "
     "afterward -- must never leave the live module in a patched state")

original = rec.TOP_PICK_MIN_PROB
ts.simulate_floor([], 0.55)
check(rec.TOP_PICK_MIN_PROB == original,
      "TOP_PICK_MIN_PROB is unchanged after a simulation run, even on an empty pick list",
      f"got {rec.TOP_PICK_MIN_PROB} vs original {original}")

head("2. a clean, well-evidenced, price-clearing candidate qualifies at a LOW floor and "
     "stops qualifying once the floor is raised past its own probability")

# 62% probability, A evidence, confirmed lineup, a price cheap enough to
# clear value_verdict comfortably even at the pessimistic CI bound.
good_pick = {"hit_probability": 0.62, "reliability": "A", "lineup_assumed": False,
            "lift": 0.10, "market_odds": 150, "prob_ci": [0.55, 0.70]}
q55 = ts.simulate_floor([good_pick], 0.55)
q60 = ts.simulate_floor([good_pick], 0.60)
q65 = ts.simulate_floor([good_pick], 0.65)
check(len(q55) == 1, "qualifies at a 55% floor (62% clears it)", f"got {len(q55)}")
check(len(q60) == 1, "qualifies at a 60% floor (62% clears it)", f"got {len(q60)}")
check(len(q65) == 0, "does NOT qualify once the floor is raised to 65% (above its own 62%)",
      f"got {len(q65)}")

head("3. simulate_floor() restores the module global even when a candidate is malformed "
     "enough to raise inside classify_recommendation()")

bad_pick = {"hit_probability": "not a number"}  # will raise a TypeError deep inside
try:
    ts.simulate_floor([bad_pick], 0.55)
except Exception:
    pass
check(rec.TOP_PICK_MIN_PROB == original,
      "TOP_PICK_MIN_PROB is restored via the finally block even when classify_recommendation "
      "itself raises", f"got {rec.TOP_PICK_MIN_PROB} vs original {original}")

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
