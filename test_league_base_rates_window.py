#!/usr/bin/env python3
"""test_league_base_rates_window.py — coverage for mlb_sources.
league_base_rates(window_days=...), the 2026-08-20 accuracy fix.

WHY THIS EXISTS. league_base_rates() feeds generate_picks.py's
MODEL_SHRINK_K formula (hits/total_bases/home_runs) and value_board.py's
live value screen -- a season-to-date CUMULATIVE average, which structurally
lags real early-season conditions (checked live, all 3 seasons on record:
hits_1plus reads 0.4712-0.4867 in early April, not converging to its
~0.535-0.539 steady state until mid-May -- NOT sample-size noise, since
5,000+ batter-games already back the April reading). window_days adds an
optional trailing-window restriction so the rate can track CURRENT
conditions instead of smearing in the season's coldest early week forever.

    /tmp/mlbvenv/bin/python3 test_league_base_rates_window.py
"""
import sys
import datetime as _dt
from unittest import mock

import pandas as pd

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
import mlb_daily as m


def _row(game_date, batter, pitcher, game_pk, inning, event):
    return {"game_date": game_date, "batter": batter, "pitcher": pitcher,
           "game_pk": game_pk, "inning": inning, "events": event}


def make_frame():
    """A small, fully synthetic season: one batter-game per day across 40
    days (2026-03-01 .. 2026-04-09), alternating hit/out, so hits_1plus is
    exactly computable by hand for any window. Distinct player_id/game_pk
    per row -- each row is its own "batter-game" (matching how
    league_base_rates groups by (batter, game_pk))."""
    rows = []
    start = _dt.date(2026, 3, 1)
    for i in range(40):
        d = (start + _dt.timedelta(days=i)).strftime("%Y-%m-%d")
        # A real PA row also needs a pitcher/inning for the strikeout side
        # of this same function -- included so it doesn't silently except.
        event = "single" if i % 2 == 0 else "field_out"
        rows.append(_row(d, batter=1000 + i, pitcher=2000, game_pk=3000 + i,
                         inning=1, event=event))
    return pd.DataFrame(rows)


def with_mocked_source(today, frame, fn):
    with mock.patch.object(m, "fetch_season_statcast", return_value=frame), \
         mock.patch.object(m, "TODAY", today):
        msrc._LEAGUE_RATES_CACHE.clear()
        return fn()


head("1. window_days=None (default): identical to the pre-fix cumulative "
     "behavior -- every game in the frame counts, regardless of date")

frame = make_frame()
today = "2026-04-09"  # the frame's own last day
out_none = with_mocked_source(today, frame, lambda: msrc.league_base_rates())
check(out_none["_n_batter_games"] == 40, "all 40 synthetic batter-games counted "
     "with no window", f"got {out_none['_n_batter_games']}")
check(out_none["hits_1plus"] == 0.5, "exactly half hit (even i -> single) -> 0.5",
     f"got {out_none['hits_1plus']}")

head("2. window_days=N restricts to the trailing N days ending at (point-in-time) "
     "m.TODAY -- fewer batter-games counted, only the recent ones")

out_10 = with_mocked_source(today, frame, lambda: msrc.league_base_rates(window_days=10))
# [today-10, today] inclusive -> 11 calendar days -> 11 of the 40 rows (one
# per day) fall in range.
check(out_10["_n_batter_games"] == 11, "trailing 10-day window keeps exactly "
     "11 of the 40 batter-games (one per day, inclusive both ends)",
     f"got {out_10['_n_batter_games']}")

head("3. window_days=N degrades gracefully to the FULL cumulative result when "
     "the window is wider than the whole season so far -- no special-cased "
     "bootstrap logic needed, this falls out of the date filter itself")

out_wide = with_mocked_source(today, frame, lambda: msrc.league_base_rates(window_days=365))
check(out_wide["_n_batter_games"] == 40, "a 365-day window still only has 40 "
     "real days to draw from -- identical to window_days=None",
     f"got {out_wide['_n_batter_games']}")
check(out_wide["hits_1plus"] == out_none["hits_1plus"], "same rate too")

head("4. the window boundary is correct -- a batter-game exactly (today - N) "
     "days old is INCLUDED (inclusive), one day older is EXCLUDED")

# today=2026-04-09, window=5 -> boundary is 2026-04-04 (inclusive).
frame2 = pd.DataFrame([
    _row("2026-04-03", 1, 2000, 3001, 1, "single"),   # 6 days old -> excluded
    _row("2026-04-04", 2, 2000, 3002, 1, "single"),   # 5 days old -> INCLUDED (boundary)
    _row("2026-04-09", 3, 2000, 3003, 1, "field_out"),  # today -> included
])
out_boundary = with_mocked_source("2026-04-09", frame2,
                                  lambda: msrc.league_base_rates(window_days=5))
check(out_boundary["_n_batter_games"] == 2, "exactly 2 of the 3 rows fall inside "
     "the inclusive 5-day window (04-03 is excluded, 04-04 and 04-09 are kept)",
     f"got {out_boundary['_n_batter_games']}")

head("5. an EMPTY window (real data exists, but none of it falls inside the "
     "requested window) returns an empty dict honestly, same as the pre-fix "
     "'no data at all' case -- never a fabricated rate from zero real rows")

frame3 = pd.DataFrame([_row("2026-01-01", 1, 2000, 3001, 1, "single")])
out_empty = with_mocked_source("2026-04-09", frame3,
                               lambda: msrc.league_base_rates(window_days=5))
check(out_empty == {}, "no rows survive the window -> empty dict, not a fake rate",
     f"got {out_empty}")

head("6. window_days=None and window_days=30 are cached SEPARATELY -- calling "
     "one must never silently return the other's cached result")

msrc._LEAGUE_RATES_CACHE.clear()
with mock.patch.object(m, "fetch_season_statcast", return_value=frame), \
     mock.patch.object(m, "TODAY", today):
    r_cum = msrc.league_base_rates()
    r_win = msrc.league_base_rates(window_days=10)
check(r_cum["_n_batter_games"] != r_win["_n_batter_games"],
     "the two calls returned genuinely different results in the same process, "
     "proving they weren't cross-cached", f"cum={r_cum['_n_batter_games']} "
     f"win={r_win['_n_batter_games']}")
check(msrc._LEAGUE_RATES_CACHE.get(None, {}).get("_n_batter_games") == 40,
     "the cumulative result is cached under the None key")
check(msrc._LEAGUE_RATES_CACHE.get(10, {}).get("_n_batter_games") == 11,
     "the windowed result is cached under its own window_days key, not "
     "overwriting the cumulative one")

head("7. a failed/empty fetch is NOT cached -- calling again must retry, "
     "matching the pre-fix 'never cache a failure' behavior exactly")

msrc._LEAGUE_RATES_CACHE.clear()
with mock.patch.object(m, "fetch_season_statcast", return_value=pd.DataFrame()):
    r1 = msrc.league_base_rates()
    r2 = msrc.league_base_rates(window_days=30)
check(r1 == {} and r2 == {}, "both calls against an empty source honestly "
     "return empty")
check(None not in msrc._LEAGUE_RATES_CACHE or not msrc._LEAGUE_RATES_CACHE.get(None),
     "the empty cumulative result was not cached as if it were real -- a "
     "later real fetch can still succeed instead of being stuck returning {}")

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
