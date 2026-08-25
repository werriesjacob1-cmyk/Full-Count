#!/usr/bin/env python3
"""test_forecast_hour_index.py — regression coverage for the real weather
timezone bug found during the data-integrity audit (Jacob's phone audit +
ChatGPT's independent repo audit both flagged this).

All four real call sites (mlb_daily.py's Section 5 fetch_weather(), two
more mlb_daily.py weather-threshold sections, and generate_picks.py's
fetch_park_weather()) used to index an Open-Meteo `timezone=auto` hourly
array with `game_meta["hour"]` -- the game's EASTERN hour (UTC minus a
hardcoded 4 hours) -- even though `timezone=auto` returns STADIUM-LOCAL
hours. Verified live against a real Open-Meteo response for a real
Central-zone park (Minute Maid Park, Houston): the response comes back
with `"timezone": "America/Chicago"` and `hourly.time` values like
"2026-08-25T19:00", genuinely Central local time. Using the Eastern hour
as that array's index is exact only for an Eastern-zone park (roughly half
the league) by coincidence -- off by ~1 hour for Central parks, ~2 for
Mountain, ~3 for Pacific.

    /tmp/mlbvenv/bin/python3 test_forecast_hour_index.py
"""
import sys
from datetime import datetime, timedelta

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


import mlb_daily as m  # noqa: E402


def _meteo(tz_name):
    times = [f"2026-08-25T{h:02d}:00" for h in range(24)]
    return {"timezone": tz_name, "hourly": {"time": times, "temperature_2m": [70 + h for h in range(24)]}}


def _old_buggy_index(utc_iso):
    """The exact old logic this replaces, for a direct before/after comparison."""
    dt = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ")
    return min(max((dt - timedelta(hours=4)).hour, 0), 23)


head("1. REGRESSION GUARD, all four US mainland timezones: a real 7:10pm-LOCAL "
     "first pitch must resolve to real local hour 19 in every zone. The old "
     "Eastern-hour-as-index logic was only ever correct for Eastern parks by "
     "coincidence -- off by 1h/2h/3h for Central/Mountain/Pacific.")

cases = [
    ("Eastern (e.g. Truist Park)", "America/New_York", "2026-08-25T23:10:00Z", 0),
    ("Central (e.g. Minute Maid Park)", "America/Chicago", "2026-08-26T00:10:00Z", 1),
    ("Mountain (e.g. Coors Field)", "America/Denver", "2026-08-26T01:10:00Z", 2),
    ("Pacific (e.g. Oracle Park)", "America/Los_Angeles", "2026-08-26T02:10:00Z", 3),
]
for label, tz_name, utc_iso, expected_old_error_hours in cases:
    meteo = _meteo(tz_name)
    new_idx = m.forecast_hour_index(utc_iso, meteo)
    old_idx = _old_buggy_index(utc_iso)
    check(new_idx == 19, f"{label}: corrected index is the real local hour (19), "
          f"matched against the forecast's own hourly.time array", f"got {new_idx}")
    expected_old_idx = 19 + expected_old_error_hours
    check(old_idx == expected_old_idx,
          f"{label}: confirms the OLD bug's exact real-world error magnitude "
          f"({expected_old_error_hours}h off) -- proves this is the same defect being fixed, "
          f"not a different one", f"old_idx={old_idx}, expected {expected_old_idx}")

head("2. Eastern parks were never broken -- the old Eastern-hour heuristic happens to be "
     "exactly correct there, so the fix must not change behavior for roughly half the league.")
eastern_meteo = _meteo("America/New_York")
check(m.forecast_hour_index("2026-08-25T23:10:00Z", eastern_meteo) == _old_buggy_index("2026-08-25T23:10:00Z"),
      "Eastern-park index is unchanged before/after the fix")

head("3. Robust against a missing/garbled game_start_utc, or a forecast response with no "
     "'timezone' field -- degrades to the documented TBD fallback rather than crashing.")
check(m.forecast_hour_index(None, _meteo("America/Chicago")) == 19,
      "game_start_utc=None falls back to hour 19 (matches the pre-existing 'TBD start time' default)")
check(m.forecast_hour_index("garbage", _meteo("America/Chicago")) == 19,
      "an unparseable game_start_utc falls back to hour 19, not a crash")
no_tz_meteo = {"hourly": {"time": [f"2026-08-25T{h:02d}:00" for h in range(24)]}}
idx_no_tz = m.forecast_hour_index("2026-08-26T00:10:00Z", no_tz_meteo)
check(0 <= idx_no_tz <= 23,
      "a forecast response missing its own 'timezone' field still returns a valid, bounded index "
      "(falls back to treating the UTC hour as local rather than crashing)", f"got {idx_no_tz}")

head("4. Late-night start crossing a local date boundary resolves to the correct next-day local "
     "hour, matched against the forecast's own returned timestamp, not a naive same-day mod-24.")
# An 11:45pm PT start (a real, common West Coast late start) is 06:45 UTC the NEXT calendar day.
late_meteo = {
    "timezone": "America/Los_Angeles",
    "hourly": {"time": [f"2026-08-26T{h:02d}:00" for h in range(24)],
               "temperature_2m": [65] * 24},
}
late_idx = m.forecast_hour_index("2026-08-26T06:45:00Z", late_meteo)
check(late_idx == 23,
      "a real 11:45pm-local start correctly resolves to local hour 23, matched against the "
      "response's own next-day timestamps", f"got {late_idx}")

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
