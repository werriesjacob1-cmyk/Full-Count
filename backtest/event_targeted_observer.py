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
BURST_INTERVAL_S = 8.0   # FanDuel polling cadence during a triggered window
BURST_POLLS = 10         # bounded burst length -- never open-ended
TRIGGER_KINDS = ("pitcher_change", "scoring_play", "inning_transition", "batter_change")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log(handle, rec):
    handle.write(json.dumps(rec) + "\n")
    handle.flush()


def detect_triggers(prev_mlb, cur_mlb):
    """Real, cheap, robust triggers from fetch_mlb_state()'s own fields --
    deliberately NOT attempting a full baserunner/leverage parse (that
    needs the play-by-play detail this coarse state doesn't carry); a
    pitcher change, a scoring play, an inning transition, and a batter
    change already cover the moments most likely to move a live market,
    and each is a single, unambiguous field comparison -- no guessing."""
    if prev_mlb is None:
        return []
    triggers = []
    if cur_mlb.get("pitcher") and cur_mlb.get("pitcher") != prev_mlb.get("pitcher"):
        triggers.append(("pitcher_change",
                         f"{prev_mlb.get('pitcher')!r} -> {cur_mlb.get('pitcher')!r}"))
    prev_score = (prev_mlb.get("away_score"), prev_mlb.get("home_score"))
    cur_score = (cur_mlb.get("away_score"), cur_mlb.get("home_score"))
    if None not in prev_score and None not in cur_score and prev_score != cur_score:
        triggers.append(("scoring_play", f"{prev_score} -> {cur_score}"))
    if (cur_mlb.get("inning"), cur_mlb.get("half")) != (prev_mlb.get("inning"), prev_mlb.get("half")):
        triggers.append(("inning_transition",
                         f"{prev_mlb.get('half')} {prev_mlb.get('inning')} -> "
                         f"{cur_mlb.get('half')} {cur_mlb.get('inning')}"))
    if cur_mlb.get("batter") and cur_mlb.get("batter") != prev_mlb.get("batter"):
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
                for kind, detail in triggers:
                    _log(handle, {"kind": "trigger_detected", "ts": _now(),
                                  "trigger": kind, "detail": detail})
                # BURST WINDOW: bounded, aborts on any real FanDuel failure.
                for i in range(BURST_POLLS):
                    fd_state, fd_lat, failures = snapshot_event(event_id)
                    n_burst_polls += 1
                    changes = diff_fanduel(prev_fd, fd_state)
                    for ch in changes:
                        ch.update({"kind_prefix": "fd_change_during_burst", "ts": _now(),
                                   "burst_poll": i})
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
                    if i < BURST_POLLS - 1:
                        time.sleep(BURST_INTERVAL_S)
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
