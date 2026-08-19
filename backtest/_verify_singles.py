#!/usr/bin/env python3
"""_verify_singles.py -- OLD-vs-NEW backtest verification for the singles
modelled-probability fix (prop_probability.p_at_least_singles, wired into
generate_picks.py's `families` list). NOT part of the shipped pipeline --
scratch tooling for this one PR's evidence-gathering, run manually.

METHODOLOGY, and why it changed from a first draft that diffed the
COMMITTED backtest/rows.jsonl against a fresh re-simulation: rows.jsonl
was built incrementally over many months by many separate commits (each
row replays a past date through WHATEVER the scoring code was AT THAT
TIME -- see SCHEMA.md's own code_git_sha/backtest_generated_at fields,
added specifically because "a backtest replays a past date through TODAY's
scoring functions, so two runs of the identical date range on two
different commits can legitimately disagree"). A live check on this exact
date (2026-08-07) found doubles/triples/total_bases -- markets this PR
never touches -- returning wildly different row counts under today's code
than what's sitting in the committed file for the same date, entirely
because of OTHER, unrelated scoring changes made since those rows were
captured. Diffing "old committed file" vs "freshly re-simulated" would
therefore NOT isolate this PR's effect -- it would be contaminated by
every other change made in between.

The rigorous fix: run OLD and NEW from IDENTICAL point-in-time inputs, in
the SAME pass, toggling ONLY the one thing this PR changes -- whether
prop_probability.p_at_least_singles is allowed to produce a value.
build_candidates() + attach_hit_probabilities() is run TWICE per date from
the same PointInTime-guarded fetch (one real network/data fetch per date,
not two): once with the real p_at_least_singles, once with it monkey-
patched to always return None (reproducing the exact pre-fix families-list
entry: fn=None -> modelled=None -> falls back to the OLD empirical/
league-only path, identically). This isolates the ablation completely from
every other piece of code drift.

Writes matched (date, player_id, game_pk) OLD/NEW pairs to
backtest/_singles_verify_pairs.jsonl, one JSON object per singles
observation with both the old_prob/old_basis and new_prob/new_basis and
the real graded outcome, appending as it goes so partial progress survives
an interruption.
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
import prop_probability as pp
import grade_results as gr

ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_singles_verify_pairs.jsonl")
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


_real_p_at_least_singles = pp.p_at_least_singles


def _disabled_p_at_least_singles(threshold, pa_dist, n_pa):
    """Monkeypatch target for the OLD pass -- raises so the family loop's
    own `except Exception: modelled = None` catches it, reproducing
    fn=None's exact downstream effect (modelled stays None, falls back to
    _blend(empirical, None)) without needing to touch generate_picks.py's
    families list itself for this script."""
    raise RuntimeError("disabled for OLD-pass verification")


def run_one_pass(date, store, singles_enabled):
    """One full point-in-time-safe candidate build + probability pass,
    mirroring simulate_date()'s real call order exactly (see that
    function's own body -- build_inputs, build_candidates,
    attach_hit_probabilities with the real league_rates argument,
    best_of_category_extras). Returns the list of singles-family entries
    best_of_category_extras() would grade, each carrying player_id/
    game_pk/prob/basis, PLUS every candidate whose own primary pick is
    singles (structurally never happens per this file's own investigation,
    checked anyway rather than assumed)."""
    if singles_enabled:
        pp.p_at_least_singles = _real_p_at_least_singles
    else:
        pp.p_at_least_singles = _disabled_p_at_least_singles
    try:
        with eng.PointInTime(date, store) as pit:
            game_meta, kwargs, comp_table, _pit_df, log = eng.build_inputs(
                date, store, use_weather=False, use_bullpen=True, verbose=False)
            if not game_meta:
                return None, "no_games"
            candidates = gp.build_candidates(game_meta, **kwargs)
            emp_batters = kwargs.get("emp_batters", {})
            emp_pitchers = kwargs.get("emp_pitchers", {})
            league_rates = msrc.league_base_rates()
            gp.attach_hit_probabilities(candidates, comp_table, emp_batters,
                                        emp_pitchers, league_rates)
            extra_candidates = eng.best_of_category_extras(candidates)
        # Keep the FULL candidate dict (not a trimmed subset) -- grade_pick()
        # needs game_pk/player_id/projection (stat, needs) to grade for
        # real, and stripping fields down here risked silently reinventing
        # its threshold logic instead of reusing it.
        singles_entries = []
        for c in candidates:
            if (c.get("projection") or {}).get("stat") == "singles":
                entry = dict(c)
                entry["_source"] = "main"
                singles_entries.append(entry)
        for c in extra_candidates:
            if (c.get("projection") or {}).get("stat") == "singles":
                entry = dict(c)
                entry["_source"] = "extra"
                singles_entries.append(entry)
        return singles_entries, "ok"
    except eng.LookaheadError:
        raise
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        pp.p_at_least_singles = _real_p_at_least_singles


def real_outcome(entry, statuses, date):
    """The real, actually-happened grade for this candidate -- reuses
    grade_results.grade_pick() directly (the SAME function simulate_date's
    own grading step calls), rather than re-deriving box-score extraction
    by hand. `entry` must carry game_pk/player_id/projection (stat=
    'singles', needs=1) -- exactly the shape build_candidates()/
    best_of_category_extras() already produce. Grading is SUPPOSED to read
    the future; that's what an outcome is -- called outside any point-in-
    time guard, same as simulate_date's own grading step."""
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
    dates = old_singles_dates()
    print(f"{len(dates)} real historical dates to re-simulate: {dates[0]} .. {dates[-1]}",
          flush=True)

    state = load_state()
    done = set(state["done"])

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

    _orig_box = m.statsapi.boxscore_data
    _box_cache = {}

    def _cached_box(game_id, *a, **kw):
        if game_id not in _box_cache:
            _box_cache[game_id] = _orig_box(game_id, *a, **kw)
        return _box_cache[game_id]
    m.statsapi.boxscore_data = _cached_box

    n_pairs_total = 0
    try:
        for i, d in enumerate(dates, 1):
            if d in done:
                print(f"[{i}/{len(dates)}] {d}  already done -- skipping", flush=True)
                continue
            store = stores[d[:4]]
            t0 = time.time()
            old_entries, old_status = run_one_pass(d, store, singles_enabled=False)
            new_entries, new_status = run_one_pass(d, store, singles_enabled=True)
            elapsed = round(time.time() - t0, 1)
            if old_status != "ok" or new_status != "ok":
                print(f"[{i}/{len(dates)}] {d}  FAILED old={old_status} new={new_status}",
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
                        "date": d, "player_id": key[0], "game_pk": key[1],
                        "player_name": grading_entry.get("name"),
                        "old_prob": old_e.get("hit_probability") if old_e else None,
                        "old_basis": old_e.get("probability_basis") if old_e else None,
                        "new_prob": new_e.get("hit_probability") if new_e else None,
                        "new_basis": new_e.get("probability_basis") if new_e else None,
                        "outcome": outcome,
                    }) + "\n")
                    pairs_this_date += 1
            n_pairs_total += pairs_this_date
            print(f"[{i}/{len(dates)}] {d}  old={len(old_entries)} new={len(new_entries)} "
                 f"graded_pairs={pairs_this_date} {elapsed}s (total so far: {n_pairs_total})",
                 flush=True)
            done.add(d)
            state["done"] = sorted(done)
            save_state(state)
    finally:
        m.statsapi.boxscore_data = _orig_box
        pp.p_at_least_singles = _real_p_at_least_singles

    print(f"\nDONE. {n_pairs_total} graded OLD/NEW pairs written to {OUT_PATH}", flush=True)
    if state["failed"]:
        print(f"{len(state['failed'])} dates failed: {state['failed']}", flush=True)


if __name__ == "__main__":
    main()
