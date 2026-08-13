#!/usr/bin/env python3
"""test_multiyear_baseline.py — direct coverage for
mlb_daily.compute_multiyear_baseline(), which had a real, currently-live
bug: it fetches real fallback data (via fg_bat()'s Statcast reroute)
whenever FanGraphs is blocked -- which it currently always is, a known
403-from-GitHub-Actions issue -- but discarded it every time, because
its weighting math only knew how to read the FanGraphs-only "wRC+"
column, which the Statcast fallback shape doesn't carry. Confirmed live
before the fix: the section always rendered "wRC+ not available in
batting data." even though fg_bat() had genuinely fetched 600+ batters.

    /tmp/mlbvenv/bin/python3 test_multiyear_baseline.py
"""
import sys
from unittest import mock

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


import pandas as pd
import mlb_daily as m

head("1. FanGraphs reachable (wRC+ present) -- the pre-existing, already-working path")

def fake_fg_bat_wrc(yr, label="", qual=50):
    return pd.DataFrame({"Name": ["Yordan Alvarez", "Aaron Judge"],
                          "Team": ["HOU", "NYY"], "wRC+": [165.0, 180.0],
                          "AVG": [0.305, 0.290], "HR": [30, 35], "PA": [400, 420]})

with mock.patch.object(m, "fg_bat", side_effect=fake_fg_bat_wrc):
    out = m.compute_multiyear_baseline()
check("wRC+ not available" not in out and "No multi-year data" not in out,
      "when wRC+ IS available, the existing wRC+-weighted path still produces real output")
check("Yordan Alvarez" in out, "a real player name appears in the wRC+ path's output")

head("2. FanGraphs blocked -- fg_bat() returns the Statcast xwOBA fallback shape "
     "(no wRC+, no HR, no PA -- matching _fg_statcast_bat_fallback's real columns). "
     "THE BUG: this used to render 'wRC+ not available in batting data.' and discard "
     "the real fetched data entirely.")

def fake_fg_bat_statcast_fallback(yr, label="", qual=50):
    return pd.DataFrame({"Name": ["Yordan Alvarez", "Aaron Judge", "Juan Soto"],
                          "player_id": [670541, 592450, 665742],
                          "AVG": [0.305, 0.290, 0.288],
                          "xBA": [0.298, 0.285, 0.280],
                          "xwOBA": [0.437, 0.442, 0.432],
                          "wOBA": [0.420, 0.430, 0.425]})

with mock.patch.object(m, "fg_bat", side_effect=fake_fg_bat_statcast_fallback):
    out2 = m.compute_multiyear_baseline()
check("wRC+ not available" not in out2,
      "the FanGraphs-blocked case no longer falls through to the dead 'wRC+ not available' message")
check("No multi-year data" not in out2,
      "real fallback data is not discarded as if nothing had been fetched")
check(any(name in out2 for name in ("Yordan Alvarez", "Aaron Judge", "Juan Soto")),
      "a real player name from the Statcast fallback appears in the rendered output",
      out2[:300])
check("xwOBA" in out2, "the fallback path's own metric (xwOBA) is what actually got weighted and shown")

head("3. neither wRC+ nor xwOBA available -- genuinely nothing usable, "
     "should say so honestly rather than crash")

def fake_fg_bat_neither(yr, label="", qual=50):
    return pd.DataFrame({"Name": ["Some Guy"], "AVG": [0.250]})

with mock.patch.object(m, "fg_bat", side_effect=fake_fg_bat_neither):
    out3 = m.compute_multiyear_baseline()
check("Neither wRC+ nor xwOBA available" in out3,
      "an honest 'nothing usable' message when truly nothing can be weighted, not a crash",
      out3[:200])

head("4. fg_bat() returning nothing at all across all three years doesn't crash")

with mock.patch.object(m, "fg_bat", return_value=pd.DataFrame()):
    out4 = m.compute_multiyear_baseline()
check("No multi-year data" in out4, "an all-empty fetch still returns the honest empty message")


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
