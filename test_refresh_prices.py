#!/usr/bin/env python3
"""test_refresh_prices.py — coverage for dashboard/refresh_prices.py, the
lightweight repricing-only refresh. Direct request: "I want all props to
update with new odds as FanDuel changes them, and compute in real time
the edge and whether it keeps it on the top 10."

Mocks every odds_fanduel fetch (same pattern as test_il_returns.py) rather
than hitting FanDuel -- this tests the merge/recompute logic, not the
network layer, which odds_fanduel.py's own tests already cover.

    /tmp/mlbvenv/bin/python3 test_refresh_prices.py
"""
import json
import os
import sys
import tempfile
import unittest.mock as mock

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


import refresh_prices as rp
import odds_fanduel as fd


def row(name, prop, stat, needs, prob, matchup="A @ B", lean=None):
    return {"name": name, "prop": prop, "matchup": matchup, "lean": lean,
            "projection": {"stat": stat, "needs": needs}, "hit_probability": prob,
            "market_odds": None, "market_implied": None, "market_edge": None, "price_clears": None}


head("1. refresh() reprices every row in 'all' against fresh FanDuel prices")

r1 = row("Aaron Judge", "Over 0.5 Hits", "hits", 1, 0.70)
payload1 = {"generated_at": "2026-08-14T18:00:00", "data": {"all": [r1], "hits": [dict(r1)],
           "top_picks": []}}
path1 = tempfile.mktemp(suffix=".json")
json.dump(payload1, open(path1, "w"))

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    mp.return_value = {fd.normalize_name("Aaron Judge"): {("hits", 1): -150}}
    out1 = rp.refresh(path1)

check(out1["data"]["all"][0]["market_odds"] == -150, "the 'all' row picked up the fresh price",
      f"got {out1['data']['all'][0]}")
check(out1["data"]["all"][0]["price_clears"] is not None, "price_clears got recomputed, not left None")

head("2. the same field updates propagate into every OTHER tab, matched by (name, prop) -- "
     "these are separate dict objects after a JSON round-trip, not shared references")

check(out1["data"]["hits"][0]["market_odds"] == -150,
      "the 'hits' tab's copy of the same row picked up the identical update",
      f"got {out1['data']['hits'][0]}")

head("3. Top Picks gets recomputed fresh: price_clears==True, ranked by edge, capped at 10")

rows3 = [row(f"P{i}", f"prop{i}", "hits", 1, 0.9) for i in range(15)]
payload3 = {"generated_at": "x", "data": {"all": rows3, "top_picks": []}}
path3 = tempfile.mktemp(suffix=".json")
json.dump(payload3, open(path3, "w"))

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    # Every candidate prices at -110 (a real, clearly-acceptable price for
    # a 90% model probability) so every one of the 15 clears -- proves the
    # cap, not the filter.
    mp.return_value = {fd.normalize_name(f"P{i}"): {("hits", 1): -110} for i in range(15)}
    out3 = rp.refresh(path3)

check(len(out3["data"]["top_picks"]) == 10, "capped at 10 even though 15 candidates clear",
      f"got {len(out3['data']['top_picks'])}")
check(all(tp.get("price_clears") for tp in out3["data"]["top_picks"]),
      "every Top Pick genuinely has price_clears==True")

head("4. prices_updated_at is stamped, generated_at is left untouched -- this is a repricing "
     "pass, not a rebuild, and the page needs to tell the two apart")

check("prices_updated_at" in out3, "prices_updated_at was added")
check(out3["generated_at"] == "x", "generated_at (the last real rescoring pass) is never touched here")
# Real bug, found live 2026-08-15: a naive datetime.now().isoformat() (no
# tz suffix) gets parsed by a browser's `new Date(iso)` as LOCAL time, not
# UTC -- the page showed an "Updated" time hours in the future for anyone
# west of UTC. Must carry a real UTC offset.
check("+00:00" in out3["prices_updated_at"] or out3["prices_updated_at"].endswith("Z"),
      "prices_updated_at is timezone-aware, not a naive local timestamp a browser would "
      "misread as local time", f"got {out3['prices_updated_at']!r}")

head("4b. a top pick whose game has already started is grandfathered into the rebuilt "
     "Top Picks even once price_clears goes false -- direct request: \"for the top picks, "
     "them to show when it's cashed... make the pick yellow when the game is happening...\" "
     "FanDuel closes the line once a game starts, so without this a pick would get bumped "
     "off the board right as dashboard/refresh_grades.py is trying to show it resolving.")

r4b_started_top = row("Started Was Top", "Over 0.5 Hits", "hits", 1, 0.6)
r4b_started_top["game_start"] = "2020-01-01T00:00:00Z"  # long past -- definitely started
r4b_started_never_top = row("Started Never Top", "Over 0.5 Hits", "hits", 1, 0.5)
r4b_started_never_top["game_start"] = "2020-01-01T00:00:00Z"  # also started
r4b_not_started = row("Not Started", "Over 0.5 Hits", "hits", 1, 0.5)
r4b_not_started["game_start"] = "2099-01-01T00:00:00Z"  # far future -- definitely not started
payload4b = {"generated_at": "x",
            "data": {"all": [r4b_started_top, r4b_started_never_top, r4b_not_started],
                    "top_picks": [dict(r4b_started_top)]}}  # only this one was already a top pick
path4b = tempfile.mktemp(suffix=".json")
json.dump(payload4b, open(path4b, "w"))

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    mp.return_value = {}  # no fresh price found for any of them -- FanDuel's line closed
    out4b = rp.refresh(path4b)

names4b = {r["name"] for r in out4b["data"]["top_picks"]}
check("Started Was Top" in names4b,
      "the started pick that WAS already a top pick survives the rebuild with no fresh price",
      f"got {names4b}")
check("Started Never Top" not in names4b,
      "a started prop that was NEVER a top pick does not sneak onto Top Picks just because "
      "its game has started -- only grandfathered picks are exempt from the price_clears filter",
      f"got {names4b}")
check("Not Started" not in names4b,
      "an unstarted, unpriced prop is correctly excluded (the ordinary price_clears filter)",
      f"got {names4b}")

os.remove(path4b)

head("5. a payload with no 'all' rows at all (e.g. a lineup-less early-look night) is a clean no-op")

payload5 = {"generated_at": "x", "data": {"all": [], "top_picks": []}}
path5 = tempfile.mktemp(suffix=".json")
json.dump(payload5, open(path5, "w"))
out5 = rp.refresh(path5)
check(out5["data"]["all"] == [], "an empty 'all' list doesn't crash and returns the payload unchanged")

for p in (path1, path3, path5):
    os.remove(p)

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
