#!/usr/bin/env python3
"""test_score_pitcher.py — smoke/edge-case coverage for generate_picks.
score_pitcher(). Had zero real test coverage (one existing test file
mentions its name in a comment, but never calls it).

Same philosophy as test_score_batter.py: doesn't re-derive every signal
formula inside score_pitcher, checks that it never crashes and always
returns a well-formed candidate given minimal, missing, or edge-case
real-world inputs (a TBD-adjacent starter with no season stats yet, an
opposing lineup with unknown handedness, no L14 form, no umpire data).

    /tmp/mlbvenv/bin/python3 test_score_pitcher.py
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
      "game_pk": 900001, "series_game": 1}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "signals", "expected_bf", "k_rate", "score", "why",
                 "watchouts", "notable_signals", "confidence",
                 "cat_matchup", "cat_recent_form", "cat_environment",
                 "cat_baseline_skill", "cat_context"}

REAL_LINEUP = [{"name": f"Batter {i}", "id": i, "bats": "R" if i % 2 else "L"} for i in range(1, 10)]


def call(sp_name="Framber Valdez", sp_id=501, sp_hand="L", side="home",
        pit_season_lookup=None, l14_form=None, opp_lineup=None, opp_team_k_pct=None,
        ump_scores=None, **kw):
    return gp.score_pitcher(
        sp_name, sp_id, sp_hand, GM, side,
        pit_season_lookup or {}, l14_form or {}, opp_lineup or REAL_LINEUP,
        opp_team_k_pct, ump_scores or {}, **kw)


head("1. a normal, complete-ish call returns a well-formed candidate")

c = call(pit_season_lookup={"Framber Valdez": {"K%": 24.5, "CSW%": 29.0, "ERA": 3.10}},
        l14_form={"Framber Valdez": {"l14_pa": 90, "l14_k_pct": 25.0}}, opp_team_k_pct=23.0)
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "pitcher" and c["name"] == "Framber Valdez" and c["player_id"] == 501,
      "identity fields pass through correctly")
check(0 <= c["score"] <= 100, "score is bounded to [0, 100]", f"got {c['score']}")
check(0 < c["k_rate"] < 1, "k_rate is a real fraction, not a raw percentage or out of range",
      f"got {c['k_rate']}")

# PROMOTED 2026-08-14: score_pitcher no longer uses the original hand-set
# 35/25/15/15/10 -- see the comment above its own `score = clamp(...)` line
# for the measured findings (cleared the old formula's CI on 5 of 5
# independent train/held-out splits, the most robust finding of the
# night). ENVIRONMENT/CONTEXT weights deliberately kept at their original
# 0.15/0.10 -- both are functionally constant for this market, so the fit
# had nothing real to say about them.
rebuilt = gp.clamp(c["cat_matchup"] * 0.11 + c["cat_recent_form"] * -0.16 + c["cat_environment"] * 0.15
                   + c["cat_baseline_skill"] * 0.48 + c["cat_context"] * 0.10)
check(abs(round(rebuilt, 1) - c["score"]) < 0.15,
      "score == clamp(0.11*matchup + -0.16*recent_form + 0.15*environment + "
      "0.48*baseline_skill + 0.10*context)",
      f"rebuilt={rebuilt:.2f} vs recorded score={c['score']}")

head("2. every optional input at its default doesn't crash")

c2 = call()  # no season stats, no L14 form, no opp_team_k_pct, empty ump_scores
check(REQUIRED_KEYS.issubset(c2.keys()), "a call with nothing but name/id/hand/lineup still "
      "returns a well-formed candidate", f"got keys={sorted(c2.keys())}")
check(0 <= c2["score"] <= 100, "score stays bounded with no season data at all")
check(0 < c2["k_rate"] < 1, "k_rate falls back to the documented default (22.5%) rather than "
      "crashing or returning None", f"got {c2['k_rate']}")

head("3. a starter with no season stats on record at all (early-season call-up)")

c3 = call(sp_name="Rookie Starter", sp_id=999, pit_season_lookup={})
check(REQUIRED_KEYS.issubset(c3.keys()), "a starter absent from the season lookup entirely still "
      "produces a well-formed candidate rather than a KeyError")

head("4. an opposing lineup with unknown handedness on every batter")

unknown_hand_lineup = [{"name": f"Batter {i}", "id": i, "bats": "?"} for i in range(1, 10)]
c4 = call(opp_lineup=unknown_hand_lineup)
check(REQUIRED_KEYS.issubset(c4.keys()),
      "an opposing lineup with no known handedness anywhere falls back gracefully "
      "(same_hand_ratio defaults rather than dividing by zero)")

head("5. an empty opposing lineup (posted but somehow zero entries)")

c5 = call(opp_lineup=[])
check(REQUIRED_KEYS.issubset(c5.keys()), "an empty opposing lineup list doesn't crash "
      "(known=0 case in the same-hand ratio must not divide by zero)")

head("6. ump_kbb/il_returns/callups all None (the pre-this-session call shape)")

c6 = call(ump_kbb=None, il_returns=None, callups=None)
check(REQUIRED_KEYS.issubset(c6.keys()),
      "the three newest optional kwargs all at None reproduce the original call shape safely")

head("7. away side vs home side both resolve team correctly")

c7a = call(side="away")
c7b = call(side="home")
check(c7a["team"] == "Athletics" and c7b["team"] == "Astros",
      "side='away'/'home' resolve to the correct team from game_meta, not swapped",
      f"got away->{c7a['team']}, home->{c7b['team']}")

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
