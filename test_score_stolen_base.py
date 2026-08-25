#!/usr/bin/env python3
"""test_score_stolen_base.py — coverage for generate_picks.score_stolen_
base(), zero test coverage despite its own docstring documenting a real,
previously-shipped bug: the "context" term used to silently default to a
flat 50 for every runner on the common (Statcast-fallback) code path
because it read a season-SB column that path never carries.

    /tmp/mlbvenv/bin/python3 test_score_stolen_base.py
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

GM = {"matchup": "Athletics @ Astros", "game_pk": 900001}
BATTER = {"name": "Speedy Runner", "id": 5, "team": "Athletics", "order": 1}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "signals", "projected_pa", "score", "why", "watchouts",
                 "notable_signals", "confidence",
                 "sb_cat_skill", "sb_cat_matchup", "sb_cat_context"}


def call(sprint_speed=28.5, opp_catcher_poptime=2.0, batter_season=None, opp_cs_pct=None,
        batter=None):
    return gp.score_stolen_base(batter or BATTER, GM, opp_catcher_poptime, sprint_speed,
                                batter_season, opp_cs_pct)


head("1. below the sprint-speed floor (27.3 ft/s) returns None -- not a plausible threat at all")

check(gp.score_stolen_base(BATTER, GM, 2.0, 27.2, None) is None,
      "27.2 ft/s (just under the 27.3 floor) returns None")
check(gp.score_stolen_base(BATTER, GM, 2.0, None, None) is None,
      "sprint_speed=None returns None rather than crashing on the comparison")
check(gp.score_stolen_base(BATTER, GM, 2.0, 0, None) is None,
      "sprint_speed=0 (falsy) also returns None, not treated as 'unknown -> default'")

head("2. at/above the floor, a well-formed candidate is returned")

c = call(sprint_speed=27.3)
check(c is not None and REQUIRED_KEYS.issubset(c.keys()),
      "exactly at the 27.3 floor returns a well-formed candidate", f"got {c}")
check(c["projection"] == {"stat": "stolen_base", "value": 1},
      "projection is pinned at exactly value=1 (deliberately, per the docstring -- "
      "grade_results.py grades actual >= projection - 0.5)", f"got {c['projection']}")

rebuilt = c["sb_cat_skill"] * 0.50 + c["sb_cat_matchup"] * 0.28 + c["sb_cat_context"] * 0.22
check(abs(round(rebuilt, 1) - c["score"]) < 0.15,
      "score == 0.50*skill + 0.28*matchup + 0.22*context, reconstructed from the recorded "
      "sb_cat_ fields -- proves the instrumentation records what the formula actually used",
      f"rebuilt={rebuilt:.2f} vs recorded score={c['score']}")

head("3. the previously-shipped bug: OBP/wOBA context is NOT a flat 50 on a real fallback frame")

c_no_obp = call(batter_season={"wOBA": 0.410, "pa": 200})  # Statcast-fallback shape, no OBP/SB
on_base_sig = c_no_obp["signals"].get("on_base")
check(on_base_sig is not None and on_base_sig != 50,
      "a real wOBA-only (Statcast-fallback) season line produces a genuine on-base score, "
      "not the old flat-50 default this function's docstring documents as the bug it replaced",
      f"got on_base signal={on_base_sig}")

c_high_woba = call(batter_season={"wOBA": 0.430, "pa": 200})
c_low_woba = call(batter_season={"wOBA": 0.250, "pa": 200})
check(c_high_woba["score"] > c_low_woba["score"],
      "a higher wOBA (more time on base) scores higher than a lower wOBA at the same speed",
      f"high wOBA score={c_high_woba['score']}, low wOBA score={c_low_woba['score']}")

head("4. a thin-PA wOBA sample (<40 PA) is treated as no signal, not a real read")

c_thin = call(batter_season={"wOBA": 0.698, "pa": 1})  # the exact absurd-thin case cited live
check("on_base" not in c_thin["signals"] or c_thin["signals"].get("on_base") is None,
      "a 1-PA .698 wOBA (absurd small-sample artifact) does NOT get scored as a real "
      "on-base read -- the 40 PA floor in _on_base_score must hold",
      f"got signals={c_thin['signals']}")
check(any("neutral" in w for w in c_thin["watchouts"]),
      "the thin-sample case is flagged with a watchout explaining the neutral default")

head("5. season_sb is a converging flag only, not a scored component")

with_sb = call(batter_season={"wOBA": 0.320, "pa": 200, "SB": 25})
without_sb = call(batter_season={"wOBA": 0.320, "pa": 200})
check(with_sb["score"] == without_sb["score"],
      "season SB presence/absence does not move the score at all -- it's a notable_signals "
      "flag only, per the function's own docstring", f"with={with_sb['score']} without={without_sb['score']}")
check(with_sb["notable_signals"] > without_sb["notable_signals"],
      "season SB >= 15 still bumps notable_signals even though it doesn't move the score")

head("6. catcher matchup: no poptime defaults to the real league average, not a fabricated crash")

c_no_ct = call(opp_catcher_poptime=None)
check(REQUIRED_KEYS.issubset(c_no_ct.keys()), "opp_catcher_poptime=None doesn't crash")
check(any("league average" in w for w in c_no_ct["watchouts"]),
      "missing catcher pop time is flagged with its own watchout")
c_league_avg = call(opp_catcher_poptime=gp.LEAGUE_AVG_POPTIME)
check(c_no_ct["score"] == c_league_avg["score"],
      "the missing-data score matches scoring the same batter against a catcher with "
      "exactly the league-average pop time, not an arbitrary flat 50",
      f"missing={c_no_ct['score']} league_avg={c_league_avg['score']}")

head("7. a genuinely hard team to run against (opp_cs_pct >= 0.30) gets a real watchout")

c_hard = call(opp_cs_pct=0.35)
check(any("throws out" in w for w in c_hard["watchouts"]),
      "a 35% team CS rate produces the hard-to-run-on watchout", f"got {c_hard['watchouts']}")

c_easy = call(opp_cs_pct=0.15)
check(not any("throws out" in w for w in c_easy["watchouts"]),
      "a 15% team CS rate (easy to run on) does NOT trigger that watchout")

check(c_hard["signals"]["opp_team_cs_pct"] < c_easy["signals"]["opp_team_cs_pct"],
      "opp_team_cs_pct's recorded signal score is lower (worse for the runner) when the "
      "real caught-stealing rate is higher", f"hard={c_hard['signals']['opp_team_cs_pct']} "
      f"easy={c_easy['signals']['opp_team_cs_pct']}")

head("8. catcher pop time direction: a SLOWER pop time (easier to steal on) scores higher")

c_slow_catcher = call(opp_catcher_poptime=2.30)
c_fast_catcher = call(opp_catcher_poptime=1.85)
check(c_slow_catcher["score"] > c_fast_catcher["score"],
      "a catcher with a slower (worse, easier-to-run-on) pop time produces a higher "
      "steal score than an elite-armed catcher", f"slow={c_slow_catcher['score']} "
      f"fast={c_fast_catcher['score']}")

head("9. 2026-08-25 explanation-quality fix (release-readiness audit): sprint speed / catcher "
     "pop time / on-base ability must not land unqualified in `why` when they're actually "
     "WEAK readings. Real production bug found via docs/data.json: Bobby Witt Jr.'s matchup "
     "against a 1.88s (elite, hard-to-steal-on) catcher rendered under 'Why It Could Hit' "
     "with no qualifying language -- 180 total stolen_base props affected on one real slate.")

c9_fast_catcher = call(opp_catcher_poptime=1.85)
check(not any("Opposing catcher pop time" in w for w in c9_fast_catcher["why"]),
      "REGRESSION GUARD: an elite/fast catcher pop time (matchup<=35, hard to run on) must "
      "NOT appear in why -- it isn't a reason to like the pick", f"got {c9_fast_catcher['why']}")
check(any("Opposing catcher pop time 1.85" in w and "hard-to-run-on" in w for w in c9_fast_catcher["watchouts"]),
      "that same fast pop time instead lands in watchouts, honestly labeled",
      f"got {c9_fast_catcher['watchouts']}")

c9_slow_catcher = call(opp_catcher_poptime=2.30)
check(any("Opposing catcher pop time 2.30" in w and "easier to run on" in w for w in c9_slow_catcher["why"]),
      "a genuinely slow (favorable) catcher pop time is labeled as such in why",
      f"got {c9_slow_catcher['why']}")

c9_weak_ob = call(batter_season={"wOBA": 0.26, "pa": 200})
check(not any("On-base ability" in w for w in c9_weak_ob["why"]),
      "REGRESSION GUARD: a weak on-base rate (on_base<=25) must NOT appear in why -- the "
      "existing 'Fast, but a weak on-base rate' watchout already covers this fact, so it "
      "must not also render as unqualified support", f"got {c9_weak_ob['why']}")
check(any("weak on-base rate" in w for w in c9_weak_ob["watchouts"]),
      "the weak on-base rate is still surfaced, honestly, in watchouts",
      f"got {c9_weak_ob['watchouts']}")

c9_strong_ob = call(batter_season={"wOBA": 0.40, "pa": 200})
check(any("On-base ability" in w and "(favorable)" in w for w in c9_strong_ob["why"]),
      "a genuinely strong on-base rate is labeled favorable in why",
      f"got {c9_strong_ob['why']}")

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
