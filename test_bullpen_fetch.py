#!/usr/bin/env python3
"""test_bullpen_fetch.py — regression coverage for the real bullpen-fatigue
defect found during the 2026-08-2X data-integrity audit:
_bullpen_fetch_one() (mlb_daily.py, reused directly by generate_picks.py's
fetch_bullpen_scores()) was folding a team's OWN STARTING PITCHER into the
same `usage` dict downstream code and customer-facing text both call
"relievers" -- a starter commonly clears the 60-pitch fatigue threshold, so
this silently inflated both the "how many relievers do we have a read on"
denominator (`tracked`) and the "how many are gassed" numerator
(`fatigued`). bullpen_fatigue_pct (built from tracked/fatigued) feeds
directly into score_batter()'s `context` component at 30% weight, and
`context` itself is 64% of the real fitted batter formula -- not a
cosmetic-copy bug.

Also covers the paired "L7" window bug: schedule() was asked for up to 7
games but box scores were only ever fetched for the first 5
(`game_ids[:5]`), silently dropping up to 2 real recent games while still
being labeled "L7" everywhere downstream.

Mocked boundary, same philosophy as the rest of this suite:
statsapi.schedule()/statsapi.boxscore_data() are real MLB Stats API network
calls -- hand-built fixture data stands in for them here, shaped exactly
like the real box scores this fix was verified against live (see
mlb_daily.py's own comment for the real games checked).

    /tmp/mlbvenv/bin/python3 test_bullpen_fetch.py
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


import mlb_daily as m  # noqa: E402

TEAM_ID = 116  # Detroit Tigers, arbitrary real id


def _header_row():
    return {"namefield": "Tigers Pitchers", "ip": "IP", "h": "H", "r": "R", "er": "ER",
            "bb": "BB", "k": "K", "hr": "HR", "era": "ERA", "p": "P", "s": "S",
            "name": "Tigers Pitchers", "personId": 0, "note": ""}


def _pitcher_row(name, ip, pitches, note=""):
    return {"namefield": f"{name}  {note}".strip(), "ip": str(ip), "p": str(pitches),
            "name": name, "personId": hash(name) % 900000 + 100000, "note": note}


def _box_for(game_id, starter_pitches, reliever_rows, team_is_home=True):
    """reliever_rows: list of (name, pitches) tuples, real relievers only."""
    pitchers = [_header_row(), _pitcher_row("Starter One", 6.0, starter_pitches, "(W, 8-2)")]
    for name, pitches in reliever_rows:
        pitchers.append(_pitcher_row(name, 1.0, pitches))
    side_key = "homePitchers" if team_is_home else "awayPitchers"
    other_key = "awayPitchers" if team_is_home else "homePitchers"
    return {
        "away": {"team": {"id": 999 if team_is_home else TEAM_ID}},
        "home": {"team": {"id": TEAM_ID if team_is_home else 999}},
        side_key: pitchers,
        other_key: [_header_row()],
    }


head("1. REGRESSION GUARD: a game's own starter never lands in `usage`, the dict "
     "downstream code and customer text both call 'relievers' -- real bug, a "
     "starter who threw 95 pitches used to silently count as a fatigued reliever.")

games = [{"game_id": 1001, "game_date": "2026-08-19"}]
box = _box_for(1001, starter_pitches=95, reliever_rows=[("Rainey", 31), ("Holton", 15)])

with mock.patch.object(m.statsapi, "schedule", return_value=games), \
     mock.patch.object(m.statsapi, "boxscore_data", return_value=box):
    team_name, usage, err = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))

check(err is None, "no error on a well-formed fixture", f"got err={err}")
check("Starter One" not in usage,
      "REGRESSION GUARD: the starter (95 pitches, would trip the >60 fatigue threshold) "
      "must NOT appear in usage at all", f"got usage keys={list(usage.keys())}")
check("Rainey" in usage and "Holton" in usage,
      "the two real relievers are still tracked correctly", f"got {list(usage.keys())}")
check(usage["Rainey"]["pitches"] == 31 and usage["Holton"]["pitches"] == 15,
      "real relievers' pitch counts are untouched by the starter exclusion",
      f"got Rainey={usage['Rainey']} Holton={usage['Holton']}")

head("2. Downstream fatigue math (generate_picks.fetch_bullpen_scores' own shape) is "
     "honest once the starter is excluded: tracked=2 real relievers, fatigued=0 "
     "(neither cleared 60) -- the old bug would have reported tracked=3, fatigued=1 "
     "(the 95-pitch starter miscounted as a fatigued reliever).")

fatigued = sum(1 for u in usage.values() if u["pitches"] > 60)
tracked = len(usage)
check(tracked == 2, "tracked == 2 (only the real relievers)", f"got tracked={tracked}")
check(fatigued == 0, "fatigued == 0 (neither real reliever cleared 60 pitches)",
      f"got fatigued={fatigued}")

head("3. REGRESSION GUARD: the 'L7' window processes every game returned for the "
     "L7 date range, not just the first 5 -- real bug, game_ids[:5] silently "
     "dropped up to 2 real recent games while the feature stayed labeled 'L7'.")

six_games = [{"game_id": 2000 + i, "game_date": f"2026-08-{14+i}"} for i in range(6)]
boxes = {2000 + i: _box_for(2000 + i, starter_pitches=90,
                             reliever_rows=[(f"Reliever{i}", 20)]) for i in range(6)}

def _boxscore_side_effect(gid):
    return boxes[gid]

with mock.patch.object(m.statsapi, "schedule", return_value=six_games), \
     mock.patch.object(m.statsapi, "boxscore_data", side_effect=_boxscore_side_effect):
    _, usage6, err6 = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))

check(err6 is None, "no error across 6 games", f"got err={err6}")
check("Reliever5" in usage6,
      "REGRESSION GUARD: the 6th game's reliever is tracked -- the old [:5] cap "
      "would have silently dropped this real game", f"got usage keys={list(usage6.keys())}")
check(len(usage6) == 6, "all 6 distinct relievers across all 6 games are tracked",
      f"got {sorted(usage6.keys())}")

head("3b. POINT-IN-TIME AUDIT (2026-08-26): a team with MORE than 7 games in the "
     "L7_START..TODAY window (a real doubleheader inside the window makes this "
     "8+ games in an 8-calendar-day span) keeps the 7 MOST RECENT games, never "
     "the oldest 7 -- the old schedule[:7] (front slice of an ascending-order "
     "list) would have silently dropped the freshest, most fatigue-relevant "
     "game(s), backwards for a signal whose whole point is recency.")

eight_games = [{"game_id": 3000 + i, "game_date": f"2026-08-{14+i}",
                "game_datetime": f"2026-08-{14+i}T23:00:00Z", "game_num": 1}
               for i in range(8)]
boxes8 = {3000 + i: _box_for(3000 + i, starter_pitches=90,
                              reliever_rows=[(f"R{i}", 20)]) for i in range(8)}

with mock.patch.object(m.statsapi, "schedule", return_value=eight_games), \
     mock.patch.object(m.statsapi, "boxscore_data", side_effect=lambda gid: boxes8[gid]):
    _, usage8, err8 = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))

check(err8 is None, "no error across 8 games", f"got err={err8}")
check(len(usage8) == 7, "exactly 7 relievers tracked out of 8 real games (the cap), "
      "not all 8 and not fewer", f"got {sorted(usage8.keys())}")
check("R0" not in usage8,
      "REGRESSION GUARD: the OLDEST game (R0, 2026-08-14) is the one dropped, not a "
      "recent one", f"got {sorted(usage8.keys())}")
check("R7" in usage8,
      "REGRESSION GUARD: the NEWEST game (R7, 2026-08-21) is kept -- the old "
      "schedule[:7] front-slice would have dropped this exact game",
      f"got {sorted(usage8.keys())}")

head("3c. POINT-IN-TIME AUDIT (2026-08-26): correctness does not depend on the real "
     "API returning games in any particular order -- explicitly sorted by real game "
     "start time rather than trusting unstated statsapi.schedule() ordering. Same "
     "8-game fixture as 3b, but shuffled into a deliberately adversarial order "
     "(reverse-chronological) before being handed to _bullpen_fetch_one().")

shuffled = list(reversed(eight_games))
with mock.patch.object(m.statsapi, "schedule", return_value=shuffled), \
     mock.patch.object(m.statsapi, "boxscore_data", side_effect=lambda gid: boxes8[gid]):
    _, usage8_shuf, err8_shuf = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))

check(err8_shuf is None, "no error on a reverse-chronological API response",
      f"got err={err8_shuf}")
check(sorted(usage8_shuf.keys()) == sorted(usage8.keys()),
      "the SAME 7 most-recent relievers are kept regardless of the order the real "
      "API happens to return games in -- reverse-chronological input produces an "
      "identical result to chronological input",
      f"forward-order={sorted(usage8.keys())} reverse-order={sorted(usage8_shuf.keys())}")

head("3d. POINT-IN-TIME AUDIT (2026-08-26): a real doubleheader's Game 1 and Game 2 "
     "(same game_date, different game_datetime/game_num) are ordered chronologically "
     "by actual start time, not conflated or reversed -- a reliever who pitched in "
     "both games on the same day shows Game 2 as his real most-recent outing.")

dh_games = [
    {"game_id": 4001, "game_date": "2026-08-20", "game_datetime": "2026-08-20T18:00:00Z", "game_num": 1},
    {"game_id": 4002, "game_date": "2026-08-20", "game_datetime": "2026-08-20T22:00:00Z", "game_num": 2},
]
dh_boxes = {
    4001: _box_for(4001, starter_pitches=85, reliever_rows=[("DoubleDipper", 10)]),
    4002: _box_for(4002, starter_pitches=80, reliever_rows=[("DoubleDipper", 25)]),
}
with mock.patch.object(m.statsapi, "schedule", return_value=dh_games), \
     mock.patch.object(m.statsapi, "boxscore_data", side_effect=lambda gid: dh_boxes[gid]):
    _, usage_dh, err_dh = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))

check(err_dh is None, "no error on a real doubleheader fixture", f"got err={err_dh}")
check(usage_dh["DoubleDipper"]["apps"] == 2 and usage_dh["DoubleDipper"]["pitches"] == 35,
      "a reliever who pitched in both ends of a real doubleheader has both real "
      "appearances counted (10 + 25 = 35 pitches, 2 apps)", f"got {usage_dh['DoubleDipper']}")
check(usage_dh["DoubleDipper"]["games"][-1]["pitches"] == 25,
      "the LAST entry in his games list is Game 2 (25 pitches), the real chronologically "
      "later outing, not Game 1 -- proves doubleheader games are ordered by actual start "
      "time, not just calendar date", f"got {usage_dh['DoubleDipper']['games']}")

head("4. Per-appearance detail (date/IP/pitches) is preserved per reliever, not just "
     "the L7-aggregate totals -- needed for naming real relievers in customer copy "
     "instead of vague 'X of Y relievers used' language.")

check(usage["Rainey"]["games"] == [{"date": "2026-08-19", "ip": 1.0, "pitches": 31}],
      "a real reliever's single L7 appearance is recorded with its real date/IP/pitches",
      f"got {usage['Rainey']['games']}")

head("5. A team with zero recent games (all-star break edge case, or a brand-new "
     "season start) returns an empty-but-valid usage dict, not a crash.")

with mock.patch.object(m.statsapi, "schedule", return_value=[]), \
     mock.patch.object(m.statsapi, "boxscore_data"):
    _, usage_empty, err_empty = m._bullpen_fetch_one(("Detroit Tigers", TEAM_ID))
check(err_empty is None and usage_empty == {},
      "zero games in the window returns an empty dict, not an exception",
      f"got err={err_empty} usage={usage_empty}")

head("6. generate_picks._reliever_detail() (detailed-bullpen-presentation fix, "
     "2026-08-26): real bug -- generate_picks.fetch_bullpen_scores() discarded the "
     "same real per-reliever detail (name, real dated appearances) checks 1-4 above "
     "already prove _bullpen_fetch_one() fetches, collapsing it down to two bare "
     "counts. Direct instruction: 'Jacob specifically wants names and context... "
     "Cade Smith, 27 pitches yesterday, 3 appearances in 4 days.'")

import generate_picks as gp  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

today_str = datetime.now().strftime("%Y-%m-%d")
yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
detail_usage = {
    "Cade Smith": {"IP": 3.0, "apps": 3, "pitches": 60,
                   "games": [{"date": "2026-08-10", "ip": 1.0, "pitches": 18},
                             {"date": "2026-08-12", "ip": 1.0, "pitches": 15},
                             {"date": yesterday_str, "ip": 1.0, "pitches": 27}]},
    "Emmanuel Clase": {"IP": 1.0, "apps": 1, "pitches": 22,
                       "games": [{"date": today_str, "ip": 1.0, "pitches": 22}]},
    "No Games Reliever": {"IP": 0.0, "apps": 0, "pitches": 0, "games": []},
}
detail = gp._reliever_detail(detail_usage)
by_name = {r["name"]: r for r in detail}
check("No Games Reliever" not in by_name,
      "a reliever with no real recorded appearances in the window is never included "
      "-- no fact to report, so nothing is fabricated", f"got names={list(by_name)}")
check(by_name["Cade Smith"]["pitches_last_outing"] == 27,
      "pitches_last_outing is the REAL most recent game (27, from yesterday), not an "
      "earlier one in the window", f"got {by_name['Cade Smith']}")
check(by_name["Cade Smith"]["appearances_l7"] == 3,
      "appearances_l7 is his real total appearance count in the window",
      f"got {by_name['Cade Smith']}")
check(by_name["Cade Smith"]["days_since_last_outing"] == 1,
      "days_since_last_outing is computed from his real last-outing date",
      f"got {by_name['Cade Smith']}")
check(by_name["Emmanuel Clase"]["days_since_last_outing"] == 0,
      "a reliever who pitched today shows 0 days since his last outing, not None or a "
      "stale value", f"got {by_name['Emmanuel Clase']}")
names_by_recency = [r["name"] for r in detail]
check(names_by_recency[0] == "Emmanuel Clase" and names_by_recency[1] == "Cade Smith",
      "relievers are sorted most-recently-used first -- the read a bettor actually "
      "wants (who pitched last night), not alphabetical", f"got {names_by_recency}")
check(gp._reliever_detail({}) == [], "an empty usage dict returns an empty list, not a crash")
many_relievers = {f"Reliever{i}": {"IP": 1.0, "apps": 1, "pitches": 20,
                                    "games": [{"date": today_str, "ip": 1.0, "pitches": 20}]}
                  for i in range(12)}
check(len(gp._reliever_detail(many_relievers)) == 8,
      "a full bullpen's raw usage table is capped at 8 -- real names past that are noise, "
      "not context, on a per-game presentation surface")

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
