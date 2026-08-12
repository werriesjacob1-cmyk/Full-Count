#!/usr/bin/env python3
"""test_prop_snapshot.py — direct coverage for prop_snapshot.capture()'s
budget/coverage logic. grade_value.py's closing_prices() trusts the
'complete' flag this produces to decide whether a captured price is a real
closing number or a stale one left over from a sweep that ran out of time
(from_partial_sweep, already tested in test_grade_value.py) -- but nothing
tested the PRODUCER of that flag until now.

    /tmp/mlbvenv/bin/python3 test_prop_snapshot.py
"""
import sys
import unittest.mock as mock

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


import prop_snapshot as ps

GAMES = [(1, "HOU@TEX", "2026-08-06T23:00:00Z"),
         (2, "NYY@BOS", "2026-08-06T23:05:00Z"),
         (3, "LAD@SF", "2026-08-06T23:10:00Z")]

PROPS = [{"player": "A", "norm": "a", "stat": "hits", "needs": 1,
          "american": -150, "in_play": False}]

head("1. a full, in-budget sweep reports complete=True")

with mock.patch("odds_fanduel.list_games", return_value=GAMES), \
     mock.patch("odds_fanduel._event_props", return_value=PROPS):
    taken_at, rows, cov = ps.capture(budget_s=9999)

check(cov["games_total"] == 3 and cov["games_captured"] == 3,
      "all 3 games are captured when the budget never binds", f"got {cov}")
check(cov["complete"] is True, "a full sweep reports complete=True", f"got {cov}")
check(len(rows) == 3, "one row per game (1 prop each) when nothing is skipped", f"got {len(rows)} rows")
check(all(r["taken_at"] == taken_at for r in rows), "every row shares the same taken_at timestamp")

head("2. a sweep that exhausts its time budget reports complete=False")

call_count = [0]


def _slow_event_props(event_id):
    call_count[0] += 1
    return PROPS


with mock.patch("odds_fanduel.list_games", return_value=GAMES), \
     mock.patch("odds_fanduel._event_props", side_effect=_slow_event_props), \
     mock.patch("time.monotonic", side_effect=[0, 0, 0.5, 100, 100]):
    # monotonic() call sequence: started=0, then per-game elapsed checks --
    # the 3rd call (100) blows the budget=10s before the 2nd game is reached.
    taken_at2, rows2, cov2 = ps.capture(budget_s=10)

check(cov2["complete"] is False,
      "a sweep that runs out of budget mid-slate reports complete=False, not True",
      f"got {cov2}")
check(cov2["games_captured"] < cov2["games_total"],
      "games_captured is strictly less than games_total on a partial sweep",
      f"got {cov2}")

head("3. one game's props fetch raising an exception doesn't abort the whole sweep")

def _one_fails(event_id):
    if event_id == 2:
        raise RuntimeError("simulated fetch failure")
    return PROPS


with mock.patch("odds_fanduel.list_games", return_value=GAMES), \
     mock.patch("odds_fanduel._event_props", side_effect=_one_fails):
    taken_at3, rows3, cov3 = ps.capture(budget_s=9999)

check(cov3["games_captured"] == 3 and cov3["complete"] is True,
      "a single game's fetch exception still counts as 'attempted' -- the sweep "
      "continues to the remaining games rather than aborting", f"got {cov3}")
check(len(rows3) == 2,
      "the failed game contributes zero rows, but the other two games' real props still land",
      f"got {len(rows3)} rows")

head("4. in_play is recorded on every row, not filtered out")

in_play_props = [{"player": "B", "norm": "b", "stat": "hits", "needs": 1,
                  "american": -120, "in_play": True}]
with mock.patch("odds_fanduel.list_games", return_value=[GAMES[0]]), \
     mock.patch("odds_fanduel._event_props", return_value=in_play_props):
    _, rows4, _ = ps.capture(budget_s=9999)
check(rows4[0]["in_play"] is True,
      "an in-play prop is recorded with in_play=True, not filtered out at capture time "
      "(closing_prices() is what filters it later)")

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
