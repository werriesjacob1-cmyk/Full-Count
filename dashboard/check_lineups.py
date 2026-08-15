#!/usr/bin/env python3
"""dashboard/check_lineups.py — cheap poll: has any of today's games gotten
a real, MLB-confirmed lineup, OR had an already-confirmed lineup CHANGE
(a late scratch), since the last check? Direct requests, verbatim: "How do
we have the board update each time a new lineup comes out?" and the
follow-up gap this closes -- a player scratched after his lineup posted
but before the next full rebuild would sit on the live board looking
fully live for up to 2 hours.

The full rebuild (dashboard-refresh.yml) is deliberately capped at every
2 hours -- build_dashboard.py's own docstring: a live pass hits real
FanGraphs/Statcast/FanDuel and is "not something to run every few
minutes." This is the cheap half of the same split
dashboard/refresh_prices.py already uses for prices: one fast, free MLB
schedule call every 10 minutes, paying for a full rebuild only when
something worth rebuilding for actually happened -- a game's lineup going
from unposted to posted, OR an already-posted lineup's roster changing.

A game counts as "confirmed" using the same threshold quality_control()
and check_scratches.py already use: 9+ posted hitters per side (nine is a
real batting order; anything fewer is a partial scrape, not a lineup).
Tracking the actual player-ID sets (not just a posted/not-posted flag) is
what catches the scratch case check_scratches.py's own docstring names
directly: "A batter confirmed in the two o'clock lineup and scratched at
six was a fully valid pick when it was made."

Writes GITHUB_OUTPUT changed=true/false. State (which games are already
known-confirmed, and their last-seen rosters) persists in
lineup_watch_state.json, committed by the calling workflow -- without
that, every run would start from empty and treat every already-known
lineup as new again, triggering a rebuild on every single 10-minute tick
instead of only the ones that actually matter.
"""
import json
import os
from datetime import datetime

import requests

STATE_PATH = os.path.join(os.path.dirname(__file__), "lineup_watch_state.json")
STATS_API = "https://statsapi.mlb.com/api/v1"
MIN_LINEUP = 9  # quality_control()'s own threshold for a real, complete batting order


def today():
    return datetime.now().strftime("%Y-%m-%d")


def fetch_confirmed_lineups(date):
    """Real, live MLB lineup check: {game_pk: {"away": {player_id, ...},
    "home": {player_id, ...}}} for games where BOTH sides have posted a
    complete (9+) batting order right now. Fails soft to {} (no known-
    confirmed games this check, not a crash) on any network hiccup -- a
    missed check just gets caught by the next one 10 minutes later."""
    try:
        r = requests.get(f"{STATS_API}/schedule",
                         params={"sportId": 1, "date": date, "hydrate": "lineups"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  (couldn't fetch schedule/lineups: {e} -- treating as no confirmed games this check)")
        return {}
    confirmed = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            lineups = g.get("lineups") or {}
            away = lineups.get("awayPlayers") or []
            home = lineups.get("homePlayers") or []
            if pk and len(away) >= MIN_LINEUP and len(home) >= MIN_LINEUP:
                confirmed[pk] = {
                    "away": {p["id"] for p in away if p.get("id")},
                    "home": {p["id"] for p in home if p.get("id")},
                }
    return confirmed


def load_state(date):
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if st.get("date") != date:
        return {}  # a new day -- yesterday's confirmed lineups don't apply
    return {int(pk): {"away": set(v["away"]), "home": set(v["home"])}
            for pk, v in st.get("games", {}).items()}


def save_state(date, confirmed):
    games = {str(pk): {"away": sorted(v["away"]), "home": sorted(v["home"])}
             for pk, v in confirmed.items()}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": date, "games": games}, f, indent=2, sort_keys=True)


def diff(seen, now_confirmed):
    """Splits what changed into newly-confirmed games and games whose
    already-confirmed roster changed (a scratch/swap) -- both are worth a
    rebuild, but they're reported separately since they mean different
    things to a reader of the workflow log."""
    new_games = [pk for pk in now_confirmed if pk not in seen]
    changed_games = [pk for pk in now_confirmed
                     if pk in seen and (now_confirmed[pk]["away"] != seen[pk]["away"]
                                        or now_confirmed[pk]["home"] != seen[pk]["home"])]
    return new_games, changed_games


def write_output(name, value):
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main():
    date = today()
    seen = load_state(date)
    now_confirmed = fetch_confirmed_lineups(date)
    new_games, changed_games = diff(seen, now_confirmed)

    if new_games or changed_games:
        if new_games:
            print(f"{len(new_games)} game(s) just got a confirmed lineup: {sorted(new_games)}")
        if changed_games:
            print(f"{len(changed_games)} already-confirmed game(s) had a roster change "
                  f"(a scratch or late swap): {sorted(changed_games)}")
        save_state(date, now_confirmed)
        write_output("changed", "true")
    else:
        print(f"No new or changed confirmed lineups ({len(now_confirmed)} confirmed total today, "
              f"all already known).")
        write_output("changed", "false")


if __name__ == "__main__":
    main()
