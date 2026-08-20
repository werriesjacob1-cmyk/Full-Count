#!/usr/bin/env python3
"""_verify_league_rate.py -- OLD-vs-NEW backtest verification for the
league_base_rates(window_days=...) rolling-window fix (mlb_sources.py,
NOT yet wired into any live call site). Scratch tooling for this PR's
evidence-gathering only, mirrors backtest/_verify_singles.py's proven
methodology exactly: run OLD and NEW from IDENTICAL point-in-time inputs,
in the SAME pass, toggling ONLY the one thing this PR changes -- whether
mlb_sources.league_base_rates() is called with window_days=None (OLD,
today's shipped cumulative average) or window_days=WINDOW_DAYS (NEW,
candidate rolling window). This isolates the ablation from all other code
drift, exactly like the singles verification did for p_at_least_singles.

SCOPE OF THIS FIRST PASS: the "hits" market only (richest date coverage --
395 real historical dates spanning 2024-2026, vs total_bases/home_run's 43
dates confined to 2026 only, per a direct check of rows.jsonl). Within
that, two deliberately different date sets:

  - APRIL dates (2024-04, 2025-04): where the cold-start bias was found
    live (checked point-in-time: hits_1plus reads 0.4712-0.4867 in early
    April vs a ~0.535-0.539 mid-May+ steady state). This is the set that
    should show the improvement if the hypothesis is right.
  - CONTROL dates (5 per year, June/July/August/September 2024/2025,
    stratified one per month): steady-state months where league_base_
    rates() is NOT expected to be meaningfully biased. This set exists
    specifically to catch the "must not overcorrect" risk -- a window
    that's too short could make the STEADY-STATE calibration WORSE by
    trading away real signal for noise. If NEW improves April but
    degrades the control set, that is a real finding to report, not a
    result to discard.

WINDOW_DAYS is a single candidate (30) for this first pass -- a sweep
across other candidates (21/45) is a natural follow-up if this pass's
result is inconclusive, not run here to keep this pass's own runtime
tractable.

Writes matched (date, player_id, game_pk) OLD/NEW pairs to
backtest/_league_rate_verify_pairs.jsonl, appending as it goes so partial
progress survives an interruption. Resumable via
backtest/_league_rate_verify_state.json exactly like _verify_singles.py.
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

WINDOW_DAYS = 30

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_league_rate_verify_pairs.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_league_rate_verify_state.json")


def target_dates():
    """April 2024/2025 (the suspect window) + a 5-per-year stratified
    control sample from steady-state months, restricted to dates that
    actually have a real graded 'hits' row in rows.jsonl (so every date
    picked is one we know the pipeline can price)."""
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
                control.append(month_dates[len(month_dates) // 2])  # one mid-month date

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
    """One full point-in-time-safe candidate build + probability pass,
    mirroring simulate_date()'s real call order (build_inputs,
    build_candidates, attach_hit_probabilities with the real league_rates
    argument). league_rates is the ONLY thing toggled between OLD and NEW
    -- window_days=None reproduces today's shipped call exactly,
    window_days=WINDOW_DAYS is the candidate fix. Everything else
    (candidates, comp_table, emp_batters/emp_pitchers) is unaffected by
    this toggle and is recomputed identically both times, same as
    _verify_singles.py's proven approach."""
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
                entry = dict(c)
                hits_entries.append(entry)
        return hits_entries, "ok"
    except eng.LookaheadError:
        raise
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def real_outcome(entry, statuses, date):
    """Same real-grading reuse as _verify_singles.py -- grade_pick() is
    the exact function simulate_date's own grading step calls."""
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
    print(f"{len(dates)} target dates (april + control): "
          f"{dates[0][0]} .. {dates[-1][0]}, window_days={WINDOW_DAYS}", flush=True)

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
            old_entries, old_status = run_one_pass(d, store, window_days=None)
            msrc._LEAGUE_RATES_CACHE.clear()
            new_entries, new_status = run_one_pass(d, store, window_days=WINDOW_DAYS)
            elapsed = round(time.time() - t0, 1)
            if old_status != "ok" or new_status != "ok":
                print(f"[{i}/{len(dates)}] {d} ({tag})  FAILED old={old_status} new={new_status}",
                     flush=True)
                state["failed"][d] = f"old={old_status} new={new_status}"
                save_state(state)
                continue

            old_by_key = {(e["player_id"], e["game_pk"]): e for e in old_entries}
            new_by_key = {(e["player_id"], e["game_pk"]): e for e in new_entries}
            all_keys = set(old_by_key) | set(new_by_key)
            statuses = gr.fetch_game_statuses(d)

            pairs_this_date = 0
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                for key in all_keys:
                    old_e = old_by_key.get(key)
                    new_e = new_by_key.get(key)
                    grading_entry = new_e or old_e
                    outcome = real_outcome(grading_entry, statuses, d)
                    if outcome is None:
                        continue
                    f.write(json.dumps({
                        "date": d, "tag": tag, "player_id": key[0], "game_pk": key[1],
                        "player_name": grading_entry.get("name"),
                        "old_prob": old_e.get("hit_probability") if old_e else None,
                        "old_basis": old_e.get("probability_basis") if old_e else None,
                        "new_prob": new_e.get("hit_probability") if new_e else None,
                        "new_basis": new_e.get("probability_basis") if new_e else None,
                        "outcome": outcome,
                    }) + "\n")
                    pairs_this_date += 1
            n_pairs_total += pairs_this_date
            print(f"[{i}/{len(dates)}] {d} ({tag})  old={len(old_entries)} new={len(new_entries)} "
                 f"graded_pairs={pairs_this_date} {elapsed}s (total so far: {n_pairs_total})",
                 flush=True)
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
