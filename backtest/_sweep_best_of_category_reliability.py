#!/usr/bin/env python3
"""_sweep_best_of_category_reliability.py -- OLD vs NEW backtest for a
proposed selection-quality fix to select_best_by_category().

MECHANISM: rank_for_board() (the real main-board ranking function) was
fixed 2026-08-13 to demote grade-D (thin-evidence) picks behind everything
else before sorting by market_edge/hit_probability -- see that function's
own docstring: sorting on probability alone put a "12-start grade-D pick
above a 107-game grade-A pick with sixteen times the lift." That exact
same _RELIABILITY_ORDER = {"A":0,"B":0,"C":0,"D":1} tiering, however, was
never applied to select_best_by_category() (generate_picks.py:3679),
which still sorts each prop family's candidates by raw hit_probability
alone. select_best_by_category() feeds the "Best of Every Category" board
section, every per-market dashboard tab, and backtest's own
best_of_category_extras() -- 476 of 660 real graded picks in the last 16
production days (72%) came from this exact code path, more real volume
than the main board itself.

WHY THIS NEEDS A SCALED BACKTEST RATHER THAN TRUSTING THE MAIN-BOARD
PRECEDENT ALONE: checking this same D-vs-non-D split directly on the 16
real production days' best_of_category picks came back statistically
flat (D-grade hit_rate=0.368 vs A-grade 0.369, D-tier share nearly
identical between real hits and misses: 19.4% vs 19.0%) -- the opposite
of what the main-board precedent would predict, on a sample too thin (38
D-grade picks) to trust either way. This script tests the same OLD vs NEW
ordering at ~40x the sample size, spread across 2024-2026, using rows
already collected for this project's other backtests.

METHODOLOGY: for each date, run the same build_inputs/build_candidates/
attach_hit_probabilities pipeline every other backtest tool in this repo
uses, then attach_reliability() (needs emp_batters/emp_pitchers, real
per-date empirical support -- this is exactly what apply_replay_policy_
precalibration() does, reused directly). Call the REAL, unmodified
select_best_by_category(candidates, ..., n_per_category=9999, min_score=0)
-- same call best_of_category_extras() already makes -- to get every
eligible candidate per stat family with reliability attached. That result
is already OLD-sorted (hit_probability descending, current shipped
behavior); take its first 5 per stat as OLD's top-5 (n_per_category=5
matches the real live call). Independently re-sort the SAME full entries
list with the proposed NEW key (D-tier demoted, then hit_probability) and
take its own top 5. No production code is modified -- both orderings are
produced by pure post-processing of the same real function's real output.

    /tmp/mlbvenv/bin/python3 backtest/_sweep_best_of_category_reliability.py
"""
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as eng
import generate_picks as gp
import mlb_sources as msrc
import mlb_daily as m
import grade_results as gr

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bc_reliability_pairs.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bc_reliability_state.json")
N_PER_CATEGORY = 5
_RELIABILITY_ORDER = {"A": 0, "B": 0, "C": 0, "D": 1}


def target_dates():
    dates = set()
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            dates.add(json.loads(line)["date"])
    dates = sorted(dates)
    return dates[::10]  # evenly spread sample across the full 2024-2026 range


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


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
    print(f"{len(dates)} target dates: {dates[0]} .. {dates[-1]}", flush=True)

    state = load_state()
    done = set(state["done"])

    by_year = defaultdict(list)
    for d in dates:
        by_year[d[:4]].append(d)
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
        for i, date in enumerate(dates, 1):
            if date in done:
                print(f"[{i}/{len(dates)}] {date}  already done -- skipping", flush=True)
                continue
            store = stores[date[:4]]
            t0 = time.time()
            try:
                with eng.PointInTime(date, store):
                    game_meta, kwargs, comp_table, _pit_df, log = eng.build_inputs(
                        date, store, use_weather=False, use_bullpen=True, verbose=False)
                    if not game_meta:
                        state["failed"][date] = "no_games"
                        save_state(state)
                        continue
                    candidates = gp.build_candidates(game_meta, **kwargs)
                    emp_batters = kwargs.get("emp_batters", {})
                    emp_pitchers = kwargs.get("emp_pitchers", {})
                    league_rates = msrc.league_base_rates(window_days=msrc.LEAGUE_RATE_WINDOW_DAYS)
                    gp.attach_hit_probabilities(candidates, comp_table, emp_batters,
                                                emp_pitchers, league_rates)
                    gp.apply_calibration(candidates, gp.load_calibrator())
                    gp.attach_reliability(candidates, emp_batters, emp_pitchers)
                    by_category = gp.select_best_by_category(
                        candidates, prices={}, fd=eng.fd, n_per_category=9999, k_prices=None, min_score=0)
            except eng.LookaheadError:
                raise
            except Exception as e:
                print(f"[{i}/{len(dates)}] {date}  FAILED: {type(e).__name__}: {e}", flush=True)
                state["failed"][date] = f"{type(e).__name__}: {e}"
                save_state(state)
                continue

            statuses = gr.fetch_game_statuses(date)
            pairs_this_date = 0
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                for stat, entries in by_category.items():
                    if not entries:
                        continue
                    old_top = entries[:N_PER_CATEGORY]  # already hit_probability-sorted (shipped OLD)
                    new_top = sorted(
                        entries,
                        key=lambda e: (-_RELIABILITY_ORDER.get(e.get("reliability") or "D", 1),
                                      e["hit_probability"]),
                        reverse=True,
                    )[:N_PER_CATEGORY]
                    old_ids = {(e.get("player_id"), (e.get("projection") or {}).get("needs")) for e in old_top}
                    new_ids = {(e.get("player_id"), (e.get("projection") or {}).get("needs")) for e in new_top}
                    if old_ids == new_ids:
                        continue  # identical selection -- nothing to compare for this stat tonight
                    for e in old_top:
                        out = real_outcome(e, statuses, date)
                        if out is None:
                            continue
                        f.write(json.dumps({"date": date, "stat": stat, "policy": "OLD",
                                            "player_id": e.get("player_id"),
                                            "reliability": e.get("reliability"),
                                            "hit_probability": e.get("hit_probability"),
                                            "outcome": out}) + "\n")
                        pairs_this_date += 1
                    for e in new_top:
                        out = real_outcome(e, statuses, date)
                        if out is None:
                            continue
                        f.write(json.dumps({"date": date, "stat": stat, "policy": "NEW",
                                            "player_id": e.get("player_id"),
                                            "reliability": e.get("reliability"),
                                            "hit_probability": e.get("hit_probability"),
                                            "outcome": out}) + "\n")
                        pairs_this_date += 1
            n_pairs_total += pairs_this_date
            elapsed = round(time.time() - t0, 1)
            print(f"[{i}/{len(dates)}] {date}  graded_pairs={pairs_this_date}  {elapsed}s "
                 f"(total so far: {n_pairs_total})", flush=True)
            done.add(date)
            state["done"] = sorted(done)
            save_state(state)
    finally:
        m.statsapi.boxscore_data = _orig_box

    print(f"\nDONE. {n_pairs_total} graded rows written to {OUT_PATH}", flush=True)
    if state["failed"]:
        print(f"{len(state['failed'])} dates failed: {state['failed']}", flush=True)


if __name__ == "__main__":
    main()
