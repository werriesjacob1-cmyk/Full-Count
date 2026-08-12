#!/usr/bin/env python3
"""test_value_board.py — direct coverage for value_board.screen(), the tier
assignment that decides which props actually reach "the board that gets
bet" (its own docstring's words). Like grade_value.py, this had zero direct
test coverage before this file, despite being a real trust boundary: a
mis-assigned tier is either a bad bet reaching the top of the board or a
real edge getting buried in "near".

Four cases below are picked to land in each of the four tiers and verified
by hand against the real prop_probability.value_verdict/market_agreement
outputs before being locked in here (not guessed -- see the comment on
each). screen() only has two real dependents (they're pure functions of
its own inputs), so no mocking is needed; this exercises the real thing.

    /tmp/mlbvenv/bin/python3 test_value_board.py
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


import value_board as vb
import prop_probability as pp

MIN_ROI = 0.05


def _row(name, player, stat, needs, american, prob, prob_lo):
    return (name, stat, needs), {"player": player, "stat": stat, "needs": needs,
                                  "american": american, "prob": prob, "prob_lo": prob_lo}


# Verified by hand against pp.value_verdict/pp.market_agreement directly
# before writing this test (see the docstring):
#   A: prob=0.30 @ +285, prob_lo=0.26 -> roi=+0.1550, robust=True,  agreement=LEAN
#   B: prob=0.65 @ -140, prob_lo=0.60 -> roi=+0.1143, robust=True,  agreement=SUSPECT
#   C: prob=0.62 @ -140, prob_lo=0.50 -> roi=+0.0629, robust=False, agreement=SUSPECT
#   D: prob=0.50 @ -140, prob_lo=0.45 -> roi=-0.1429, robust=False, agreement=LEAN
# A is the rare real case: clearing 5% ROI at a LEAN/AGREE-level market
# disagreement needs a longer price, since a short price's de-vigged fair
# probability leaves too little room between "no edge" and "SUSPECT".

head("1. tier assignment -- each of A/B/C/D lands where hand-verified")

entries = dict([
    _row("A", "Player A", "home_runs", 1, 285, 0.30, 0.26),
    _row("B", "Player B", "hits", 1, -140, 0.65, 0.60),
    _row("C", "Player C", "hits", 1, -140, 0.62, 0.50),
    _row("D", "Player D", "hits", 1, -140, 0.50, 0.45),
])
bets, near, rejected = vb.screen(entries, min_roi=MIN_ROI, require_robust=True)

by_player = {r["player"]: r for r in bets + near + rejected}
check(by_player["Player A"]["tier"] == "A",
      "clears ROI + robust + LEAN agreement -> tier A",
      f"got {by_player['Player A']['tier']}")
check(by_player["Player B"]["tier"] == "B",
      "clears ROI + robust + SUSPECT agreement -> tier B (still a bet, sized down)",
      f"got {by_player['Player B']['tier']}")
check(by_player["Player C"]["tier"] == "C",
      "clears ROI but NOT robust (fails at the pessimistic end) -> tier C (near, not a bet)",
      f"got {by_player['Player C']['tier']}")
check(by_player["Player D"]["tier"] == "D",
      "negative ROI -> tier D (rejected)",
      f"got {by_player['Player D']['tier']}")

check({r["player"] for r in bets} == {"Player A", "Player B"},
      "only A/B tiers land in the actual bets list",
      f"got {[r['player'] for r in bets]}")
check([r["player"] for r in near] == ["Player C"],
      "tier C lands in near, not bets",
      f"got {[r['player'] for r in near]}")
check([r["player"] for r in rejected] == ["Player D"],
      "tier D lands in rejected",
      f"got {[r['player'] for r in rejected]}")

head("2. sort order within bets: tier A before tier B")

check([r["player"] for r in bets] == ["Player A", "Player B"],
      "bets are sorted A before B (tier value 2 before 1), not by roi alone",
      f"got {[r['player'] for r in bets]}")

head("3. require_robust=False ignores the pessimistic-end check")

entries_c_only = dict([_row("C", "Player C", "hits", 1, -140, 0.62, 0.50)])
bets2, near2, rejected2 = vb.screen(entries_c_only, min_roi=MIN_ROI, require_robust=False)
check(bets2 and bets2[0]["player"] == "Player C",
      "with require_robust=False, Player C's positive ROI alone is enough to bet",
      f"bets={[r['player'] for r in bets2]}")

head("4. reject_suspect=False promotes a SUSPECT-but-clearing bet to tier A")

entries_b_only = dict([_row("B", "Player B", "hits", 1, -140, 0.65, 0.60)])
bets3, _, _ = vb.screen(entries_b_only, min_roi=MIN_ROI, require_robust=True,
                        reject_suspect=False)
check(bets3[0]["tier"] == "A",
      "reject_suspect=False lets a SUSPECT-agreement bet reach tier A instead of B",
      f"got {bets3[0]['tier']}")

head("5. an empty screen doesn't raise")

b4, n4, r4 = vb.screen({}, min_roi=MIN_ROI)
check(b4 == [] and n4 == [] and r4 == [], "an empty entries dict returns three empty lists")

head("6. every row carries the arithmetic it was judged on, not just the verdict")

row_a = by_player["Player A"]
check(row_a.get("agreement") == "LEAN" and row_a.get("roi") is not None,
      "each row keeps its own agreement/roi/tier_note for display, not just pass/fail")
check(row_a.get("tier_note") == vb.TIER_NOTE["A"],
      "tier_note matches the real TIER_NOTE text for the assigned tier")

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
