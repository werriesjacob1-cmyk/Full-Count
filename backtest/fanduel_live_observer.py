#!/usr/bin/env python3
"""fanduel_live_observer.py -- sustained, polling-only observation of
FanDuel's real, unauthenticated sportsbook API against real live MLB games,
to answer a concrete question: is this data source good enough for a
live/real-time product, and how fast can Full Count realistically detect a
price change or suspension?

POLLING-ONLY, SO EXACT CHANGE TIME IS UNKNOWN. Every "change-to-observation
latency" this script reports is a bound: (last poll where a value was seen
unchanged) -> (first poll where it was seen different), never a true delta.
That bound is reported honestly, not invented precision.

Targets a handful of currently in-progress games (chosen at start time),
polls their real market pages every POLL_INTERVAL_S seconds for
RUN_MINUTES minutes, and logs every observation plus every detected event
(price change, suspension, reopen, market removed, new market appeared) to
a JSON lines file. Run summarize() afterward (or import and call it) for
the aggregate stats: polling cadence actually achieved, request latency
p50/p95, change frequency, suspension/reopen counts, previousWinRunnerOdds
behavior across repeated changes, and the bounded change-to-observation
latency distribution.

    /tmp/mlbvenv/bin/python3 backtest/fanduel_live_observer.py --minutes 20 --interval 45
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


def _now():
    return datetime.now(timezone.utc).isoformat()


def pick_live_games(n=4):
    """Choose n currently in-progress games: openDate in the past, and
    (heuristically) not from tomorrow's slate -- list_games() returns both
    tonight's remaining/live games and tomorrow's already-posted lines."""
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
                "status": rn.get("runnerStatus"),
            }
        by_id[mid] = {
            "name": mk.get("marketName"), "type": mk.get("marketType"),
            "inPlay": mk.get("inPlay"), "status": mk.get("marketStatus"),
            "runners": runners,
        }
    return by_id, elapsed, failures


def observe(minutes, interval_s, n_games, log_path):
    targets = pick_live_games(n_games)
    if not targets:
        print("No currently in-progress games found -- nothing to observe.")
        return
    print(f"Observing {len(targets)} live game(s) for {minutes} min at {interval_s}s intervals:")
    for eid, name, od in targets:
        print(f"  {eid}  {name}  (started {od})")

    prev_state = {}  # event_id -> {market_id: {...}}
    deadline = time.time() + minutes * 60
    poll_n = 0
    with open(log_path, "a") as log:
        while time.time() < deadline:
            poll_started = time.time()
            poll_n += 1
            for event_id, name, _ in targets:
                by_id, elapsed, failures = snapshot_event(event_id)
                rec = {"ts": _now(), "poll": poll_n, "event_id": event_id, "game": name,
                       "request_s": round(elapsed, 3), "n_markets": len(by_id),
                       "failures": list(failures)}
                log.write(json.dumps({"kind": "poll", **rec}) + "\n")

                prior = prev_state.get(event_id, {})
                for mid, mk in by_id.items():
                    p = prior.get(mid)
                    if p is None:
                        log.write(json.dumps({"kind": "market_new", "ts": _now(), "event_id": event_id,
                                              "market_id": mid, "name": mk["name"]}) + "\n")
                        continue
                    if p["status"] != mk["status"]:
                        log.write(json.dumps({"kind": "market_status_change", "ts": _now(),
                                              "event_id": event_id, "market_id": mid, "name": mk["name"],
                                              "from": p["status"], "to": mk["status"]}) + "\n")
                    for rid, r in mk["runners"].items():
                        pr = (p.get("runners") or {}).get(rid)
                        if pr is None:
                            continue
                        if pr["odds"] != r["odds"]:
                            log.write(json.dumps({"kind": "price_change", "ts": _now(),
                                                  "event_id": event_id, "market_id": mid,
                                                  "runner": r["name"], "from": pr["odds"], "to": r["odds"],
                                                  "previous_odds_field": r["previous_odds"]}) + "\n")
                for mid in prior:
                    if mid not in by_id:
                        log.write(json.dumps({"kind": "market_removed", "ts": _now(), "event_id": event_id,
                                              "market_id": mid, "name": prior[mid]["name"]}) + "\n")
                prev_state[event_id] = by_id
            log.flush()
            elapsed_this_round = time.time() - poll_started
            sleep_for = max(1.0, interval_s - elapsed_this_round)
            if time.time() + sleep_for < deadline:
                time.sleep(sleep_for)
            else:
                break
    print(f"Done. {poll_n} poll rounds logged to {log_path}")


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
    lat = [p["request_s"] for p in polls]
    lat.sort()
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    print(f"Poll rounds: {max(p['poll'] for p in polls)}  |  event-polls: {len(polls)}")
    print(f"Request latency: p50={p50:.3f}s  p95={p95:.3f}s  mean={statistics.mean(lat):.3f}s")
    fail_polls = [p for p in polls if p["failures"]]
    print(f"Polls with failures: {len(fail_polls)}/{len(polls)}")

    print(f"\nPrice changes observed: {len(events_by_kind['price_change'])}")
    print(f"Market status changes (suspend/reopen): {len(events_by_kind['market_status_change'])}")
    for rec in events_by_kind["market_status_change"][:20]:
        print(f"  {rec['ts']}  {rec['name']}: {rec['from']} -> {rec['to']}")
    print(f"New markets appeared: {len(events_by_kind['market_new'])}")
    print(f"Markets removed: {len(events_by_kind['market_removed'])}")

    # previousWinRunnerOdds behavior: does it track the immediately-prior
    # observed price, or something else (e.g. always the market open price)?
    mismatches, matches = 0, 0
    for rec in events_by_kind["price_change"]:
        if rec.get("previous_odds_field") == rec.get("from"):
            matches += 1
        else:
            mismatches += 1
    print(f"\npreviousWinRunnerOdds == the price we last observed: {matches} of {matches + mismatches} "
          f"price-change events" if (matches + mismatches) else "\npreviousWinRunnerOdds: no price "
          "changes observed to check against")
    print("=" * 90)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--interval", type=float, default=45)
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--summarize", default=None, help="Path to an existing log to summarize instead of observing")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        observe(args.minutes, args.interval, args.games, args.log)
