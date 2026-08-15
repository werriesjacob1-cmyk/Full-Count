#!/usr/bin/env python3
"""test_check_lineups.py — coverage for dashboard/check_lineups.py, the
cheap 10-minute lineup poll. Direct request, verbatim: "How do we have
the board update each time a new lineup comes out?"

Mocks requests.get directly (same pattern test_refresh_grades.py uses for
grade_results) rather than hitting the real MLB schedule endpoint -- this
tests check_lineups.py's own state-diffing logic (what counts as
"newly confirmed", the new-day reset, fail-soft on a network error), not
MLB's API itself.

    /tmp/mlbvenv/bin/python3 test_check_lineups.py
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


import check_lineups as cl


def schedule_resp(games):
    """games: list of (game_pk, has_away_lineup, has_home_lineup)."""
    out = []
    for pk, away, home in games:
        lineups = {}
        if away:
            lineups["awayPlayers"] = [{"id": 1, "fullName": "Away Batter"}]
        if home:
            lineups["homePlayers"] = [{"id": 2, "fullName": "Home Batter"}]
        out.append({"gamePk": pk, "lineups": lineups})
    return {"dates": [{"games": out}]}


head("1. fetch_confirmed_game_pks: a game only counts as confirmed once BOTH "
     "sides have a real posted lineup -- one side alone is not a bettable slate")

with mock.patch.object(cl.requests, "get") as mp1:
    mp1.return_value.json.return_value = schedule_resp([
        (100, True, True),   # both sides -- confirmed
        (200, True, False),  # away only -- not confirmed
        (300, False, False), # neither -- not confirmed
    ])
    mp1.return_value.raise_for_status = lambda: None
    confirmed1 = cl.fetch_confirmed_game_pks("2026-08-15")

check(confirmed1 == {100}, "only the fully-confirmed game counts", f"got {confirmed1}")

head("2. fetch_confirmed_game_pks fails soft to an empty set on a network error "
     "-- a missed check is caught by the next one 10 minutes later, never a crash")

with mock.patch.object(cl.requests, "get", side_effect=Exception("network down")):
    confirmed2 = cl.fetch_confirmed_game_pks("2026-08-15")
check(confirmed2 == set(), "a fetch failure returns an empty set, not an exception")

head("3. load_state/save_state round-trip, and a new day resets state "
     "(yesterday's confirmed games don't carry over)")

tmpdir = tempfile.mkdtemp()
state_path = os.path.join(tmpdir, "lineup_watch_state.json")
with mock.patch.object(cl, "STATE_PATH", state_path):
    check(cl.load_state("2026-08-15") == set(), "no state file yet -- empty set, not a crash")

    cl.save_state("2026-08-15", {100, 200})
    check(cl.load_state("2026-08-15") == {100, 200}, "state round-trips for the same date")
    check(cl.load_state("2026-08-16") == set(),
          "a different date (a new day) resets to empty -- yesterday's confirmed games are stale")

head("4. main(): the first confirmed lineup of the day is reported as changed, "
     "and the full confirmed set is persisted")

tmpdir2 = tempfile.mkdtemp()
state_path2 = os.path.join(tmpdir2, "lineup_watch_state.json")
gh_out2 = os.path.join(tmpdir2, "gh_output.txt")
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out2}), \
     mock.patch.object(cl.requests, "get") as mp4:
    mp4.return_value.json.return_value = schedule_resp([(100, True, True)])
    mp4.return_value.raise_for_status = lambda: None
    cl.main()
    state_after_4 = cl.load_state("2026-08-15")

with open(gh_out2) as f:
    out4 = f.read()
check("changed=true" in out4, "GITHUB_OUTPUT reports changed=true on the first confirmed game",
      f"got {out4!r}")
check(state_after_4 == {100}, "the confirmed game is persisted to state", f"got {state_after_4}")

head("5. main(): re-running with the SAME confirmed game reports no change -- "
     "must not re-trigger a rebuild for a lineup already seen")

gh_out5 = os.path.join(tmpdir2, "gh_output2.txt")
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out5}), \
     mock.patch.object(cl.requests, "get") as mp5:
    mp5.return_value.json.return_value = schedule_resp([(100, True, True)])
    mp5.return_value.raise_for_status = lambda: None
    cl.main()

with open(gh_out5) as f:
    out5 = f.read()
check("changed=false" in out5, "GITHUB_OUTPUT reports changed=false when nothing is new",
      f"got {out5!r}")

head("6. main(): a SECOND game getting confirmed alongside an already-known one "
     "still reports changed -- the new one must not be masked by the old one")

gh_out6 = os.path.join(tmpdir2, "gh_output3.txt")
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out6}), \
     mock.patch.object(cl.requests, "get") as mp6:
    mp6.return_value.json.return_value = schedule_resp([(100, True, True), (200, True, True)])
    mp6.return_value.raise_for_status = lambda: None
    cl.main()
    state_after_6 = cl.load_state("2026-08-15")

with open(gh_out6) as f:
    out6 = f.read()
check("changed=true" in out6, "a newly-confirmed second game still reports changed=true",
      f"got {out6!r}")
check(state_after_6 == {100, 200}, "state now holds both confirmed games", f"got {state_after_6}")

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
