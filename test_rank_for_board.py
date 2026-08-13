#!/usr/bin/env python3
"""test_rank_for_board.py — direct coverage for generate_picks.rank_for_board(),
the function that orders the gated candidate pool into the top10 board.

Locks in a real bug found 2026-08-13 checking the actual graded record: this
used to sort every priced candidate by raw hit_probability alone, and never
read price_clears (computed on every candidate, but pure display metadata
until this fix). Probability and "how short the market has already priced
it" are highly correlated, so probability-only ranking systematically
promoted heavily-juiced, no-edge favorites. Confirmed against the real
graded record: 54 of the last 57 main-board picks (08-07..08-12) carried
price_clears=False and shipped anyway -- average price -254 (needs 71.3%
to break even), actual hit rate 56.1%, real flat-stake ROI -22.1%.

    /tmp/mlbvenv/bin/python3 test_rank_for_board.py
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


def cand(name, prob=0.65, odds=-250, edge=-0.09, clears=None, reliability="A", score=70.0):
    return {
        "name": name, "type": "batter", "score": score, "reliability": reliability,
        "hit_probability": prob, "market_odds": odds, "market_edge": edge,
        "price_clears": clears,
    }


head("1. a real-edge (price_clears=True) pick outranks a heavily-juiced, "
     "no-edge favorite -- the exact bug found live 2026-08-13")

no_edge_favorite = cand("Chalk Favorite", prob=0.72, odds=-350, edge=-0.13, clears=False)
real_edge_dog = cand("Real Value", prob=0.55, odds=150, edge=0.06, clears=True)
ranked = gp.rank_for_board([no_edge_favorite, real_edge_dog])
check([r["name"] for r in ranked] == ["Real Value", "Chalk Favorite"],
      "the lower-probability, positive-edge pick ranks first, not the higher-probability "
      "no-edge favorite", f"got {[r['name'] for r in ranked]}")


head("2. within the real-edge tier, ranked by market_edge, not raw probability")

small_edge_high_prob = cand("A", prob=0.70, odds=-200, edge=0.02, clears=True)
big_edge_lower_prob = cand("B", prob=0.58, odds=120, edge=0.09, clears=True)
ranked2 = gp.rank_for_board([small_edge_high_prob, big_edge_lower_prob])
check([r["name"] for r in ranked2] == ["B", "A"],
      "the bigger real edge ranks first even with lower raw probability",
      f"got {[r['name'] for r in ranked2]}")


head("3. price_clears=None (no market price to check) is NOT treated as real edge")

unclear = cand("No Line", prob=0.75, odds=None, edge=None, clears=None)
real_edge = cand("Clears", prob=0.60, odds=-110, edge=0.03, clears=True)
ranked3 = gp.rank_for_board([unclear, real_edge])
check(ranked3[0]["name"] == "Clears",
      "a genuinely price-clearing pick outranks an unpriced high-probability one",
      f"got {[r['name'] for r in ranked3]}")


head("4. reliability still gates ahead of edge -- evidence before confidence is preserved")

thin_sample_big_edge = cand("Thin Sample", prob=0.60, odds=150, edge=0.15, clears=True, reliability="D")
real_sample_smaller_edge = cand("Real Sample", prob=0.58, odds=120, edge=0.05, clears=True, reliability="A")
ranked4 = gp.rank_for_board([thin_sample_big_edge, real_sample_smaller_edge])
check(ranked4[0]["name"] == "Real Sample",
      "a grade-A pick still outranks a grade-D pick even with a smaller edge",
      f"got {[r['name'] for r in ranked4]}")


head("5. unpriced candidates (hit_probability is None) fall to the very end, "
     "kept only as a fallback")

unpriced = {"name": "No Probability", "type": "batter", "score": 90.0,
            "reliability": "A", "hit_probability": None, "market_odds": None,
            "market_edge": None, "price_clears": None}
priced_no_edge = cand("Priced No Edge", prob=0.55, odds=-180, edge=-0.04, clears=False)
ranked5 = gp.rank_for_board([unpriced, priced_no_edge])
check([r["name"] for r in ranked5] == ["Priced No Edge", "No Probability"],
      "any priced candidate outranks an unpriced one regardless of its 0-100 score",
      f"got {[r['name'] for r in ranked5]}")


head("6. an empty pool doesn't crash")

check(gp.rank_for_board([]) == [], "empty input returns empty output")


head("7. select_main_board -- real edge only, board 2026-08-13")

head("7a. no-edge picks are excluded entirely, not used to pad the board")

only_no_edge = [cand("Chalk A", clears=False), cand("Chalk B", clears=False)]
check(gp.select_main_board(gp.rank_for_board(only_no_edge)) == [],
      "a pool of only no-edge candidates selects an empty board, not a padded one")

head("7b. unpriced (price_clears=None) candidates are also excluded, not a confirmed edge")

unpriced_only = [{"name": "No Line", "type": "batter", "score": 90.0, "reliability": "A",
                  "hit_probability": None, "market_odds": None, "market_edge": None,
                  "price_clears": None}]
check(gp.select_main_board(gp.rank_for_board(unpriced_only)) == [],
      "an unconfirmed edge does not fill a board slot")

head("7c. a mixed pool keeps only the genuinely clearing picks, in rank order")

clears_a = cand("Clears A", prob=0.60, odds=-110, edge=0.04, clears=True)
clears_b = cand("Clears B", prob=0.58, odds=130, edge=0.08, clears=True)
no_edge = cand("No Edge", prob=0.75, odds=-400, edge=-0.15, clears=False)
board = gp.select_main_board(gp.rank_for_board([clears_a, clears_b, no_edge]))
check([c["name"] for c in board] == ["Clears B", "Clears A"],
      "only the two clearing picks ship, ordered by edge, and the heavily-juiced "
      "no-edge favorite does not fill the remaining slot",
      f"got {[c['name'] for c in board]}")

head("7d. caps at n even when more than n picks clear")

many_clears = [cand(f"C{i}", prob=0.55 + i * 0.01, odds=120, edge=0.05 + i * 0.01, clears=True)
               for i in range(12)]
board2 = gp.select_main_board(gp.rank_for_board(many_clears), n=10)
check(len(board2) == 10, "board is capped at n even with 12 clearing candidates",
      f"got {len(board2)}")


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
