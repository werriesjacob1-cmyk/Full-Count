#!/usr/bin/env python3
"""build_stable_base_rates.py -- (re)builds data/stable_base_rates/{stat}.json,
the daily-delta ledger stable_base_rate.py reads at generation time.

WHAT THIS IS NOT: it does not touch predicted_prob, hit_probability, or the
_apply_shrinkage() prior -- see stable_base_rate.py's own docstring for the
clean separation this exists to preserve. This is purely the LIFT reference
table: a real, point-in-time-safe, per-(stat,needs) daily hit/n ledger,
aggregated from backtest/rows.jsonl's real graded outcomes (the same
population and methodology validated in backtest/stable_baseline_challenger.py
-- season-to-date pooling, no same-day leakage, no future-season leakage).

Bootstrapped once from the full historical rows.jsonl (2024-04-23 onward);
re-run this script periodically (same cadence as the calibration refit
workflow -- see .github/workflows/*calibrat*) to extend coverage as more
real graded data accumulates. Between refreshes, stable_base_rate.py's
season-to-date window simply doesn't include the most recent few days --
an accepted freshness lag for a slow-moving pooled reference, not a
point-in-time safety issue (it can only ever look LESS current than a
live full rebuild would, never forward).

hits_runs_rbis is the only stat this feeds into a real recommendation change
(the Lean gate -- see recommendation.py). runs/rbis are built here too, for
the shadow-only tracking authorized alongside it -- same table shape, not
read by classify_recommendation() at all yet.

    /tmp/mlbvenv/bin/python3 backtest/build_stable_base_rates.py
"""
import json
import os
from collections import defaultdict
from datetime import datetime

ROWS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.jsonl")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "stable_base_rates")
STATS = ("hits_runs_rbis", "runs", "rbis")


def build():
    by_stat = defaultdict(lambda: defaultdict(lambda: {"hit": 0, "n": 0}))
    dates_by_stat = defaultdict(set)
    n_rows_by_stat = defaultdict(int)
    with open(ROWS_FILE) as f:
        for line in f:
            row = json.loads(line)
            stat = row.get("prop_type")
            if stat not in STATS:
                continue
            if row.get("outcome") is None or row.get("date") is None or row.get("needs") is None:
                continue
            key = (row["date"], row["needs"])
            by_stat[stat][key]["hit"] += int(row["outcome"])
            by_stat[stat][key]["n"] += 1
            dates_by_stat[stat].add(row["date"])
            n_rows_by_stat[stat] += 1

    os.makedirs(OUT_DIR, exist_ok=True)
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for stat in STATS:
        daily = [
            {"date": date, "needs": needs, "hit": agg["hit"], "n": agg["n"]}
            for (date, needs), agg in sorted(by_stat[stat].items())
        ]
        dates = sorted(dates_by_stat[stat])
        out = {
            "stat": stat,
            "generated_at": generated_at,
            "source": "backtest/rows.jsonl",
            "min_sample_n": 30,
            "date_range": [dates[0], dates[-1]] if dates else None,
            "n_source_rows": n_rows_by_stat[stat],
            "daily": daily,
        }
        out_path = os.path.join(OUT_DIR, f"{stat}.json")
        with open(out_path, "w") as f:
            json.dump(out, f, indent=1)
        print(f"{stat}: {len(daily)} daily entries, {n_rows_by_stat[stat]} source rows, "
              f"{dates[0] if dates else '?'}..{dates[-1] if dates else '?'} -> {out_path}")


if __name__ == "__main__":
    build()
