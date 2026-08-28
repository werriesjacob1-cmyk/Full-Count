#!/usr/bin/env python3
"""Run one reconciliation pass inside the live observer.

Invoked by dashboard-live.yml, which infra/live-heartbeat wakes every 5
minutes from Cloudflare -- outside GitHub's throttled `schedule` queue.
That is deliberately the ONLY recovery path: a second GitHub cron would
inherit the exact scheduling defect that let the 2026-08-28 board rot for
ten hours undetected.

Writes only its own `reconciliation` key in docs/live.json. docs/data.json
keeps exactly one semantic writer (dashboard-refresh.yml), and this module
never touches it.

Exit codes:
    0  publication matches authoritative state, or a rebuild was requested
    1  a mismatch is open and could not be acted on -- visible failure
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .live_state import (apply_live_overlay, atomic_write_json, load_live_state,
                             parse_utc, utc_now)
    from . import reconcile as rc
except ImportError:
    from live_state import (apply_live_overlay, atomic_write_json, load_live_state,
                            parse_utc, utc_now)
    import reconcile as rc


def _confirmed_lineup(game_pk, *, fetcher=None):
    """MLB's own posted batting order for one game, or None.

    battingOrder is populated only once a real lineup is posted, so absence
    here means "nobody has posted yet" -- not a mismatch. Uses the boxscore
    endpoint directly rather than mlb_daily's heavier path: the live
    observer installs only requests/mlb-statsapi, and this must stay cheap
    enough to run every five minutes.
    """
    import requests
    get = fetcher or (lambda url: requests.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20))
    try:
        r = get(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore")
        if r.status_code != 200:
            return None
        box = r.json()
    except Exception:
        return None
    # Keyed PER SIDE, because the two teams post independently and a
    # confirmed away lineup says nothing about the home one.
    out = {}
    for side in ("away", "home"):
        team = (box.get("teams") or {}).get(side) or {}
        order = (team.get("battingOrder") or [])[:9]
        players = team.get("players") or {}
        slots = {}
        for slot, pid in enumerate(order, 1):
            person = (players.get(f"ID{pid}") or {}).get("person") or {}
            if person.get("id"):
                slots[slot] = int(person["id"])
        # A partial scrape is not a posted lineup. Nine is a real batting
        # order; fewer means the feed is mid-populate, and treating that as
        # authoritative would manufacture a mismatch against a lineup MLB
        # has not actually finished posting.
        if len(slots) == 9:
            out[side] = slots
    return out or None


def confirmed_lineups(game_pks, *, fetcher=None, max_workers=8):
    """Fetch every game's confirmed lineup concurrently.

    Sequential fetching is what turned real MLB latency into a 15-minute
    live job on 2026-08-25; this stays bounded and parallel for the same
    reason grade_results.fetch_game_contexts() does.
    """
    pks = sorted({int(p) for p in game_pks if p})
    if not pks:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(pks))) as pool:
        results = list(pool.map(lambda p: (p, _confirmed_lineup(p, fetcher=fetcher)), pks))
    out = {}
    for game_pk, sides in results:
        for side, slots in (sides or {}).items():
            out[(game_pk, side)] = slots
    return out


def dispatch_rebuild(*, repo=None, token=None, runner=None):
    """Ask for a canonical rebuild. Returns (ok, detail).

    Never treated as resolution -- see reconcile.mark_rebuild_requested.
    """
    import subprocess
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or "werriesjacob1-cmyk/Full-Count"
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return False, "no token available to request a rebuild"
    run = runner or (lambda a: subprocess.run(a, capture_output=True, text=True, timeout=30))
    proc = run(["curl", "-sS", "-X", "POST", "-o", "/dev/null", "-w", "%{http_code}",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/vnd.github+json",
                "-H", "X-GitHub-Api-Version: 2022-11-28",
                f"https://api.github.com/repos/{repo}/actions/workflows/"
                f"{rc.REBUILD_WORKFLOW}/dispatches",
                "-d", '{"ref":"main"}'])
    code = (proc.stdout or "").strip()
    if getattr(proc, "returncode", 1) == 0 and code in ("204", "201", "200"):
        return True, f"dispatched {rc.REBUILD_WORKFLOW}"
    return False, f"dispatch failed (http {code or 'n/a'})"


def run(data_path, live_path, *, now=None, lineup_fetcher=None, runner=None,
        dispatcher=None, repo=None, token=None):
    with open(data_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    live = load_live_state(live_path)
    effective = apply_live_overlay(payload, live)

    game_pks = {r.get("game_pk") for r in (effective.get("props") or [])}
    lineups = confirmed_lineups(game_pks, fetcher=lineup_fetcher)

    state = rc.reconcile(effective, confirmed_lineups=lineups, now=now,
                         prior=live.get("reconciliation"))

    dispatched, reason = False, "no open mismatch"
    if rc.needs_rebuild(state):
        should, reason = rc.should_dispatch_rebuild(state, repo=repo, token=token, runner=runner)
        if should:
            dispatch = dispatcher or dispatch_rebuild
            ok, detail = dispatch(repo=repo, token=token, runner=runner)
            reason = detail
            dispatched = ok
            # Recorded whether or not the dispatch succeeded. A failed
            # request must still leave the mismatch open.
            rc.mark_rebuild_requested(state)

    state["last_dispatch_ok"] = dispatched
    state["last_dispatch_reason"] = reason
    # Reconciliation gets its OWN clock and touches nothing else
    # (2026-08-28 P0 follow-up). Advancing the global `updated_at` here
    # would have let a perfectly healthy reconciliation pass make a dead
    # sportsbook-price or game-state channel look healthy to
    # check_live_freshness -- the precise architecture this whole branch
    # exists to remove. reconciliation.checked_at answers only "did
    # reconciliation run", and check_live_freshness reports it without
    # ever counting it toward health.
    live["reconciliation"] = state
    atomic_write_json(live_path, live)

    counts = state["counts"]
    print(f"reconciliation {state['checked_at']}: "
          f"open={len(state['open'])} "
          f"(board_age={counts['board_age']} lineup={counts['lineup']} "
          f"line_moved={counts['line_moved']}) "
          f"resolved_this_cycle={len(state['resolved_this_cycle'])} -- {reason}")
    for entry in list(state["open"].values())[:10]:
        print(f"  MISMATCH {entry['kind']}: {entry['detail']} "
              f"(open since {entry['first_seen_at']}, "
              f"{entry['rebuild_requests']} rebuild request(s))")
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=os.path.join(REPO_ROOT, "docs", "data.json"))
    ap.add_argument("--live", default=os.path.join(REPO_ROOT, "docs", "live.json"))
    args = ap.parse_args(argv)
    if not os.path.exists(args.data):
        print("no board to reconcile yet.")
        return 0
    state = run(args.data, args.live)
    if rc.needs_rebuild(state) and not state.get("last_dispatch_ok"):
        # Open and not acted on. Visible, and it stays visible every cycle
        # until publication actually matches -- never cleared by asking.
        print(f"::error::Publication does not match authoritative state and no rebuild "
              f"was started this cycle: {state.get('last_dispatch_reason')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
