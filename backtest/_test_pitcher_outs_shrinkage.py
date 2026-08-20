#!/usr/bin/env python3
"""_test_pitcher_outs_shrinkage.py -- tests alternative shrinkage priors
(n0) for pitcher_outs against the SAME real historical rows already graded
in rows.jsonl, WITHOUT touching production code or the heavy backtest
pipeline. Scratch tooling, not part of the shipped pipeline.

WHY THIS DOESN'T NEED THE FULL BACKTEST ENGINE: empirical_pitcher_outs_rates
reads pitcher game logs from MLB's raw statsapi gameLog endpoint (see
_game_log), never pybaseball/Statcast -- so there is no StatcastStore to
load and no PointInTime.window() call to guard. PointInTime is still used
here (constructed with an UNLOADED StatcastStore, since one is never
touched) purely to get m.YEAR/m.TODAY repointed correctly for the season
and cutoff-date semantics the real call site (backtest/engine.py:910)
relies on. This makes the whole test far lighter than a real backtest pass
-- no Statcast parquet loads, no candidate-building, no lineup/weather
fetches -- and safe to run alongside the (Statcast-heavy) backfill and
window-sweep jobs without resource contention on their cache files.

METHODOLOGY: for each real historical date with graded pitcher_outs rows,
fetch the RAW (pre-shrinkage) hit/n counts for every starter's every
threshold via mlb_sources._empirical_pitcher_outs_one directly (bypassing
_apply_shrinkage entirely), compute that date's slate-scoped league rate
per threshold (sum(hit)/sum(n) across that date's starters -- exactly how
_apply_shrinkage computes it in the real call), then re-shrink with each
candidate n0 and compare against the REAL outcome already recorded in
rows.jsonl. No new box-score fetches needed -- outcome is ground truth
already captured at grading time.

    /tmp/mlbvenv/bin/python3 backtest/_test_pitcher_outs_shrinkage.py
"""
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine as eng
import mlb_sources as msrc

CANDIDATE_N0 = (3, 6, 10, 15, 20, 30, 40)
ROWS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pitcher_outs_shrinkage_pairs.jsonl")
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pitcher_outs_shrinkage_state.json")


def load_target_rows():
    """Every real historical pitcher_outs row, grouped by date. Each row
    carries player_id/needs/outcome -- exactly what's needed to re-price
    it under a different n0 and compare against the real result."""
    by_date = defaultdict(list)
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("prop_type") != "pitcher_outs":
                continue
            if d.get("outcome") is None or d.get("needs") is None or d.get("player_id") is None:
                continue
            by_date[d["date"]].append(d)
    return dict(sorted(by_date.items()))


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"done": []}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def main():
    by_date = load_target_rows()
    dates = list(by_date.keys())
    print(f"{sum(len(v) for v in by_date.values())} real historical pitcher_outs rows "
         f"across {len(dates)} dates: {dates[0]} .. {dates[-1]}", flush=True)

    state = load_state()
    done = set(state["done"])

    # An unloaded store -- never touched, since empirical_pitcher_outs_rates
    # routes through MLB's raw statsapi gameLog endpoint, not Statcast/
    # pybaseball. Constructing (not .load()-ing) it is free.
    dummy_store = eng.StatcastStore(2024, "2024-04-01", verbose=False)

    n_pairs_total = 0
    t_start = time.time()
    with open(OUT_PATH, "a", encoding="utf-8") as out_f:
        for i, date in enumerate(dates, 1):
            if date in done:
                continue
            rows = by_date[date]
            pids = sorted({r["player_id"] for r in rows})
            t0 = time.time()
            try:
                with eng.PointInTime(date, dummy_store):
                    cutoff = eng.shift(date, -1)
                    with ThreadPoolExecutor(max_workers=12) as ex:
                        raw = dict(ex.map(
                            msrc._empirical_pitcher_outs_one,
                            [(pid, 5, cutoff) for pid in pids]))
            except eng.LookaheadError:
                raise
            except Exception as e:
                print(f"[{i}/{len(dates)}] {date}  FAILED: {type(e).__name__}: {e}", flush=True)
                continue

            # Slate-scoped league rate per threshold, exactly how
            # _apply_shrinkage computes it in the real call (sum(hit)/sum(n)
            # over every starter this function was asked about that day).
            league_hit = defaultdict(int)
            league_n = defaultdict(int)
            for r in raw.values():
                if not r:
                    continue
                for key, rate in r["rates"].items():
                    league_hit[key] += rate["hit"]
                    league_n[key] += rate["n"]
            league_p = {k: (league_hit[k] / league_n[k] if league_n[k] else 0.0)
                       for k in league_hit}

            pairs_this_date = 0
            for row in rows:
                pid = row["player_id"]
                r = raw.get(pid)
                if not r:
                    continue
                key = f"outs_{row['needs']}plus"
                rate = r["rates"].get(key)
                if not rate:
                    continue
                lg = league_p.get(key)
                if lg is None:
                    continue
                hit, n = rate["hit"], rate["n"]
                record = {"date": date, "player_id": pid, "needs": row["needs"],
                          "outcome": row["outcome"], "hit": hit, "n": n, "league_p": round(lg, 4)}
                for n0 in CANDIDATE_N0:
                    record[f"p_hat_{n0}"] = round((hit + n0 * lg) / (n + n0), 4)
                out_f.write(json.dumps(record) + "\n")
                pairs_this_date += 1
            out_f.flush()
            n_pairs_total += pairs_this_date
            elapsed = round(time.time() - t0, 1)
            done.add(date)
            state["done"] = sorted(done)
            save_state(state)
            if i % 10 == 0 or i == len(dates):
                total_elapsed = round(time.time() - t_start, 1)
                print(f"[{i}/{len(dates)}] {date}  {len(pids)} pitchers, "
                     f"{pairs_this_date} rows priced, {elapsed}s "
                     f"(total: {n_pairs_total} rows, {total_elapsed}s elapsed)", flush=True)

    print(f"\nDONE. {n_pairs_total} rows written to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
