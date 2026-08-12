#!/usr/bin/env python3
"""test_score_pitcher_outs.py — coverage for generate_picks.score_pitcher_
outs(), the "Pitcher Outs Recorded" market. Had zero test coverage despite
its own docstring documenting a real, previously-shipped bug: running
FanDuel's single real posted line through _pick_line's MIN_LINE_PROB=0.60
floor guaranteed that real line was EXCLUDED (the book sets it near a
coinflip by construction) and a much easier, uninformative threshold won
instead -- with no price attached, since market_odds only attaches when
the recommended `needs` matches a real posted line.

    /tmp/mlbvenv/bin/python3 test_score_pitcher_outs.py
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
      "game_pk": 900001}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "hit_probability", "base_rate", "lift", "probability_basis",
                 "probability_detail", "sample_n", "alternatives", "signals", "score",
                 "why", "watchouts", "notable_signals", "confidence"}


def outs_rates(rates_by_threshold, starts=15, avg_outs=16.5, sp_id=501):
    """rates_by_threshold: {threshold: (p_hat, league_p)}"""
    rates = {}
    for t, (p_hat, lg) in rates_by_threshold.items():
        rates[f"outs_{t}plus"] = {"p_hat": p_hat, "league_p": lg}
    return {sp_id: {"rates": rates, "starts": starts, "avg_outs": avg_outs}}


head("1. no id / not in outs_rates / empty rates -> None, not a crash")

check(gp.score_pitcher_outs("X", None, GM, "home", {}) is None, "sp_id=None returns None")
check(gp.score_pitcher_outs("X", 501, GM, "home", {}) is None,
      "sp_id not present in outs_rates returns None")
check(gp.score_pitcher_outs("X", 501, GM, "home", {501: {}}) is None,
      "an entry with no 'rates' key returns None")
check(gp.score_pitcher_outs("X", 501, GM, "home", {501: {"rates": {}}}) is None,
      "an entry with an empty rates dict returns None")

head("2. a normal call with no real market line falls back to the pitcher's own average workload")

rates = outs_rates({15: (0.70, 0.55), 16: (0.60, 0.45), 17: (0.55, 0.35), 18: (0.40, 0.20)},
                   avg_outs=17.2)
c = gp.score_pitcher_outs("Framber Valdez", 501, GM, "home", rates)
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["projection"]["needs"] == 17,
      "with no real line, the model anchors on the threshold closest to avg_outs=17.2 "
      "(rounds to 17), not the highest-probability or highest-lift one", f"got {c['projection']}")
check(any("No real FanDuel line" in w for w in c["watchouts"]),
      "the no-real-line case carries an explicit watchout saying so")

head("3. THE BUG THIS FUNCTION REPLACES: when a real market line IS posted, it is used "
     "directly, even though it's near a coinflip and would never clear _pick_line's "
     "MIN_LINE_PROB=0.60 floor")

po_prices = {"framber valdez": {"needs": 17}}  # a real, coinflip-ish posted line at 17+
c_real_line = gp.score_pitcher_outs("Framber Valdez", 501, GM, "home", rates, po_prices=po_prices)
check(c_real_line["projection"]["needs"] == 17,
      "the real posted line (17+, priced near a coinflip at 55% model prob) is used "
      "directly, not excluded for failing to clear a 60% floor and replaced with an "
      "easier threshold", f"got {c_real_line['projection']}")
check(not any("No real FanDuel line" in w for w in c_real_line["watchouts"]),
      "when a real line WAS found, the no-real-line watchout is absent")

head("4. name matching for po_prices goes through odds_fanduel.normalize_name")

po_prices_case = {"framber valdez": {"needs": 18}}
c_case = gp.score_pitcher_outs("Framber Valdez", 501, GM, "home", rates, po_prices=po_prices_case)
check(c_case["projection"]["needs"] == 18,
      "the real line is matched via normalize_name, not a raw case-sensitive dict lookup "
      "(po_prices key is lowercase, sp_name passed in is mixed-case)", f"got {c_case['projection']}")

head("5. a real line at a threshold with no rate data for this pitcher falls back to workload anchor")

po_prices_missing = {"framber valdez": {"needs": 25}}  # 25+ not in this pitcher's opts (12-21 range)
c_missing = gp.score_pitcher_outs("Framber Valdez", 501, GM, "home", rates, po_prices=po_prices_missing)
check(c_missing["projection"]["needs"] == 17,
      "a real line at a threshold this pitcher has no rate for falls back to the "
      "workload-anchor selection, not a KeyError or a phantom threshold",
      f"got {c_missing['projection']}")

head("6. confidence-start floor: fewer than PITCHER_OUTS_SCORE_CONFIDENCE_STARTS caps the score")

thin_rates = outs_rates({17: (0.70, 0.35)}, starts=3, avg_outs=17.0, sp_id=502)
c_thin = gp.score_pitcher_outs("Rookie SP", 502, GM, "home", thin_rates)
check(c_thin["score"] <= 55,
      "only 3 real starts (under the 10-start confidence floor) caps the score at 55",
      f"got {c_thin['score']}")
check(any("real starts of workload history" in w for w in c_thin["watchouts"]),
      "the thin-starts case carries an explicit watchout")

thick_rates = outs_rates({17: (0.70, 0.35)}, starts=20, avg_outs=17.0, sp_id=503)
c_thick = gp.score_pitcher_outs("Vet SP", 503, GM, "home", thick_rates)
check(c_thick["score"] > c_thin["score"],
      "the identical rate/lift with 20 real starts scores strictly higher than the "
      "3-start version, purely from the confidence cap lifting",
      f"thick={c_thick['score']} thin={c_thin['score']}")

head("7. alternatives excludes the recommended line and caps at 3")

rates_many = outs_rates({t: (0.5 + t * 0.01, 0.4) for t in range(12, 22)}, avg_outs=16.0, sp_id=504)
c_many = gp.score_pitcher_outs("Many Lines SP", 504, GM, "home", rates_many)
alt_thresholds = [a["needs"] for a in c_many["alternatives"]]
check(c_many["projection"]["needs"] not in alt_thresholds,
      "the recommended threshold never appears in its own alternatives list")
check(len(c_many["alternatives"]) <= 3, "alternatives is capped at 3 entries",
      f"got {len(c_many['alternatives'])}")

head("8. away vs home side both resolve team correctly")

c_away = gp.score_pitcher_outs("JP Sears", 501, GM, "away", rates)
c_home = gp.score_pitcher_outs("Framber Valdez", 501, GM, "home", rates)
check(c_away["team"] == "Athletics" and c_home["team"] == "Astros",
      "side='away'/'home' resolve to the correct team, not swapped",
      f"away->{c_away['team']} home->{c_home['team']}")

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
