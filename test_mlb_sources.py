#!/usr/bin/env python3
"""test_mlb_sources.py — coverage for mlb_sources.batter_recent_game_log()/
pitcher_recent_starts(), the two real per-game-log fetchers built for the
STREAKS feature. Direct request, verbatim: "STREAKS. Hits in a row, 2+
bases in a row, over X strikeouts in a row, any trends that are useful."

Mocks _game_log() directly (same pattern _empirical_batter_one/
_empirical_pitcher_one already use internally) rather than hitting the
real MLB stats API -- this tests the order-reversal, participation
filter, and fail-soft behavior these two functions add on top of
_game_log(), not _game_log() itself.

    /tmp/mlbvenv/bin/python3 test_mlb_sources.py
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


import mlb_sources as msrc


head("1. batter_recent_game_log: MLB's gameLog splits come back oldest-first "
     "-- reversed so index 0 is always the MOST RECENT game")

# Oldest-first, as MLB's real API returns it (verified live against a real
# player before this logic was written -- see the function's own docstring).
OLDEST_FIRST = [
    {"date": "2026-08-01", "stat": {"plateAppearances": 4, "hits": 0, "totalBases": 0}},
    {"date": "2026-08-02", "stat": {"plateAppearances": 4, "hits": 1, "totalBases": 1}},
    {"date": "2026-08-03", "stat": {"plateAppearances": 4, "hits": 2, "totalBases": 3}},
]
with mock.patch.object(msrc, "_game_log", return_value=OLDEST_FIRST) as mp1:
    games1 = msrc.batter_recent_game_log(12345)

check(mp1.call_args[0] == (12345, "hitting"), "fetches the 'hitting' group for the given player_id",
      f"got {mp1.call_args}")
check(games1[0]["date"] == "2026-08-03", "index 0 is the most recent game, not the oldest",
      f"got {games1[0]}")
check(games1[-1]["date"] == "2026-08-01", "the oldest game is last")
check(games1[0] == {"date": "2026-08-03", "hits": 2, "total_bases": 3},
      "hits/total_bases pass through under the field names the streak counter expects")

head("2. batter_recent_game_log filters out games with no real plate appearance "
     "(didn't really play -- not evidence for or against a streak)")

WITH_DNP = [
    {"date": "2026-08-01", "stat": {"plateAppearances": 4, "hits": 1, "totalBases": 1}},
    {"date": "2026-08-02", "stat": {"plateAppearances": 0, "hits": 0, "totalBases": 0}},
]
with mock.patch.object(msrc, "_game_log", return_value=WITH_DNP):
    games2 = msrc.batter_recent_game_log(12345)
check(len(games2) == 1, "the 0-PA game is dropped, not counted as a miss",
      f"got {games2}")
check(games2[0]["date"] == "2026-08-01", "the surviving game is the one with real plate appearances")

head("3. batter_recent_game_log caps at max_games and fails soft to [] on any fetch error")

LONG_LOG = [{"date": f"2026-08-{i:02d}", "stat": {"plateAppearances": 4, "hits": 1, "totalBases": 1}}
            for i in range(1, 30)]
with mock.patch.object(msrc, "_game_log", return_value=LONG_LOG):
    games3 = msrc.batter_recent_game_log(12345, max_games=5)
check(len(games3) == 5, "capped at max_games", f"got {len(games3)}")
check(games3[0]["date"] == "2026-08-29", "the cap keeps the MOST RECENT games, not the oldest")

with mock.patch.object(msrc, "_game_log", side_effect=Exception("network down")):
    games3_fail = msrc.batter_recent_game_log(12345)
check(games3_fail == [], "a fetch failure returns [] rather than raising -- must never take down "
      "the whole streaks computation over one player's log")

head("4. pitcher_recent_starts: same reversal, filtered to real starts "
     "(gamesStarted >= 1, not relief appearances)")

PITCHER_LOG = [
    {"date": "2026-08-01", "stat": {"gamesStarted": 1, "strikeOuts": 6}},
    {"date": "2026-08-04", "stat": {"gamesStarted": 0, "strikeOuts": 1}},  # relief appearance
    {"date": "2026-08-06", "stat": {"gamesStarted": 1, "strikeOuts": 8}},
]
with mock.patch.object(msrc, "_game_log", return_value=PITCHER_LOG) as mp4:
    starts4 = msrc.pitcher_recent_starts(54321)
check(mp4.call_args[0] == (54321, "pitching"), "fetches the 'pitching' group")
check(len(starts4) == 2, "the relief appearance (gamesStarted=0) is excluded, only real starts "
      "count toward a strikeouts-prop streak", f"got {starts4}")
check(starts4[0] == {"date": "2026-08-06", "strikeouts": 8},
      "most recent start is first")
check(starts4[1] == {"date": "2026-08-01", "strikeouts": 6}, "second-most-recent start is last")

head("5. pitcher_recent_starts fails soft to [] on any fetch error")

with mock.patch.object(msrc, "_game_log", side_effect=Exception("network down")):
    starts5_fail = msrc.pitcher_recent_starts(54321)
check(starts5_fail == [], "a fetch failure returns [] rather than raising")

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
