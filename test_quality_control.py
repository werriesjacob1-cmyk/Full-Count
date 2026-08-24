#!/usr/bin/env python3
"""test_quality_control.py — direct coverage for generate_picks.quality_
control(), the gate that decides which candidates are trustworthy enough
to reach the board at all. Had zero test coverage despite being one of
the two most central functions in the whole scoring engine (build_
candidates being the other) -- a bug here either lets an untrustworthy
pick onto the board (a real bet on bad information) or silently drops a
good one.

    /tmp/mlbvenv/bin/python3 test_quality_control.py
"""
import sys

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


import generate_picks as gp

GAME_PK = 900001

CONFIRMED_LINEUP = [{"id": i, "name": f"Batter {i}"} for i in range(1, 10)]
ASSUMED_LINEUP = [{"id": i, "name": f"Batter {i}", "assumed": True} for i in range(1, 10)]
INCOMPLETE_LINEUP = [{"id": i, "name": f"Batter {i}"} for i in range(1, 7)]  # only 6


def game_meta(away_lineup=None, home_lineup=None, away_team="Athletics", home_team="Astros"):
    return [{"game_pk": GAME_PK, "away_team": away_team, "home_team": home_team,
             "matchup": f"{away_team} @ {home_team}",
             "away_lineup": away_lineup if away_lineup is not None else CONFIRMED_LINEUP,
             "home_lineup": home_lineup if home_lineup is not None else CONFIRMED_LINEUP}]


def pitcher_pick(stat, player_id=501, **over):
    p = {"type": "pitcher", "player_id": player_id, "team": "Athletics",
         "game_pk": GAME_PK, "matchup": "Athletics @ Astros",
         "projection": {"stat": stat}}
    p.update(over)
    return p


def batter_pick(team="Athletics", **over):
    p = {"type": "batter", "player_id": 5, "team": team, "game_pk": GAME_PK,
         "matchup": "Athletics @ Astros", "projection": {"stat": "hits"}}
    p.update(over)
    return p


NO_RAIN = {"Athletics @ Astros": {"dome": False, "precip_prob": 10}}
HEAVY_RAIN = {"Athletics @ Astros": {"dome": False, "precip_prob": 85}}
DOME_HEAVY_RAIN = {"Athletics @ Astros": {"dome": True, "precip_prob": 85}}

REAL_STARTER = {"starts": 20, "avg_bf": 25.0}
OPENER = {"starts": 20, "avg_bf": 8.0}
THIN_SAMPLE = {"starts": 2, "avg_bf": 25.0}

head("1. strikeouts/pitcher_outs: opener detection")

for stat in ("strikeouts", "pitcher_outs"):
    kept, rejected, _ = gp.quality_control(
        [pitcher_pick(stat)], game_meta(), NO_RAIN, {501: REAL_STARTER})
    check(len(kept) == 1 and not rejected,
          f"{stat}: a real starter (25 BF/outing, 20 starts) is kept", f"got kept={kept} rej={rejected}")

    kept, rejected, _ = gp.quality_control(
        [pitcher_pick(stat)], game_meta(), NO_RAIN, {501: OPENER})
    check(len(rejected) == 1 and "opener" in rejected[0]["qc_reason"],
          f"{stat}: a real opener (8 BF/outing) is rejected with an opener-specific reason",
          f"got {rejected}")

    kept, rejected, _ = gp.quality_control(
        [pitcher_pick(stat)], game_meta(), NO_RAIN, {501: THIN_SAMPLE})
    check(len(rejected) == 1 and "start(s)" in rejected[0]["qc_reason"],
          f"{stat}: only 2 starts of evidence is rejected for thin sample, not an opener reason",
          f"got {rejected}")

head("2. combined_strikeouts: either starter being an opener rejects the whole combo")

kept, rejected, _ = gp.quality_control(
    [pitcher_pick("combined_strikeouts", combo_player_ids=[501, 502])],
    game_meta(), NO_RAIN, {501: REAL_STARTER, 502: REAL_STARTER})
check(len(kept) == 1, "combined_strikeouts: both real starters -> kept")

kept, rejected, _ = gp.quality_control(
    [pitcher_pick("combined_strikeouts", combo_player_ids=[501, 502])],
    game_meta(), NO_RAIN, {501: REAL_STARTER, 502: OPENER})
check(len(rejected) == 1 and "one of the two starters" in rejected[0]["qc_reason"],
      "combined_strikeouts: ONE starter is an opener -> the whole combo is rejected",
      f"got {rejected}")

head("3. batter lineup confirmation states: confirmed / missing / assumed")

kept, rejected, assumed = gp.quality_control(
    [batter_pick()], game_meta(away_lineup=CONFIRMED_LINEUP), NO_RAIN, {})
check(len(kept) == 1, "a batter in a real, posted 9-man lineup is kept")

kept, rejected, assumed = gp.quality_control(
    [batter_pick()], game_meta(away_lineup=INCOMPLETE_LINEUP), NO_RAIN, {})
check(len(rejected) == 1 and "not confirmed" in rejected[0]["qc_reason"],
      "a batter whose team's lineup has fewer than 9 posted hitters is rejected, "
      "not treated as a real miss", f"got {rejected}")

kept, rejected, assumed = gp.quality_control(
    [batter_pick()], game_meta(away_lineup=ASSUMED_LINEUP), NO_RAIN, {})
check(not kept and not rejected and len(assumed) == 1,
      "a batter in an ASSUMED (last-known, not yet posted) lineup goes to the "
      "early-look list -- neither a kept pick nor a rejected one", f"got kept={kept} rej={rejected} assumed={assumed}")
check(assumed[0].get("lineup_assumed") is True,
      "an assumed-lineup candidate is tagged lineup_assumed=True for downstream consumers")

head("4. side resolution: away batter checked against away_lineup, home against home_lineup")

kept, rejected, _ = gp.quality_control(
    [batter_pick(team="Athletics")],
    game_meta(away_lineup=INCOMPLETE_LINEUP, home_lineup=CONFIRMED_LINEUP), NO_RAIN, {})
check(len(rejected) == 1,
      "an Athletics (away team) batter is checked against the AWAY lineup, which is incomplete here")

kept, rejected, _ = gp.quality_control(
    [batter_pick(team="Astros")],
    game_meta(away_lineup=INCOMPLETE_LINEUP, home_lineup=CONFIRMED_LINEUP), NO_RAIN, {})
check(len(kept) == 1,
      "an Astros (home team) batter is checked against the HOME lineup, which is complete here, "
      "unaffected by the away lineup's own state")

head("5. rain risk rejection, and the dome exemption")

kept, rejected, _ = gp.quality_control([batter_pick()], game_meta(), HEAVY_RAIN, {})
check(len(rejected) == 1 and "rain risk" in rejected[0]["qc_reason"],
      "a real 85% precip risk in an outdoor park rejects the pick", f"got {rejected}")

kept, rejected, _ = gp.quality_control([batter_pick()], game_meta(), DOME_HEAVY_RAIN, {})
check(len(kept) == 1,
      "the SAME 85% precip reading in a DOME park does not reject -- weather cannot reach the game")

head("6. check ordering: a missing lineup is reported before rain is ever checked")

kept, rejected, _ = gp.quality_control(
    [batter_pick()], game_meta(away_lineup=INCOMPLETE_LINEUP), HEAVY_RAIN, {})
check(len(rejected) == 1 and "not confirmed" in rejected[0]["qc_reason"],
      "when BOTH the lineup is unconfirmed AND rain risk is real, the lineup reason wins "
      "(reported first) rather than a double-rejection or the wrong reason surfacing",
      f"got {rejected}")

head("7. an empty candidate list and an empty game_meta don't raise")

kept, rejected, assumed = gp.quality_control([], game_meta(), NO_RAIN, {})
check(kept == [] and rejected == [] and assumed == [], "an empty candidate list returns three empty lists")

kept, rejected, assumed = gp.quality_control([batter_pick()], [], NO_RAIN, {})
check(len(rejected) == 1 and "not confirmed" in rejected[0]["qc_reason"],
      "a candidate whose game isn't in game_meta at all is treated as an unconfirmed lineup, "
      "not a crash", f"got {rejected}")

head("8. 2026-08-24 accuracy investigation, real live finding: pitcher candidates are NEVER "
     "put through the batting-lineup confirmation check at all -- lineup_assumed stays unset "
     "for a pitcher regardless of whether either team's batting lineup is confirmed, missing, "
     "or a guessed fallback. Verified deliberate and correct, not an oversight: a strikeout/"
     "outs prop needs a REAL, named starting pitcher (never 'TBD' -- see generate_picks.py's "
     "own gm['away_sp'] != 'TBD' guard before a pitcher candidate is ever built at all, which "
     "MLB typically confirms 1-5 days out) and the OPPOSING team's season-long aggregate rate, "
     "neither of which depends on the exact 1-9 batting order the way a batter's own PA-count "
     "projection does. This test locks that real, already-correct behavior in so a future "
     "'tighten the lineup gate' change doesn't accidentally start blocking legitimate early-day "
     "pitcher Top Picks that were never lineup-dependent to begin with.")

for lineup_state_label, away_lu, home_lu in [
    ("both lineups fully confirmed", CONFIRMED_LINEUP, CONFIRMED_LINEUP),
    ("both lineups entirely missing", [], []),
    ("away lineup a guessed fallback", ASSUMED_LINEUP, CONFIRMED_LINEUP),
]:
    kept, rejected, assumed = gp.quality_control(
        [pitcher_pick("strikeouts", player_id=501)],
        game_meta(away_lineup=away_lu, home_lineup=home_lu), NO_RAIN, {501: REAL_STARTER})
    check(len(kept) == 1 and not rejected and not assumed,
          f"pitcher candidate is kept as a normal, non-assumed pick when {lineup_state_label} "
          "-- the batting-lineup state never touches a pitcher candidate at all",
          f"got kept={kept} rejected={rejected} assumed={assumed}")
    if kept:
        check(not kept[0].get("lineup_assumed"),
              f"lineup_assumed is falsy on the pitcher candidate when {lineup_state_label}",
              f"got lineup_assumed={kept[0].get('lineup_assumed')!r}")

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
