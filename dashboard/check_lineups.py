#!/usr/bin/env python3
"""dashboard/check_lineups.py — cheap poll: has any of today's games gotten
a real, MLB-confirmed lineup since the last check? Direct request: "How do
we have the board update each time a new lineup comes out?"

The full rebuild (dashboard-refresh.yml) is deliberately capped at every 2
hours -- build_dashboard.py's own docstring: a live pass hits real
FanGraphs/Statcast/FanDuel and is "not something to run every few
minutes" -- so on its own it can sit on a stale, lineup-less board for up
to 2 hours after a real lineup posts (verified live 2026-08-15: exactly
this happened). This is the cheap half of the same split
dashboard/refresh_prices.py already uses for prices: one fast, free MLB
schedule call every 10 minutes, paying for a full rebuild only when a
game's lineup status actually changed since the last check, not on a
fixed clock.

Same hydrate MLB_daily.fetch_lineups() uses for the real pipeline
("lineups") -- a game counts as confirmed the moment BOTH sides have a
posted batting order, which is exactly what quality_control ultimately
needs to let a batter prop onto the board. Deliberately NOT importing
mlb_daily itself: that module pulls in pandas/numpy/bs4 and a lot of
report-building machinery this job has no use for; a plain requests call
against the same endpoint gets the identical signal for a fraction of the
weight, matching refresh_prices.py's own reasoning for staying minimal-
dependency on a frequent job.

Writes GITHUB_OUTPUT changed=true/false. State (which games are already
known-confirmed) persists in lineup_watch_state.json, committed by the
calling workflow -- without that, every run would start from empty and
treat every already-known lineup as "new" again, triggering a rebuild on
every single 10-minute tick instead of only the ones that actually matter.
"""
import json
import os
from datetime import datetime

import requests

STATE_PATH = os.path.join(os.path.dirname(__file__), "lineup_watch_state.json")
STATS_API = "https://statsapi.mlb.com/api/v1"


def today():
    return datetime.now().strftime("%Y-%m-%d")


def fetch_confirmed_game_pks(date):
    """Real, live MLB lineup check. Fails soft to an empty set (no known-
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
        return set()
    confirmed = set()
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            lineups = g.get("lineups") or {}
            if pk and lineups.get("awayPlayers") and lineups.get("homePlayers"):
                confirmed.add(pk)
    return confirmed


def load_state(date):
    if not os.path.exists(STATE_PATH):
        return set()
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    if st.get("date") != date:
        return set()  # a new day -- yesterday's confirmed games don't apply
    return set(st.get("confirmed_pks", []))


def save_state(date, confirmed_pks):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"date": date, "confirmed_pks": sorted(confirmed_pks)}, f, indent=2)


def write_output(name, value):
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main():
    date = today()
    seen = load_state(date)
    now_confirmed = fetch_confirmed_game_pks(date)
    newly_confirmed = now_confirmed - seen

    if newly_confirmed:
        print(f"{len(newly_confirmed)} game(s) just got a confirmed lineup: {sorted(newly_confirmed)} "
              f"({len(now_confirmed)} confirmed total today)")
        save_state(date, now_confirmed)
        write_output("changed", "true")
    else:
        print(f"No new confirmed lineups ({len(now_confirmed)} confirmed total today, all already known).")
        write_output("changed", "false")


if __name__ == "__main__":
    main()
