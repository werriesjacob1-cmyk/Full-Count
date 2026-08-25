#!/usr/bin/env python3
"""event_targeted_observer.py -- the redesigned live-edge experiment.

Two passive full-slate observer runs this session (2.1min + 82.6min, 3-4
real games, 96-100 unique markets, ~5,989 open-market-minutes -- see
backtest/fanduel_observer_final_report.md) produced ZERO confirmed FanDuel
odds/line changes. That is now real evidence that "watch a handful of games
continuously and hope for repricing" is low-signal, not that FanDuel never
reprices. This script changes the experiment instead of repeating it:

    MLB live event detector
    -> detect a high-leverage state transition (pitcher change, scoring
       play, inning transition, batter change)
    -> identify the affected FanDuel event (already known -- one game)
    -> BURST: temporarily raise FanDuel polling cadence for a short window
       right after the trigger, then fall back to a slow baseline cadence
    -> track stable market/runner identity across tab migrations (reuses
       fanduel_live_observer.py's own snapshot_event(), same shape)
    -> capture suspend -> reopen -> old/new odds or line, previousWinRunnerOdds,
       and how long a new price stays open, honestly as a BOUND (polling can
       never know the true server-side repricing instant)

Does not hammer endpoints: the burst only fires on a real detected trigger,
runs a bounded number of polls, and aborts back to baseline cadence on any
FanDuel fetch failure -- same discipline as fanduel_live_observer.py's own
progressive-cadence health gate.

    /tmp/mlbvenv/bin/python3 backtest/event_targeted_observer.py \
        --game-pk 776970 --event-id 35973135 --minutes 90

If no live game is available right now (verified via pick_live_games()),
this exits cleanly rather than fabricating a run -- see main() below.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0] if "/" in __file__ else ".")
sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from alive_brain_prototype import fetch_mlb_state  # noqa: E402
from fanduel_live_observer import pick_live_games, snapshot_event  # noqa: E402

DEFAULT_LOG = __file__.rsplit("/", 1)[0] + "/event_targeted_observer_log.jsonl"
BASE_INTERVAL_S = 25.0   # idle cadence: cheap MLB poll every cycle, slow FD poll to hold a baseline
BURST_INTERVAL_S = 8.0   # FanDuel polling cadence during a triggered window (default tier)
BURST_POLLS = 10         # bounded burst length -- never open-ended (default tier)
TRIGGER_KINDS = (
    "pitcher_change", "home_run", "multi_run_scoring_play", "scoring_play",
    "bases_loaded", "inning_transition", "batting_order_turnover", "batter_change",
)

# 2026-08-25: ranked by expected FanDuel pricing impact, per the explicit
# targeting-strategy improvement this session was asked for after the first
# live run produced real triggers but zero confirmed repricing -- the fix
# for "more signal, still no price change" is BETTER TARGETING, not a
# longer run. Tier 1 = burst hardest (most polls, fastest cadence); tier 5
# = lightest touch (a real event worth logging, but not worth spending
# request budget chasing as hard). Honest limitation: MLB's coarse live
# feed lets us detect a REAL home run via `result.eventType == "home_run"`
# (not inferred from score deltas -- see alive_brain_prototype.fetch_mlb_state's
# own docstring), but cannot distinguish "starter pulled for a reliever"
# from "regularly scheduled pitching change" without deeper roster-role
# context this prototype doesn't carry -- pitcher_change is treated as
# tier 1 regardless, since either case is a real event that can move a
# pitcher-specific market.
TRIGGER_TIERS = {
    "pitcher_change": 1,
    "home_run": 2,
    "multi_run_scoring_play": 2,
    "scoring_play": 3,          # single-run or otherwise-unclassified scoring
    "bases_loaded": 3,
    "inning_transition": 4,
    "batting_order_turnover": 5,
    "batter_change": 5,
}
# (burst_interval_s, burst_polls) per tier -- tier 1 hardest, tier 5 lightest.
# Tier 1's ceiling (14 polls @ 5s = 70s of coverage) is deliberately ABOVE
# the old flat default (10 @ 8s = 80s -- comparable total window, tighter
# cadence) specifically for pitcher changes, the single highest-value
# trigger; tier 5 is deliberately BELOW it (6 @ 10s = 60s) so a routine
# batter change doesn't spend the same request budget as a real event.
TIER_BURST_PLAN = {
    1: (5.0, 14),
    2: (6.0, 12),
    3: (7.0, 10),
    4: (8.0, 8),
    5: (10.0, 6),
}


def burst_plan_for(triggers):
    """Given this cycle's detected (kind, detail) triggers, return the
    (interval_s, n_polls) burst plan for the single HIGHEST-priority tier
    present -- simultaneous triggers (e.g. a scoring play during an inning
    transition) burst at the more aggressive of the two, not diluted."""
    if not triggers:
        return BURST_INTERVAL_S, BURST_POLLS
    best_tier = min(TRIGGER_TIERS.get(kind, 5) for kind, _detail in triggers)
    return TIER_BURST_PLAN[best_tier]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log(handle, rec):
    handle.write(json.dumps(rec) + "\n")
    handle.flush()


def detect_triggers(prev_mlb, cur_mlb):
    """Real, cheap, robust triggers from fetch_mlb_state()'s own fields, each
    a single unambiguous comparison -- no guessing. 2026-08-25: upgraded from
    4 to 8 trigger kinds (see TRIGGER_TIERS) once alive_brain_prototype's
    fetch_mlb_state() started surfacing real baserunner occupancy, batting
    order, and MLB's own play-classification (`result.eventType`) instead of
    just inning/score/batter/pitcher -- lets this distinguish an actual home
    run from a routine scoring play, and real bases-loaded leverage / lineup
    turnover from a generic batter change, rather than only inferring from
    score deltas. Backward-compatible: a caller still passing the old
    4-field state dict (no on_1b/on_2b/on_3b/batting_order/last_event_type)
    simply won't trigger the new kinds -- .get() everywhere, no KeyError."""
    if prev_mlb is None:
        return []
    triggers = []
    if cur_mlb.get("pitcher") and cur_mlb.get("pitcher") != prev_mlb.get("pitcher"):
        triggers.append(("pitcher_change",
                         f"{prev_mlb.get('pitcher')!r} -> {cur_mlb.get('pitcher')!r}"))

    if cur_mlb.get("last_event_type") == "home_run" and (
            prev_mlb.get("last_event_type") != "home_run"
            or prev_mlb.get("batter") != cur_mlb.get("batter")):
        triggers.append(("home_run", f"{cur_mlb.get('batter')!r} — {cur_mlb.get('last_event')!r}"))

    prev_score = (prev_mlb.get("away_score"), prev_mlb.get("home_score"))
    cur_score = (cur_mlb.get("away_score"), cur_mlb.get("home_score"))
    if None not in prev_score and None not in cur_score and prev_score != cur_score:
        run_delta = (cur_score[0] - prev_score[0]) + (cur_score[1] - prev_score[1])
        if run_delta >= 2:
            triggers.append(("multi_run_scoring_play", f"{prev_score} -> {cur_score} (+{run_delta})"))
        else:
            triggers.append(("scoring_play", f"{prev_score} -> {cur_score}"))

    bases_now = (cur_mlb.get("on_1b"), cur_mlb.get("on_2b"), cur_mlb.get("on_3b"))
    if all(v is not None for v in bases_now) and all(bases_now):
        bases_before = (prev_mlb.get("on_1b"), prev_mlb.get("on_2b"), prev_mlb.get("on_3b"))
        if bases_before != bases_now:
            triggers.append(("bases_loaded", f"outs={cur_mlb.get('outs')}"))

    if (cur_mlb.get("inning"), cur_mlb.get("half")) != (prev_mlb.get("inning"), prev_mlb.get("half")):
        triggers.append(("inning_transition",
                         f"{prev_mlb.get('half')} {prev_mlb.get('inning')} -> "
                         f"{cur_mlb.get('half')} {cur_mlb.get('inning')}"))

    prev_order, cur_order = prev_mlb.get("batting_order"), cur_mlb.get("batting_order")
    if (isinstance(prev_order, int) and isinstance(cur_order, int)
            and cur_order < prev_order):
        triggers.append(("batting_order_turnover", f"slot {prev_order} -> {cur_order}"))
    elif cur_mlb.get("batter") and cur_mlb.get("batter") != prev_mlb.get("batter"):
        triggers.append(("batter_change",
                         f"{prev_mlb.get('batter')!r} -> {cur_mlb.get('batter')!r}"))
    return triggers


def diff_fanduel(prev, cur):
    """Compares two snapshot_event() results (market_id -> {name, status,
    runners: {runner_id: {name, odds, previous_odds, handicap, status}}}).
    Returns a list of change records -- odds, handicap (line), and status
    changes reported SEPARATELY (a line move and a price move at the same
    line are different events, matching fanduel_live_observer.py's own
    documented convention)."""
    changes = []
    if prev is None:
        return changes
    for mid, mk in cur.items():
        pmk = prev.get(mid)
        if pmk is None:
            continue  # a brand-new market this cycle -- not a "change" to an existing price
        if pmk.get("status") != mk.get("status"):
            changes.append({"kind": "status_change", "market_id": mid, "market_name": mk.get("name"),
                            "from": pmk.get("status"), "to": mk.get("status")})
        for rid, r in (mk.get("runners") or {}).items():
            pr = (pmk.get("runners") or {}).get(rid)
            if pr is None:
                continue
            if pr.get("odds") != r.get("odds"):
                changes.append({
                    "kind": "odds_change", "market_id": mid, "market_name": mk.get("name"),
                    "runner_id": rid, "runner_name": r.get("name"),
                    "old_odds": pr.get("odds"), "new_odds": r.get("odds"),
                    "previous_win_runner_odds_field": r.get("previous_odds"),
                    "previous_win_runner_odds_matches_last_observed":
                        r.get("previous_odds") == pr.get("odds"),
                    "market_status_at_change": mk.get("status"),
                    "runner_status_at_change": r.get("status"),
                })
            if pr.get("handicap") != r.get("handicap"):
                changes.append({
                    "kind": "line_change", "market_id": mid, "market_name": mk.get("name"),
                    "runner_id": rid, "runner_name": r.get("name"),
                    "old_line": pr.get("handicap"), "new_line": r.get("handicap"),
                    "market_status_at_change": mk.get("status"),
                })
    return changes


def run(game_pk, event_id, total_minutes, log_path):
    prev_mlb, prev_fd = None, None
    last_unchanged_ts = {}  # (market_id, runner_id) -> ts of the last poll where odds were unchanged
    deadline = time.time() + total_minutes * 60
    n_triggers, n_burst_polls, n_changes = 0, 0, 0
    with open(log_path, "a") as handle:
        _log(handle, {"kind": "run_start", "ts": _now(), "game_pk": game_pk, "event_id": event_id,
                      "base_interval_s": BASE_INTERVAL_S, "burst_interval_s": BURST_INTERVAL_S,
                      "burst_polls": BURST_POLLS})
        while time.time() < deadline:
            cur_mlb, mlb_lat = fetch_mlb_state(game_pk)
            triggers = detect_triggers(prev_mlb, cur_mlb)
            _log(handle, {"kind": "mlb_poll", "ts": _now(), "state": cur_mlb,
                          "latency_s": round(mlb_lat, 3)})

            if triggers:
                n_triggers += len(triggers)
                burst_interval, burst_polls = burst_plan_for(triggers)
                best_tier = min(TRIGGER_TIERS.get(kind, 5) for kind, _detail in triggers)
                for kind, detail in triggers:
                    _log(handle, {"kind": "trigger_detected", "ts": _now(),
                                  "trigger": kind, "detail": detail,
                                  "tier": TRIGGER_TIERS.get(kind, 5)})
                _log(handle, {"kind": "burst_plan", "ts": _now(), "tier": best_tier,
                              "burst_interval_s": burst_interval, "burst_polls": burst_polls})
                # BURST WINDOW: bounded, aborts on any real FanDuel failure.
                for i in range(burst_polls):
                    fd_state, fd_lat, failures = snapshot_event(event_id)
                    n_burst_polls += 1
                    changes = diff_fanduel(prev_fd, fd_state)
                    for ch in changes:
                        ch.update({"kind_prefix": "fd_change_during_burst", "ts": _now(),
                                   "burst_poll": i, "trigger_tier": best_tier})
                        _log(handle, ch)
                        n_changes += 1
                    _log(handle, {"kind": "fd_burst_poll", "ts": _now(), "burst_poll": i,
                                  "latency_s": round(fd_lat, 3), "n_markets": len(fd_state),
                                  "failures": failures})
                    prev_fd = fd_state
                    if failures:
                        _log(handle, {"kind": "burst_aborted_on_failure", "ts": _now(),
                                      "burst_poll": i, "failures": failures})
                        break
                    if i < burst_polls - 1:
                        time.sleep(burst_interval)
                prev_mlb = cur_mlb
                continue

            # Idle cadence: still poll FanDuel (slower) to maintain a real
            # prev_fd baseline -- a burst comparing against a stale
            # baseline from several minutes ago would misattribute a
            # change's timing to the trigger when it may have happened
            # earlier, during the idle period.
            fd_state, fd_lat, failures = snapshot_event(event_id)
            changes = diff_fanduel(prev_fd, fd_state)
            for ch in changes:
                ch.update({"kind_prefix": "fd_change_during_idle", "ts": _now()})
                _log(handle, ch)
                n_changes += 1
            _log(handle, {"kind": "fd_idle_poll", "ts": _now(), "latency_s": round(fd_lat, 3),
                          "n_markets": len(fd_state), "failures": failures})
            prev_fd, prev_mlb = fd_state, cur_mlb
            time.sleep(max(1.0, BASE_INTERVAL_S))

        _log(handle, {"kind": "run_end", "ts": _now(), "n_triggers": n_triggers,
                      "n_burst_polls": n_burst_polls, "n_fd_changes_observed": n_changes})
    print(f"Done. {n_triggers} triggers detected, {n_burst_polls} burst polls, "
          f"{n_changes} real FanDuel changes observed. Log: {log_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-pk", type=int, default=None)
    ap.add_argument("--event-id", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=90)
    ap.add_argument("--log", default=DEFAULT_LOG)
    args = ap.parse_args()

    if args.game_pk is None or args.event_id is None:
        live = pick_live_games(n=3)
        if not live:
            print("No live games available right now (pick_live_games() found none) -- "
                  "this is a real, honest null, not an error. This script is ready; the "
                  "next live test needs an actual live slate. Not fabricating a run.")
            sys.exit(0)
        print("No --game-pk/--event-id given. Live FanDuel events found (pick a game_pk "
              "yourself from the real MLB schedule for the SAME game, then re-run with "
              "both --game-pk and --event-id):")
        for event_id, name, open_date in live:
            print(f"  event_id={event_id}  {name}  opened={open_date}")
        sys.exit(0)

    run(args.game_pk, args.event_id, args.minutes, args.log)


if __name__ == "__main__":
    main()
