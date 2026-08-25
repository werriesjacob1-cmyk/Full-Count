#!/usr/bin/env python3
"""residual_challenger_model.py -- Priority 4 of the residual-opportunity
phase (2026-08-25): does jointly conditioning the PA distribution on
order + days_rest + getaway_day (the two real residual predictors found
in backtest/residual_priority1_2_2026-08-25.md) beat the order-only
challenger at the test that actually matters -- equal-volume selection?

SAME DISCIPLINE AS pa_opportunity_model.py, not a new methodology: strict
train (2024-2025) / holdout (2026) split, empirical conditional
distributions (no black box), P(prop hits) = sum_k P(PA=k|context) *
P(prop hits|PA=k), evaluated via (a) within-bucket discrimination and (b)
the equal-volume ranking test -- reusing pa_opportunity_model.py's
dedupe_player_games() and fit_hit_rate_given_pa() directly rather than
reimplementing them.

SPARSE-CELL HANDLING: 9 orders x 4 days_rest groups x 2 getaway groups =
72 joint cells over ~90K training player-games (~1,250/cell average, but
real distribution is uneven). A joint cell with fewer than MIN_CELL_N
training rows falls back to the order-only distribution
(pa_opportunity_model.fit_pa_distribution's own table) rather than fitting
on a handful of noisy rows -- this is a real, stated design choice, not a
silent gap.

    /tmp/mlbvenv/bin/python3 backtest/residual_challenger_model.py \
        backtest/rows_canonical.jsonl hits
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, load_rows
from opportunity_decomposition import derive_batting_order, pa_bucket_fine, HITTER_MARKETS
from pa_opportunity_model import (
    dedupe_player_games, fit_pa_distribution, fit_hit_rate_given_pa,
    equal_volume_ranking_comparison, PA_STATES, _rate,
)
from residual_opportunity_decomposition import _days_rest_group, _getaway_day_group

DEFAULT_PATH = "backtest/rows_canonical.jsonl"
TRAIN_YEARS = {"2024", "2025"}
HOLDOUT_YEARS = {"2026"}
MIN_CELL_N = 200


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def joint_key(row):
    order = derive_batting_order((row.get("signals") or {}).get("lineup_slot"))
    if order is None:
        return None
    signals = row.get("signals") or {}
    dr = _days_rest_group(signals)
    ga = _getaway_day_group(signals)
    if dr is None or ga is None:
        return None
    return (order, dr, ga)


def fit_joint_pa_distribution(player_game_rows, min_cell_n=MIN_CELL_N):
    counts = defaultdict(lambda: defaultdict(int))
    totals = defaultdict(int)
    for r in player_game_rows:
        key = joint_key(r)
        pa = r.get("actual_pa")
        if key is None or pa is None:
            continue
        totals[key] += 1
        counts[key][pa_bucket_fine(pa)] += 1
    dist = {}
    for key, total in totals.items():
        if total < min_cell_n:
            continue
        dist[key] = {state: round(counts[key].get(state, 0) / total, 6) for state in PA_STATES}
        dist[key]["_n"] = total
    return dist


def challenger_probability_joint(row, joint_dist, order_dist, hit_rate_given_pa):
    """Try the joint (order, days_rest, getaway_day) cell first; fall back
    to the order-only distribution if the joint cell was too sparse to fit
    (or the row is missing days_rest/getaway_day)."""
    key = joint_key(row)
    dist = joint_dist.get(key) if key else None
    if dist is None:
        order = derive_batting_order((row.get("signals") or {}).get("lineup_slot"))
        dist = order_dist.get(order)
    if not dist:
        return None
    total_weight = 0.0
    total = 0.0
    for state in PA_STATES:
        p_pa = dist.get(state, 0.0)
        p_hit = hit_rate_given_pa.get(state)
        if p_hit is None or p_pa <= 0:
            continue
        total += p_pa * p_hit
        total_weight += p_pa
    if total_weight <= 0:
        return None
    return round(total / total_weight, 6)


def build_report(rows, market="hits"):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter_rows = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]

    train_rows = [r for r in hitter_rows if _year(r.get("date")) in TRAIN_YEARS]
    holdout_rows = [r for r in hitter_rows if _year(r.get("date")) in HOLDOUT_YEARS]

    train_player_games = dedupe_player_games(train_rows)
    joint_dist = fit_joint_pa_distribution(train_player_games)
    order_dist = fit_pa_distribution(train_player_games)
    hit_rate_given_pa = fit_hit_rate_given_pa(train_rows, market)

    n_joint_cells_fit = len(joint_dist)
    n_joint_cells_possible = len({joint_key(r) for r in train_player_games if joint_key(r)})

    market_holdout_rows = [r for r in holdout_rows if r.get("prop_type") == market]
    comparisons = []
    n_used_joint_cell = 0
    for r in market_holdout_rows:
        key = joint_key(r)
        used_joint = key is not None and key in joint_dist
        challenger = challenger_probability_joint(r, joint_dist, order_dist, hit_rate_given_pa)
        if challenger is None:
            continue
        if used_joint:
            n_used_joint_cell += 1
        comparisons.append({
            "current_prob": r.get("predicted_prob"),
            "challenger_prob": challenger,
            "outcome": r["outcome"],
            "used_joint_cell": used_joint,
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
        "n_train_player_games": len(train_player_games),
        "n_joint_cells_fit": n_joint_cells_fit,
        "n_joint_cells_possible": n_joint_cells_possible,
        "n_holdout_market_rows_with_challenger_prob": len(comparisons),
        "n_holdout_rows_using_joint_cell_vs_order_fallback": {
            "joint_cell": n_used_joint_cell, "order_fallback": len(comparisons) - n_used_joint_cell,
        },
        "discrimination_within_current_probability_bucket": discrimination_within_bucket,
        "equal_volume_ranking_comparison_holdout": equal_volume_ranking_comparison(comparisons),
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    market = sys.argv[2] if len(sys.argv) > 2 else "hits"
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows, market=market)
    report["_source_file"] = path
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
