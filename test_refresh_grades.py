#!/usr/bin/env python3
"""test_refresh_grades.py — coverage for dashboard/refresh_grades.py, the
live grading refresh for Top Picks. Direct request, verbatim: "for the top
picks, them to show when it's cashed... make them go green if it hits" --
refined to "make the pick yellow when the game is happening. And have it
turn green if it cashes, red if it doesn't."

Mocks grade_results.fetch_game_statuses()/grade_pick() (same pattern as
test_refresh_prices.py mocking odds_fanduel) rather than hitting real MLB
box scores -- this tests refresh_grades.py's own state machine (Preview ->
nothing, Live -> "live", Final -> grade_pick()'s real hit/miss, terminal
once graded), not grade_pick() itself, which test_grade_results.py already
covers directly.

    /tmp/mlbvenv/bin/python3 test_refresh_grades.py
"""
import json
import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/dashboard" if "/" in __file__ else "dashboard")

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


import refresh_grades as rg
import grade_results as gr


def row(name, prop, stat, needs, game_pk, grade=None):
    return {"type": "batter", "name": name, "prop": prop, "matchup": "A @ B", "team": "A",
            "side": "away", "lean": None, "player_id": 100, "combo_player_ids": None,
            "game_pk": game_pk, "projection": {"stat": stat, "needs": needs}, "grade": grade}


PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress"}
FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}


def write_payload(top_picks, extra_tabs=None):
    payload = {"date": "2026-08-14", "data": {"top_picks": top_picks, **(extra_tabs or {})}}
    path = tempfile.mktemp(suffix=".json")
    json.dump(payload, open(path, "w"))
    return path


head("1. a pick whose game hasn't started yet is left completely alone")

path1 = write_payload([row("A", "Over 0.5 Hits", "hits", 1, game_pk=1)])
with mock.patch.object(gr, "fetch_game_statuses", return_value={1: PREVIEW}):
    out1 = rg.refresh(path1)
check(out1["data"]["top_picks"][0].get("grade") is None,
      "a Preview-state game gets no grade at all", f"got {out1['data']['top_picks'][0]}")

head("2. a pick whose game is in progress gets marked 'live'")

path2 = write_payload([row("B", "Over 0.5 Hits", "hits", 1, game_pk=2)])
with mock.patch.object(gr, "fetch_game_statuses", return_value={2: LIVE}):
    out2 = rg.refresh(path2)
check(out2["data"]["top_picks"][0]["grade"] == "live",
      "a Live-state game is marked 'live', not left unset or graded")

head("3. a pick whose game is Final gets the REAL grade_pick() result -- hit")

path3 = write_payload([row("C", "Over 0.5 Hits", "hits", 1, game_pk=3)])
with mock.patch.object(gr, "fetch_game_statuses", return_value={3: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "hit", "actual": 2}) as mp:
    out3 = rg.refresh(path3)
check(out3["data"]["top_picks"][0]["grade"] == "hit",
      "a Final game with a real hit result is marked 'hit'")
check(mp.call_args.kwargs.get("date") == "2026-08-14",
      "grade_pick() is called with the payload's own date, not a hardcoded one",
      f"got call_args={mp.call_args}")

head("4. a Final game grade_pick() can't actually grade (e.g. scratched) is marked 'live', "
     "not silently left unset -- a reader shouldn't see an untouched pick after its game ended")

path4 = write_payload([row("D", "Over 0.5 Hits", "hits", 1, game_pk=4)])
with mock.patch.object(gr, "fetch_game_statuses", return_value={4: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "ungraded", "reason": "scratched"}):
    out4 = rg.refresh(path4)
check(out4["data"]["top_picks"][0]["grade"] == "live",
      "an ungraded-at-final pick still gets SOME visible state, not silence")

head("5. a pick already graded hit/miss is TERMINAL -- never re-checked, even if the mocked "
     "status would say something different this cycle")

path5 = write_payload([row("E", "Over 0.5 Hits", "hits", 1, game_pk=5, grade="hit")])
with mock.patch.object(gr, "fetch_game_statuses") as mp5:
    out5 = rg.refresh(path5)
check(out5["data"]["top_picks"][0]["grade"] == "hit", "the terminal grade is untouched")
check(not mp5.called, "fetch_game_statuses isn't even called when every top pick is "
      "already terminally graded -- no wasted API call")

head("6. the grade propagates into every OTHER tab carrying the same (name, prop), same "
     "pattern refresh_prices.py already uses for price fields")

path6 = write_payload(
    [row("F", "Over 0.5 Hits", "hits", 1, game_pk=6)],
    extra_tabs={"hits": [row("F", "Over 0.5 Hits", "hits", 1, game_pk=6)],
               "all": [row("F", "Over 0.5 Hits", "hits", 1, game_pk=6)]})
with mock.patch.object(gr, "fetch_game_statuses", return_value={6: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "miss"}):
    out6 = rg.refresh(path6)
check(out6["data"]["hits"][0]["grade"] == "miss", "the 'hits' tab's copy of the same pick "
      "picked up the identical grade")
check(out6["data"]["all"][0]["grade"] == "miss", "the 'all' tab's copy also picked it up")

head("7. grades_updated_at is stamped after a real grading pass")

check("grades_updated_at" in out6, "grades_updated_at was added to the payload")

head("8. an empty top_picks list is a clean no-op, no crash, no fetch")

path8 = write_payload([])
with mock.patch.object(gr, "fetch_game_statuses") as mp8:
    out8 = rg.refresh(path8)
check(out8["data"]["top_picks"] == [], "empty top_picks stays empty")
check(not mp8.called, "no game-status fetch happens when there's nothing to grade")

for p in (path1, path2, path3, path4, path5, path6, path8):
    os.remove(p)

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
