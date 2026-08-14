#!/usr/bin/env python3
"""test_select_deep_moonshots.py — coverage for generate_picks.select_
deep_moonshots(), the "To Hit a Moonshot (420+ FT)" board category, built
2026-08-14 after the user's own FanDuel screenshot confirmed the market is
real. Unlike select_moonshots (home_runs), score_moonshot already builds
its own standalone candidate with a real projection.stat -- so this
selects directly from `candidates`, not from a batter's line_options.

    /tmp/mlbvenv/bin/python3 test_select_deep_moonshots.py
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
import odds_fanduel as fd


def moonshot_c(name="Slugger", prob=0.08, score=25.0, player_id=5, **over):
    c = {
        "type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
        "matchup": "Athletics @ Astros", "game_pk": 900001, "score": score,
        "confidence": "Low", "notable_signals": 1, "signals": {},
        "prop": "To Hit a Moonshot (420+ FT)",
        "projection": {"stat": "moonshot_420", "value": 1, "needs": 1},
        "hit_probability": prob,
    }
    c.update(over)
    return c


head("1. a pitcher candidate is never eligible, regardless of its own fields")

pitcher_c = {"type": "pitcher", "name": "Some SP", "score": 90,
            "projection": {"stat": "moonshot_420", "value": 1, "needs": 1},
            "hit_probability": 0.5}
check(gp.select_deep_moonshots([pitcher_c], {}, fd) == [],
      "only type='batter' candidates are ever considered")

head("2. a batter candidate for a DIFFERENT stat is skipped -- this function must not "
     "accidentally scoop up hits/home_runs/laser candidates")

wrong_stat = moonshot_c()
wrong_stat["projection"] = {"stat": "hard_hit_105", "value": 1, "needs": 1}
check(gp.select_deep_moonshots([wrong_stat], {}, fd) == [],
      "a batter candidate whose projection.stat isn't moonshot_420 is excluded")

head("3. a moonshot_420 candidate with hit_probability=None is skipped, not a crash")

no_prob = moonshot_c()
no_prob["hit_probability"] = None
check(gp.select_deep_moonshots([no_prob], {}, fd) == [],
      "a candidate with no computed probability is excluded honestly, not defaulted to 0")

head("4. NO MIN_QUALITY_SCORE gate -- deliberately, same reasoning select_moonshots gives: "
     "a quality floor built for the 5-category batter formula would exclude every real "
     "candidate here, since score_moonshot's own score is scaled off this exact rare "
     "probability")

low_score = moonshot_c(score=5.0)  # far under MIN_QUALITY_SCORE (55)
out = gp.select_deep_moonshots([low_score], {}, fd)
check(len(out) == 1, "a candidate with score=5 (far under MIN_QUALITY_SCORE) is still "
      "included -- this category is deliberately not gated by it", f"got {out}")

head("5. a well-formed selection carries the real probability and a 'moonshot_420' category tag")

out = gp.select_deep_moonshots([moonshot_c(prob=0.09)], {}, fd)
check(len(out) == 1, "one qualifying batter produces one entry")
check(out[0]["hit_probability"] == 0.09, "hit_probability passes through unchanged")
check(out[0]["category"] == "moonshot_420", "category is tagged 'moonshot_420', distinct from "
      "select_moonshots' plain 'moonshot' tag so the two markets are never confused downstream")

head("6. real market price lookup via fd.normalize_name, keyed on (moonshot_420, 1), and "
     "price_clears computed against it")

prices = {fd.normalize_name("Aaron Judge Jr."): {("moonshot_420", 1): 900}}
c_priced = moonshot_c(name="Aaron Judge Jr.", prob=0.11)
out = gp.select_deep_moonshots([c_priced], prices, fd)
check(out[0]["market_odds"] == 900, "market odds are found via normalize_name matching "
      "('Aaron Judge Jr.' -> the suffix-stripped key)", f"got {out[0]['market_odds']}")
check(out[0]["market_implied"] is not None, "market_implied is computed from the real odds")

c_unpriced = moonshot_c(name="Nobody Priced", prob=0.11)
out2 = gp.select_deep_moonshots([c_unpriced], prices, fd)
check(out2[0]["market_odds"] is None and out2[0]["market_implied"] is None,
      "a batter with no matching FanDuel price gets market_odds=None honestly, not a "
      "fabricated number")

head("7. ranked by hit_probability descending, and truncated to n")

batters = [moonshot_c(name=f"B{i}", player_id=i, prob=p)
           for i, p in enumerate([0.06, 0.03, 0.05, 0.09, 0.02, 0.08], start=1)]
out = gp.select_deep_moonshots(batters, {}, fd, n=3)
check(len(out) == 3, "the result is truncated to n=3 even though 6 batters qualified")
check([o["hit_probability"] for o in out] == [0.09, 0.08, 0.06],
      "the top 3 by hit_probability descending are kept, in the right order",
      f"got {[o['hit_probability'] for o in out]}")

head("8. an empty candidate list returns an empty list")

check(gp.select_deep_moonshots([], {}, fd) == [], "no candidates at all returns an empty list")

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
