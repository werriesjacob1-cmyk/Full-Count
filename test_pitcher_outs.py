#!/usr/bin/env python3
"""test_pitcher_outs.py — locks in score_pitcher_outs' real-line-vs-average-
anchor selection (task list item: "Fix pitcher_outs line-selection mismatch
vs real FanDuel lines").

THE BUG THIS GUARDS AGAINST. Pitcher Outs Recorded posts exactly ONE real
line per starter, set by the book near his own median workload -- close to
a coinflip by construction. _pick_line's floor-then-lift search (right for
markets where the book posts SEVERAL thresholds, like hits/strikeouts)
guarantees the real line is EXCLUDED and some easier, lower threshold wins
instead -- a real run once recommended "Over 11.5 Outs" the same night
FanDuel's actual market was 17.5, a number the model never priced, and
because market_odds only attaches when the recommended `needs` matches a
real posted line, that mismatch also meant the pick carried no price at
all. score_pitcher_outs fixed this by using the real posted line directly
whenever one is available, and only falling back to the pitcher's own
average-workload anchor when it isn't. Verified live before this file
existed; this file makes that verification permanent.

    /tmp/mlbvenv/bin/python3 test_pitcher_outs.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

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


GM = {"away_team": "Away", "home_team": "Home", "matchup": "Away @ Home", "game_pk": 1}

# A pitcher whose own rate table would self-select an easier, lower
# threshold if left to probability alone (rates fall off steeply above his
# own average of 15 outs/start).
OUTS_RATES = {
    123: {"starts": 10, "avg_outs": 15,
          "rates": {f"outs_{t}plus": {"p_hat": max(0.05, 0.9 - (t - 12) * 0.09),
                                      "p": 0.5, "n": 10, "hit": 5}
                    for t in range(12, 22)}}
}

# Real FanDuel line, well above the pitcher's own average -- the exact
# shape of the real Skenes/Perez lines the bug's own docstring cites.
PO_PRICES = {"sample pitcher": {"line": 17.5, "needs": 18, "over": -144, "under": 108,
                                "true_over": 0.58, "true_under": 0.44, "hold": 0.02}}

c_real_line = gp.score_pitcher_outs("Sample Pitcher", 123, GM, "away", OUTS_RATES,
                                    po_prices=PO_PRICES)
check(c_real_line is not None, "score_pitcher_outs returns a candidate when a real line exists")
check(c_real_line["projection"]["needs"] == 18,
      "recommends the REAL posted line (18), not a self-selected easier threshold",
      f"got needs={c_real_line['projection']['needs']}")
check(c_real_line["prop"] == "Over 17.5 Outs Recorded",
      "prop text matches the real posted line",
      f"got {c_real_line['prop']!r}")
check("No real FanDuel line" not in " ".join(c_real_line.get("watchouts", [])),
      "no 'no real line found' watchout when a real line WAS used")

c_no_line = gp.score_pitcher_outs("Sample Pitcher", 123, GM, "away", OUTS_RATES, po_prices={})
check(c_no_line is not None, "score_pitcher_outs still returns a candidate with no real line")
check(c_no_line["projection"]["needs"] == 15,
      "falls back to the pitcher's own average-workload anchor (15) when no real line exists",
      f"got needs={c_no_line['projection']['needs']}")
check(any("No real FanDuel line" in w for w in c_no_line.get("watchouts", [])),
      "flags the fallback with a watchout so it's visibly model-anchored, not a posted market number")

# A real line for a DIFFERENT pitcher must not leak onto this one (name
# matching has to be exact via odds_fanduel.normalize_name, not "any price
# in the dict").
c_wrong_pitcher = gp.score_pitcher_outs("Sample Pitcher", 123, GM, "away", OUTS_RATES,
                                        po_prices={"someone else": PO_PRICES["sample pitcher"]})
check(c_wrong_pitcher["projection"]["needs"] == 15,
      "a real line keyed to a DIFFERENT pitcher's name doesn't leak onto this one",
      f"got needs={c_wrong_pitcher['projection']['needs']}")

# A real line outside the 12-21 range this rate table computes (FanDuel's
# usual territory per score_pitcher_outs' own docstring) has no matching
# threshold to select -- must degrade to the average anchor, not crash.
c_out_of_range = gp.score_pitcher_outs("Sample Pitcher", 123, GM, "away", OUTS_RATES,
                                       po_prices={"sample pitcher": {**PO_PRICES["sample pitcher"],
                                                                      "needs": 25}})
check(c_out_of_range["projection"]["needs"] == 15,
      "a real line outside the computed 12-21 threshold range falls back to the average anchor",
      f"got needs={c_out_of_range['projection']['needs']}")

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
