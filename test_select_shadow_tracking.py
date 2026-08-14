#!/usr/bin/env python3
"""test_select_shadow_tracking.py — coverage for generate_picks.select_
shadow_tracking(), which recovers the alternate thresholds (hard_hit_110
chief among them) that _pick_line demotes on every candidate into
`alternatives` and turns each into its own gradable pick, so their real
hit rate can finally be measured. Direct request: "There should be no
prop not rated and bet on to know the hit percentage... I understand if
it isn't included in the final card but I still want to know."

    /tmp/mlbvenv/bin/python3 test_select_shadow_tracking.py
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

GM = {"matchup": "Athletics @ Astros", "away_team": "Athletics", "home_team": "Astros", "game_pk": 900001}


def laser_winner(name="Slugger", player_id=5, score=70, alt_prob=0.03, alt_threshold=110):
    """Mimics score_laser's real output shape: the winning threshold (105+)
    is the candidate itself, 110+ is demoted to `alternatives`."""
    return {
        "type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
        "matchup": GM["matchup"], "game_pk": GM["game_pk"], "side": "away",
        "score": score, "confidence": "Medium", "notable_signals": 0, "signals": {},
        "prop": "To Hit a Laser (105+ MPH)",
        "projection": {"stat": "hard_hit_105", "value": 1, "needs": 1},
        "hit_probability": 0.30, "base_rate": 0.18, "lift": 0.12,
        "alternatives": [{"stat": f"hard_hit_{alt_threshold}", "line": 1, "needs": 1,
                          "prob": alt_prob, "base_rate": 0.02, "lift": round(alt_prob - 0.02, 4)}],
    }


head("1. a candidate's demoted alternate threshold becomes its own gradable pick")

out = gp.select_shadow_tracking([laser_winner()])
check(("hard_hit_110", 1) in out, "hard_hit_110 (the alternate) gets its own key, distinct from "
      "hard_hit_105 (the winner) which never appears here at all",
      f"got keys={list(out.keys())}")
entry = out[("hard_hit_110", 1)][0]
check(entry["projection"]["stat"] == "hard_hit_110" and entry["hit_probability"] == 0.03,
      "the shadow entry carries the alternate's own stat/probability, not the winner's")
check(entry["category"] == "shadow", "tagged category='shadow', distinct from best_of_category/main/moonshot")
check(entry["market_odds"] is None and entry["price_clears"] is None,
      "deliberately unpriced -- these were never going to reach the card, only tracked for hit-rate")

head("2. a candidate with no alternatives contributes nothing")

no_alt = laser_winner(name="NoAlt")
no_alt["alternatives"] = []
out2 = gp.select_shadow_tracking([no_alt])
check(out2 == {}, "an empty alternatives list produces no shadow entries")

head("3. pitcher_outs-style alternates (same stat, different needs) don't collide")

po_a = {"type": "pitcher", "name": "SP A", "player_id": 501, "team": "Astros",
        "matchup": GM["matchup"], "game_pk": GM["game_pk"], "side": "home",
        "score": 60, "confidence": "Medium", "notable_signals": 0, "signals": {},
        "alternatives": [
            {"stat": "pitcher_outs", "line": 14.5, "needs": 15, "prob": 0.55, "base_rate": 0.5, "lift": 0.05},
            {"stat": "pitcher_outs", "line": 17.5, "needs": 18, "prob": 0.20, "base_rate": 0.5, "lift": -0.3},
        ]}
out3 = gp.select_shadow_tracking([po_a])
check(("pitcher_outs", 15) in out3 and ("pitcher_outs", 18) in out3,
      "two alternates sharing one stat name but different needs land in SEPARATE keys, "
      "not overwriting each other", f"got keys={list(out3.keys())}")

head("4. across multiple candidates, the same (stat, needs) key keeps only the best by probability")

better = laser_winner(name="Better", player_id=6, alt_prob=0.09)
worse = laser_winner(name="Worse", player_id=7, alt_prob=0.04)
out4 = gp.select_shadow_tracking([worse, better], n_per_key=1)
kept = out4[("hard_hit_110", 1)]
check(len(kept) == 1 and kept[0]["name"] == "Better",
      "n_per_key=1 keeps the higher-probability alternate across candidates, not just first-seen order")

head("5. a candidate missing player_id/game_pk still produces a well-formed (later ungradeable) entry")

odd = laser_winner(name="Weird")
odd["player_id"] = None
out5 = gp.select_shadow_tracking([odd])
entry5 = out5[("hard_hit_110", 1)][0]
check(entry5["player_id"] is None, "player_id=None passes through rather than crashing -- "
      "grade_pick() is the one that will mark it ungraded, not this selector")

head("6. game/pitcher_combo types are included, walk/first_inning-style non-listed types are not")

game_type = {"type": "game", "name": "NRFI", "player_id": None, "team": None,
            "matchup": GM["matchup"], "game_pk": GM["game_pk"], "score": 50,
            "confidence": "Low", "notable_signals": 0, "signals": {},
            "alternatives": [{"stat": "some_alt", "line": 1, "needs": 1, "prob": 0.4, "base_rate": 0.3, "lift": 0.1}]}
weird_type = dict(game_type, type="something_else")
out6a = gp.select_shadow_tracking([game_type])
out6b = gp.select_shadow_tracking([weird_type])
check(("some_alt", 1) in out6a, "type='game' candidates are scanned for alternatives")
check(out6b == {}, "an unrecognized type is skipped entirely, not silently included")

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
