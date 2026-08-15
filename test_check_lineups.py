#!/usr/bin/env python3
"""test_check_lineups.py — coverage for dashboard/check_lineups.py, the
cheap 10-minute lineup poll. Direct requests, verbatim: "How do we have
the board update each time a new lineup comes out?" and the follow-up:
catching a lineup that was already posted but then CHANGED (a late
scratch) -- the exact gap check_scratches.py's own docstring names: "A
batter confirmed in the two o'clock lineup and scratched at six was a
fully valid pick when it was made."

Mocks requests.get directly (same pattern test_refresh_grades.py uses for
grade_results) rather than hitting the real MLB schedule endpoint -- this
tests check_lineups.py's own state-diffing logic (what counts as "new" vs
"changed", the new-day reset, fail-soft on a network error), not MLB's
API itself.

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


def lineup(n, start_id=1):
    """n players with sequential ids, matching the {"id": ...} shape the
    real hydrate returns."""
    return [{"id": start_id + i, "fullName": f"Player {start_id + i}"} for i in range(n)]


def schedule_resp(games):
    """games: list of (game_pk, away_player_list_or_None, home_player_list_or_None)."""
    out = []
    for pk, away, home in games:
        lineups = {}
        if away is not None:
            lineups["awayPlayers"] = away
        if home is not None:
            lineups["homePlayers"] = home
        out.append({"gamePk": pk, "lineups": lineups})
    return {"dates": [{"games": out}]}


head("1. fetch_confirmed_lineups: a game only counts as confirmed once BOTH sides "
     "have a REAL, COMPLETE (9+) posted lineup -- matching quality_control()'s own "
     "threshold, not just 'something non-empty'")

with mock.patch.object(cl.requests, "get") as mp1:
    mp1.return_value.json.return_value = schedule_resp([
        (100, lineup(9, 1), lineup(9, 101)),   # both complete -- confirmed
        (200, lineup(9, 1), lineup(3, 201)),   # home incomplete -- not confirmed
        (300, lineup(5, 1), lineup(5, 301)),   # both incomplete -- not confirmed
        (400, None, None),                     # neither posted -- not confirmed
    ])
    mp1.return_value.raise_for_status = lambda: None
    confirmed1 = cl.fetch_confirmed_lineups("2026-08-15")

check(set(confirmed1.keys()) == {100}, "only the fully-confirmed (9+/9+) game counts",
      f"got {list(confirmed1.keys())}")
check(confirmed1[100]["away"] == set(range(1, 10)), "away roster is the real player-id set")
check(confirmed1[100]["home"] == set(range(101, 110)), "home roster is the real player-id set")

head("2. fetch_confirmed_lineups fails soft to {} on a network error -- a missed "
     "check is caught by the next one 10 minutes later, never a crash")

with mock.patch.object(cl.requests, "get", side_effect=Exception("network down")):
    confirmed2 = cl.fetch_confirmed_lineups("2026-08-15")
check(confirmed2 == {}, "a fetch failure returns an empty dict, not an exception")

head("3. load_state/save_state round-trip real player-id sets, and a new day resets "
     "state entirely (yesterday's confirmed lineups don't carry over)")

tmpdir = tempfile.mkdtemp()
state_path = os.path.join(tmpdir, "lineup_watch_state.json")
with mock.patch.object(cl, "STATE_PATH", state_path):
    check(cl.load_state("2026-08-15") == {}, "no state file yet -- empty dict, not a crash")

    cl.save_state("2026-08-15", {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}})
    st = cl.load_state("2026-08-15")
    check(st == {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}},
          "state round-trips real player-id sets for the same date", f"got {st}")
    check(cl.load_state("2026-08-16") == {},
          "a different date (a new day) resets to empty -- yesterday's lineups are stale")

head("4. diff(): a game confirmed for the first time is 'new', not 'changed'")

new4, changed4 = cl.diff({}, {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}})
check(new4 == [100] and changed4 == [], "first-ever confirmation is reported as new")

head("5. diff(): THE SCRATCH CASE -- an already-confirmed game whose roster changes "
     "(a player swapped out) is 'changed', not silently ignored, even though the "
     "game itself was already known-confirmed")

seen5 = {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}}
now5 = {100: {"away": {1, 2, 99}, "home": {4, 5, 6}}}  # player 3 scratched, 99 subbed in
new5, changed5 = cl.diff(seen5, now5)
check(new5 == [] and changed5 == [100],
      "a roster change on an already-confirmed game is reported as changed", f"got {new5}, {changed5}")

head("6. diff(): an unchanged, already-confirmed game is neither new nor changed -- "
     "must not re-trigger a rebuild for a lineup nothing happened to")

seen6 = {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}}
now6 = {100: {"away": {1, 2, 3}, "home": {4, 5, 6}}}
new6, changed6 = cl.diff(seen6, now6)
check(new6 == [] and changed6 == [], "identical roster is neither new nor changed")

head("7. main(): the first confirmed lineup of the day reports changed=true and "
     "persists the full roster")

tmpdir2 = tempfile.mkdtemp()
state_path2 = os.path.join(tmpdir2, "lineup_watch_state.json")
gh_out7 = os.path.join(tmpdir2, "gh_output.txt")
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out7}), \
     mock.patch.object(cl.requests, "get") as mp7:
    mp7.return_value.json.return_value = schedule_resp([(100, lineup(9, 1), lineup(9, 101))])
    mp7.return_value.raise_for_status = lambda: None
    cl.main()
    state_after_7 = cl.load_state("2026-08-15")

with open(gh_out7) as f:
    out7 = f.read()
check("changed=true" in out7, "GITHUB_OUTPUT reports changed=true on the first confirmed game",
      f"got {out7!r}")
check(state_after_7 == {100: {"away": set(range(1, 10)), "home": set(range(101, 110))}},
      "the confirmed roster is persisted to state", f"got {state_after_7}")

head("8. main(): re-running with the SAME confirmed roster reports no change")

gh_out8 = os.path.join(tmpdir2, "gh_output2.txt")
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out8}), \
     mock.patch.object(cl.requests, "get") as mp8:
    mp8.return_value.json.return_value = schedule_resp([(100, lineup(9, 1), lineup(9, 101))])
    mp8.return_value.raise_for_status = lambda: None
    cl.main()

with open(gh_out8) as f:
    out8 = f.read()
check("changed=false" in out8, "GITHUB_OUTPUT reports changed=false when nothing is new or changed",
      f"got {out8!r}")

head("9. main(): a LATE SCRATCH on an already-confirmed game reports changed=true "
     "end-to-end, and the new roster overwrites the stale one in state")

gh_out9 = os.path.join(tmpdir2, "gh_output3.txt")
scratched_away = lineup(8, 1) + [{"id": 999, "fullName": "Replacement Batter"}]  # player 9 -> 999
with mock.patch.object(cl, "STATE_PATH", state_path2), \
     mock.patch.object(cl, "today", return_value="2026-08-15"), \
     mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out9}), \
     mock.patch.object(cl.requests, "get") as mp9:
    mp9.return_value.json.return_value = schedule_resp([(100, scratched_away, lineup(9, 101))])
    mp9.return_value.raise_for_status = lambda: None
    cl.main()
    state_after_9 = cl.load_state("2026-08-15")

with open(gh_out9) as f:
    out9 = f.read()
check("changed=true" in out9, "a late scratch on an already-confirmed game triggers changed=true",
      f"got {out9!r}")
check(999 in state_after_9[100]["away"] and 9 not in state_after_9[100]["away"],
      "the new (post-scratch) roster overwrites the stale one in state",
      f"got {state_after_9[100]['away']}")

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
