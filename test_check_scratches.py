#!/usr/bin/env python3
"""test_check_scratches.py — direct coverage for check_scratches.check(),
the function that tells Jacob "DO NOT BET" on a pick whose player has been
scratched since the board was made. Had zero test coverage despite being a
real, actionable safety check (its own module docstring: "the difference
between a bet that loses and a bet that should never have been placed").

    /tmp/mlbvenv/bin/python3 test_check_scratches.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check_(cond, msg, detail=""):
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


import check_scratches as cs

GAME_PK = 700001


def roster(away_ids=None, home_ids=None, away_posted=True, home_posted=True,
          away_sp_id=501, home_sp_id=502, away_team="Athletics", home_team="Astros"):
    return {GAME_PK: {
        "away_ids": set(away_ids or range(1, 10)),
        "home_ids": set(home_ids or range(101, 110)),
        "away_posted": away_posted, "home_posted": home_posted,
        "away_sp_id": away_sp_id, "home_sp_id": home_sp_id,
        "away_team": away_team, "home_team": home_team,
        "status": "Scheduled",
    }}


def batter_pick(player_id, team="Athletics", **over):
    p = {"rank": 1, "name": "Test Batter", "prop": "Over 1.5 Hits", "team": team,
         "matchup": "Athletics @ Astros", "game_pk": GAME_PK, "player_id": player_id,
         "type": "batter", "projection": {"stat": "hits"}}
    p.update(over)
    return p


head("1. batter picks against a posted lineup")

r = cs.check([batter_pick(5)], roster())  # 5 is in away_ids range(1,10)
check_(r[0]["state"] == "ok", "a batter present in his team's posted lineup is 'ok'", f"got {r[0]}")

r = cs.check([batter_pick(99)], roster())  # 99 is not in away_ids
check_(r[0]["state"] == "scratched" and "not in it" in r[0]["note"],
      "a batter absent from a POSTED, COMPLETE lineup is 'scratched'", f"got {r[0]}")

r = cs.check([batter_pick(5)], roster(away_posted=False))
check_(r[0]["state"] == "unknown" and "not posted yet" in r[0]["note"],
      "an incomplete/unposted lineup reports 'unknown', not a false scratch", f"got {r[0]}")

head("2. side resolution: team vs away_team/home_team")

r = cs.check([batter_pick(105, team="Astros")], roster())  # 105 is in home_ids range(101,110)
check_(r[0]["state"] == "ok", "a batter on the HOME team is checked against home_ids, not away_ids",
      f"got {r[0]}")

head("3. pitchers -- checked against the probable starter, not a batting order")

r = cs.check([batter_pick(501, team="Athletics", type="pitcher")], roster())
check_(r[0]["state"] == "ok", "a pitcher who is still the listed probable starter is 'ok'", f"got {r[0]}")

r = cs.check([batter_pick(999, team="Athletics", type="pitcher")], roster())
check_(r[0]["state"] == "scratched" and "no longer the listed probable" in r[0]["note"],
      "a pitcher who is no longer the probable starter is 'scratched'", f"got {r[0]}")

r = cs.check([batter_pick(501, team="Athletics", type="pitcher")], roster(away_sp_id=None))
check_(r[0]["state"] == "unknown",
      "no probable starter listed at all for this side -> 'unknown', not a false scratch")

# A "batter"-typed dict whose STAT says strikeouts/first_inning_run must
# STILL route to the pitcher check (these are pitcher props scored under
# type=="batter" in some paths) -- real scenario this project has hit before.
r = cs.check([batter_pick(501, team="Athletics", type="batter",
                          projection={"stat": "strikeouts"})], roster())
check_(r[0]["state"] == "ok",
      "a strikeouts-stat pick routes to the pitcher check even if type isn't literally 'pitcher'")
r = cs.check([batter_pick(501, team="Athletics", type="batter",
                          projection={"stat": "first_inning_run"})], roster())
check_(r[0]["state"] == "ok",
      "a first_inning_run-stat pick also routes to the pitcher check")

head("4. pitcher_combo -- BOTH starters must still be listed")

def combo_pick(ids, **over):
    p = {"rank": 1, "name": "Combined K", "prop": "Combined Strikeouts",
         "team": None, "matchup": "Athletics @ Astros", "game_pk": GAME_PK,
         "player_id": ids[0] if ids else None, "type": "pitcher_combo",
         "combo_player_ids": ids}
    p.update(over)
    return p

r = cs.check([combo_pick([501, 502])], roster())
check_(r[0]["state"] == "ok", "pitcher_combo: both real starters still listed -> ok", f"got {r[0]}")

r = cs.check([combo_pick([501, 999])], roster())
check_(r[0]["state"] == "scratched" and "no longer the listed probables" in r[0]["note"],
      "pitcher_combo: ONE starter changed -> scratched (the whole combo is invalid)", f"got {r[0]}")

r = cs.check([combo_pick([501, 502])], roster(away_sp_id=None))
check_(r[0]["state"] == "unknown", "pitcher_combo: no probables listed at all -> unknown")

r = cs.check([combo_pick([])], roster())
check_(r[0]["state"] == "unknown", "pitcher_combo: empty combo_player_ids -> unknown, not a crash")

head("5. game or player not found in the current schedule at all")

r = cs.check([batter_pick(5, **{"game_pk": 999999})], {999999 - 1: {}})  # deliberately absent key
check_(r[0]["state"] == "unknown" and "not found" in r[0]["note"],
      "a game_pk absent from the current schedule (postponed/moved) -> unknown", f"got {r[0]}")

r = cs.check([batter_pick(None)], roster())
check_(r[0]["state"] == "unknown", "a pick with no player_id at all -> unknown, not a crash")

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
