#!/usr/bin/env python3
"""
odds_snapshot.py — periodically captures the current betting market and
appends it to data/odds/odds_{DATE}.json.

WHY THIS EXISTS AS ITS OWN THING, RUN ON ITS OWN SCHEDULE:

Almost every input this pipeline uses can be fetched retroactively. Box
scores, player stats, Statcast, weather history — if we want last Tuesday's
version, MLB and Savant will still hand it to us. Line movement cannot be
backfilled by anyone. The market's opening number and the path it took to
close exist only if something was watching while it happened. Every day we
don't capture it is a day permanently missing from the dataset.

That asymmetry is the whole justification: this is cheap to run (one HTTP
call, a few seconds) and impossible to recover later, so it runs hourly on
its own lightweight workflow rather than once a day inside the main
15-20 minute pipeline.

WHAT THE DATA IS FOR:

The existing scoring already uses one market signal — the tickets%-vs-money%
split, which says whether sharp money disagrees with public money right now.
Movement is the stronger version of that question: a line drifting *against*
heavy public money is a much more specific signal than the split alone, and
it's only visible across time.

Deliberately NOT computing movement here. This writes raw observations and
nothing else. Derived numbers belong wherever they're consumed, so a change
in how movement is defined doesn't require re-collecting history we can't
re-collect.

is_live is recorded per entry and matters: once first pitch happens the book
switches to in-game pricing, which is a different thing from pregame drift.
Verified live at 20:00 UTC on a real slate — games already underway were
correctly returning is_live=True while the rest were still pregame. Anything
consuming this must filter on it rather than assume a snapshot is uniform.
"""
import os, sys, json
from datetime import datetime, timezone

import requests

OUT_DIR = os.environ.get("ODDS_DIR", "data/odds")
BOOK_ID = 15  # FanDuel — the book this project's picks are checked against
API = "https://api.actionnetwork.com/web/v2/scoreboard/mlb"
UA = {"User-Agent": "Mozilla/5.0"}

# Every market the endpoint exposes. Captured in full even though scoring
# currently reads only some of them -- storage is trivial and an unrecorded
# market is unrecoverable, so the cost of over-capturing is far below the
# cost of finding out later we needed it.
MARKETS = ["moneyline", "spread", "total", "core_bet_type_6_team_score"]


def fetch_snapshot(date_str):
    r = requests.get(API, params={"bookIds": BOOK_ID, "date": date_str.replace("-", "")},
                     headers=UA, timeout=25)
    r.raise_for_status()
    games = r.json().get("games", [])
    taken_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for g in games:
        teams = {t.get("id"): t.get("full_name") for t in g.get("teams", [])}
        try:
            ev = g["markets"][str(BOOK_ID)]["event"]
        except (KeyError, TypeError):
            continue
        for market in MARKETS:
            for e in ev.get(market, []) or []:
                bi = e.get("bet_info") or {}
                rows.append({
                    "taken_at": taken_at,
                    "game_id": g.get("id"),
                    "start_time": g.get("start_time"),
                    "status": g.get("status"),
                    "team": teams.get(e.get("team_id")),
                    "market": market,
                    "side": e.get("side"),
                    "value": e.get("value"),
                    "odds": e.get("odds"),
                    "is_live": e.get("is_live"),
                    "line_status": e.get("line_status"),
                    "tickets_pct": (bi.get("tickets") or {}).get("percent"),
                    "money_pct": (bi.get("money") or {}).get("percent"),
                })
    return taken_at, rows


def main() -> int:
    date_str = os.environ.get("ODDS_DATE") or datetime.now().strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"odds_{date_str}.json")

    try:
        taken_at, rows = fetch_snapshot(date_str)
    except Exception as e:
        # Never fail the workflow over a missed snapshot -- an hourly job that
        # breaks the build on one bad response is worse than a gap in the series.
        print(f"Odds snapshot failed ({e}) — skipping this interval.")
        return 0
    if not rows:
        print(f"No odds returned for {date_str} — nothing to record.")
        return 0

    payload = {"date": date_str, "snapshots": []}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            # A corrupt file must not cost us the rest of the day's series.
            print(f"Existing {path} unreadable — starting a fresh series for {date_str}.")
            payload = {"date": date_str, "snapshots": []}

    # Idempotent per minute: reruns/overlapping fires within the same minute
    # replace rather than duplicate, so a retried workflow doesn't distort the
    # series with phantom "movement" between identical observations.
    minute = taken_at[:16]
    payload["snapshots"] = [s for s in payload.get("snapshots", [])
                            if s.get("taken_at", "")[:16] != minute]
    payload["snapshots"].append({"taken_at": taken_at, "rows": rows})
    payload["snapshots"].sort(key=lambda s: s["taken_at"])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    live = sum(1 for r in rows if r.get("is_live"))
    print(f"Recorded {len(rows)} odds rows at {taken_at} "
          f"({live} in-game, {len(rows)-live} pregame) — "
          f"{len(payload['snapshots'])} snapshot(s) today in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
