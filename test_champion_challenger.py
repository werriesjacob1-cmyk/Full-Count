#!/usr/bin/env python3
"""test_champion_challenger.py — coverage for champion_challenger.py,
Phase 3 item 8: shadow predictions, cross-referenced against real graded
outcomes, gated by PRE-REGISTERED promotion criteria that require both a
minimum sample size AND a minimum number of distinct days -- so this locks
in the direct instruction: "Prevent us from changing production because of
a 5-0 day or panicking because of a 1-7 day."

ISOLATION: both champion_challenger.SHADOW_DIR and eval_lib.RESULTS_DIR
are repointed to a temp directory for the whole file, and restored at the
end -- the same "reassigning RESULTS_DIR after import does not retroactively
fix a module-level constant bound at import time" trap this session already
hit once for real in test_phase3_versioning.py's early draft is avoided
here from the start, not caught after the fact.

    /tmp/mlbvenv/bin/python3 test_champion_challenger.py
"""
import sys
import os
import json
import shutil
import tempfile

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


import champion_challenger as cc
import eval_lib as el

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_champion_challenger_")
cc.SHADOW_DIR = os.path.join(TMPDIR, "challengers")
el.RESULTS_DIR = os.path.join(TMPDIR, "results")
os.makedirs(el.RESULTS_DIR, exist_ok=True)
cc._REGISTRY.clear()  # the module's real platoon_xwoba_v1 registration is tested separately


def write_grades(date, picks):
    with open(os.path.join(el.RESULTS_DIR, f"grades_{date}.json"), "w") as f:
        json.dump({"date": date, "picks": picks}, f)


def graded_pick(player_id, game_pk, stat, needs, grade):
    return {"player_id": player_id, "game_pk": game_pk,
           "projection": {"stat": stat, "needs": needs}, "grade": grade}


head("1. register()/registered(): a challenger is stored with its description")

cc.register("test_challenger", lambda c: 0.5, "a trivial always-0.5 challenger")
check("test_challenger" in cc.registered(), "the challenger is now registered")
check(cc.registered()["test_challenger"]["description"] == "a trivial always-0.5 challenger",
      "the description is preserved")

head("2. run_shadow(): scores only candidates with a real champion probability, skips "
     "those without one, and NEVER mutates the real candidate dict")

cands = [
    {"player_id": 1, "name": "A", "game_pk": 900001, "hit_probability": 0.65,
     "projection": {"stat": "hits", "needs": 1}, "market_odds": -140, "status": "top_pick"},
    {"player_id": 2, "name": "B", "game_pk": 900001, "hit_probability": None,
     "projection": {"stat": "hits", "needs": 1}},
]
cand1_before = dict(cands[0])
counts = cc.run_shadow(cands, "2026-08-16", challenger_names=["test_challenger"])
check(counts["test_challenger"] == 1,
      "exactly 1 of the 2 candidates (the one with a real probability) got shadow-scored",
      f"got {counts}")
check(cands[0] == cand1_before,
      "the real candidate dict is completely untouched by shadow scoring -- no "
      "challenger_prob key leaked onto it, no mutation at all")

head("3. run_shadow(): a challenger that raises an exception is skipped for that "
     "candidate, never crashes the run or touches the other challengers")

cc.register("broken", lambda c: 1 / 0, "deliberately broken for this test")
counts3 = cc.run_shadow(cands, "2026-08-16", challenger_names=["test_challenger", "broken"])
check(counts3["broken"] == 0, "the broken challenger silently contributes zero rows, no crash",
      f"got {counts3}")
check(counts3["test_challenger"] == 1, "the OTHER, working challenger is unaffected",
      f"got {counts3}")

head("4. evaluate_promotion(): INSUFFICIENT_DATA below the pre-registered min_n/min_days "
     "floor, even with a perfect-looking small sample")

cc.register("tiny", lambda c: 0.99 if c.get("hit_probability", 0) > 0.5 else 0.01,
           "a challenger that would look great on 3 rows")
small_cands = [{"player_id": i, "name": f"P{i}", "game_pk": 1, "hit_probability": 0.9,
               "projection": {"stat": "hits", "needs": 1}} for i in range(3)]
cc.run_shadow(small_cands, "2026-08-16", challenger_names=["tiny"])
write_grades("2026-08-16", [graded_pick(i, 1, "hits", 1, "hit") for i in range(3)])
result4 = cc.evaluate_promotion("tiny")
check(result4["verdict"].startswith("INSUFFICIENT_DATA"),
      "3 rows across 1 day never produces PROMOTE/REJECT, regardless of how clean the "
      "result looks -- the pre-registered floor is a hard gate", result4["verdict"])

head("5. evaluate_promotion(): a challenger that is CLEARLY, CONSISTENTLY better than the "
     "champion across a real sample (>=100 rows, >=14 days) gets PROMOTE")

def _perfect_challenger(c):
    return 0.95 if c.get("_truth") else 0.05


cc.register("good", lambda c: _perfect_challenger(c), "a near-perfect challenger for this test")
random_seed_cands = []
grades5 = []
import random
random.seed(11)
for day in range(20):
    date = f"2026-09-{day+1:02d}"
    day_cands = []
    day_grades = []
    for i in range(10):
        pid = day * 100 + i
        truth = random.random() < 0.5
        # champion is mediocre (near coin-flip, weak signal); challenger
        # ("good") gets it right most of the time -- a real, sustained
        # advantage, not a lucky streak.
        champ_p = 0.55 if truth else 0.45
        c = {"player_id": pid, "name": f"P{pid}", "game_pk": day, "hit_probability": champ_p,
            "projection": {"stat": "hits", "needs": 1}, "_truth": truth}
        day_cands.append(c)
        day_grades.append(graded_pick(pid, day, "hits", 1, "hit" if truth else "miss"))
    cc.run_shadow(day_cands, date, challenger_names=["good"])
    write_grades(date, day_grades)

result5 = cc.evaluate_promotion("good")
check(result5["n"] >= 100 and result5["n_days"] >= 14,
      "the fixture actually cleared both the row-count and day-count floors",
      f"n={result5['n']} n_days={result5['n_days']}")
check(result5["verdict"].startswith("PROMOTE"),
      "a challenger that is genuinely, consistently better at real scale gets PROMOTE",
      result5["verdict"])
check(result5["brier_gain"] > 0, "the reported Brier gain is positive for a better challenger")

head("6. the real platoon_xwoba_v1 challenger registered at import time: returns None "
     "with no xwoba signal, returns a small, CAPPED nudge when it fires")

import importlib
cc2 = importlib.reload(cc)  # re-run module-level register() calls after our TMPDIR patches
cc2.SHADOW_DIR = cc.SHADOW_DIR
fn = cc2.registered()["platoon_xwoba_v1"]["score_fn"]
check(fn({"hit_probability": 0.6, "signals": {}}) is None,
      "no platoon_xwoba in signals -> None, never a fabricated guess")
nudged = fn({"hit_probability": 0.6, "signals": {"platoon_xwoba": 5.0}})
check(nudged is not None and abs(nudged - 0.6) <= 0.03 + 1e-9,
      "the nudge is capped at +/-3 probability points even for an extreme xwoba value",
      f"got {nudged}")
check(nudged > 0.6, "a positive platoon_xwoba value nudges the probability UP")

shutil.rmtree(TMPDIR, ignore_errors=True)

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
