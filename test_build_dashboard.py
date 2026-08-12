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
import sys

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
