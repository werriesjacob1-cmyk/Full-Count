#!/usr/bin/env python3
"""test_score_laser.py — coverage for generate_picks.score_laser(), the
"Laser" (105+/110+ MPH exit velocity) FanDuel market. Had zero test
coverage. The function's own docstring flags a specific bug class it
deliberately avoids (picking the higher-probability 105+ line via a naive
max() would ALWAYS win over 110+ since 110+ is a strict subset) -- this
locks that behavior in via _pick_line's real lift-based selection.

    /tmp/mlbvenv/bin/python3 test_score_laser.py
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
BATTER = {"name": "Slugger", "id": 5, "team": "Athletics"}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "hit_probability", "base_rate", "lift", "probability_basis",
                 "probability_detail", "sample_n", "alternatives", "signals", "score",
                 "why", "watchouts", "notable_signals", "confidence"}


def rates(p105=None, p110=None, n=80, lg105=0.35, lg110=0.15):
    r = {}
    if p105 is not None:
        r["hard_hit_105_1plus"] = {"p_hat": p105, "league_p": lg105, "n": n}
    if p110 is not None:
        r["hard_hit_110_1plus"] = {"p_hat": p110, "league_p": lg110, "n": n}
    return r


head("1. no id / not in hard_hit_rates / no rates dict -> None, not a crash")

check(gp.score_laser({"name": "No ID"}, GM, {}) is None, "a batter with no id returns None")
check(gp.score_laser(BATTER, GM, {}) is None, "batter not present in hard_hit_rates returns None")
check(gp.score_laser(BATTER, GM, {5: {}}) is None, "an entry present but with no 'rates' key returns None")
check(gp.score_laser(BATTER, GM, {5: {"rates": {}}}) is None,
      "an entry with an empty rates dict returns None")

head("2. a normal call with both thresholds present returns a well-formed candidate")

c = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.40, p110=0.18)}})
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "batter" and c["player_id"] == 5, "identity fields pass through correctly")
check(c["probability_basis"] == "empirical_shrunk", "uses the empirical-shrunk basis, no modelled blend")
check(c["probability_detail"] == {"empirical": c["hit_probability"], "modelled": None},
      "probability_detail records empirical only, modelled=None")

head("3. only one threshold has real rate data -> that's the only option, no crash")

c105_only = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.40)}})
check(REQUIRED_KEYS.issubset(c105_only.keys()) and c105_only["projection"]["needs"] == 1,
      "only 105+ data present still returns a well-formed candidate")
check("110" not in c105_only["prop"], "the 105-only case never mentions 110+ in its prop label",
      f"got {c105_only['prop']}")

head("4. the bug this function's docstring exists to prevent: naive max() would always pick "
     "105+ since it's structurally a superset of 110+ -- _pick_line must be able to choose "
     "110+ when it carries the better LIFT even though its raw probability is lower "
     "(both thresholds must clear _pick_line's own MIN_LINE_PROB=0.60 floor to be eligible "
     "at all -- this is not just about raw lift, the floor has to be cleared first)")

# 105+: raw prob 65% but only 10 points above its 55% league base rate -> modest lift
# 110+: raw prob lower (62%, still clears the 60% floor) but 52 points above its 10%
# league base rate -> much bigger lift
c_should_pick_110 = gp.score_laser(
    BATTER, GM, {5: {"rates": rates(p105=0.65, p110=0.62, lg105=0.55, lg110=0.10)}})
check(c_should_pick_110["projection"]["needs"] == 1 and "110" in c_should_pick_110["prop"],
      "when 110+ carries the larger lift, it is recommended even though its raw probability "
      "(62%) is lower than 105+'s (65%) -- proves this isn't a naive max(prob) selection",
      f"got prop={c_should_pick_110['prop']!r}")
check(len(c_should_pick_110["alternatives"]) == 1 and c_should_pick_110["alternatives"][0]["stat"] == "hard_hit_105",
      "the non-selected threshold (105+) is carried as an alternative")

head("5. thin sample (< LASER_SCORE_CONFIDENCE_GAMES) caps the score at 55")

c_thin = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.55, lg105=0.20, n=10)}})
check(c_thin["score"] <= 55, "a 10-game sample (well under the 60-game confidence floor) caps "
      "the score at 55 regardless of how strong the raw rate looks", f"got {c_thin['score']}")
check(c_thin["sample_n"] == 10, "sample_n correctly reports the real (thin) sample size")

c_thick = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.55, lg105=0.20, n=200)}})
check(c_thick["score"] > c_thin["score"],
      "the identical rate/lift with a real 200-game sample scores strictly higher than the "
      "10-game version, purely from the confidence cap lifting", f"thick={c_thick['score']} thin={c_thin['score']}")

head("6. notable_signals reflects whether the lift clears the 0.05 bar")

c_big_lift = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.50, lg105=0.20, n=100)}})
check(c_big_lift["notable_signals"] == 1, "a lift of +0.30 (well over 0.05) sets notable_signals=1")

c_small_lift = gp.score_laser(BATTER, GM, {5: {"rates": rates(p105=0.21, lg105=0.20, n=100)}})
check(c_small_lift["notable_signals"] == 0, "a lift of +0.01 (under 0.05) leaves notable_signals=0")

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
