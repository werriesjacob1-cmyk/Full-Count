#!/usr/bin/env python3
"""_sweep_league_rate_window.py -- tests window_days in {7, 14, 21, 30}
against identical historical inputs for the "hits" market, extending the
OLD-vs-NEW verification already done for the shipped 30-day default.
Scratch tooling, not part of the shipped pipeline.

WHY 7/14/21/30 AND NOT A WIDER SET (e.g. 45): a direct point-in-time check
(2024-04-10 through 2024-05-20) found that on early-season dates, window=30
is frequently IDENTICAL to window=None (no window at all) -- e.g. at
2024-04-10 (season 10 days old), window=30 and window=None both read
hits_1plus=0.486, while window=7 reads 0.556 and window=14 reads 0.551.
A trailing N-day window can only differ from the cumulative average once
the season itself is older than N days; a 30-day window is therefore
nearly powerless to fix the EARLY April dates specifically, which is where
the largest slice of hits' calibration gap concentrates (checked directly:
excluding April, the historically worst-miscalibrated probability bucket's
gap drops from +0.100 to +0.026 -- in line with every other bucket).
This sweep tests whether a shorter window trades that unresponsiveness for
an acceptable amount of week-to-week noise, rather than assuming either
direction.

Reuses the exact same 68-date target set as the original league-rate
verification (60 April 2024/2025 dates -- where the cold-start bias was
found -- plus 8 June-Sept control dates), so results are directly
comparable to the already-shipped evidence. All three windows are computed
from the SAME point-in-time fetch per date (one real fetch, three probes),
isolating the comparison from any other code drift.

    /tmp/mlbvenv/bin/python3 backtest/_sweep_league_rate_window.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as eng
import generate_picks as gp
import mlb_sources as msrc
import mlb_daily as m
import grade_results as gr

WINDOWS = (7, 14, 21, 30)
ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sweep_window_pairs.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sweep_window_state.json")


def target_dates():
    """Identical selection logic to the original _verify_league_rate.py:
    April 2024/2025 (the suspect window) + a 5-per-year stratified control
    sample from steady-state months, restricted to dates with a real graded
    'hits' row in rows.jsonl."""
    hits_dates = set()
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("prop_type") == "hits":
                hits_dates.add(row["date"])

    april = sorted(d for d in hits_dates if d[:7] in ("2024-04", "2025-04"))

    control = []
    for year in ("2024", "2025"):
        for month in ("06", "07", "08", "09"):
            month_dates = sorted(d for d in hits_dates if d[:7] == f"{year}-{month}")
            if month_dates:
                control.append(month_dates[len(month_dates) // 2])

    tagged = [(d, "april") for d in april] + [(d, "control") for d in control]
    tagged.sort(key=lambda x: x[0])
    return tagged


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def run_one_pass(date, store, window_days):
    try:
        with eng.PointInTime(date, store) as pit:
            game_meta, kwargs, comp_table, _pit_df, log = eng.build_inputs(
                date, store, use_weather=False, use_bullpen=True, verbose=False)
            if not game_meta:
                return None, "no_games"
            candidates = gp.build_candidates(game_meta, **kwargs)
            emp_batters = kwargs.get("emp_batters", {})
            emp_pitchers = kwargs.get("emp_pitchers", {})
            league_rates = msrc.league_base_rates(window_days=window_days)
            gp.attach_hit_probabilities(candidates, comp_table, emp_batters,
                                        emp_pitchers, league_rates)
        hits_entries = []
        for c in candidates:
            if (c.get("projection") or {}).get("stat") == "hits":
                hits_entries.append(dict(c))
        return hits_entries, "ok"
    except eng.LookaheadError:
        raise
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def real_outcome(entry, statuses, date):
    try:
        graded = gr.grade_pick(entry, statuses, date=date)
    except Exception:
        return None
    grade = graded.get("grade")
    if grade == "hit":
        return 1
    if grade == "miss":
        return 0
    return None


def main():
    dates = target_dates()
    print(f"{len(dates)} target dates: {dates[0][0]} .. {dates[-1][0]}, "
          f"windows={WINDOWS}", flush=True)

    state = load_state()
    done = set(state["done"])

    by_year = {}
    for d, _tag in dates:
        by_year.setdefault(d[:4], []).append(d)
    stores = {}
    for year, yd in sorted(by_year.items()):
        through = eng.shift(max(yd), -1)
        print(f"Loading StatcastStore for {year} (through {through})...", flush=True)
        store = eng.StatcastStore(int(year), through, verbose=True)
        store.load()
        stores[year] = store

    _orig_box = m.statsapi.boxscore_data
    _box_cache = {}

    def _cached_box(game_id, *a, **kw):
        if game_id not in _box_cache:
            _box_cache[game_id] = _orig_box(game_id, *a, **kw)
        return _box_cache[game_id]
    m.statsapi.boxscore_data = _cached_box

    n_pairs_total = 0
    try:
        for i, (d, tag) in enumerate(dates, 1):
            if d in done:
                print(f"[{i}/{len(dates)}] {d} ({tag})  already done -- skipping", flush=True)
                continue
            store = stores[d[:4]]
            t0 = time.time()
            by_window = {}
            status_ok = True
            for w in WINDOWS:
                msrc._LEAGUE_RATES_CACHE.clear()
                entries, status = run_one_pass(d, store, window_days=w)
                if status != "ok":
                    status_ok = False
                    break
                by_window[w] = {(e["player_id"], e["game_pk"]): e for e in entries}
            elapsed = round(time.time() - t0, 1)
            if not status_ok:
                print(f"[{i}/{len(dates)}] {d} ({tag})  FAILED", flush=True)
                state["failed"][d] = "one or more windows failed"
                save_state(state)
                continue

            all_keys = set()
            for w in WINDOWS:
                all_keys |= set(by_window[w].keys())
            statuses = gr.fetch_game_statuses(d)

            pairs_this_date = 0
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                for key in all_keys:
                    entries = {w: by_window[w].get(key) for w in WINDOWS}
                    grading_entry = next((e for e in entries.values() if e), None)
                    if grading_entry is None:
                        continue
                    outcome = real_outcome(grading_entry, statuses, d)
                    if outcome is None:
                        continue
                    row = {
                        "date": d, "tag": tag, "player_id": key[0], "game_pk": key[1],
                        "player_name": grading_entry.get("name"),
                        "outcome": outcome,
                    }
                    for w in WINDOWS:
                        e = entries[w]
                        row[f"prob_{w}"] = e.get("hit_probability") if e else None
                        row[f"basis_{w}"] = e.get("probability_basis") if e else None
                    f.write(json.dumps(row) + "\n")
                    pairs_this_date += 1
            n_pairs_total += pairs_this_date
            print(f"[{i}/{len(dates)}] {d} ({tag})  graded_pairs={pairs_this_date} "
                 f"{elapsed}s (total so far: {n_pairs_total})", flush=True)
            done.add(d)
            state["done"] = sorted(done)
            save_state(state)
    finally:
        m.statsapi.boxscore_data = _orig_box

    print(f"\nDONE. {n_pairs_total} graded rows written to {OUT_PATH}", flush=True)
    if state["failed"]:
        print(f"{len(state['failed'])} dates failed: {state['failed']}", flush=True)


if __name__ == "__main__":
    main()
