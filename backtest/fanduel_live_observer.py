#!/usr/bin/env python3
"""fanduel_live_observer.py -- sustained, polling-only observation of
FanDuel's real, unauthenticated sportsbook API against real live MLB games,
to answer a concrete question: is this data source good enough for a
live/real-time product, and how fast can Full Count realistically detect a
price/line change or suspension?

POLLING-ONLY, SO EXACT CHANGE TIME IS UNKNOWN. Every "change-to-observation
latency" this script reports is a bound: (last poll where a value was seen
unchanged) -> (first poll where it was seen different), never a true delta.
That bound is reported honestly, not invented precision.

Supports PROGRESSIVE CADENCE: pass multiple --interval values (e.g.
--interval 40 --interval 20 --interval 10) and each phase only runs if the
previous phase had zero request failures -- a phase that sees any failure
stops the step-down and the run ends there, so this never blindly hammers
the source. Each phase's own request/failure/latency counts are logged and
reported separately.

Every detected change is logged with FULL detail: stable market/runner
identity, previous and current line (handicap) and odds separately (a line
move and a price move at the same line are different events), the
previousWinRunnerOdds field, market status immediately around the change,
and the last-unchanged/first-changed poll timestamps. Market removals are
checked against a short lookback window for a same-event/same-name market
reappearing under a new id (tab migration) before being logged as a true
removal.

    /tmp/mlbvenv/bin/python3 backtest/fanduel_live_observer.py --minutes 60 --interval 40 --interval 20 --interval 10 --games 6
    /tmp/mlbvenv/bin/python3 backtest/fanduel_live_observer.py --summarize backtest/fanduel_observer_log.jsonl
"""
import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0] if "/" in __file__ else ".")
import odds_fanduel as fd  # noqa: E402

DEFAULT_LOG = __file__.rsplit("/", 1)[0] + "/fanduel_observer_log.jsonl"
TABS = ("popular", "batter-props", "innings")
MIGRATION_LOOKBACK_POLLS = 3  # a removed market reappearing under a new id within this many polls counts as migration, not removal


def _now():
    return datetime.now(timezone.utc).isoformat()


def pick_live_games(n=6):
    """Choose n currently in-progress games: openDate in the past. list_games()
    also returns future slates' already-posted lines, so this filters to
    games whose first pitch has already happened."""
    games = fd.list_games()
    now = datetime.now(timezone.utc)
    live = []
    for event_id, name, open_date in games:
        try:
            start = datetime.fromisoformat(open_date.replace("Z", "+00:00"))
        except Exception:
            continue
        if start < now:
            live.append((event_id, name, open_date))
    live.sort(key=lambda g: g[2])
    return live[:n]


def snapshot_event(event_id):
    """One real poll of one event's real market pages. Returns
    (markets_by_id, elapsed_s, failures)."""
    t0 = time.time()
    markets, ok, failures = fd._market_pages(event_id, TABS)
    elapsed = time.time() - t0
    by_id = {}
    for mk in markets:
        mid = mk.get("marketId")
        if mid is None:
            continue
        runners = {}
        for rn in (mk.get("runners") or []):
            rid = rn.get("runnerId")
            if rid is None:
                continue
            odds = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
            prev = ((rn.get("previousWinRunnerOdds") or {}).get("americanDisplayOdds", {}) or {}).get("americanOddsInt")
            runners[rid] = {
                "name": rn.get("runnerName"), "odds": odds, "previous_odds": prev,
                "handicap": rn.get("handicap"), "status": rn.get("runnerStatus"),
            }
        by_id[mid] = {
            "name": mk.get("marketName"), "type": mk.get("marketType"),
            "inPlay": mk.get("inPlay"), "status": mk.get("marketStatus"),
            "runners": runners,
        }
    return by_id, elapsed, failures


def _log(handle, rec):
    handle.write(json.dumps(rec) + "\n")


def run_phase(phase_idx, targets, minutes, interval_s, log_path, prev_state, last_seen_ts,
             pending_removed, phase_stats):
    """Runs one cadence phase. Returns True if the phase completed with zero
    request failures (the step-down gate), False otherwise (or if it had to
    stop early because a failure occurred -- the caller does not start the
    next, faster phase in that case)."""
    print(f"\n--- PHASE {phase_idx}: interval={interval_s}s, up to {minutes} min, "
          f"{len(targets)} game(s) ---")
    deadline = time.time() + minutes * 60
    poll_n = 0
    phase_failures = 0
    phase_requests = 0
    phase_latencies = []
    healthy = True
    with open(log_path, "a") as log:
        while time.time() < deadline:
            poll_started = time.time()
            poll_n += 1
            for event_id, name, _ in targets:
                by_id, elapsed, failures = snapshot_event(event_id)
                phase_requests += 1
                phase_latencies.append(elapsed)
                if failures:
                    phase_failures += 1
                rec = {"kind": "poll", "ts": _now(), "phase": phase_idx, "interval_s": interval_s,
                       "poll": poll_n, "event_id": event_id, "game": name,
                       "request_s": round(elapsed, 3), "n_markets": len(by_id),
                       "failures": list(failures)}
                _log(log, rec)

                prior = prev_state.get(event_id, {})
                now_ts = _now()
                for mid, mk in by_id.items():
                    p = prior.get(mid)
                    if p is None:
                        # Check if this is really a migration: a market removed
                        # from this event recently, same name, reappearing under
                        # a new id.
                        key = (event_id, mk["name"])
                        recent_removed = pending_removed.get(key)
                        if recent_removed and poll_n - recent_removed["poll"] <= MIGRATION_LOOKBACK_POLLS:
                            _log(log, {"kind": "market_migrated", "ts": now_ts, "event_id": event_id,
                                       "name": mk["name"], "old_market_id": recent_removed["market_id"],
                                       "new_market_id": mid, "polls_gap": poll_n - recent_removed["poll"]})
                            del pending_removed[key]
                        else:
                            _log(log, {"kind": "market_new", "ts": now_ts, "event_id": event_id,
                                       "market_id": mid, "name": mk["name"]})
                        last_seen_ts[(event_id, mid)] = now_ts
                        continue
                    if p["status"] != mk["status"]:
                        _log(log, {"kind": "market_status_change", "ts": now_ts,
                                   "event_id": event_id, "market_id": mid, "name": mk["name"],
                                   "from": p["status"], "to": mk["status"], "inPlay": mk["inPlay"]})
                    for rid, r in mk["runners"].items():
                        pr = (p.get("runners") or {}).get(rid)
                        if pr is None:
                            continue
                        odds_changed = pr["odds"] != r["odds"]
                        line_changed = pr.get("handicap") != r.get("handicap")
                        if odds_changed or line_changed:
                            last_unchanged = last_seen_ts.get((event_id, mid, rid), "unknown")
                            _log(log, {
                                "kind": "odds_or_line_change", "ts": now_ts,
                                "event_id": event_id, "market_id": mid, "market_name": mk["name"],
                                "runner": r["name"], "runner_id": rid,
                                "odds_changed": odds_changed, "line_changed": line_changed,
                                "prev_odds": pr["odds"], "cur_odds": r["odds"],
                                "prev_line": pr.get("handicap"), "cur_line": r.get("handicap"),
                                "previous_win_runner_odds_field": r["previous_odds"],
                                "market_status_at_change": mk["status"],
                                "last_unchanged_observation_ts": last_unchanged,
                                "first_changed_observation_ts": now_ts,
                            })
                        last_seen_ts[(event_id, mid, rid)] = now_ts
                    last_seen_ts[(event_id, mid)] = now_ts
                for mid in prior:
                    if mid not in by_id:
                        pending_removed[(event_id, prior[mid]["name"])] = {"market_id": mid, "poll": poll_n}
                        _log(log, {"kind": "market_removed_pending_migration_check", "ts": now_ts,
                                   "event_id": event_id, "market_id": mid, "name": prior[mid]["name"]})
                prev_state[event_id] = by_id
            log.flush()
            if phase_failures > 0:
                print(f"  [phase {phase_idx}] request failure observed at poll {poll_n} -- "
                      f"stopping this phase, will not step down to a faster interval.")
                healthy = False
                break
            elapsed_this_round = time.time() - poll_started
            sleep_for = max(1.0, interval_s - elapsed_this_round)
            if time.time() + sleep_for < deadline:
                time.sleep(sleep_for)
            else:
                break
    lat_sorted = sorted(phase_latencies)
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else None
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))] if lat_sorted else None
    stats = {"phase": phase_idx, "interval_s": interval_s, "polls": poll_n,
             "requests": phase_requests, "failures": phase_failures,
             "latency_p50": p50, "latency_p95": p95, "healthy": healthy}
    phase_stats.append(stats)
    print(f"  [phase {phase_idx}] requests={phase_requests} failures={phase_failures} "
          f"p50={p50:.3f}s p95={p95:.3f}s healthy={healthy}" if p50 else
          f"  [phase {phase_idx}] no requests completed")
    return healthy


def observe(minutes_per_phase, intervals, n_games, log_path):
    targets = pick_live_games(n_games)
    if not targets:
        print("No currently in-progress games found -- nothing to observe.")
        return
    print(f"Observing {len(targets)} live game(s), progressive cadence {intervals}s, "
          f"up to {minutes_per_phase} min/phase:")
    for eid, name, od in targets:
        print(f"  {eid}  {name}  (started {od})")

    prev_state = {}
    last_seen_ts = {}
    pending_removed = {}
    phase_stats = []
    for i, interval_s in enumerate(intervals, 1):
        healthy = run_phase(i, targets, minutes_per_phase, interval_s, log_path,
                            prev_state, last_seen_ts, pending_removed, phase_stats)
        if not healthy:
            print(f"Stopping progressive cadence after phase {i} (interval={interval_s}s) "
                  f"due to a request failure -- not stepping down further.")
            break
    print("\nPer-phase summary:")
    for s in phase_stats:
        print(f"  phase {s['phase']}: interval={s['interval_s']}s requests={s['requests']} "
              f"failures={s['failures']} p50={s['latency_p50']} p95={s['latency_p95']} "
              f"healthy={s['healthy']}")
    print(f"Done. Logged to {log_path}")


def summarize(log_path):
    polls = []
    events_by_kind = defaultdict(list)
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events_by_kind[rec["kind"]].append(rec)
            if rec["kind"] == "poll":
                polls.append(rec)

    print("=" * 90)
    print(f"FANDUEL LIVE OBSERVER SUMMARY -- {log_path}")
    print("=" * 90)
    if not polls:
        print("No poll records found.")
        return

    by_phase = defaultdict(list)
    for p in polls:
        by_phase[p.get("phase", 1)].append(p)
    for phase, prs in sorted(by_phase.items()):
        lat = sorted(p["request_s"] for p in prs)
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        fails = sum(1 for p in prs if p["failures"])
        interval = prs[0].get("interval_s", "?")
        print(f"Phase {phase} (interval={interval}s): requests={len(prs)} failures={fails} "
              f"p50={p50:.3f}s p95={p95:.3f}s max={max(lat):.3f}s")

    lat_all = [p["request_s"] for p in polls]
    lat_all.sort()
    print(f"\nOverall: {len(polls)} event-polls, p50={lat_all[len(lat_all)//2]:.3f}s, "
          f"p95={lat_all[min(len(lat_all)-1, int(len(lat_all)*0.95))]:.3f}s, "
          f"mean={statistics.mean(lat_all):.3f}s")
    fail_polls = [p for p in polls if p["failures"]]
    print(f"Total polls with failures: {len(fail_polls)}/{len(polls)}")

    changes = events_by_kind["odds_or_line_change"]
    odds_changes = [c for c in changes if c["odds_changed"]]
    line_changes = [c for c in changes if c["line_changed"]]
    print(f"\nOdds changes: {len(odds_changes)}   Line changes: {len(line_changes)}   "
          f"(a single event can be both)")
    for c in changes[:30]:
        print(f"  {c['ts']}  {c['market_name']} / {c['runner']}: "
              f"odds {c['prev_odds']}->{c['cur_odds']}  line {c['prev_line']}->{c['cur_line']}  "
              f"status_at_change={c['market_status_at_change']}  "
              f"last_unchanged={c['last_unchanged_observation_ts']}")

    print(f"\nMarket status changes (suspend/reopen): {len(events_by_kind['market_status_change'])}")
    dirs = defaultdict(int)
    for rec in events_by_kind["market_status_change"]:
        dirs[(rec["from"], rec["to"])] += 1
    print(f"  directions: {dict(dirs)}")

    print(f"New markets (genuinely new, not migration): {len(events_by_kind['market_new'])}")
    print(f"Market migrations (removed id -> new id, same name, within "
          f"{MIGRATION_LOOKBACK_POLLS} polls): {len(events_by_kind['market_migrated'])}")
    for rec in events_by_kind["market_migrated"][:10]:
        print(f"  {rec['name']}: {rec['old_market_id']} -> {rec['new_market_id']} "
              f"(gap {rec['polls_gap']} polls)")
    removed_pending = len(events_by_kind["market_removed_pending_migration_check"])
    migrated = len(events_by_kind["market_migrated"])
    print(f"Markets removed and NOT reclaimed as a migration (true removal): "
          f"{removed_pending - migrated} of {removed_pending} pending-removal events")

    matches, mismatches = 0, 0
    for rec in odds_changes:
        if rec.get("previous_win_runner_odds_field") == rec.get("prev_odds"):
            matches += 1
        else:
            mismatches += 1
    if matches + mismatches:
        print(f"\npreviousWinRunnerOdds == the price we last observed: {matches} of "
              f"{matches + mismatches} odds-change events")
    else:
        print("\npreviousWinRunnerOdds: no odds changes observed to check against")
    print("=" * 90)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20, help="minutes per cadence phase")
    ap.add_argument("--interval", type=float, action="append", default=None,
                    help="poll interval in seconds; repeat for progressive step-down, e.g. "
                         "--interval 40 --interval 20 --interval 10")
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--summarize", default=None, help="Path to an existing log to summarize instead of observing")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        intervals = args.interval or [40.0]
        observe(args.minutes, intervals, args.games, args.log)
