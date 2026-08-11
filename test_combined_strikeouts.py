#!/usr/bin/env python3
"""test_combined_strikeouts.py — checks the new "Starting Pitcher Combined
Alt Strikeouts" market: prop_probability's two-pitcher convolution math and
generate_picks.score_combined_strikeouts's line selection / pricing / candidate
shape. Direct request: "we shouldn't be blind to ANY prop" -- this is the
first concrete market built off that audit.

    /tmp/mlbvenv/bin/python3 test_combined_strikeouts.py
    python3 test_combined_strikeouts.py -v
"""
import random
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import prop_probability as pp
import generate_picks as gp

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


head("1. combined_strikeouts_distribution: sums to 1, matches Monte Carlo")

dist = pp.combined_strikeouts_distribution(24, 0.25, 22, 0.22)
check(abs(sum(dist.values()) - 1.0) < 1e-9, "the combined pmf sums to 1.0", f"sum={sum(dist.values())}")

random.seed(0)
N = 100_000
hits = 0
for _ in range(N):
    a = sum(1 for _ in range(24) if random.random() < 0.25)
    b = sum(1 for _ in range(22) if random.random() < 0.22)
    if a + b >= 12:
        hits += 1
mc = hits / N
model = pp.p_at_least_combined_strikeouts(12, 24, 0.25, 22, 0.22)
check(abs(mc - model) < 0.01, "P(>=12 combined) matches a 100k-trial Monte Carlo simulation within 1pt",
      f"model={model:.4f} monte_carlo={mc:.4f}")

head("2. combined_strikeouts_distribution: edge cases")

check(abs(pp.p_at_least_combined_strikeouts(0, 24, 0.25, 22, 0.22) - 1.0) < 1e-9,
      "P(>=0) is always 1.0 (within floating-point tolerance)")
check(pp.combined_strikeouts_distribution(0, 0.25, 0, 0.22) == {0: 1.0},
      "two pitchers who face nobody combine for exactly 0 Ks with certainty")

head("3. score_combined_strikeouts: prices against the REAL market line, correct shape")

matchup = "Cincinnati Reds @ Chicago White Sox"
gm = {"matchup": matchup, "game_pk": 823456}
away_c = {"player_id": 111, "name": "Nick Lodolo", "expected_bf": 22.0, "k_rate": 0.28,
          "sample_n": 15, "confidence": "Medium"}
home_c = {"player_id": 222, "name": "Sean Burke", "expected_bf": 21.0, "k_rate": 0.24,
          "sample_n": 12, "confidence": "Medium"}
combined_prices = {matchup: {"pitchers": ("Nick Lodolo", "Sean Burke"),
                              "rungs": {12: -144, 13: 118, 14: 198, 15: 340, 16: 570, 17: 980, 18: 1500}}}

pick = gp.score_combined_strikeouts(gm, away_c, home_c, combined_prices)
check(pick is not None, "a real market + two real pitcher reads produces a candidate")
check(pick["projection"]["stat"] == "combined_strikeouts", "projection.stat is combined_strikeouts")
check(pick["projection"]["needs"] in combined_prices[matchup]["rungs"],
      "the chosen threshold IS one of the real rungs FanDuel actually posted",
      f"needs={pick['projection']['needs']}")
check(pick["market_odds"] == combined_prices[matchup]["rungs"][pick["projection"]["needs"]],
      "market_odds attached matches the real price at the chosen rung")
check(pick["type"] == "pitcher_combo", "candidate type is pitcher_combo, not batter/pitcher")
check(pick["combo_player_ids"] == [111, 222], "both real player ids are carried for grading")
check(pick["player_id"] == 111, "player_id is the away starter's id, for persistence")
check("Nick Lodolo" in pick["name"] and "Sean Burke" in pick["name"], "both names appear in the display name")

head("4. score_combined_strikeouts: never fabricates a line when no real market exists")

no_market_pick = gp.score_combined_strikeouts(gm, away_c, home_c, {})
check(no_market_pick is None, "no combined_prices at all -> no candidate (never invents a line)")

other_game_pick = gp.score_combined_strikeouts(
    {"matchup": "Some Other @ Game", "game_pk": 1}, away_c, home_c, combined_prices)
check(other_game_pick is None, "a matchup with no posted market -> no candidate")

head("5. score_combined_strikeouts: missing pitcher reads degrade to no candidate, not a crash")

no_bf = gp.score_combined_strikeouts(gm, {**away_c, "expected_bf": None}, home_c, combined_prices)
check(no_bf is None, "a missing expected_bf on either pitcher -> no candidate")

no_k = gp.score_combined_strikeouts(gm, away_c, {**home_c, "k_rate": None}, combined_prices)
check(no_k is None, "a missing k_rate on either pitcher -> no candidate")

head("6. score_combined_strikeouts: a well-priced edge scores high, a bad-value line scores low")

# Model favors 18+ heavily but market prices it as a longshot (+1500) -- a real edge.
generous_prices = {matchup: {"pitchers": ("A", "B"), "rungs": {12: -1000}}}  # market thinks 12+ is a lock too
stingy_pick = gp.score_combined_strikeouts(gm, away_c, home_c, generous_prices)
check(stingy_pick["lift"] < 0, "when the market is MORE confident than the model, lift is negative",
      f"lift={stingy_pick['lift']}")

favorable_prices = {matchup: {"pitchers": ("A", "B"), "rungs": {12: 500}}}  # market pays out big on a likely rung
value_pick = gp.score_combined_strikeouts(gm, away_c, home_c, favorable_prices)
check(value_pick["lift"] > 0, "when the market underprices a rung the model likes, lift is positive",
      f"lift={value_pick['lift']}")
check(value_pick["score"] > stingy_pick["score"],
      "a genuine positive-edge line scores higher than a negative-edge line at the same rung")

head("7. score_combined_strikeouts: sample_n is honest (None), not a fabricated 0")

check(pick["sample_n"] is None,
      "sample_n is None, not 0 -- score_pitcher()'s own return dict has no sample_n key at this "
      "point in the pipeline (attach_reliability adds it later), so reading it here would always "
      "silently evaluate to 0 and misreport 'zero evidence' for a pick built on real workload data",
      f"sample_n={pick['sample_n']!r}")

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
