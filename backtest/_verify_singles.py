#!/usr/bin/env python3
"""_verify_singles.py -- one-off OLD-vs-NEW backtest verification script for
the singles modelled-probability fix (see prop_probability.p_at_least_singles
and generate_picks.py's `families` list). NOT part of the shipped pipeline --
scratch tooling for this one PR's evidence-gathering, run manually.

Re-simulates the EXACT 130 real historical dates that already produced the
694 OLD singles rows in backtest/rows.jsonl (committed, frozen, never
touched by this script), using simulate_date() -- the same point-in-time-safe
machinery every other backtest row in this repo was built from -- with the
NEW singles code path active (this script runs against a checkout that
already has p_at_least_singles wired in). Writes one NEW row per (date,
player, game) singles observation to backtest/_singles_verify_new.jsonl,
appending as it goes so partial progress survives an interruption.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as eng
import mlb_daily as m

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_singles_verify_new.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_singles_verify_state.json")


def old_singles_dates():
    dates = set()
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("prop_type") == "singles":
                dates.add(row["date"])
    return sorted(dates)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def main():
    dates = old_singles_dates()
    print(f"{len(dates)} real historical dates to re-simulate: {dates[0]} .. {dates[-1]}",
          flush=True)

    state = load_state()
    done = set(state["done"])

    # One StatcastStore per season -- same construction run_backtest() uses,
    # just built once per year up front since our date list spans 3 seasons
    # non-contiguously (run_backtest itself assumes one contiguous range).
    by_year = {}
    for d in dates:
        by_year.setdefault(d[:4], []).append(d)
    stores = {}
    for year, yd in sorted(by_year.items()):
        through = eng.shift(max(yd), -1)
        print(f"Loading StatcastStore for {year} (through {through})...", flush=True)
        store = eng.StatcastStore(int(year), through, verbose=True)
        store.load()
        stores[year] = store

    # Same box-score dedup wrapper run_backtest() applies internally.
    _orig_box = m.statsapi.boxscore_data
    _box_cache = {}

    def _cached_box(game_id, *a, **kw):
        if game_id not in _box_cache:
            _box_cache[game_id] = _orig_box(game_id, *a, **kw)
        return _box_cache[game_id]
    m.statsapi.boxscore_data = _cached_box

    n_new_rows = 0
    try:
        for i, d in enumerate(dates, 1):
            if d in done:
                print(f"[{i}/{len(dates)}] {d}  already done -- skipping", flush=True)
                continue
            store = stores[d[:4]]
            t0 = time.time()
            try:
                res = eng.simulate_date(d, store, verbose=True)
            except Exception as e:
                print(f"[{i}/{len(dates)}] {d}  FAILED: {e!r}", flush=True)
                state["failed"][d] = repr(e)
                save_state(state)
                continue
            elapsed = round(time.time() - t0, 1)
            if res.status != "ok":
                print(f"[{i}/{len(dates)}] {d}  status={res.status} reason={getattr(res, 'reason', None)}",
                     flush=True)
                state["failed"][d] = f"status={res.status} reason={getattr(res, 'reason', None)}"
                save_state(state)
                continue
            singles_rows = [r for r in res.rows if r.get("prop_type") == "singles"]
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                for row in singles_rows:
                    f.write(json.dumps(row) + "\n")
            n_new_rows += len(singles_rows)
            print(f"[{i}/{len(dates)}] {d}  {len(singles_rows)} new singles rows, "
                 f"{elapsed}s ({n_new_rows} total so far)", flush=True)
            done.add(d)
            state["done"] = sorted(done)
            save_state(state)
    finally:
        m.statsapi.boxscore_data = _orig_box

    print(f"\nDONE. {n_new_rows} new singles rows written to {OUT_PATH}", flush=True)
    if state["failed"]:
        print(f"{len(state['failed'])} dates failed: {state['failed']}", flush=True)


if __name__ == "__main__":
    main()
