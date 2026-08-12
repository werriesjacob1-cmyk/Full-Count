#!/usr/bin/env python3
"""test_final_card.py — direct coverage for final_card.py's parse_odds() and
evaluate(), the functions that turn Jacob's typed-in real prices into a
BET/NO-BET verdict per pick. Had zero test coverage. parse_odds() in
particular has real, easy-to-get-wrong text parsing (rank vs name matching,
partial-name matching, "skip" tokens, unmatched lines) where a silent
mismatch would misprice or drop a real bet.

    /tmp/mlbvenv/bin/python3 test_final_card.py
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


import final_card as fc
import prop_probability as pp

PICKS = [
    {"rank": 1, "name": "Bobby Witt Jr.", "hit_probability": 0.65},
    {"rank": 2, "name": "Yordan Alvarez", "hit_probability": 0.55},
    {"rank": 3, "name": "Aaron Judge", "hit_probability": 0.72},
]

head("1. parse_odds: matching by rank")

mapping, problems = fc.parse_odds("1: -150\n2: -140\n3: +120", PICKS)
check(mapping == {1: -150, 2: -140, 3: 120} and not problems,
      "three lines matched by bare rank number, no problems reported", f"got {mapping}, {problems}")

head("2. parse_odds: matching by full name and partial name")

mapping, problems = fc.parse_odds("Bobby Witt Jr. -150\nWitt -145", [PICKS[0]])
check(mapping == {1: -145} and not problems,
      "a full-name line then a partial-name line for the SAME pick: the later line wins "
      "(later lines overwrite earlier ones in the mapping, same as odds_fanduel's "
      "closing-price convention)", f"got {mapping}")

mapping, problems = fc.parse_odds("Alvarez -140", PICKS)
check(mapping.get(2) == -140, "a partial name ('Alvarez' for 'Yordan Alvarez') resolves uniquely",
      f"got {mapping}")

head("3. parse_odds: ambiguous partial match is reported, not guessed")

ambiguous_picks = [{"rank": 1, "name": "Bobby Witt Jr."}, {"rank": 2, "name": "Bobby Miller"}]
mapping, problems = fc.parse_odds("Bobby -150", ambiguous_picks)
check(1 not in mapping and 2 not in mapping,
      "an ambiguous partial match ('Bobby' matches two picks) is NOT silently assigned to either")
check(any("matches" in p and "2 picks" in p for p in problems),
      "the ambiguity is reported as a problem, not silently dropped", f"got {problems}")

head("4. parse_odds: skip tokens and unmatched lines")

mapping, problems = fc.parse_odds("1: skip\n2: n/a\n3: unavailable", PICKS)
check(mapping == {1: None, 2: None, 3: None},
      "skip/n-a/unavailable tokens map to None (no price), not dropped or zero", f"got {mapping}")

mapping, problems = fc.parse_odds("1: not a real price line", PICKS)
check(1 not in mapping and any("could not find a price" in p for p in problems),
      "a line with no parseable price and no skip token is reported as a problem")

mapping, problems = fc.parse_odds("99: -150", PICKS)
check(99 not in mapping.values() and not mapping and any("no pick matches" in p for p in problems),
      "a rank/name that matches no real pick is reported, not silently created", f"got {mapping}, {problems}")

head("5. parse_odds: comments and blank lines are ignored")

mapping, problems = fc.parse_odds("# this is a header\n\n1: -150  # trailing comment\n", PICKS)
check(mapping == {1: -150} and not problems,
      "a comment-only line, a blank line, and a trailing comment are all handled cleanly",
      f"got {mapping}, {problems}")

head("6. evaluate: the three real verdict branches, hand-verified against prop_probability")

# prob=0.65, generous user_limit -> fair-value limit -186; a posted price
# BETTER than that (-150) clears with real positive edge.
r = fc.evaluate([PICKS[0]], {1: -150}, user_limit=-1000, margin=0.0)[0]
check(r["verdict"] == "BET" and "edge" in r["note"],
      "a price better than the fair-value limit clears as BET with a real edge note", f"got {r}")

# Same prob=0.65 but a TIGHT user_limit=-150 makes -170 fail on the user's
# own price rule despite having real edge (implied(-170)=0.630 < prob=0.65).
r = fc.evaluate([PICKS[0]], {1: -170}, user_limit=-150, margin=0.0)[0]
check(r["verdict"] == "NO BET" and "price rule rejected this, not the model" in r["note"],
      "a price with real edge that still fails the USER's price limit is correctly "
      "attributed to the price rule, not the model", f"got {r}")

# prob=0.55, generous user_limit -> fair-value limit -122; a posted price of
# -150 implies 60% > the model's 55%, genuine negative expectation.
r = fc.evaluate([PICKS[1]], {2: -150}, user_limit=-1000, margin=0.0)[0]
check(r["verdict"] == "NO BET" and "negative expectation" in r["note"],
      "a price implying MORE than the model's own probability is correctly "
      "attributed to negative expectation, not the price rule", f"got {r}")

head("7. evaluate: PENDING / no-probability / not-offered")

r = fc.evaluate([PICKS[0]], {}, user_limit=-1000, margin=0.0)[0]
check(r["verdict"] == "PENDING", "a pick with no price entered yet is PENDING, not a default verdict")

r = fc.evaluate([{"rank": 1, "name": "X", "hit_probability": None}], {1: -150},
               user_limit=-1000, margin=0.0)[0]
check(r["verdict"] == "NO BET" and "no calibrated probability" in r["note"],
      "a pick with no calibrated probability at all is NO BET, explicitly, not skipped silently")

r = fc.evaluate([PICKS[0]], {1: None}, user_limit=-1000, margin=0.0)[0]
check(r["verdict"] == "NO BET" and "not offered" in r["note"],
      "an explicit 'skip' entry (None in the odds map) reports 'not offered at the book'")

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
