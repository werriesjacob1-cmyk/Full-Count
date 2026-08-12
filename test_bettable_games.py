#!/usr/bin/env python3
"""test_bettable_games.py — coverage for generate_picks.bettable_games(),
the gate that decides which games are still safe to generate picks for.
Had zero test coverage despite its own docstring/comment block calling out
a real failure mode: re-scoring an already-finished game corrupts the
accuracy record (grading a "prediction" against an outcome that was
already known when it was made) and can overwrite the picks file that was
actually bet on with a later, useless one.

    /tmp/mlbvenv/bin/python3 test_bettable_games.py
"""
import sys

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


import generate_picks as gp


def gm(status, gid=1):
    return {"game_pk": gid, "matchup": "Athletics @ Astros", "status": status}


head("1. every real BETTABLE_STATES entry is classified as live, case-insensitively")

for state in gp.BETTABLE_STATES:
    live, done = gp.bettable_games([gm(state)])
    check(len(live) == 1 and not done, f"status={state!r} is bettable", f"got live={live} done={done}")

    live, done = gp.bettable_games([gm(state.upper())])
    check(len(live) == 1 and not done, f"status={state.upper()!r} (uppercase) is still bettable "
          "-- matching is case-insensitive", f"got live={live} done={done}")

head("2. a genuinely in-progress or final game is NOT bettable")

for state in ("in progress", "final", "game over", "live"):
    live, done = gp.bettable_games([gm(state)])
    check(len(done) == 1 and not live, f"status={state!r} is correctly excluded from bettable games",
          f"got live={live} done={done}")

head("3. only a MISSING/empty status defaults to bettable -- an unrecognized non-empty "
     "status is NOT given the same benefit of the doubt")

live, done = gp.bettable_games([gm("some_new_status_mlb_added")])
check(len(done) == 1 and not live,
      "an unrecognized but non-empty status string is excluded, not defaulted to bettable -- "
      "the fail-open behavior only covers a genuinely missing field, not an unknown value",
      f"got live={live} done={done}")

live, done = gp.bettable_games([{"game_pk": 1, "matchup": "x"}])  # no "status" key at all
check(len(live) == 1 and not done,
      "a game_meta entry with NO status field at all defaults to bettable")

live, done = gp.bettable_games([gm(None)])
check(len(live) == 1 and not done,
      "an explicit status=None (not just a missing key) also defaults to bettable")

live, done = gp.bettable_games([gm("")])
check(len(live) == 1 and not done,
      "an empty-string status also defaults to bettable")

head("4. allow_started=True overrides everything into live, even a final game")

live, done = gp.bettable_games([gm("final")], allow_started=True)
check(len(live) == 1 and not done,
      "allow_started=True forces even a finished game into the live list", f"got live={live} done={done}")

head("5. a real mixed slate splits correctly")

slate = [gm("scheduled", 1), gm("final", 2), gm("warmup", 3), gm("in progress", 4)]
live, done = gp.bettable_games(slate)
check({g["game_pk"] for g in live} == {1, 3}, "the two pregame games land in live", f"got {live}")
check({g["game_pk"] for g in done} == {2, 4}, "the two started/finished games land in done", f"got {done}")

head("6. an empty game_meta returns two empty lists, not a crash")

live, done = gp.bettable_games([])
check(live == [] and done == [], "empty input returns two empty lists cleanly")

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
