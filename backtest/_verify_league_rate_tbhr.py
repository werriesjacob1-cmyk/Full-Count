#!/usr/bin/env python3
"""_verify_league_rate_tbhr.py -- extends _verify_league_rate.py's OLD-vs-NEW
league_base_rates(window_days=...) verification to total_bases and home_runs,
the other two markets the original accuracy directive named alongside hits
("across hits/total_bases/home_runs"). _verify_league_rate.py only covered
hits because backtest/rows.jsonl's own total_bases/home_run rows are all
confined to 2026 (43 dates, checked directly) -- too thin for a real
cold-start-vs-steady-state comparison on their own.

That thinness doesn't actually block testing them: grade_results.grade_pick()
grades ANY candidate's projection (stat, needs) against the real box score
for that game_pk/player_id, independent of whether rows.jsonl happens to
already contain a prior graded row for that stat on that date. So this
script reuses the EXACT SAME 68 target dates as _verify_league_rate.py (same
real games, same real lineups) and simply extracts total_bases/home_runs
candidates from the same per-date candidate build instead of hits --
directly comparable results, same OLD-vs-NEW methodology, same window
candidate (30 days).

Separate output files (_league_rate_verify_tbhr_pairs.jsonl /
..._tbhr_state.json) so this never collides with the hits run, and can run
concurrently with it (confirmed real CPU/memory headroom before launching).
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
import _verify_league_rate as vlr  # reuse target_dates() -- identical date list to the hits pass

WINDOW_DAYS = 30
STATS = ("total_bases", "home_runs")

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_league_rate_verify_tbhr_pairs.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_league_rate_verify_tbhr_state.json")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def run_one_pass(date, store, window_days):
    """Same call order as _verify_league_rate.py's run_one_pass, but
    extracts total_bases AND home_runs entries (both share the identical
    league-rate shrink mechanism this PR targets)."""
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
            # total_bases/home_runs almost never win build_candidates()'s
            # single primary-pick slot per batter (see engine.py's own
            # comment above its best_of_category_extras() call) -- they
            # ship live via that separate extras path, so this MUST include
            # it too or the by_stat lists come back structurally empty,
            # exactly like _verify_singles.py already had to for singles.
            extra_candidates = eng.best_of_category_extras(candidates)
        by_stat = {s: [] for s in STATS}
        for c in list(candidates) + list(extra_candidates):
            stat = (c.get("projection") or {}).get("stat")
            if stat in by_stat:
                by_stat[stat].append(dict(c))
        return by_stat, "ok"
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
    dates = vlr.target_dates()
    print(f"{len(dates)} target dates (same as hits pass): "
          f"{dates[0][0]} .. {dates[-1][0]}, window_days={WINDOW_DAYS}, stats={STATS}", flush=True)

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
            msrc._LEAGUE_RATES_CACHE.clear()
            old_by_stat, old_status = run_one_pass(d, store, window_days=None)
            msrc._LEAGUE_RATES_CACHE.clear()
            new_by_stat, new_status = run_one_pass(d, store, window_days=WINDOW_DAYS)
            elapsed = round(time.time() - t0, 1)
            if old_status != "ok" or new_status != "ok":
                print(f"[{i}/{len(dates)}] {d} ({tag})  FAILED old={old_status} new={new_status}",
                     flush=True)
                state["failed"][d] = f"old={old_status} new={new_status}"
                save_state(state)
                continue

            statuses = gr.fetch_game_statuses(d)
            pairs_this_date = 0
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                for stat in STATS:
                    old_by_key = {(e["player_id"], e["game_pk"]): e for e in old_by_stat[stat]}
                    new_by_key = {(e["player_id"], e["game_pk"]): e for e in new_by_stat[stat]}
                    all_keys = set(old_by_key) | set(new_by_key)
                    for key in all_keys:
                        old_e = old_by_key.get(key)
                        new_e = new_by_key.get(key)
                        grading_entry = new_e or old_e
                        outcome = real_outcome(grading_entry, statuses, d)
                        if outcome is None:
                            continue
                        f.write(json.dumps({
                            "date": d, "tag": tag, "stat": stat,
                            "player_id": key[0], "game_pk": key[1],
                            "player_name": grading_entry.get("name"),
                            "old_prob": old_e.get("hit_probability") if old_e else None,
                            "old_basis": old_e.get("probability_basis") if old_e else None,
                            "new_prob": new_e.get("hit_probability") if new_e else None,
                            "new_basis": new_e.get("probability_basis") if new_e else None,
                            "outcome": outcome,
                        }) + "\n")
                        pairs_this_date += 1
            n_pairs_total += pairs_this_date
            print(f"[{i}/{len(dates)}] {d} ({tag})  graded_pairs={pairs_this_date} {elapsed}s "
                 f"(total so far: {n_pairs_total})", flush=True)
            done.add(d)
            state["done"] = sorted(done)
            save_state(state)
    finally:
        m.statsapi.boxscore_data = _orig_box

    print(f"\nDONE. {n_pairs_total} graded OLD/NEW pairs written to {OUT_PATH}", flush=True)
    if state["failed"]:
        print(f"{len(state['failed'])} dates failed: {state['failed']}", flush=True)


if __name__ == "__main__":
    main()
