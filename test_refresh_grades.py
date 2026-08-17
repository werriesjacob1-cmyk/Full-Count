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

PHASE 4 REWRITE NOTE (2026-08-16): the old version of this file tested the
payload["data"]["top_picks"]/["hits"]/["all"] per-tab-duplication schema and
its own (name, prop) propagation step. Both are gone: the payload is now
one flat `props` array (see build_dashboard.py's build_payload()), so
"Top Picks" is just every row whose real recommendation_status ==
"top_pick" -- there is exactly one copy of each row, so grading it once
grades it everywhere it's referenced.

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

_row_id = [0]


def row(name, prop, stat, needs, game_pk, grade=None, recommendation_status="top_pick"):
    _row_id[0] += 1
    return {"id": f"fixture-{_row_id[0]}", "type": "batter", "name": name, "prop": prop,
            "matchup": "A @ B", "team": "A", "side": "away", "lean": None, "player_id": 100,
            "combo_player_ids": None, "game_pk": game_pk,
            "projection": {"stat": stat, "needs": needs}, "grade": grade,
            "recommendation_status": recommendation_status}


PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress"}
FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}


def write_payload(props):
    payload = {"date": "2026-08-14", "props": props}
    path = tempfile.mktemp(suffix=".json")
    json.dump(payload, open(path, "w"))
    return path


def _by_id(payload, row_id):
    return next(r for r in payload["props"] if r["id"] == row_id)


head("1. a pick whose game hasn't started yet is left completely alone")

r1 = row("A", "Over 0.5 Hits", "hits", 1, game_pk=1)
path1 = write_payload([r1])
live1 = tempfile.mktemp(suffix=".json")
with mock.patch.object(gr, "fetch_game_statuses", return_value={1: PREVIEW}):
    out1 = rg.refresh(path1, live_path=live1)
check(_by_id(out1, r1["id"]).get("grade") is None,
      "a Preview-state game gets no grade at all", f"got {_by_id(out1, r1['id'])}")


head("2. a pick whose game is in progress gets marked 'live'")

r2 = row("B", "Over 0.5 Hits", "hits", 1, game_pk=2)
path2 = write_payload([r2])
live2 = tempfile.mktemp(suffix=".json")
with mock.patch.object(gr, "fetch_game_statuses", return_value={2: LIVE}):
    out2 = rg.refresh(path2, live_path=live2)
check(_by_id(out2, r2["id"])["grade"] == "live",
      "a Live-state game is marked 'live', not left unset or graded")


head("3. a pick whose game is Final gets the REAL grade_pick() result -- hit")

r3 = row("C", "Over 0.5 Hits", "hits", 1, game_pk=3)
path3 = write_payload([r3])
live3 = tempfile.mktemp(suffix=".json")
with mock.patch.object(gr, "fetch_game_statuses", return_value={3: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "hit", "actual": 2}) as mp:
    out3 = rg.refresh(path3, live_path=live3)
check(_by_id(out3, r3["id"])["grade"] == "hit",
      "a Final game with a real hit result is marked 'hit'")
check(mp.call_args.kwargs.get("date") == "2026-08-14",
      "grade_pick() is called with the payload's own date, not a hardcoded one",
      f"got call_args={mp.call_args}")


head("4. a Final game grade_pick() can't actually grade (e.g. scratched) is marked 'live', "
     "not silently left unset -- a reader shouldn't see an untouched pick after its game ended")

r4 = row("D", "Over 0.5 Hits", "hits", 1, game_pk=4)
path4 = write_payload([r4])
live4 = tempfile.mktemp(suffix=".json")
with mock.patch.object(gr, "fetch_game_statuses", return_value={4: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "ungraded", "reason": "scratched"}):
    out4 = rg.refresh(path4, live_path=live4)
check(_by_id(out4, r4["id"])["grade"] == "live",
      "an ungraded-at-final pick still gets SOME visible state, not silence")


head("5. a pick already graded hit/miss is TERMINAL -- never re-checked, even if the mocked "
     "status would say something different this cycle")

r5 = row("E", "Over 0.5 Hits", "hits", 1, game_pk=5, grade="hit")
path5 = write_payload([r5])
with mock.patch.object(gr, "fetch_game_statuses") as mp5:
    out5 = rg.refresh(path5)
check(_by_id(out5, r5["id"])["grade"] == "hit", "the terminal grade is untouched")
check(not mp5.called, "fetch_game_statuses isn't even called when every top pick is "
      "already terminally graded -- no wasted API call")


head("6. only rows currently recommendation_status=='top_pick' are ever graded -- a Lean/"
     "Value/Neutral row's game going Final does not get touched, even in the same payload")

r6_top = row("F", "Over 0.5 Hits", "hits", 1, game_pk=6, recommendation_status="top_pick")
r6_lean = row("G", "Over 0.5 Total Bases", "total_bases", 2, game_pk=6, recommendation_status="lean")
path6 = write_payload([r6_top, r6_lean])
live6 = tempfile.mktemp(suffix=".json")
with mock.patch.object(gr, "fetch_game_statuses", return_value={6: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "miss"}):
    out6 = rg.refresh(path6, live_path=live6)
check(_by_id(out6, r6_top["id"])["grade"] == "miss", "the real top pick gets graded")
check(_by_id(out6, r6_lean["id"]).get("grade") is None,
      "a Lean-status row in the same game is left alone -- 'Only ever touches Top Picks' "
      "per this script's own docstring", f"got {_by_id(out6, r6_lean['id'])}")


head("7. grades_updated_at is stamped after a real grading pass, and merges into an "
     "EXISTING live.json (by id) rather than overwriting it -- dashboard-prices.yml and "
     "dashboard-grades.yml are separate 5-minute workflows writing the same file")

existing_live = {"prices_updated_at": "2026-08-14T00:00:00+00:00", "grades_updated_at": None,
                 "props": {r6_top["id"]: {"market_odds": -150}}}
with open(live6, "w") as f:
    json.dump(existing_live, f)
r7 = row("H", "Over 0.5 Hits", "hits", 1, game_pk=7)
path7 = write_payload([r6_top, r7])
with mock.patch.object(gr, "fetch_game_statuses", return_value={6: FINAL, 7: FINAL}), \
     mock.patch.object(gr, "grade_pick", return_value={"grade": "hit"}):
    out7 = rg.refresh(path7, live_path=live6)
check("grades_updated_at" in out7, "grades_updated_at was added to the payload")
# Real bug, found live 2026-08-15: datetime.now().isoformat() (naive, no
# tz suffix) writes e.g. "2026-08-15T13:47:59" -- a viewer's browser
# parses a tz-less ISO string as LOCAL time, not UTC, so the page showed
# an "Updated" time HOURS in the future for anyone west of UTC. Must
# carry a real UTC offset so `new Date(iso)` parses it correctly.
check("+00:00" in out7["grades_updated_at"] or out7["grades_updated_at"].endswith("Z"),
      "grades_updated_at is timezone-aware (a real UTC offset), not a naive local timestamp "
      "that a browser would misread as local time", f"got {out7['grades_updated_at']!r}")

with open(live6, encoding="utf-8") as f:
    merged_live = json.load(f)
check(merged_live["prices_updated_at"] == "2026-08-14T00:00:00+00:00",
      "a grade-only refresh never touches the prices_updated_at another workflow wrote",
      f"got {merged_live}")
check(merged_live["props"][r6_top["id"]].get("market_odds") == -150,
      "an existing price delta for this id survives a later grade refresh -- fields merge "
      "per-id, the row isn't overwritten wholesale", f"got {merged_live['props'][r6_top['id']]}")
check(merged_live["props"][r6_top["id"]].get("grade") == "hit",
      "the new grade delta is ALSO present alongside the surviving price fields",
      f"got {merged_live['props'][r6_top['id']]}")
check(r7["id"] in merged_live["props"] and merged_live["props"][r7["id"]].get("grade") == "hit",
      "a second top pick graded the same cycle also gets its own delta entry",
      f"got {merged_live['props'].get(r7['id'])}")


head("8. an empty props list is a clean no-op, no crash, no fetch")

path8 = write_payload([])
with mock.patch.object(gr, "fetch_game_statuses") as mp8:
    out8 = rg.refresh(path8)
check(out8["props"] == [], "empty props stays empty")
check(not mp8.called, "no game-status fetch happens when there's nothing to grade")

for p in (path1, path2, path3, path4, path5, path6, path7, path8):
    os.remove(p)
for p in (live1, live2, live3, live4, live6):
    if os.path.exists(p):
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
