#!/usr/bin/env python3
"""test_backtest_weather_timezone.py — regression coverage for a real bug
found during the 2026-08-26 release-candidate point-in-time audit:
backtest/engine.py's park_weather_asof() (the historical-replay weather
path) indexed the Open-Meteo ARCHIVE endpoint's `timezone=auto` hourly
array with `gmeta["hour"]` -- the game's EASTERN hour -- the exact same
bug mlb_daily.forecast_hour_index() (commit 8d00954b) already fixed at
all 4 LIVE call sites. The archive request is ALSO made with
timezone=auto (same code, verified by reading backtest/engine.py's own
request params), so its hourly.time array is ALSO stadium-local, not
Eastern -- backtest weather was silently reading the wrong hour for
every non-Eastern park, with the identical 0/1/2/3-hour error by zone
the live-path fix already measured and fixed.

Not a leakage bug (temperature/wind are not information about the
outcome), but a real correctness bug in what the backtest actually
measures -- this is what "prove the entire feature is point-in-time
safe" turns up when you check the historical path too, not just the
live one.

    /tmp/mlbvenv/bin/python3 test_backtest_weather_timezone.py
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


import backtest.engine as be  # noqa: E402

head("1. REGRESSION GUARD: a real open-air Central-zone park (Kauffman Stadium, "
     "Kansas City) gets the STADIUM-LOCAL hour's temperature from the archive "
     "response, not the Eastern hour's -- a 1-hour real-world error the old "
     "gmeta['hour'] indexing produced. (A retractable-roof park like Daikin Park "
     "can't test this branch -- see check 3, it never reaches the archive read at "
     "all in the current backtest weather path.)")

# Real shape: a 7:10pm CT first pitch is 00:10 UTC the next day. The game's
# EASTERN hour (the old, buggy index) would read 20 (8pm ET); the real
# STADIUM-LOCAL hour is 19 (7pm CT) -- these must produce DIFFERENT array
# reads, and the fix must return the LOCAL one.
game_meta = [{
    "matchup": "Seattle Mariners @ Kansas City Royals",
    "venue": "Kauffman Stadium",
    "hour": 20,  # the OLD buggy Eastern-hour field, still populated -- must be IGNORED
    "game_start_utc": "2026-06-15T00:10:00Z",  # 7:10pm CT / 8:10pm ET
}]

hourly_times = [f"2026-06-15T{h:02d}:00" for h in range(24)]
# Distinct, checkable temperatures per local hour: hour 19 (correct, local
# first-pitch hour) = 91.0F; hour 20 (the OLD buggy Eastern-hour index) = 77.0F
# -- if the bug is present, the test reads 77.0, not 91.0.
temps = [70.0 + h for h in range(24)]
temps[19] = 91.0
temps[20] = 77.0

fake_archive_response = {
    "timezone": "America/Chicago",
    "hourly": {
        "time": hourly_times,
        "temperature_2m": temps,
        "windspeed_10m": [5.0] * 24,
        "winddirection_10m": [180] * 24,
        "relativehumidity_2m": [50] * 24,
    },
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


with mock.patch.object(be.m, "retry_get", return_value=_FakeResp(fake_archive_response)):
    out = be.park_weather_asof(game_meta, "2026-06-15", sleep=0)

entry = out.get("Seattle Mariners @ Kansas City Royals")
check(entry is not None, "an entry was produced for the real matchup", f"got out={out}")
check(entry["temp"] == 91.0,
      "REGRESSION GUARD: temp is read from the real STADIUM-LOCAL hour (19, 91.0F), "
      "not the old Eastern-hour index (20, which would read 77.0F)",
      f"got temp={entry.get('temp')}")
check(entry["dome"] is False, "Kauffman Stadium (real open-air park) is not treated "
      "as a dome entry", f"got {entry}")

head("2. An Eastern-zone park is unaffected by the fix (the old bug's error was 0 "
     "hours there, by coincidence) -- confirms this isn't a regression for the "
     "roughly half the league where the bug never showed up.")

game_meta_et = [{
    "matchup": "Boston Red Sox @ New York Yankees",
    "venue": "Yankee Stadium",
    "hour": 19,
    "game_start_utc": "2026-06-15T23:05:00Z",  # 7:05pm ET
}]
fake_et_response = {
    "timezone": "America/New_York",
    "hourly": {
        "time": hourly_times,
        "temperature_2m": temps,
        "windspeed_10m": [5.0] * 24,
        "winddirection_10m": [180] * 24,
        "relativehumidity_2m": [50] * 24,
    },
}
with mock.patch.object(be.m, "retry_get", return_value=_FakeResp(fake_et_response)):
    out_et = be.park_weather_asof(game_meta_et, "2026-06-15", sleep=0)
entry_et = out_et.get("Boston Red Sox @ New York Yankees")
check(entry_et is not None and entry_et["temp"] == temps[19],
      "an Eastern-zone park reads its own real local hour (19) correctly, same "
      "result the old Eastern-hour index would have produced for this one zone",
      f"got {entry_et}")

head("3. KNOWN LIMITATION (documented, not fixed this pass -- release-candidate "
     "audit, 2026-08-26): a real retractable-roof park (Daikin Park, Houston) is "
     "collapsed into the SAME neutral 'dome' treatment (park_hr_index=50, temp=None) "
     "as a true fixed dome in the BACKTEST weather path, regardless of what the roof "
     "actually was for that specific historical game -- because STADIUMS' own `dome` "
     "flag is True for BOTH true fixed domes AND retractable-roof parks (the tuple's "
     "`retract` field is what distinguishes them), and park_weather_asof()'s only "
     "branch on it is `if dome: <neutral>`. The LIVE path's mlb_daily.real_roof_status() "
     "(5ad97da0) reads MLB's own real per-game weather.condition string to tell a "
     "genuinely-open retractable roof from a genuinely-closed one -- the backtest path "
     "was never wired to do the same. This is a real live/backtest INCONSISTENCY (the "
     "backtest UNDER-credits real outdoor conditions on days a retractable roof was "
     "actually open), not a leakage risk (it doesn't use any information from after the "
     "fact) -- documented here explicitly per the audit's own instruction not to pretend "
     "a historical replay reproduces a feature it was never wired to reproduce.")

dh_game_meta = [{
    "matchup": "Seattle Mariners @ Houston Astros",
    "venue": "Daikin Park",
    "hour": 20,
    "game_start_utc": "2026-06-15T00:10:00Z",
}]
with mock.patch.object(be.m, "retry_get", return_value=_FakeResp(fake_archive_response)):
    out_dh = be.park_weather_asof(dh_game_meta, "2026-06-15", sleep=0)
entry_dh = out_dh.get("Seattle Mariners @ Houston Astros")
check(entry_dh == {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None},
      "documents the CURRENT real behavior precisely -- a retractable-roof park always "
      "gets the neutral dome treatment in backtest, never the live path's honest "
      "per-game open/closed read. If this check ever fails, the gap it documents has "
      "been closed and this test (and the report note describing it) should be updated, "
      "not the assertion loosened", f"got {entry_dh}")

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
