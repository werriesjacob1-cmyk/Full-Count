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

PHASE 4 REBUILD (2026-08-16): the payload is now one flat `props` array
(see build_dashboard.py's build_payload()), each row carrying a stable
`id` (game_pk-subject-stat-needs) instead of the old (name, prop) string
match. That kills the entire old "propagate the same update into every
duplicate tab" step below -- there IS no duplicate tab anymore, so this
script now mutates `props` once and is done. It also kills the old
"grandfather a started game's Top Pick back in" hack: that existed only
because the old board capped/sorted a separate "top_picks" bucket
server-side, so an unrelated price move elsewhere could evict an
in-progress pick from the Top-N list right as it was resolving. There is
no server-side cap anymore (direct instruction: "never force Top Picks");
the client filters recommendation_status=="top_pick" straight out of the
full array, so a pick that already earned that status keeps showing
unless its own inputs genuinely change.

Reprices every row in payload["props"] in place, re-runs
recommendation.classify_recommendation() (the one authoritative
Top Pick/Lean/Value/Neutral decision, also used by the full build) since
a price move can genuinely flip that verdict, recomputes summary counts,
writes data.json back whole (so a fresh page load always gets today's
latest price even between full rebuilds), and merges a small live.json
delta keyed by id (only the fields that actually changed) for
app.js's pollLive() -- the cheap, frequent channel that lets an
already-open tab pick up a price move without re-fetching the whole
board. See dashboard/static/app.js's pollLive()/pollFullBoard().

Never touches docs/index.html, docs/app.css, docs/app.js, or
generated_at -- those represent the last real rescoring pass.

    python3 dashboard/refresh_prices.py [--data docs/data.json] [--live docs/live.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Fields a reprice/reclassify pass can change on a row. Used both to detect
# what actually changed (for the live.json delta) and to know what to send.
LIVE_FIELDS = ("market_odds", "market_implied", "market_edge", "price_clears",
              "market_hold", "recommendation_status", "status_reasons", "stale")


def _load_live(live_path):
    try:
        with open(live_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"prices_updated_at": None, "grades_updated_at": None, "props": {}}


def _write_live(live_path, live):
    with open(live_path, "w", encoding="utf-8") as f:
        json.dump(live, f, separators=(",", ":"))


def refresh(data_path, live_path=None):
    live_path = live_path or os.path.join(os.path.dirname(os.path.abspath(data_path)), "live.json")

    with open(data_path, encoding="utf-8") as f:
        payload = json.load(f)

    props = payload.get("props")
    if not props:
        print(f"{data_path}: no props to reprice -- nothing to do.")
        return payload

    import odds_fanduel as fd
    import recommendation as gprec

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

    before = {r["id"]: {f: r.get(f) for f in LIVE_FIELDS} for r in props}

    _, matched = fd.attach_market_prices(props, prices=prices, k_prices=k_prices, fi_prices=fi_prices,
                                         po_prices=po_prices, combined_k_prices=combined_k_prices)
    print(f"Repriced {matched} of {len(props)} candidates against fresh FanDuel lines.")

    # RECOMMENDATION LAYER, 2026-08-15 rebuild. A price move can genuinely
    # flip a pick's real recommendation status -- a shortened price can push
    # a Top Pick's own robustness test negative, or a lengthened one can
    # newly clear it -- so this re-runs the SAME classify_recommendation()
    # build_dashboard.py uses at full-build time, on the SAME Python module,
    # rather than approximating it with a second, separate rule that could
    # silently drift from what a full rebuild would actually say. One
    # authoritative implementation, called from both places.
    odds_fetched_at = datetime.now(timezone.utc).isoformat()
    board_generated_at = payload.get("generated_at")
    gprec.attach_recommendations(props, odds_fetched_at=odds_fetched_at,
                                 board_generated_at=board_generated_at)
    # attach_recommendations() writes its verdict into "status" (the field
    # name generate_picks.py's candidates carry); the payload's own schema
    # calls that same concept "recommendation_status" (see build_dashboard.
    # py's clean()). Fold it back so the payload never carries both names.
    for r in props:
        r["recommendation_status"] = r.pop("status", r.get("recommendation_status"))

    n_top_pick = sum(1 for r in props if r.get("recommendation_status") == "top_pick")
    n_lean = sum(1 for r in props if r.get("recommendation_status") == "lean")
    n_value = sum(1 for r in props if r.get("recommendation_status") == "value")
    summary = payload.setdefault("summary", {})
    summary["n_top_pick"] = n_top_pick
    summary["n_lean"] = n_lean
    summary["n_value"] = n_value

    payload["odds_fetched_at"] = odds_fetched_at
    payload["prices_updated_at"] = odds_fetched_at

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {data_path} ({n_top_pick} top picks, {n_lean} leans, {n_value} value after repricing)")

    live = _load_live(live_path)
    live["prices_updated_at"] = odds_fetched_at
    live_props = live.setdefault("props", {})
    n_changed = 0
    for r in props:
        old = before[r["id"]]
        new = {f: r.get(f) for f in LIVE_FIELDS}
        if new != old:
            live_props.setdefault(r["id"], {}).update(new)
            n_changed += 1
    _write_live(live_path, live)
    print(f"Wrote {live_path} ({n_changed} prop(s) changed)")

    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"),
                    help="path to the payload build_dashboard.py's --data-out wrote")
    ap.add_argument("--live", default=None,
                    help="path to the small delta file app.js's pollLive() fetches "
                         "(default: live.json next to --data)")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        print(f"{args.data} doesn't exist yet -- nothing to reprice until a full build runs first.")
        return 0

    refresh(args.data, live_path=args.live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
