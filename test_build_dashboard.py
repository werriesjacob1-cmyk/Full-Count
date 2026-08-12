#!/usr/bin/env python3
"""test_build_dashboard.py — coverage for dashboard/build_dashboard.py's pure
functions (build_payload, render_html). Does NOT test run_live_fetch() --
that's a live re-run of generate_picks.py's real scoring pass (network calls
to MLB/Statcast/FanDuel), out of scope for a fast unit test the same way the
rest of this project never unit-tests a live fetcher directly. What's tested
here is the part that's actually pure logic: given a scored-candidates dict
shaped like run_live_fetch()'s real output, does the payload it builds for
the page (tabs, Top Picks ranking, the home_runs/moonshot dedup) come out
right, and does the HTML it renders actually embed that payload.

    /tmp/mlbvenv/bin/python3 test_build_dashboard.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/dashboard" if "/" in __file__ else "dashboard")

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


import build_dashboard as bd


def row(name, stat, prob, needs=1, value=0.5, odds=None, implied=None, edge=None,
       clears=None, confidence="Medium", ptype="batter"):
    return {
        "type": ptype, "name": name, "team": "Team", "matchup": "A @ B", "side": None,
        "prop": f"Over {value} {stat}", "projection": {"stat": stat, "value": value, "needs": needs},
        "lean": None, "score": 65.0, "confidence": confidence,
        "hit_probability": prob, "market_odds": odds, "market_implied": implied,
        "market_edge": edge, "price_clears": clears,
        "reliability": "B", "sample_n": 80, "why": [], "watchouts": [],
        "base_rate": None, "lift": None,
    }


head("1. Top Picks ranks by market_edge among price_clears==True only, capped at 10")

result = {
    "generated_at": "2026-08-12T20:00:00", "date": "2026-08-12",
    "hits": [
        row("Big Edge Clears", "hits", 0.70, odds=-150, implied=0.55, edge=0.15, clears=True),
        row("Small Edge Clears", "hits", 0.60, odds=-120, implied=0.58, edge=0.02, clears=True),
        row("Doesnt Clear", "hits", 0.90, odds=-800, implied=0.85, edge=0.05, clears=False),
        row("No Line At All", "hits", 0.55, odds=None, implied=None, edge=None, clears=None),
    ],
    "stolen_base": [
        row("Mid Edge Clears", "stolen_base", 0.35, odds=200, implied=0.28, edge=0.07, clears=True),
    ],
}
payload = bd.build_payload(result)
tp = payload["data"]["top_picks"]
check(len(tp) == 3, "only the 3 genuinely price_clears==True rows make Top Picks",
      f"got {[r['name'] for r in tp]}")
check([r["name"] for r in tp] == ["Big Edge Clears", "Mid Edge Clears", "Small Edge Clears"],
      "Top Picks is ordered by market_edge descending, not by probability",
      f"got {[r['name'] for r in tp]}")
check("Doesnt Clear" not in [r["name"] for r in tp],
      "a big-probability pick that fails price_clears is excluded from Top Picks")
check("No Line At All" not in [r["name"] for r in tp],
      "an unpriced candidate (price_clears is None, not True) is excluded from Top Picks")


head("2. Top Picks caps at 10 even when more than 10 clear")

many = {"generated_at": "x", "date": "2026-08-12",
       "hits": [row(f"Clearer {i}", "hits", 0.6, odds=-110, implied=0.5,
                    edge=0.10 - i * 0.001, clears=True) for i in range(15)]}
payload2 = bd.build_payload(many)
check(len(payload2["data"]["top_picks"]) == 10,
      "Top Picks never exceeds 10 even with 15 real qualifying candidates",
      f"got {len(payload2['data']['top_picks'])}")
check(payload2["data"]["top_picks"][0]["name"] == "Clearer 0",
      "still the genuinely highest-edge ones that make the cut")


head("3. home_runs is dropped as a duplicate of moonshot (same underlying field, per audit)")

dup = {
    "generated_at": "x", "date": "2026-08-12",
    "moonshot": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
    "home_runs": [row("Slugger", "home_runs", 0.20, needs=1, value=1)],
}
payload3 = bd.build_payload(dup)
check("home_runs" not in payload3["tabs_order"],
      "the raw 'home_runs' key never becomes its own tab",
      f"got {payload3['tabs_order']}")
check("moonshot" in payload3["tabs_order"],
      "moonshot is kept as the one real Home Runs tab")
check(payload3["labels"]["moonshot"] == "Home Runs",
      "moonshot's display label is still the human name, not the internal key")


head("4. tabs_order always starts with the two fixed tabs, then only categories with real rows")

payload4 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-12",
    "hits": [row("A", "hits", 0.7, odds=-200, implied=0.6, edge=0.1, clears=True)],
    "triples": [],  # present in the data but empty -- must not become a tab
})
check(payload4["tabs_order"][0] == "top_picks" and payload4["tabs_order"][1] == "all",
      "top_picks and all are always first, in that order", f"got {payload4['tabs_order'][:2]}")
check("triples" not in payload4["tabs_order"],
      "a category with zero real rows never becomes an empty tab")
check("hits" in payload4["tabs_order"],
      "a category with real rows does become a tab")


head("5. estimated_odds is computed for every priced row via the real prop_probability.american_odds")

import prop_probability as pp
payload5 = bd.build_payload({
    "generated_at": "x", "date": "2026-08-12",
    "hits": [row("A", "hits", 0.75, odds=-300, implied=0.7, edge=0.05, clears=True)],
})
a_row = payload5["data"]["hits"][0]
check(a_row["estimated_odds"] == pp.american_odds(0.75),
      "estimated_odds matches the real american_odds() function directly, not a reimplementation",
      f"got {a_row['estimated_odds']}, expected {pp.american_odds(0.75)}")


head("6. render_html embeds the payload and every font placeholder is substituted")

fonts = {"archivo": "QUJD", "plexsans": "REVG", "plexmono500": "R0hJ", "plexmono600": "SktM"}
html = bd.render_html(payload5, fonts)
check("{payload_json}" not in html and "{archivo}" not in html,
      "no leftover template placeholders survive into the rendered page")
check("QUJD" in html and "REVG" in html and "R0hJ" in html and "SktM" in html,
      "all four font payloads actually landed in the output")
embedded = json.loads(html.split("const PAYLOAD = ", 1)[1].split(";\n", 1)[0])
check(embedded["data"]["hits"][0]["name"] == "A",
      "the embedded PAYLOAD is genuinely the same data build_payload produced, not a stale copy")
check(html.count("<style>") == 1 and html.count("</style>") == 1,
      "style block is well-formed (exactly one open/close)")
check(html.count("<script>") == 1 and html.count("</script>") == 1,
      "script block is well-formed (exactly one open/close)")


head("7. priced candidates rank ahead of unpriced ones within a tab, and in All Props -- "
     "even when the unpriced one has the higher raw probability")

result7 = {
    "generated_at": "x", "date": "2026-08-12",
    "pitcher_outs": [
        # No real FanDuel line yet (market_odds=None) -- found live 2026-08-12:
        # David Peterson's Outs Recorded read (63.2%, no line) sorted above
        # several real, priced, lower-probability candidates for exactly this
        # reason, reading as a recommended pick that wasn't actually a bet
        # anyone could place.
        row("No Line High Prob", "pitcher_outs", 0.90, odds=None, implied=None, edge=None, clears=None),
        row("Priced Lower Prob", "pitcher_outs", 0.60, odds=-140, implied=0.58, edge=0.02, clears=True),
    ],
}
payload7 = bd.build_payload(result7)
tab_names = [r["name"] for r in payload7["data"]["pitcher_outs"]]
check(tab_names == ["Priced Lower Prob", "No Line High Prob"],
      "within a tab, a real-priced candidate ranks first even against a higher-probability "
      "unpriced one", f"got {tab_names}")
all_names = [r["name"] for r in payload7["data"]["all"]]
check(all_names == ["Priced Lower Prob", "No Line High Prob"],
      "the same priced-first rule applies to the All Props tab", f"got {all_names}")


head("8. load_track_record reads the real main_hit_rate, not the blended overall one, "
     "and degrades to None honestly rather than a fabricated 0%")

with tempfile.TemporaryDirectory() as td:
    hist_path = os.path.join(td, "history.json")
    with open(hist_path, "w") as f:
        json.dump({
            "overall_hit_rate": 0.452, "main_hit_rate": 0.553,
            "last_14_days_hit_rate": 0.452,
            "by_category_totals": {"main": {"hits": 26, "misses": 21, "ungraded": 0}},
        }, f)
    tr = bd.load_track_record(hist_path)
    check(tr["main_hit_rate"] == 0.553,
          "reads main_hit_rate, not overall_hit_rate", f"got {tr}")
    check(tr["main_n"] == 47, "main_n is hits+misses from by_category_totals.main", f"got {tr}")

    empty_path = os.path.join(td, "empty.json")
    with open(empty_path, "w") as f:
        json.dump({"main_hit_rate": None, "by_category_totals": {}}, f)
    check(bd.load_track_record(empty_path) is None,
          "no graded main-board picks yet returns None, not a fabricated 0% record")

    check(bd.load_track_record(os.path.join(td, "does_not_exist.json")) is None,
          "a missing history.json returns None rather than crashing the build")


head("9. suggested parlay: real correlation-screened legs via the actual parlay_builder.py "
     "engine, honest None when fewer than 2 real legs exist")

def parlay_candidate(name, team, matchup, game_pk, player_id, prop, stat, prob, odds, conf="High"):
    return {
        "name": name, "team": team, "matchup": matchup, "game_pk": game_pk,
        "type": "batter", "player_id": player_id, "prop": prop, "side": None,
        "projection": {"stat": stat, "value": 0.5, "needs": 1},
        "hit_probability": prob, "market_odds": odds, "market_implied": None,
        "confidence": conf, "price_clears": True,
    }

check(bd._decimal_to_american(2.0) == 100, "decimal 2.0 (even money) is +100")
check(bd._decimal_to_american(1.5) == -200, "decimal 1.5 is -200")
check(bd._decimal_to_american(None) is None, "a missing decimal price returns None, not a crash")

three_legs = [
    parlay_candidate("A", "T1", "T1 @ T2", 1, 101, "Over 0.5 Hits", "hits", 0.72, -150),
    parlay_candidate("B", "T3", "T3 @ T4", 2, 102, "Over 0.5 Total Bases", "total_bases", 0.68, -130),
    parlay_candidate("C", "T5", "T5 @ T6", 3, 103, "Over 0.5 Runs", "runs", 0.65, -120, conf="Medium"),
]
sp = bd._build_suggested_parlay(three_legs)
check(sp is not None and len(sp["legs"]) >= 2,
      "a real slate with enough independent legs produces a real suggested parlay",
      f"got {sp}")
if sp:
    check(sp["combined_american_odds"] is not None,
          "combined_american_odds is populated when every leg is priced", f"got {sp}")
    check("naive_probability_note" in sp and sp["naive_probability_note"],
          "the honest 'not a final answer' caveat travels with the parlay, not just the number")

check(bd._build_suggested_parlay([three_legs[0]]) is None,
      "a single real leg is NOT dressed up as a parlay -- honest None instead")
check(bd._build_suggested_parlay([]) is None,
      "no candidates at all returns None, not an empty/fabricated parlay")


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
