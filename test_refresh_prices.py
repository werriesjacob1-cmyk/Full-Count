#!/usr/bin/env python3
"""test_refresh_prices.py — coverage for dashboard/refresh_prices.py, the
lightweight repricing-only refresh. Direct request: "I want all props to
update with new odds as FanDuel changes them, and compute in real time
the edge and whether it keeps it on the top 10."

Mocks every odds_fanduel fetch (same pattern as test_il_returns.py) rather
than hitting FanDuel -- this tests the merge/recompute logic, not the
network layer, which odds_fanduel.py's own tests already cover.

PHASE 4 REWRITE NOTE (2026-08-16): the old version of this file tested the
payload["data"]["all"]/["hits"]/["top_picks"] per-tab-duplication schema and
a "started top pick survives an artificial top-N cap" grandfathering rule.
Both are gone: the payload is now one flat `props` array (see
build_dashboard.py's build_payload()), so there is no separate tab to
propagate an update into, and there is no server-side Top Picks cap to be
grandfathered out of in the first place (direct instruction: "never force
Top Picks" -- the client filters recommendation_status=="top_pick" straight
out of the full array, with no ranking/eviction). refresh_prices.py's own
module docstring documents this simplification directly.

    /tmp/mlbvenv/bin/python3 test_refresh_prices.py
"""
import json
import os
import sys
import tempfile
import unittest.mock as mock
from datetime import datetime, timezone, timedelta

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

_row_id = [0]


def row(name, prop, stat, needs, prob, matchup="A @ B", lean=None, reliability=None,
       lineup_assumed=False, lift=None, prob_ci=None, market_odds=None):
    _row_id[0] += 1
    return {"id": f"fixture-{_row_id[0]}", "name": name, "prop": prop, "matchup": matchup,
            "lean": lean, "projection": {"stat": stat, "needs": needs},
            "hit_probability": prob,
            "market_odds": market_odds, "market_implied": None, "market_edge": None,
            "price_clears": None, "market_hold": None,
            "reliability": reliability, "lineup_assumed": lineup_assumed, "lift": lift,
            "prob_ci": prob_ci, "recommendation_status": None, "status_reasons": [],
            "stale": False}


def _mocked_fetch(return_value):
    return (mock.patch.object(fd, "fetch_prop_prices", return_value=return_value),
           mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}),
           mock.patch.object(fd, "fetch_first_inning_totals", return_value={}),
           mock.patch.object(fd, "fetch_pitcher_outs", return_value={}),
           mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}))


head("1. refresh() reprices every row in the flat 'props' array against fresh FanDuel "
     "prices, using the real odds_fanduel.attach_market_prices() and recommendation."
     "attach_recommendations() -- not a reimplementation of either")

r1 = row("Aaron Judge", "Over 0.5 Hits", "hits", 1, 0.70)
payload1 = {"generated_at": "2026-08-14T18:00:00", "props": [r1], "summary": {}}
path1 = tempfile.mktemp(suffix=".json")
json.dump(payload1, open(path1, "w"))
live1 = tempfile.mktemp(suffix=".json")

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    mp.return_value = {fd.normalize_name("Aaron Judge"): {("hits", 1): -150}}
    out1 = rp.refresh(path1, live_path=live1)

check(out1["props"][0]["market_odds"] == -150, "the row picked up the fresh price",
      f"got {out1['props'][0]}")
check(out1["props"][0]["price_clears"] is not None, "price_clears got recomputed, not left None")
check("status" not in out1["props"][0],
      "the internal 'status' key attach_recommendations() writes is folded back into "
      "'recommendation_status' and never leaks into the payload as a second, duplicate field",
      f"got {out1['props'][0]}")


head("2. recommendation_status is recomputed fresh via the real recommendation."
     "classify_recommendation() for every row -- 2026-08-15 rebuild: NO cap exists on Top "
     "Picks (\"if only 3 bets qualify, show 3\"), so a real 15-qualifier night ships all 15")

rows3 = [row(f"P{i}", f"prop{i}", "hits", 1, 0.9, reliability="A", lift=0.10,
            prob_ci=[0.75, 0.95]) for i in range(15)]
generated_at3 = datetime.now(timezone.utc).isoformat()
payload3 = {"generated_at": generated_at3, "props": rows3, "summary": {}}
path3 = tempfile.mktemp(suffix=".json")
json.dump(payload3, open(path3, "w"))
live3 = tempfile.mktemp(suffix=".json")

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    # Every candidate prices at -110 (a real, clearly-acceptable price for
    # a 90% model probability, robust even at the pessimistic 75% end of
    # its own real interval) so every one of the 15 genuinely qualifies.
    mp.return_value = {fd.normalize_name(f"P{i}"): {("hits", 1): -110} for i in range(15)}
    out3 = rp.refresh(path3, live_path=live3)

top_picks3 = [r for r in out3["props"] if r["recommendation_status"] == "top_pick"]
check(len(top_picks3) == 15,
      "every one of the 15 genuinely qualifying picks ships, uncapped -- there is no "
      "server-side Top Picks list to trim in the new architecture at all",
      f"got {len(top_picks3)}")
check(all(tp.get("price_clears") for tp in top_picks3),
      "every Top Pick genuinely has price_clears==True")
check(out3["summary"]["n_top_pick"] == 15,
      "summary.n_top_pick is recomputed to match the real repriced count",
      f"got {out3['summary']}")


head("3. prices_updated_at is stamped, generated_at is left untouched -- this is a "
     "repricing pass, not a rebuild, and the page needs to tell the two apart")

check("prices_updated_at" in out3, "prices_updated_at was added")
check(out3["generated_at"] == generated_at3,
      "generated_at (the last real rescoring pass) is never touched here")
# Real bug, found live 2026-08-15: a naive datetime.now().isoformat() (no
# tz suffix) gets parsed by a browser's `new Date(iso)` as LOCAL time, not
# UTC -- the page showed an "Updated" time hours in the future for anyone
# west of UTC. Must carry a real UTC offset.
check("+00:00" in out3["prices_updated_at"] or out3["prices_updated_at"].endswith("Z"),
      "prices_updated_at is timezone-aware, not a naive local timestamp a browser would "
      "misread as local time", f"got {out3['prices_updated_at']!r}")


head("4. a merged live.json delta is written alongside data.json, containing only the "
     "fields that actually changed, keyed by the row's stable id -- the cheap channel "
     "app.js's pollLive() polls every few minutes instead of re-fetching the whole board")

with open(live3, encoding="utf-8") as f:
    live_out3 = json.load(f)
check(live_out3["prices_updated_at"] == out3["prices_updated_at"],
      "live.json's prices_updated_at matches the same stamp written into data.json")
check(set(live_out3["props"].keys()) == {r["id"] for r in rows3},
      "every repriced row (all 15 changed from no-price to a real price) has a delta "
      "entry keyed by its real id", f"got {list(live_out3['props'].keys())}")
sample_delta = live_out3["props"][rows3[0]["id"]]
check(sample_delta.get("market_odds") == -110 and sample_delta.get("recommendation_status") == "top_pick",
      "the delta entry itself carries the real changed field values, not just an empty marker",
      f"got {sample_delta}")


head("5. refresh() merges into an EXISTING live.json rather than overwriting it -- "
     "dashboard-prices.yml and dashboard-grades.yml are separate 5-minute workflows that "
     "both write this same file, so a price refresh must never blow away a grade another "
     "cycle already recorded for the same id")

existing_live = {"prices_updated_at": "2026-08-14T00:00:00+00:00",
                 "grades_updated_at": "2026-08-14T00:05:00+00:00",
                 "props": {rows3[0]["id"]: {"grade": "hit"}}}
live5 = tempfile.mktemp(suffix=".json")
json.dump(existing_live, open(live5, "w"))
path5b = tempfile.mktemp(suffix=".json")
json.dump({"generated_at": generated_at3, "props": [dict(rows3[0])], "summary": {}}, open(path5b, "w"))

with mock.patch.object(fd, "fetch_prop_prices") as mp, \
     mock.patch.object(fd, "fetch_pitcher_strikeouts", return_value={}), \
     mock.patch.object(fd, "fetch_first_inning_totals", return_value={}), \
     mock.patch.object(fd, "fetch_pitcher_outs", return_value={}), \
     mock.patch.object(fd, "fetch_combined_pitcher_strikeouts", return_value={}):
    mp.return_value = {fd.normalize_name("P0"): {("hits", 1): -200}}
    rp.refresh(path5b, live_path=live5)

with open(live5, encoding="utf-8") as f:
    merged_live = json.load(f)
check(merged_live["grades_updated_at"] == "2026-08-14T00:05:00+00:00",
      "a price-only refresh never touches the grades_updated_at another workflow wrote",
      f"got {merged_live}")
check(merged_live["props"][rows3[0]["id"]].get("grade") == "hit",
      "an existing grade delta for this id survives a later price refresh -- fields "
      "merge per-id, the row isn't overwritten wholesale", f"got {merged_live['props'][rows3[0]['id']]}")
check(merged_live["props"][rows3[0]["id"]].get("market_odds") == -200,
      "the new price delta is ALSO present alongside the surviving grade -- both a price "
      "and a grade delta can coexist for the same id", f"got {merged_live['props'][rows3[0]['id']]}")


head("6. a payload with no props at all (e.g. a no-games night) is a clean no-op")

payload6 = {"generated_at": "x", "props": [], "summary": {}}
path6 = tempfile.mktemp(suffix=".json")
json.dump(payload6, open(path6, "w"))
out6 = rp.refresh(path6)
check(out6["props"] == [], "an empty props list doesn't crash and returns the payload unchanged")

for p in (path1, path3, path5b, path6):
    os.remove(p)
for p in (live1, live3, live5):
    if os.path.exists(p):
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
