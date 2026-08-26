#!/usr/bin/env python3
"""test_real_roof_status.py — regression coverage for the 2026-08-2X
all-30-park roof-model fix (data-integrity audit).

Two real bugs fixed together:
  1. Truist Park (Atlanta) was wrongly classified dome=True/retract=True
     in mlb_daily.STADIUMS. It is a fully open-air ballpark with NO roof
     of any kind. Confirmed live against the MLB Stats API's own per-game
     weather field on 2026-08-25: {"condition": "Clear", "temp": "89",
     "wind": "3 mph, Out To CF"} -- a real outdoor reading, not a dome.
  2. Every retractable-roof park (Rogers Centre, Globe Life Field, Daikin
     Park, T-Mobile Park, loanDepot park, American Family Field, Chase
     Field) was force-treated as PERMANENTLY closed on every game,
     regardless of real per-game roof state -- and Rogers Centre's own
     retractable_roof flag was additionally wrong (False, should be True).
     mlb_daily.real_roof_status() reads the MLB Stats API's own real
     weather.condition string per game instead of assuming.

    /tmp/mlbvenv/bin/python3 test_real_roof_status.py
"""
import sys
from unittest.mock import patch, MagicMock

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
import generate_picks as gp  # noqa: E402

head("1. real_roof_status(): a non-retractable park (retract=False) is N/A -- "
     "not this function's concern (a fixed dome or open-air park is fully "
     "determined by the dome flag alone, no per-game check needed)")

check(m.real_roof_status("Roof Closed", False) is None, "retract=False always returns None")
check(m.real_roof_status(None, False) is None, "retract=False returns None even with no condition data")

head("2. real_roof_status(): a retractable park with no real condition data yet "
     "(MLB hasn't populated weather.condition -- common well before first pitch) "
     "is UNKNOWN, not silently CLOSED")

check(m.real_roof_status(None, True) == "unknown", "condition=None -> 'unknown'")
check(m.real_roof_status("", True) == "unknown", "condition='' -> 'unknown'")
check(m.real_roof_status("   ", True) == "unknown", "condition=whitespace-only -> 'unknown'")

head("3. real_roof_status(): a real 'Roof Closed' reading is CLOSED")

check(m.real_roof_status("Roof Closed", True) == "closed",
      "real MLB 'Roof Closed' condition (loanDepot park, 2026-08-25 live slate) -> 'closed'")
check(m.real_roof_status("roof closed", True) == "closed", "case-insensitive")

head("4. real_roof_status(): a real outdoor-weather reading is OPEN")

check(m.real_roof_status("Partly Cloudy", True) == "open",
      "real MLB 'Partly Cloudy' condition (Rogers Centre, 2026-08-25 live slate) -> 'open'")
check(m.real_roof_status("Clear", True) == "open", "'Clear' -> 'open'")
check(m.real_roof_status("Sunny", True) == "open", "'Sunny' -> 'open'")

head("5. mlb_daily.STADIUMS table corrections")

truist = m.STADIUMS["Truist Park"]
check(truist[2] is False, "Truist Park is no longer classified as a dome (index 2, is_dome)")
check(truist[16] is False, "Truist Park's retractable_roof flag is also False -- it has no roof at all")

rogers = m.STADIUMS["Rogers Centre"]
check(rogers[2] is True, "Rogers Centre stays classified as having a roof (it does -- can close)")
check(rogers[16] is True, "Rogers Centre's retractable_roof flag is now True -- it was the first "
      "retractable-roof stadium in pro sports (1989) and was wrongly marked non-retractable")

tropicana = m.STADIUMS["Tropicana Field"]
check(tropicana[2] is True and tropicana[16] is False,
      "Tropicana Field is untouched -- a genuine fixed dome with no way to open at all")


head("5b. mlb_daily.STADIUMS: full all-30-park roof/dome/humidor ground-truth audit "
     "(2026-08-26 P0 ledger reconciliation) -- the original audit found and fixed 2 real "
     "errors (Truist Park, Rogers Centre) via live-data spot verification, but nothing "
     "durably locked in the correctness of the other 28 parks' dome/retractable/humidor "
     "facts -- a future accidental edit to this table would go uncaught. This is the missing "
     "regression guard: real-world ground truth for is_dome/retractable_roof/humidor for "
     "every one of the 30 real MLB teams, cross-checked against STADIUMS by team abbreviation "
     "(not dict key, since venue names change -- e.g. Minute Maid Park -> Daikin Park -- but "
     "team abbreviations are the stable join key). is_dome here means 'has a roof that can be "
     "closed, fixed or retractable' per this file's own STADIUMS comment header and "
     "fetch_weather()'s actual usage (dome=True + retract=False => permanently closed; "
     "dome=True + retract=True => real per-game roof state; dome=False => never has a roof "
     "at all, real outdoor weather always applies).")

# (team_abbr, is_dome, retractable_roof, humidor) -- the 30 real facts as of the
# 2026 season. Fixed/permanent domes: Tropicana Field (TB) only. Retractable-roof
# parks: Rogers Centre (TOR), Globe Life Field (TEX), Daikin Park (HOU), T-Mobile
# Park (SEA), loanDepot park (MIA), American Family Field (MIL), Chase Field
# (ARI). Every other park is fully open-air (is_dome=False). Publicly known
# humidor parks: Coors Field (COL, since 2002), Chase Field (ARI, since 2018),
# Globe Life Field (TEX, since it opened in 2020).
ROOF_GROUND_TRUTH = {
    "NYY": (False, False, False), "BOS": (False, False, False), "BAL": (False, False, False),
    "TOR": (True, True, False), "TB": (True, False, False), "CWS": (False, False, False),
    "CHC": (False, False, False), "CIN": (False, False, False), "CLE": (False, False, False),
    "KC": (False, False, False), "MIN": (False, False, False), "WSH": (False, False, False),
    "TEX": (True, True, True), "HOU": (True, True, False), "LAA": (False, False, False),
    "ATH": (False, False, False), "LAD": (False, False, False), "SD": (False, False, False),
    "SF": (False, False, False), "SEA": (True, True, False), "MIA": (True, True, False),
    "ATL": (False, False, False), "MIL": (True, True, False), "STL": (False, False, False),
    "COL": (False, False, True), "ARI": (True, True, True), "DET": (False, False, False),
    "PHI": (False, False, False), "PIT": (False, False, False), "NYM": (False, False, False),
}
check(len(ROOF_GROUND_TRUTH) == 30, "ground truth itself covers exactly the 30 real MLB teams, "
      "not a stale/incomplete list", f"got {len(ROOF_GROUND_TRUTH)}")

by_team = {row[3]: (name, row) for name, row in m.STADIUMS.items()}
check(len(by_team) == 30, "STADIUMS has exactly 30 entries -- no team missing, none duplicated "
      "under two different venue-name keys", f"got {len(by_team)}: {sorted(by_team)}")
check(set(by_team) == set(ROOF_GROUND_TRUTH),
      "STADIUMS' real team abbreviations exactly match the ground-truth set -- no team "
      "missing from either side", f"STADIUMS only: {set(by_team) - set(ROOF_GROUND_TRUTH)}, "
      f"ground-truth only: {set(ROOF_GROUND_TRUTH) - set(by_team)}")

for team, (want_dome, want_retract, want_humidor) in ROOF_GROUND_TRUTH.items():
    if team not in by_team:
        continue  # already flagged by the set-equality check above
    venue, row = by_team[team]
    got_dome, got_humidor, got_retract = row[2], row[14], row[16]
    check(got_dome is want_dome,
          f"{team} ({venue}): is_dome should be {want_dome}", f"got {got_dome}")
    check(got_retract is want_retract,
          f"{team} ({venue}): retractable_roof should be {want_retract}", f"got {got_retract}")
    check(got_humidor is want_humidor,
          f"{team} ({venue}): humidor should be {want_humidor}", f"got {got_humidor}")
    # A fixed dome with a real per-game "retractable" read makes no physical
    # sense (nothing to retract), and a park marked as having no roof at all
    # can't simultaneously be retractable -- catches a future edit that sets
    # these two flags into a combination fetch_weather()/real_roof_status()
    # was never designed to handle, independent of whether it happens to
    # match either ground-truth row above.
    check(not (got_dome is False and got_retract is True),
          f"{team} ({venue}): retractable_roof=True with is_dome=False is a self-contradiction "
          "-- a park with no roof at all cannot have a retractable one")


def _fake_meteo(condition_desc, wind_deg=180, wind_mph=12.0):
    return {"timezone": "America/New_York", "hourly": {
        "time": ["2026-08-25T19:00"],
        "temperature_2m": [78.0], "windspeed_10m": [wind_mph],
        "winddirection_10m": [wind_deg], "relativehumidity_2m": [50.0],
        "precipitation_probability": [10]}}


def _mock_retry_get(url, **kw):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    if "open-meteo" in url:
        resp.json = lambda: _fake_meteo("n/a")
    else:
        raise Exception("NWS unreachable in test")
    return resp


head("6. fetch_park_weather(): Truist Park (now dome=False) fetches real weather "
     "instead of the old fabricated neutral 50")

gm_truist = {"matchup": "Test @ Atlanta Braves", "venue": "Truist Park",
             "game_start_utc": "2026-08-25T23:20:00Z", "hour": 19,
             "mlb_weather_condition": "Clear"}
with patch.object(gp.m, "retry_get", side_effect=_mock_retry_get), \
     patch.object(gp, "fetch_nws_weather", return_value=None):
    out = gp.fetch_park_weather([gm_truist])
c = out["Test @ Atlanta Braves"]
check(c["dome"] is False, "Truist Park no longer forced to dome=True")
check(c["park_hr_index"] != 50 or c.get("wind_mph") is not None,
      "Truist Park gets a real weather-derived read, not the old fabricated neutral-50 short-circuit",
      f"got {c}")

head("7. fetch_park_weather(): a retractable park confirmed CLOSED today still gets "
     "the honest neutral/indoor treatment (this is real, not fabricated)")

gm_closed = {"matchup": "Test @ Miami Marlins", "venue": "loanDepot park",
             "game_start_utc": "2026-08-25T23:20:00Z", "hour": 19,
             "mlb_weather_condition": "Roof Closed"}
out = gp.fetch_park_weather([gm_closed])
c = out["Test @ Miami Marlins"]
check(c["dome"] is True and c["park_hr_index"] == 50 and c.get("roof_status") == "closed",
      "a real confirmed-closed roof correctly stays neutral/indoor", f"got {c}")

head("8. fetch_park_weather(): a retractable park confirmed OPEN today fetches real "
     "weather instead of being force-treated as a permanently-closed dome -- the "
     "core bug (real complaint pattern: Rogers Centre reporting real outdoor wind)")

gm_open = {"matchup": "Test @ Toronto Blue Jays", "venue": "Rogers Centre",
           "game_start_utc": "2026-08-25T23:20:00Z", "hour": 19,
           "mlb_weather_condition": "Partly Cloudy"}
with patch.object(gp.m, "retry_get", side_effect=_mock_retry_get), \
     patch.object(gp, "fetch_nws_weather", return_value=None):
    out = gp.fetch_park_weather([gm_open])
c = out["Test @ Toronto Blue Jays"]
check(c["dome"] is False, "REGRESSION GUARD: a confirmed-open retractable roof must NOT be forced to dome=True",
      f"got {c}")
check(c.get("roof_status") == "open", "roof_status is honestly recorded as 'open'", f"got {c}")

head("9. fetch_park_weather(): a retractable park with UNKNOWN status (MLB hasn't "
     "posted weather.condition yet) fetches real weather as a best estimate AND "
     "flags the uncertainty -- never silently treated as confirmed-closed")

gm_unknown = {"matchup": "Test @ Arizona Diamondbacks", "venue": "Chase Field",
              "game_start_utc": "2026-08-25T23:20:00Z", "hour": 19,
              "mlb_weather_condition": None}
with patch.object(gp.m, "retry_get", side_effect=_mock_retry_get), \
     patch.object(gp, "fetch_nws_weather", return_value=None):
    out = gp.fetch_park_weather([gm_unknown])
c = out["Test @ Arizona Diamondbacks"]
check(c["dome"] is False,
      "REGRESSION GUARD: unknown roof status must NOT silently default to dome=True/indoor-neutral",
      f"got {c}")
check(c.get("roof_status") == "unknown", "the uncertainty is honestly recorded, not hidden", f"got {c}")

head("10. score_batter(): the roof_status=='unknown' case is surfaced to the user as "
     "an honest watchout, not silently baked into the weather note")

batter = {"name": "Test Batter", "id": 1, "team": "Away", "bats": "R", "order": 3}
gm2 = {"matchup": "Away @ Home", "away_team": "Away", "home_team": "Home", "game_pk": 1, "series_game": 1}
park_wx_unknown = {"dome": False, "park_hr_index": 55, "wind_effect": "out", "wind_mph": 10,
                    "roof_status": "unknown"}
c10 = gp.score_batter(batter, gm2, {"ERA": 4.25}, None, "R", park_wx_unknown,
                       {"wRC+": 100, "ISO": 0.16, "Barrel%": 8},
                       {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20}, {}, {}, {}, extras={})
check(any("roof status" in w.lower() and "wasn't confirmed" in w for w in c10["watchouts"]),
      "an unconfirmed retractable-roof read gets an honest watchout", f"got {c10['watchouts']}")

park_wx_confirmed = {"dome": False, "park_hr_index": 55, "wind_effect": "out", "wind_mph": 10,
                      "roof_status": "open"}
c10b = gp.score_batter(batter, gm2, {"ERA": 4.25}, None, "R", park_wx_confirmed,
                        {"wRC+": 100, "ISO": 0.16, "Barrel%": 8},
                        {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20}, {}, {}, {}, extras={})
check(not any("wasn't confirmed" in w for w in c10b["watchouts"]),
      "a confirmed-open roof read does not get the uncertainty watchout", f"got {c10b['watchouts']}")

head("11. SEMANTIC AUDIT (release-candidate review, 2026-08-26): does a non-'closed' "
     "MLB condition string actually prove the roof is open, or could it coexist with "
     "a closed roof? Locks in the real cross-check: 11 real retractable-roof-park "
     "games across 4 real dates/7 real parks, each verified live -- every 'Roof "
     "Closed' reading paired with real 0mph/None wind (physically correct, no "
     "outdoor sensor reading indoors); every non-closed reading paired with real, "
     "non-zero, directional wind (physically incoherent for an actually-closed "
     "dome). Re-encoded here as a real fixed dataset so this finding is re-checkable, "
     "not just narrated in a report.")

_real_roof_wind_observations = [
    # (park, date, condition, wind_is_zero) -- exact values verified live
    # against the real MLB Stats API schedule endpoint (hydrate=weather).
    ("Rogers Centre", "2026-08-10", "Cloudy", False),          # 17 mph, Out To RF
    ("Chase Field", "2026-08-10", "Roof Closed", True),        # 0 mph, None
    ("Rogers Centre", "2026-08-15", "Sunny", False),           # 14 mph, R To L
    ("Daikin Park", "2026-08-15", "Roof Closed", True),        # 0 mph, None
    ("American Family Field", "2026-08-20", "Sunny", False),   # 7 mph, In From CF
    ("Globe Life Field", "2026-08-20", "Roof Closed", True),   # 0 mph, None
    ("Daikin Park", "2026-08-20", "Roof Closed", True),        # 0 mph, None
    ("loanDepot park", "2026-08-25", "Roof Closed", True),     # 0 mph, None
    ("Rogers Centre", "2026-08-25", "Clear", False),           # 3 mph, R To L
    ("Chase Field", "2026-08-25", "Roof Closed", True),        # 0 mph, None
    ("T-Mobile Park", "2026-08-25", "Clear", False),           # 8 mph, Out To CF
]
mismatches = []
for park, date, cond, wind_is_zero in _real_roof_wind_observations:
    status = m.real_roof_status(cond, retract=True)
    # The classification (open/closed) must agree with whether real wind was
    # observed -- a closed classification with real nonzero wind, or an open
    # classification with zero wind, would be the exact "generic weather
    # field disconnected from roof state" failure this audit checked for.
    if status == "closed" and not wind_is_zero:
        mismatches.append((park, date, cond, "classified closed but real wind was nonzero"))
    if status == "open" and wind_is_zero:
        mismatches.append((park, date, cond, "classified open but real wind was zero"))
check(len(mismatches) == 0,
      "REGRESSION GUARD: real_roof_status()'s open/closed classification agrees with "
      "the real wind-sensor reading for all 11 real observations -- zero mismatches",
      f"mismatches: {mismatches}")
check(sum(1 for _, _, c, z in _real_roof_wind_observations if not z) == 5,
      "sanity check on the fixture itself: 5 of 11 real observations are real "
      "non-closed (open) reads with real nonzero wind")

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
