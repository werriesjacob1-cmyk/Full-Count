#!/usr/bin/env python3
"""test_render_parlay.py — checks render_parlay.py's HTML output against
hand-built parlay results, focused on the exact bug class already found
once in render_board.py (HTML being double-escaped into literal text).

    /tmp/mlbvenv/bin/python3 test_render_parlay.py
    python3 test_render_parlay.py -v
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import parlay_builder as pb
import render_parlay as rp

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


head("1. a filled, fully-priced parlay")

leg_a = {"name": "A. Batter", "team": "Mets", "matchup": "Mets @ Pirates", "game_pk": 1,
        "type": "batter", "player_id": "a", "projection": {"stat": "hits"},
        "prop": "Over 0.5 Hits", "hit_probability": 0.7, "market_odds": -260}
leg_b = {"name": "B. Batter", "team": "Mets", "matchup": "Mets @ Pirates", "game_pk": 1,
        "type": "batter", "player_id": "b", "projection": {"stat": "total_bases"},
        "prop": "Over 1.5 Total Bases", "hit_probability": 0.55, "market_odds": -120}

req = pb.ParlayRequest(prop_counts={"hits": 1, "total_bases": 1}, risk_tier="safest", stake=10)
result = {
    "request": req, "legs": [leg_a, leg_b], "shortfalls": [],
    "naive_combined_probability": 0.385,
    "naive_probability_note": "floor note",
    "combined_decimal_odds": 2.5, "stake": 10.0,
    "estimated_payout_if_priced": 25.0,
    "correlation_notes": [f"{leg_a['name']} + {leg_b['name']}: same team, same game"],
}
out = rp.render(result, request_text="2 legs, safest")

check("&lt;span" not in out and "&quot;" not in out,
      "no double-escaped HTML leaks into visible text (the exact bug already "
      "found once in render_board.py's price badge)",
      out[:200])
check(out.count("<li") == out.count("</li>") and out.count("<div") == out.count("</div>"),
      "tags balance (li and div)")
check("A. Batter" in out and "B. Batter" in out, "both leg names appear")
check("$25.00" in out, "the real payout figure renders")
check("same team, same game" in out, "the correlation note renders as real text")

head("2. an empty parlay (no legs filled)")

empty_result = {
    "request": pb.ParlayRequest(prop_counts={"triples": 3}, risk_tier="safest"),
    "legs": [], "shortfalls": [{"stat": "triples", "requested": 3, "found": 0}],
    "naive_combined_probability": None, "naive_probability_note": None,
    "combined_decimal_odds": None, "stake": None, "estimated_payout_if_priced": None,
    "correlation_notes": [],
}
out2 = rp.render(empty_result, request_text="5 triples")
check("No legs could be filled" in out2, "an empty result shows the honest empty state, not a blank slip")
check("triples" in out2, "the shortfall reason (which stat, how many) renders")
check("&lt;" not in out2, "no escaping leaks in the empty-state path either")

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
