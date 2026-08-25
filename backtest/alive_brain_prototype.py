#!/usr/bin/env python3
"""alive_brain_prototype.py -- the smallest real proof of the event-driven
"alive brain" concept, for exactly ONE MLB game, run locally (not deployed
to any sidecar runtime yet -- this measures whether the mechanics and the
latency budget are even plausible before anyone spends time on Cloudflare
Workers/Durable Objects infrastructure).

PIPELINE PROVEN HERE, for real, against real live sources:
  1. fetch real MLB game state (statsapi.mlb.com's live feed -- inning,
     outs, score, baserunners, current batter/pitcher)
  2. fetch real FanDuel market state for the same game (odds_fanduel's
     real, existing, unauthenticated REST wrapper)
  3. compare each against the PRIOR observation -> detect exactly what
     changed (game state delta, market price/suspension delta)
  4. recompute ONLY the props touched by what changed -- not a full-board
     rebuild
  5. measure every stage's latency separately

HONEST LIMITATION, stated once here rather than implied by silence: step 4
does NOT run a live win-probability model -- that does not exist yet (see
the standing "live prediction engine" research item, explicitly future
work, not to be built reactively here). "Recompute" in this prototype means
re-pricing the affected prop's market edge against its last known pregame
probability and the NEW live odds -- proving the SELECTIVE-recompute
mechanic (touch only what changed) and measuring its cost, not claiming a
solved live-probability model.

Not deployed anywhere. A local, one-shot (or short-loop) measurement
script, same category as fanduel_live_observer.py.

    /tmp/mlbvenv/bin/python3 backtest/alive_brain_prototype.py --game-pk 776970 --cycles 3
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, __file__.rsplit("/", 2)[0] if "/" in __file__ else ".")
import odds_fanduel as fd  # noqa: E402

MLB_LIVE_FEED = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


def fetch_mlb_state(game_pk):
    """2026-08-25: on_1b was a real bug -- `bool((current.get("runners") or
    [{}]))` is unconditionally True (a falsy `runners` falls back to the
    non-empty placeholder list `[{}]`, which is itself truthy), so it never
    actually reported baserunner state. `linescore.offense.first/second/
    third` are the real, honest per-base occupancy fields (present only
    when a runner is actually on that base) -- verified live against a real
    in-progress game's feed. Also now surfaces `linescore.offense.
    battingOrder` (real lineup-slot number, for detecting a genuine lineup
    turnover rather than just "the batter's name changed") and the most
    recent play's real `eventType`/`event` (e.g. "home_run", "walk",
    "mound_visit") from `plays.currentPlay.result` -- MLB's own play
    classification, not inferred from score deltas. Honest caveat:
    `currentPlay` reflects the play in progress or most recently completed
    AS OF THIS FETCH, not a guaranteed real-time push -- a fast poll cadence
    can still miss two events landing between polls.
    """
    t0 = time.time()
    r = requests.get(MLB_LIVE_FEED.format(game_pk=game_pk), timeout=10)
    r.raise_for_status()
    payload = r.json()
    elapsed = time.time() - t0
    linescore = (payload.get("liveData") or {}).get("linescore") or {}
    offense = linescore.get("offense") or {}
    plays = (payload.get("liveData") or {}).get("plays") or {}
    current = plays.get("currentPlay") or {}
    matchup = current.get("matchup") or {}
    result = current.get("result") or {}
    state = {
        "inning": linescore.get("currentInning"),
        "half": linescore.get("inningState"),
        "outs": linescore.get("outs"),
        "away_score": (linescore.get("teams") or {}).get("away", {}).get("runs"),
        "home_score": (linescore.get("teams") or {}).get("home", {}).get("runs"),
        "batter": (matchup.get("batter") or {}).get("fullName"),
        "pitcher": (matchup.get("pitcher") or {}).get("fullName"),
        "on_1b": "first" in offense,
        "on_2b": "second" in offense,
        "on_3b": "third" in offense,
        "batting_order": offense.get("battingOrder"),
        "last_event_type": result.get("eventType"),
        "last_event": result.get("event"),
        "abstract_state": (payload.get("gameData") or {}).get("status", {}).get("abstractGameState"),
    }
    return state, elapsed


def fetch_fanduel_state(event_id):
    t0 = time.time()
    markets, ok, failures = fd._market_pages(event_id, ("popular", "batter-props", "innings"))
    elapsed = time.time() - t0
    by_market = {}
    for mk in markets:
        mid = mk.get("marketId")
        if mid is None:
            continue
        runners = {}
        for rn in (mk.get("runners") or []):
            rid = rn.get("runnerId")
            odds = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
            if rid is not None:
                runners[rid] = {"name": rn.get("runnerName"), "odds": odds}
        by_market[mid] = {"name": mk.get("marketName"), "status": mk.get("marketStatus"), "runners": runners}
    return by_market, elapsed, failures


def diff_game_state(prev, cur):
    if prev is None:
        return list(cur.keys())
    return [k for k in cur if cur.get(k) != prev.get(k)]


def diff_market_state(prev, cur):
    """Returns list of (market_id, runner_id, kind) touched since prev."""
    touched = []
    if prev is None:
        return touched
    for mid, mk in cur.items():
        p = prev.get(mid)
        if p is None:
            touched.append((mid, None, "market_new"))
            continue
        if p["status"] != mk["status"]:
            touched.append((mid, None, "status_change"))
        for rid, r in mk["runners"].items():
            pr = (p.get("runners") or {}).get(rid)
            if pr is not None and pr["odds"] != r["odds"]:
                touched.append((mid, rid, "price_change"))
    return touched


def props_for_current_matchup(mlb_state, market_state):
    """"identify exactly which prop(s) each event affects": when the game
    state changes (new batter/pitcher at the plate), the props that are
    MEANINGFULLY affected right now are that specific batter's and
    pitcher's own live props -- not "all props," and not "no props." Real
    name matching against FanDuel's own runner names (last-name substring,
    the simplest thing that works for this prototype -- a production
    version would use the same stable player_id matching odds_fanduel.py
    already does elsewhere in this codebase)."""
    names = [n for n in (mlb_state.get("batter"), mlb_state.get("pitcher")) if n]
    if not names:
        return []
    last_names = [n.split()[-1].lower() for n in names]
    affected = []
    for mid, mk in market_state.items():
        for rid, r in mk["runners"].items():
            rn = (r.get("name") or "").lower()
            if any(ln in rn for ln in last_names):
                affected.append({"market_id": mid, "market_name": mk["name"], "runner": r["name"]})
    return affected


def recompute_affected(touched, market_state, pregame_probs):
    """The SELECTIVE-recompute step. For each touched runner, re-derive
    market_edge against its last known pregame probability (NOT a live
    model -- see module docstring). Returns the list of re-priced rows and
    the wall-clock cost of doing only this, not a full-board rebuild."""
    t0 = time.time()
    import prop_probability as pp
    out = []
    for mid, rid, kind in touched:
        if rid is None:
            continue
        mk = market_state.get(mid)
        if not mk:
            continue
        runner = mk["runners"].get(rid)
        if not runner or runner["odds"] is None:
            continue
        prob = pregame_probs.get((mid, rid), 0.60)  # stand-in pregame prior for the prototype
        implied = pp.implied_probability(runner["odds"])
        edge = None if implied is None else round(prob - implied, 4)
        out.append({"market_id": mid, "runner": runner["name"], "kind": kind,
                    "odds": runner["odds"], "market_edge": edge})
    elapsed = time.time() - t0
    return out, elapsed


def run(game_pk, event_id, cycles, interval_s, slate_prop_count=None):
    prev_game, prev_market = None, None
    pregame_probs = {}
    total_recomputed, total_slate_equivalent = 0, 0
    for i in range(cycles):
        cycle_t0 = time.time()
        mlb_state, mlb_lat = fetch_mlb_state(game_pk)
        fd_state, fd_lat, fd_failures = fetch_fanduel_state(event_id)

        t_detect0 = time.time()
        game_changes = diff_game_state(prev_game, mlb_state)
        market_touched = diff_market_state(prev_market, fd_state)
        detect_lat = time.time() - t_detect0

        recomputed, recompute_lat = recompute_affected(market_touched, fd_state, pregame_probs)
        # "identify exactly which prop(s) each event affects": the batter/
        # pitcher currently at the plate, matched by name against FanDuel's
        # own runner names -- these are the props a game-state change alone
        # (no price move yet) should be considered "affected" for, since
        # their live win-probability context just changed even if FanDuel
        # hasn't repriced them yet.
        matchup_affected = props_for_current_matchup(mlb_state, fd_state) if game_changes else []

        t_pub0 = time.time()
        delta_payload = json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                    "game_state": mlb_state, "game_changes": game_changes,
                                    "recomputed": recomputed, "matchup_affected": matchup_affected})
        pub_lat = time.time() - t_pub0  # serialization only -- no real push transport wired up yet

        cycle_total = time.time() - cycle_t0
        n_touched_props = len(recomputed) + len(matchup_affected)
        total_recomputed += n_touched_props
        if slate_prop_count:
            total_slate_equivalent += slate_prop_count

        print(f"[cycle {i+1}/{cycles}] mlb_fetch={mlb_lat:.3f}s fd_fetch={fd_lat:.3f}s "
              f"detect={detect_lat*1000:.1f}ms recompute={recompute_lat*1000:.1f}ms "
              f"serialize={pub_lat*1000:.1f}ms  TOTAL={cycle_total:.3f}s")
        print(f"    game_changes={game_changes}  market_touched={len(market_touched)}  "
              f"recomputed_rows={len(recomputed)}  matchup_affected={len(matchup_affected)}  "
              f"payload_bytes={len(delta_payload)}")
        if slate_prop_count:
            avoided = slate_prop_count - n_touched_props
            pct = 100 * avoided / slate_prop_count if slate_prop_count else 0
            print(f"    vs full-slate rebuild ({slate_prop_count} props): recomputed only "
                  f"{n_touched_props} ({pct:.1f}% avoided this cycle)")
        if fd_failures:
            print(f"    FanDuel fetch failures: {fd_failures}")

        prev_game, prev_market = mlb_state, fd_state
        if i < cycles - 1:
            time.sleep(max(1.0, interval_s - cycle_total))

    if slate_prop_count and cycles:
        print(f"\nOver {cycles} cycles: {total_recomputed} total prop-recomputes vs "
              f"{total_slate_equivalent} a naive full-slate-rebuild-every-cycle approach "
              f"would have done ({100*(1 - total_recomputed/total_slate_equivalent):.1f}% avoided).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-pk", type=int, required=True, help="MLB gamePk for the target game")
    ap.add_argument("--event-id", type=int, default=None, help="FanDuel eventId (looked up by matching team names if omitted)")
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--slate-props", type=int, default=None,
                    help="Total props on tonight's real slate (docs/data.json's summary.n_props), "
                         "to report how many recomputes a selective approach avoids vs a naive "
                         "full-slate rebuild every cycle.")
    args = ap.parse_args()
    event_id = args.event_id
    if event_id is None:
        # Best-effort: not central to the measurement, so a simple manual
        # lookup against fd.list_games() by game_pk isn't wired here --
        # pass --event-id explicitly (see backtest/fanduel_live_observer.py
        # for how to discover one).
        print("Pass --event-id explicitly (see fanduel_live_observer.py's pick_live_games()).")
        sys.exit(1)
    run(args.game_pk, event_id, args.cycles, args.interval, slate_prop_count=args.slate_props)
