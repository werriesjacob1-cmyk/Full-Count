#!/usr/bin/env python3
"""dashboard/refresh_prices.py — repricing-only refresh for the Full Count
Board dashboard. Direct request: "I want all props to update with new
odds as FanDuel changes them, and compute in real time the edge and
whether it keeps it on the top 10."

build_dashboard.py's full rebuild is deliberately infrequent (a live
rescoring pass against FanGraphs/Statcast/lineups -- see its own
docstring, "not something to run every few minutes"). But re-pricing an
EXISTING candidate against a fresh FanDuel line has nothing to do with
the model: the candidate's hit_probability doesn't change, only the price
does, and odds_fanduel.attach_market_prices() already does exactly that
recompute (market_odds/market_implied/market_edge/price_clears) given a
probability and a price. Both odds_fanduel.py and prop_probability.py
import nothing beyond `requests` and the stdlib, so this script is cheap
enough to run every few minutes -- the "not every few minutes" constraint
belongs to the full rebuild, not to this.

Loads the last full build's raw payload (docs/data.json, written by
build_dashboard.py's --data-out), reprices every row in payload["data"]
["all"] in place, propagates those same field updates into every other
tab by matching on (name, prop) -- the one stable identity a row keeps
across a JSON round-trip -- recomputes Top Picks fresh (the same
price_clears==True / sorted-by-edge / capped-at-10 rule build_payload()
uses server-side), stamps prices_updated_at, and writes the file back.

Never touches docs/index.html or generated_at -- those represent the last
real rescoring pass. The page itself (build_dashboard.py's pollPrices())
is what picks this file's updates up without a reload.

    python3 dashboard/refresh_prices.py [--data docs/data.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _row_key(r):
    return (r.get("name"), r.get("prop"))


def refresh(data_path):
    with open(data_path, encoding="utf-8") as f:
        payload = json.load(f)

    all_rows = payload.get("data", {}).get("all")
    if not all_rows:
        print(f"{data_path}: no 'all' rows to reprice -- nothing to do.")
        return payload

    import odds_fanduel as fd

    prices = fd.fetch_prop_prices()
    try:
        k_prices = fd.fetch_pitcher_strikeouts()
    except Exception:
        k_prices = {}
    try:
        fi_prices = fd.fetch_first_inning_totals()
    except Exception:
        fi_prices = {}
    try:
        po_prices = fd.fetch_pitcher_outs()
    except Exception:
        po_prices = {}
    try:
        combined_k_prices = fd.fetch_combined_pitcher_strikeouts()
    except Exception:
        combined_k_prices = {}

    _, matched = fd.attach_market_prices(all_rows, prices=prices, k_prices=k_prices, fi_prices=fi_prices,
                                         po_prices=po_prices, combined_k_prices=combined_k_prices)
    print(f"Repriced {matched} of {len(all_rows)} candidates against fresh FanDuel lines.")

    # Propagate the SAME field updates into every other tab -- these are
    # separate dict objects from "all" (this is a fresh process each run,
    # not the in-memory sharing build_payload() gets for free within one
    # build), so a row that lives in both "hits" and "all" needs the
    # update applied twice, matched by identity rather than object equality.
    PRICE_FIELDS = ("market_odds", "market_implied", "market_edge", "price_clears", "market_hold")
    by_key = {_row_key(r): r for r in all_rows}
    for tab_name, rows in payload.get("data", {}).items():
        if tab_name in ("all", "top_picks"):
            continue
        for r in rows:
            fresh = by_key.get(_row_key(r))
            if fresh is None:
                continue
            for field in PRICE_FIELDS:
                r[field] = fresh.get(field)

    # Same rule build_payload() uses: price_clears is True, ranked by
    # edge, capped at 10 -- recomputed here so a price move can genuinely
    # push a prop on or off Top Picks between full rebuilds. A pick whose
    # game has already started is grandfathered in regardless of its
    # current price_clears value (FanDuel's own line for it is closed by
    # then anyway) -- direct request: "for the top picks, them to show
    # when it's cashed... make the pick yellow when the game is
    # happening... green if it cashes, red if it doesn't." Without this,
    # a cashed pick could get bumped off the board by an unrelated later
    # game's price move right as dashboard/refresh_grades.py marks it
    # green, defeating the entire point of watching it resolve. Mirrored
    # client-side in mergePriceUpdate() (build_dashboard.py).
    now = datetime.now().astimezone()
    was_top_pick = {_row_key(r) for r in (payload.get("data", {}).get("top_picks") or [])}
    def _already_started(r):
        gs = r.get("game_start")
        if not gs:
            return False
        try:
            return datetime.fromisoformat(gs.replace("Z", "+00:00")) <= now
        except ValueError:
            return False
    top_picks = [r for r in all_rows if r.get("price_clears") is True
                or (_row_key(r) in was_top_pick and _already_started(r))]
    top_picks.sort(key=lambda r: r.get("market_edge") or 0, reverse=True)
    payload["data"]["top_picks"] = top_picks[:10]

    payload["prices_updated_at"] = datetime.now().isoformat()

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {data_path} ({len(payload['data']['top_picks'])} top picks after repricing)")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"),
                    help="path to the payload build_dashboard.py's --data-out wrote")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        print(f"{args.data} doesn't exist yet -- nothing to reprice until a full build runs first.")
        return 0

    refresh(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
