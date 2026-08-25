#!/usr/bin/env python3
"""disagreement_challenger_model.py -- Priority 4/5 of the model/context
disagreement phase (2026-08-25): does the baseline_context_conflict metric
(backtest/disagreement_priority1_2_3_2026-08-25.md) actually improve real
selection at fixed volume, or is it "descriptively interesting but not
enough incremental selection signal," per the directive's own explicit
closure test?

METHOD: same discipline as pa_opportunity_model.py -- strict train
(2024-2025) / holdout (2026) split. The challenger probability for a
holdout row is the EMPIRICAL realized hit rate of its own
(probability_bucket, conflict_tier) cell, fit ONLY on training data (never
the row's own outcome, never any holdout data) -- this is the data-driven
analogue of "adjust the nominal probability by what we've learned this
specific probability+disagreement combination actually resolves to."
Falls back to the bucket's own tier-blind average when a specific
(bucket, tier) cell is too sparse to trust.

    /tmp/mlbvenv/bin/python3 backtest/disagreement_challenger_model.py \
        backtest/rows_canonical.jsonl hits_runs_rbis
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, load_rows
from pa_opportunity_model import equal_volume_ranking_comparison, _rate
from disagreement_decomposition import baseline_context_conflict, conflict_tier

DEFAULT_PATH = "backtest/rows_canonical.jsonl"
TRAIN_YEARS = {"2024", "2025"}
HOLDOUT_YEARS = {"2026"}
MIN_CELL_N = 200


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def fit_bucket_tier_hit_rate(train_rows, min_cell_n=MIN_CELL_N):
    """Returns (cell_rate, bucket_rate): cell_rate keyed by
    (bucket, tier) -> hit rate (only cells with >= min_cell_n rows);
    bucket_rate keyed by bucket -> hit rate (all rows in that bucket,
    tier-blind fallback)."""
    cell_counts = defaultdict(lambda: {"n": 0, "hits": 0})
    bucket_counts = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in train_rows:
        bucket = prob_bucket(r.get("predicted_prob"))
        if bucket is None:
            continue
        bucket_counts[bucket]["n"] += 1
        bucket_counts[bucket]["hits"] += r["outcome"]
        tier = conflict_tier(baseline_context_conflict(r))
        if tier is None:
            continue
        cell_counts[(bucket, tier)]["n"] += 1
        cell_counts[(bucket, tier)]["hits"] += r["outcome"]

    cell_rate = {k: _rate(v["hits"], v["n"]) for k, v in cell_counts.items() if v["n"] >= min_cell_n}
    bucket_rate = {k: _rate(v["hits"], v["n"]) for k, v in bucket_counts.items()}
    return cell_rate, bucket_rate


def challenger_probability(row, cell_rate, bucket_rate):
    bucket = prob_bucket(row.get("predicted_prob"))
    if bucket is None:
        return None
    tier = conflict_tier(baseline_context_conflict(row))
    if tier is not None and (bucket, tier) in cell_rate:
        return cell_rate[(bucket, tier)]
    return bucket_rate.get(bucket)


def build_report(rows, market):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    market_rows = [r for r in graded if r.get("prop_type") == market
                   and r.get("cat_baseline_skill") is not None]

    train_rows = [r for r in market_rows if _year(r.get("date")) in TRAIN_YEARS]
    holdout_rows = [r for r in market_rows if _year(r.get("date")) in HOLDOUT_YEARS]

    cell_rate, bucket_rate = fit_bucket_tier_hit_rate(train_rows)

    comparisons = []
    n_used_cell = 0
    for r in holdout_rows:
        challenger = challenger_probability(r, cell_rate, bucket_rate)
        if challenger is None:
            continue
        bucket = prob_bucket(r.get("predicted_prob"))
        tier = conflict_tier(baseline_context_conflict(r))
        used_cell = tier is not None and (bucket, tier) in cell_rate
        if used_cell:
            n_used_cell += 1
        comparisons.append({
            "current_prob": r.get("predicted_prob"),
            "challenger_prob": challenger,
            "outcome": r["outcome"],
        })

    by_current_bucket = defaultdict(list)
    for c in comparisons:
        b = prob_bucket(c["current_prob"])
        if b is not None:
            by_current_bucket[b].append(c)

    discrimination_within_bucket = {}
    for bucket, items in sorted(by_current_bucket.items()):
        if len(items) < 40:
            continue
        items_sorted = sorted(items, key=lambda c: c["challenger_prob"])
        half = len(items_sorted) // 2
        low_half, high_half = items_sorted[:half], items_sorted[half:]
        low_rate = _rate(sum(c["outcome"] for c in low_half), len(low_half))
        high_rate = _rate(sum(c["outcome"] for c in high_half), len(high_half))
        discrimination_within_bucket[bucket] = {
            "n": len(items),
            "challenger_low_half_hit_rate": low_rate,
            "challenger_high_half_hit_rate": high_rate,
            "delta": round(high_rate - low_rate, 4) if (low_rate is not None and high_rate is not None) else None,
        }

    return {
        "market": market,
        "n_train_rows": len(train_rows),
        "n_holdout_rows_with_challenger_prob": len(comparisons),
        "n_holdout_rows_using_cell_vs_bucket_fallback": {
            "cell": n_used_cell, "bucket_fallback": len(comparisons) - n_used_cell,
        },
        "discrimination_within_current_probability_bucket": discrimination_within_bucket,
        "equal_volume_ranking_comparison_holdout": equal_volume_ranking_comparison(comparisons),
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    market = sys.argv[2] if len(sys.argv) > 2 else "hits_runs_rbis"
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows, market)
    report["_source_file"] = path
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
