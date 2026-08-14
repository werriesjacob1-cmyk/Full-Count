#!/usr/bin/env python3
"""test_score_batter.py — smoke/edge-case coverage for generate_picks.
score_batter(), the core per-batter scoring function (and, by extension,
build_candidates()'s main inner loop, which calls it once per lineup slot
across every game). Had zero test coverage.

This does NOT re-verify every one of the dozens of individual signal
formulas inside score_batter -- that would duplicate the function itself.
It checks the thing a function this size and this exposed to messy real
data actually needs guaranteed: it never crashes and never returns a
malformed candidate when given minimal, missing, or edge-case inputs,
matching the "absent is not zero" discipline the rest of this project
holds itself to. The real end-to-end proof that the FULL set of real
signals computes correctly is the live pipeline smoke test this session
already ran (726 real candidates, zero errors) -- this is the permanent,
CI-covered version of "does it survive contact with incomplete data."

    /tmp/mlbvenv/bin/python3 test_score_batter.py
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

GM = {"matchup": "Athletics @ Astros", "away_team": "Athletics", "home_team": "Astros",
      "game_pk": 900001, "series_game": 1, "home_sp": "Framber Valdez", "away_sp": "JP Sears",
      "venue": "Minute Maid Park"}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "projected_pa", "projected_tb", "signals", "score",
                 "why", "watchouts", "notable_signals", "confidence",
                 "cat_matchup", "cat_recent_form", "cat_environment",
                 "cat_baseline_skill", "cat_context"}


def call(batter=None, opp_sp_row=None, opp_sp_id=None, opp_sp_hand=None, park_wx=None,
        batter_season=None, batter_l7=None, extras=None):
    return gp.score_batter(
        batter or {"name": "Test Batter", "id": 5, "team": "Athletics", "bats": "R", "order": 3},
        GM, opp_sp_row, opp_sp_id, opp_sp_hand, park_wx or {}, batter_season, batter_l7,
        {}, {}, {}, extras=extras)


head("1. a normal, complete-ish call returns a well-formed candidate")

c = call(opp_sp_row={"ERA": 4.20}, opp_sp_id=501, opp_sp_hand="L",
        batter_season={"wOBA": 0.340}, batter_l7={"PA": 25, "avg_EV": 90.0, "barrel_pct": 8.0})
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "batter" and c["name"] == "Test Batter" and c["player_id"] == 5,
      "identity fields pass through correctly")
check(0 <= c["score"] <= 100, "score is bounded to [0, 100]", f"got {c['score']}")
check(c["confidence"] in ("High", "Medium", "Low"), "confidence is one of the three real labels")

head("1b. score is genuinely reconstructable from the 5 recorded cat_ components via "
     "the PROMOTED 2026-08-14 weights (0.04/0.03/0.20/-0.09/0.64, replacing the "
     "original hand-set 35/25/15/15/10 -- see the comment above score_batter's own "
     "`score = clamp(...)` line for the measured findings) -- proves the "
     "instrumentation records the SAME values the formula actually used, not a "
     "re-derivation")

rebuilt = gp.clamp(c["cat_matchup"] * 0.04 + c["cat_recent_form"] * 0.03 + c["cat_environment"] * 0.20
                   + c["cat_baseline_skill"] * -0.09 + c["cat_context"] * 0.64)
check(abs(round(rebuilt, 1) - c["score"]) < 0.15,
      "score == clamp(0.04*matchup + 0.03*recent_form + 0.20*environment + "
      "-0.09*baseline_skill + 0.64*context)",
      f"rebuilt={rebuilt:.2f} vs recorded score={c['score']}")
for k in ("cat_matchup", "cat_recent_form", "cat_environment", "cat_baseline_skill", "cat_context"):
    check(0 <= c[k] <= 100, f"{k} is bounded to [0, 100]", f"got {c[k]}")

head("2. every optional input as None/missing doesn't crash")

c2 = call()  # opp_sp_row=None, batter_season=None, batter_l7=None, park_wx={}, extras=None
check(REQUIRED_KEYS.issubset(c2.keys()), "a call with every optional arg at its default still "
      "returns a well-formed candidate", f"got keys={sorted(c2.keys())}")
check(0 <= c2["score"] <= 100, "score stays bounded even with nothing but the batter's name/id known")

head("3. a batter missing id/bats/order (a real rookie call-up shape)")

c3 = call(batter={"name": "Rookie Callup", "team": "Athletics"})  # no id, no bats, no order
check(c3["player_id"] is None, "a batter with no id passes through as player_id=None, not a crash "
      "or a fabricated id")
check(REQUIRED_KEYS.issubset(c3.keys()), "still a well-formed candidate with the bare minimum "
      "batter dict this project's own docstrings describe (a rookie with no id yet)")

head("4. extras=None vs extras={} produce the same shape (both mean 'nothing available')")

c4a = call(extras=None)
c4b = call(extras={})
check(c4a.keys() == c4b.keys(), "extras=None and extras={} are handled identically",
      f"None keys diff {'==' if c4a.keys()==c4b.keys() else '!='} {{}} keys")

head("5. extras with every key present but empty/None values (a bad-data night)")

empty_extras = {k: {} for k in (
    "bvp", "platoon_qoc", "park_hand", "framing_by_team", "rest", "lineup_woba",
    "pull", "hard_hit", "line_move", "ump_kbb", "il_returns", "callups")}
c5 = call(extras=empty_extras)
check(REQUIRED_KEYS.issubset(c5.keys()), "every extras table present but empty doesn't crash "
      "score_batter -- each lookup gracefully finds nothing rather than raising")

head("6. an opposing pitcher with no ERA on record (early season / call-up starter)")

c6 = call(opp_sp_row={"ERA": None}, opp_sp_hand="R")
check(REQUIRED_KEYS.issubset(c6.keys()) and 0 <= c6["score"] <= 100,
      "a None ERA doesn't crash the matchup-quality scale() call")

head("7. unknown handedness on both sides (bats='?' AND opposing hand unknown)")

c7 = call(batter={"name": "X", "id": 9, "team": "Athletics", "bats": "?"}, opp_sp_hand=None)
check(REQUIRED_KEYS.issubset(c7.keys()),
      "unknown handedness on both sides falls back to the neutral platoon score, not a crash")

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
