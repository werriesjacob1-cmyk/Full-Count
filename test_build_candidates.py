#!/usr/bin/env python3
"""test_build_candidates.py — coverage for generate_picks.build_candidates(),
the orchestrator score_slate()/main()/backtest.engine all funnel through.
Had zero direct test coverage. score_batter()/score_pitcher() (the per-
player scorers it calls in a loop) already have their own dedicated tests;
this checks the logic that is UNIQUE to build_candidates() itself: catcher/
framing resolution, the lineup_woba wrap asymmetry (a real, subtle, already-
documented rule -- "ahead" does not wrap innings, "behind" does), TBD-
starter skipping, and the final watchout post-processing pass.

    /tmp/mlbvenv/bin/python3 test_build_candidates.py
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

# A real 4-batter lineup (small on purpose, to make the wOBA wrap check
# hand-verifiable) with a catcher at slot 4, plus two real starters.
LINEUP = [
    {"name": "Leadoff", "id": 1, "bats": "R", "order": 1},
    {"name": "Two Hole", "id": 2, "bats": "L", "order": 2},
    {"name": "Cleanup", "id": 3, "bats": "R", "order": 3},
    {"name": "Catcher", "id": 4, "bats": "L", "order": 4, "pos": "C"},
]

BATTER_LOOKUP = {
    1: {"wOBA": 0.300}, 2: {"wOBA": 0.320}, 3: {"wOBA": 0.400}, 4: {"wOBA": 0.310},
}

GM = {"matchup": "Athletics @ Astros", "away_team": "Athletics", "home_team": "Astros",
      "game_pk": 900001, "series_game": 1, "venue": "Minute Maid Park",
      "away_sp": "JP Sears", "away_sp_id": 501, "away_sp_hand": "L",
      "home_sp": "Framber Valdez", "home_sp_id": 502, "home_sp_hand": "L",
      "away_lineup": LINEUP, "home_lineup": LINEUP}

REQUIRED_KWARGS = dict(
    extras=None, batter_lookup=BATTER_LOOKUP, pitcher_lookup={}, team_k_lookup={},
    park_wx={}, ump_scores={}, bullpen_scores={}, bullpen_quality={},
    sharp_bias={}, l7_form={}, bat_speed_trend={}, batter_arsenal={},
    pitcher_arsenal={}, sprint_speed={}, catcher_poptime={},
    l14_pitcher_form={}, fi_form={},
)


def build(game_meta, **over):
    kw = dict(REQUIRED_KWARGS)
    kw.update(over)
    return gp.build_candidates(game_meta, **kw)


head("1. a normal slate produces real candidates without crashing")

cands = build([GM])
check(len(cands) > 0, "a real 4-batter, 2-starter game produces at least one candidate",
      f"got {len(cands)}")
check(all("type" in c and c["type"] in ("batter", "pitcher") for c in cands),
      "every candidate is typed batter or pitcher")

head("2. lineup_woba: 'ahead' does NOT wrap, 'behind' DOES wrap (documented asymmetry)")

extras_used = {}
orig_score_batter = gp.score_batter
def _capture(batter, gm, *a, **kw):
    extras_used[batter["id"]] = kw.get("extras")
    return orig_score_batter(batter, gm, *a, **kw)
gp.score_batter = _capture
try:
    build([GM])
finally:
    gp.score_batter = orig_score_batter

lw1 = extras_used[1]["lineup_woba"][1]   # leadoff (slot 1, i=0): no one batted before him this inning
lw4 = extras_used[4]["lineup_woba"][4]   # catcher (slot 4, i=3, last slot): no one bats after in this lineup length

check(lw1["woba_ahead"] is None,
      "the LEADOFF batter has no 'ahead' wOBA -- prev slot would need to wrap, and ahead "
      "deliberately does not wrap (three outs reset the bases between innings)",
      f"got {lw1}")
check(lw4["woba_behind"] == BATTER_LOOKUP[1]["wOBA"],
      "the LAST lineup slot's 'behind' wOBA DOES wrap to the leadoff batter's wOBA "
      "(a pitcher pitching around the last slot genuinely faces the leadoff man next)",
      f"got {lw4}, want {BATTER_LOOKUP[1]['wOBA']}")
check(extras_used[3]["lineup_woba"][3]["woba_ahead"] == BATTER_LOOKUP[2]["wOBA"],
      "a middle-lineup batter's 'ahead' wOBA is the real previous slot's wOBA (no wrap needed)",
      f"got {extras_used[3]['lineup_woba'][3]}")

head("3. catcher/framing resolution: only a real 'C' with an id is captured")

extras_in = {"framing": {4: {"Steal%": 6.5}}}
extras_used2 = {}
def _capture2(batter, gm, *a, **kw):
    extras_used2[batter["id"]] = kw.get("extras")
    return orig_score_batter(batter, gm, *a, **kw)
gp.score_batter = _capture2
try:
    build([GM], extras=extras_in)
finally:
    gp.score_batter = orig_score_batter

fr = extras_used2[1]["framing_by_team"]
check(fr.get("Astros") == 6.5,
      "the away team's opposing catcher (home team's catcher, id=4, Steal%=6.5) "
      "resolves correctly into framing_by_team", f"got {fr}")

head("4. TBD starters are skipped, not scored as a phantom pitcher")

gm_tbd = dict(GM, away_sp="TBD", away_sp_id=None)
cands_tbd = build([gm_tbd])
pitcher_names = {c["name"] for c in cands_tbd if c["type"] == "pitcher"}
check("TBD" not in pitcher_names and "JP Sears" not in pitcher_names,
      "a TBD away starter produces no pitcher candidate for that side at all",
      f"got pitcher names: {pitcher_names}")
check("Framber Valdez" in pitcher_names,
      "the OTHER side's real, confirmed starter is still scored normally",
      f"got pitcher names: {pitcher_names}")

head("5. rain-risk watchout is appended, not a rejection (that's quality_control's job)")

rain_wx = {"Athletics @ Astros": {"dome": False, "precip_prob": 60}}
cands_rain = build([GM], park_wx=rain_wx)
check(len(cands_rain) > 0, "candidates still get produced under real rain risk -- "
      "build_candidates() only annotates, it never rejects")
check(any("Rain risk" in w for c in cands_rain for w in c.get("watchouts", [])),
      "at least one candidate carries the rain-risk watchout text")

head("6. an empty game_meta produces an empty candidate list, not a crash")

check(build([]) == [], "no games at all returns an empty list cleanly")

head("7. team_k_source lets a caller flag which team_k_lookup entries came from "
     "FanGraphs ('team') vs the MLB Stats API fallback ('mlb_team') -- direct request, "
     "verbatim: \"why do I still see 'Opposing team K% unavailable'?\" Real bug, found "
     "live 2026-08-15: mlb_sources.team_batting_table() (a real MLB Stats API team K%, "
     "not FanGraphs) was already being fetched into extras['team_bat'] and never used "
     "to fill this gap -- so whenever FanGraphs' team page AND its individual page (whose "
     "Statcast fallback has no K% column at all) were both down, opposing K% went "
     "unavailable on every pitcher pick, confirmed lineup or not.")

opp_k_seen = {}
orig_score_pitcher = gp.score_pitcher
def _capture_pitcher(name, pid, hand, gm, side, *a, **kw):
    args = list(a)
    opp_k_seen[name] = (args[3] if len(args) > 3 else None, args[5] if len(args) > 5 else None)
    return orig_score_pitcher(name, pid, hand, gm, side, *a, **kw)
gp.score_pitcher = _capture_pitcher
try:
    # Away starter (JP Sears) faces the home lineup -- opposing team is
    # "Astros". Tagged "mlb_team" here, meaning this game's number came
    # from the MLB Stats API fallback, not FanGraphs.
    build([GM], team_k_lookup={"Astros": 24.5}, team_k_source={"Astros": "mlb_team"})
finally:
    gp.score_pitcher = orig_score_pitcher

opp_k, opp_src = opp_k_seen["JP Sears"]
check(opp_k == 24.5, "the real team_k_lookup value reaches score_pitcher unchanged",
      f"got {opp_k}")
check(opp_src == "mlb_team",
      "the real source tag ('mlb_team', not the old hardcoded 'team') reaches "
      "score_pitcher, so its own why-note can honestly say which source it used",
      f"got {opp_src!r}")

head("8. team_k_source defaults to treating every team_k_lookup entry as FanGraphs-"
     "sourced ('team') when the caller doesn't pass it at all -- backtest/engine.py's "
     "existing call shape, unchanged by this fix")

opp_k_seen2 = {}
def _capture_pitcher2(name, pid, hand, gm, side, *a, **kw):
    args = list(a)
    opp_k_seen2[name] = (args[3] if len(args) > 3 else None, args[5] if len(args) > 5 else None)
    return orig_score_pitcher(name, pid, hand, gm, side, *a, **kw)
gp.score_pitcher = _capture_pitcher2
try:
    build([GM], team_k_lookup={"Astros": 22.0})   # no team_k_source kwarg at all
finally:
    gp.score_pitcher = orig_score_pitcher

opp_k2, opp_src2 = opp_k_seen2["JP Sears"]
check(opp_k2 == 22.0, "the real team_k_lookup value still reaches score_pitcher with "
      "no team_k_source passed at all", f"got {opp_k2}")
check(opp_src2 == "team",
      "with no team_k_source supplied, every team_k_lookup entry defaults to the "
      "original 'team' (FanGraphs) tag -- exact prior behavior, unchanged for callers "
      "that don't know about the new source", f"got {opp_src2!r}")

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
