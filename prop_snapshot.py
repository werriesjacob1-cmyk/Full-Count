#!/usr/bin/env python3
"""
prop_snapshot.py — captures FanDuel's player-prop prices to disk, hourly.

WHY THIS RUNS ON A CLOCK AND NOT ON DEMAND.

The value screen cannot be backtested. Every other component in this project
was validated by replaying history: box scores, Statcast, weather and season
stats can all be re-fetched for any past date, so backtest/engine.py can
reconstruct what the model would have said in May and grade it against what
happened. Prices are different. FanDuel publishes what a prop costs RIGHT
NOW, and nothing publishes what it cost last Tuesday. There is no archive to
buy and none to scrape.

So the screen can only ever be validated FORWARD, against prices captured
while they were live, and the dataset that makes that possible does not exist
until something starts writing it. Every hour that passes without a capture
is an hour permanently missing from that validation.

This is the same reasoning that put odds_snapshot.py on an hourly schedule
for game lines. The difference is that game lines were never the thing being
bet -- these are.

WHAT IS CAPTURED AND WHY IT IS RAW.

Every priced batter prop, with its American price, the market type, whether
the market was in-play, and the timestamp. No edge, no model probability, no
verdict: those are derived numbers, and a change in how edge is defined must
never require re-collecting history that cannot be re-collected. Derived
values belong wherever they are consumed.

The closing price is the one that matters most for validation -- it is the
market's final word and the sharpest number available -- so captures continue
right up to first pitch, and each row records whether the game had started.
"""
import json
import os
import sys
from datetime import datetime, timezone

import odds_fanduel as fd

OUT_DIR = os.environ.get("PROPS_DIR", "data/props")


def capture():
    """One full sweep of the slate. Returns (taken_at, rows)."""
    taken_at = datetime.now(timezone.utc).isoformat()
    games = fd.list_games()
    rows = []
    for event_id, name, start in games:
        try:
            props = fd._event_props(event_id)
        except Exception:
            continue
        for p in props:
            rows.append({
                "taken_at": taken_at,
                "event_id": event_id,
                "game": name,
                "start_time": start,
                "player": p["player"],
                "player_norm": p["norm"],
                "stat": p["stat"],
                "needs": p["needs"],
                "american": p["american"],
                # Pregame and in-play prices are different bets on the same
                # words. Recorded rather than filtered so a consumer can
                # choose, and so the transition itself is visible.
                "in_play": p["in_play"],
            })
    return taken_at, rows


def main():
    date_str = os.environ.get("PROPS_DATE") or datetime.now().strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"props_{date_str}.json")

    try:
        taken_at, rows = capture()
    except Exception as e:
        # A missed hour is bad; a broken workflow that stops all future hours
        # is worse. Never fail the job over one bad response.
        print(f"Prop snapshot failed ({e}) — skipping this interval.")
        return 0
    if not rows:
        print(f"No props returned for {date_str} — nothing to record.")
        return 0

    payload = {"date": date_str, "snapshots": []}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"Existing {path} unreadable — starting a fresh series.")
            payload = {"date": date_str, "snapshots": []}

    # Idempotent per minute, so a retried or overlapping run replaces rather
    # than duplicates and cannot invent phantom movement between identical
    # observations.
    minute = taken_at[:16]
    payload["snapshots"] = [s for s in payload.get("snapshots", [])
                            if s.get("taken_at", "")[:16] != minute]
    payload["snapshots"].append({"taken_at": taken_at, "rows": rows})
    payload["snapshots"].sort(key=lambda s: s["taken_at"])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    live = sum(1 for r in rows if r["in_play"])
    players = len({r["player_norm"] for r in rows})
    print(f"Recorded {len(rows)} prop prices for {players} players at {taken_at} "
          f"({live} in-play, {len(rows)-live} pregame) — "
          f"{len(payload['snapshots'])} snapshot(s) today in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
