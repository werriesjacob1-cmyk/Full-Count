#!/usr/bin/env python3
"""test_rest_and_usage.py — checks mlb_sources.rest_and_usage's asof cutoff
filtering.

_rest_batter_one/_rest_pitcher_one pull a player's FULL raw gameLog (no
window param the API respects) and take the most recent date to compute
days_since_last_game / consecutive_games / days_since_last_start. Without an
explicit asof filter, a backtest simulating a past date D would see whatever
games actually happened after D (since the real gameLog reflects reality up
to whenever the request runs), not the games that had happened as of D --
a lookahead leak. This locks in the fix: asof=None (the live default) keeps
today's unfiltered behaviour, and asof='YYYY-MM-DD' drops every date after
the cutoff before any of the three metrics are computed.

    /tmp/mlbvenv/bin/python3 test_rest_and_usage.py
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


import mlb_daily as m
import mlb_sources as src

TODAY = "2026-08-12"  # the real, unrepointed "today" -- simulates a live run
                       # requesting data mid-backtest, after the cutoff below.
CUTOFF = "2026-07-20"  # the date being simulated minus one, per PointInTime


def _hit_split(d):
    return {"date": d, "stat": {"gamesStarted": 0}}


def _pit_split(d, started=True):
    return {"date": d, "stat": {"gamesStarted": 1 if started else 0}}


head("1. batter: asof=None (live default) keeps the unfiltered / most-recent date")

BAT_GAMELOG = {"stats": [{"splits": [
    _hit_split("2026-07-17"), _hit_split("2026-07-18"), _hit_split("2026-07-19"),
    _hit_split("2026-07-25"), _hit_split("2026-08-10"),  # "future" relative to CUTOFF
]}]}

with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = BAT_GAMELOG
    mock_get.return_value.raise_for_status = lambda: None
    res_live = src._rest_batter_one((1, "Player 1", None))

check(res_live is not None, "live (asof=None) call returns a result")
check(res_live.get("days_since_last_game") == 2,
      "asof=None uses the real most recent date (2026-08-10, 2 days before TODAY)",
      f"got {res_live}")

head("2. batter: asof=CUTOFF drops every date after the cutoff")

with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = BAT_GAMELOG
    mock_get.return_value.raise_for_status = lambda: None
    res_pit = src._rest_batter_one((1, "Player 1", CUTOFF))

check(res_pit is not None, "asof=CUTOFF call still returns a result (games remain before cutoff)")
check(res_pit.get("consecutive_games") == 3,
      "asof=CUTOFF correctly ignores the 07-25 and 08-10 rows for the streak calc",
      f"got {res_pit}")
# NOTE: days_since_last_game is (today - last), and "today" here is m.TODAY,
# which PointInTime always repoints to the same cutoff -- so in real backtest
# use this is always small and correct. This unit test doesn't repoint TODAY
# to isolate the asof filter itself, so only the filtered *set* of dates
# (consecutive_games) is asserted here, not days_since_last_game's magnitude.

head("3. pitcher: asof=CUTOFF drops post-cutoff starts and recomputes days-since-last-start")

PIT_GAMELOG = {"stats": [{"splits": [
    _pit_split("2026-07-10"), _pit_split("2026-07-16", started=False),  # relief outing, excluded either way
    _pit_split("2026-07-21"), _pit_split("2026-08-01"),
]}]}

with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    mock_get.return_value.json.return_value = PIT_GAMELOG
    mock_get.return_value.raise_for_status = lambda: None
    res_pit_live = src._rest_pitcher_one((2, "Pitcher 1", None))
    res_pit_cut = src._rest_pitcher_one((2, "Pitcher 1", CUTOFF))

check(res_pit_live.get("starts_this_season") == 3,
      "asof=None counts all 3 real starts (relief row excluded regardless)",
      f"got {res_pit_live}")
check(res_pit_cut.get("starts_this_season") == 1,
      "asof=CUTOFF only counts the 07-10 start (07-21 and 08-01 are after cutoff)",
      f"got {res_pit_cut}")

head("4. rest_and_usage passes asof through to both worker pools")

GAME_META = [{"away_lineup": [{"id": 1, "name": "Player 1"}], "home_lineup": [],
              "away_sp": "Pitcher 1", "away_sp_id": 2, "home_sp": "TBD", "home_sp_id": None}]

with mock.patch.object(m, "retry_get") as mock_get, mock.patch.object(m, "TODAY", TODAY):
    def _side_effect(url, **kw):
        resp = mock.Mock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = BAT_GAMELOG if "hitting" in str(kw.get("params")) else PIT_GAMELOG
        return resp
    mock_get.side_effect = _side_effect
    out = src.rest_and_usage(GAME_META, asof=CUTOFF)

check(out["batters"].get(1, {}).get("consecutive_games") == 3,
      "rest_and_usage(..., asof=CUTOFF) applies the cutoff to batters",
      f"got {out['batters'].get(1)}")
check(out["starters"].get(2, {}).get("starts_this_season") == 1,
      "rest_and_usage(..., asof=CUTOFF) applies the cutoff to starters",
      f"got {out['starters'].get(2)}")

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
