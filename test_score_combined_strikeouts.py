#!/usr/bin/env python3
"""test_score_combined_strikeouts.py — coverage for generate_picks.score_
combined_strikeouts(), the "Starting Pitcher Combined Alt Strikeouts"
market. Had zero test coverage. Unlike score_pitcher_outs, this market
posts SEVERAL real thresholds per game and is ONLY scored when FanDuel has
already posted it -- no model-only fallback, since a made-up combined line
would have no real market to grade or settle against.

    /tmp/mlbvenv/bin/python3 test_score_combined_strikeouts.py
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

REQUIRED_KEYS = {"type", "name", "player_id", "combo_player_ids", "team", "matchup",
                 "game_pk", "prop", "projection", "hit_probability", "base_rate", "lift",
                 "probability_basis", "probability_detail", "market_odds", "market_implied",
                 "market_edge", "price_clears", "sample_n", "alternatives", "signals",
                 "score", "why", "watchouts", "notable_signals", "confidence"}

AWAY_C = {"player_id": 501, "expected_bf": 24.0, "k_rate": 0.26, "confidence": "High"}
HOME_C = {"player_id": 502, "expected_bf": 25.0, "k_rate": 0.24, "confidence": "High"}


def prices(rungs, matchup="Athletics @ Astros", pitchers=("JP Sears", "Framber Valdez")):
    return {matchup: {"rungs": rungs, "pitchers": pitchers}}


head("1. no combined_prices entry for this game -> None, not a crash")

check(gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, {}) is None,
      "no combined_prices dict at all returns None")
check(gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, {"Other @ Game": {"rungs": {12: -110}}}) is None,
      "combined_prices present but keyed to a different matchup returns None")
check(gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, prices({})) is None,
      "a matchup entry with an empty rungs dict returns None")

head("2. missing expected_bf/k_rate on either pitcher returns None rather than crashing")

incomplete_away = {"player_id": 501, "expected_bf": None, "k_rate": 0.26}
check(gp.score_combined_strikeouts(GM, incomplete_away, HOME_C, prices({12: -110})) is None,
      "away pitcher with expected_bf=None returns None")

no_k_home = {"player_id": 502, "expected_bf": 25.0, "k_rate": None}
check(gp.score_combined_strikeouts(GM, AWAY_C, no_k_home, prices({12: -110})) is None,
      "home pitcher with k_rate=None returns None")

head("3. a real, priced combined line returns a well-formed candidate")

c = gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, prices({12: -150, 13: -110, 14: 120, 15: 180}))
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "pitcher_combo", "type is pitcher_combo, not a plain batter/pitcher")
check(c["combo_player_ids"] == [501, 502], "combo_player_ids carries both starters in away-then-home order")
check(c["name"] == "JP Sears & Framber Valdez", "name combines both pitcher names from the rungs entry")
check(c["probability_basis"] == "modelled_independent_binomials",
      "uses the modelled-independent-binomials basis, not empirical_shrunk (no such table exists yet)")
check(c["sample_n"] is None,
      "sample_n is explicitly None (not 0) -- a brand-new derived market genuinely has no "
      "sample count of its own to report, and this function's own comment explains why "
      "reading .get('sample_n') off the per-pitcher candidate dicts would be wrong (that key "
      "doesn't exist yet at this point in the pipeline)")

head("4. price_clears reflects prop_probability.price_is_acceptable on the recommended line")

import prop_probability as pp
expected_clears = pp.price_is_acceptable(c["market_odds"], c["hit_probability"])
check(c["price_clears"] == expected_clears,
      "price_clears matches the real prop_probability.price_is_acceptable computation",
      f"got {c['price_clears']} want {expected_clears}")

head("5. base_rate/lift are computed against the MARKET's implied probability, not a league rate")

for rung in ((12, -150), (13, -110), (14, 120)):
    threshold, odds = rung
    implied = pp.implied_probability(odds)
    prob = pp.p_at_least_combined_strikeouts(threshold, 24.0, 0.26, 25.0, 0.24)
    single = gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, prices({threshold: odds}))
    check(abs(single["base_rate"] - implied) < 1e-9,
          f"threshold={threshold}: base_rate is exactly the market's own implied probability, "
          f"not a league-average read", f"got {single['base_rate']} want {implied}")
    check(abs(single["hit_probability"] - round(prob, 4)) < 1e-9,
          f"threshold={threshold}: hit_probability matches the real independent-binomials "
          f"model computation for these two starters' real bf/k_rate")

head("6. thin confidence on either starter caps the combined score at 55")

thin_away = dict(AWAY_C, confidence="Low")
c_thin = gp.score_combined_strikeouts(GM, thin_away, HOME_C, prices({12: -150, 13: -110}))
check(c_thin["score"] <= 55,
      "a Low-confidence AWAY starter caps the combined score at 55 even with strong "
      "underlying probability/lift", f"got {c_thin['score']}")

thin_home = dict(HOME_C, confidence="Low")
c_thin2 = gp.score_combined_strikeouts(GM, AWAY_C, thin_home, prices({12: -150, 13: -110}))
check(c_thin2["score"] <= 55,
      "a Low-confidence HOME starter also caps the combined score at 55")

c_both_high = gp.score_combined_strikeouts(GM, AWAY_C, HOME_C, prices({12: -150, 13: -110}))
check(c_both_high["score"] > c_thin["score"] or c_both_high["score"] == c_thin["score"],
      "both starters at High confidence never scores LOWER than the thin-confidence case",
      f"both_high={c_both_high['score']} thin={c_thin['score']}")

head("7. this market is explicitly flagged as unproven (not in the confidence measurement suite)")

check(any("unproven" in w or "brand new" in w for w in c["watchouts"]),
      "every combined_strikeouts candidate carries a watchout flagging it as unmeasured",
      f"got {c['watchouts']}")

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
