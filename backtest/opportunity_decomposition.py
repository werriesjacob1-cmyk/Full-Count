#!/usr/bin/env python3
"""opportunity_decomposition.py -- Priority 1 of the opportunity-modeling
phase (2026-08-25): fully characterize the Priority-6 finding ("opportunity
shortfall is the dominant source of within-probability-bucket variance")
BEFORE building any new model, using only canonical history.

KEY DISCOVERY THIS SCRIPT DEPENDS ON: `signals.lineup_slot` (present on
~89% of canonical rows) is `scale(10 - order, 1, 9)` from
generate_picks.py:1379 -- a deterministic, invertible, PREGAME-KNOWABLE
encoding of a batter's real batting-order slot (1-9). This is the first
genuinely pregame opportunity proxy found in canonical history. Verified
live: reconstructed order buckets are almost perfectly balanced (~12,830
rows each across 9 slots on the `hits` market) and average `actual_pa`
declines monotonically from 4.37 (leadoff) to 3.33 (9th) -- exactly the
relationship a real batting-order opportunity signal should show.

WHAT IS HONESTLY UNAVAILABLE from canonical rows (stated once here, not
silently reattempted per section): confirmed-vs-assumed lineup status
(`lineup_assumed` is a LIVE/registry-only field, absent from every backtest
row by SCHEMA.md's own design), home/away, and team-level run-environment
-- none of these exist in backtest/rows_canonical.jsonl's schema. Any
future opportunity model that wants them needs a live/prospective source
(the candidate-funnel track), not this canonical file.

SCOPE: this script analyzes HITTER markets only (a batter's own actual_pa
is the opportunity denominator) -- pitcher opportunity (BF/workload) is a
structurally different mechanism, explicitly deferred to a future pitcher-
specific script per the roadmap, not conflated with batting order here.

    /tmp/mlbvenv/bin/python3 backtest/opportunity_decomposition.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, season_phase, load_rows

DEFAULT_PATH = "backtest/rows_canonical.jsonl"

HITTER_MARKETS = frozenset({
    "hits", "total_bases", "hits_runs_rbis", "home_run", "singles",
    "doubles", "triples", "rbis", "runs", "hard_hit_105",
})


def derive_batting_order(lineup_slot):
    """Invert generate_picks.py:1379's scale(10 - order, 1, 9) -> order.
    Returns None if lineup_slot is absent (signal never fired for this row)."""
    if lineup_slot is None:
        return None
    order = 9.0 - lineup_slot * 8.0 / 100.0
    order = round(order)
    if 1 <= order <= 9:
        return order
    return None


def order_tier(order):
    """Coarse 3-tier grouping for statistical power in the controlled test."""
    if order is None:
        return "unknown"
    if order <= 3:
        return "top_1_3"
    if order <= 6:
        return "mid_4_6"
    return "bottom_7_9"


def pa_bucket_fine(actual_pa):
    if actual_pa is None:
        return "unknown"
    if actual_pa >= 6:
        return "6+"
    return str(int(actual_pa))


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def market_pa_collapse_table(rows):
    """Question 1+2: is low actual_pa the mechanism across ALL hitter markets,
    and at what actual_pa does hit rate collapse? Per-market x actual_pa
    breakdown, hitter markets only."""
    table = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0}))
    for r in rows:
        pt = r.get("prop_type")
        if pt not in HITTER_MARKETS:
            continue
        b = pa_bucket_fine(r.get("actual_pa"))
        cell = table[pt][b]
        cell["n"] += 1
        cell["hits"] += r["outcome"]
    return {
        market: {b: {"n": c["n"], "hit_rate": _rate(c["hits"], c["n"])}
                  for b, c in sorted(buckets.items())}
        for market, buckets in sorted(table.items())
    }


def batting_order_opportunity_table(rows):
    """Question 5 (bottom-of-order effect) + the core pregame-proxy check:
    does derived batting order predict BOTH actual_pa and realized hit rate,
    pooled across hitter markets. Hitter markets only, rows with a real
    lineup_slot signal only."""
    by_order = defaultdict(lambda: {"n": 0, "hits": 0, "pa_sum": 0.0, "pa_n": 0})
    for r in rows:
        if r.get("prop_type") not in HITTER_MARKETS:
            continue
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        if order is None:
            continue
        d = by_order[order]
        d["n"] += 1
        d["hits"] += r["outcome"]
        pa = r.get("actual_pa")
        if pa is not None:
            d["pa_sum"] += pa
            d["pa_n"] += 1
    return {
        order: {
            "n": d["n"], "hit_rate": _rate(d["hits"], d["n"]),
            "avg_actual_pa": round(d["pa_sum"] / d["pa_n"], 3) if d["pa_n"] else None,
        }
        for order, d in sorted(by_order.items())
    }


def controlled_order_tier_by_probability_bucket(rows, markets=None):
    """The core test: WITHIN a fixed nominal-probability bucket (so the model
    already believes the same thing about every row in the cell), does the
    pregame-knowable batting-order tier still separate realized hit rate?
    If yes, order carries information beyond what predicted_prob already
    captures -- exactly the premise Priority 2-4 need before building
    anything. `markets`, if given, restricts to those prop_types (None =
    all hitter markets pooled)."""
    allowed = set(markets) if markets else HITTER_MARKETS
    by_bucket = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0}))
    for r in rows:
        if r.get("prop_type") not in allowed:
            continue
        pb = prob_bucket(r.get("predicted_prob"))
        if pb is None:
            continue
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        tier = order_tier(order)
        cell = by_bucket[pb][tier]
        cell["n"] += 1
        cell["hits"] += r["outcome"]
    return {
        bucket: {tier: {"n": c["n"], "hit_rate": _rate(c["hits"], c["n"])}
                 for tier, c in sorted(tiers.items())}
        for bucket, tiers in sorted(by_bucket.items())
    }


def year_stability_of_order_effect(rows, markets=None):
    """Question 10: is the batting-order effect stable by year? Pools
    top_1_3 vs bottom_7_9 tiers (skip mid/unknown for a clean two-group
    comparison) per year, hitter markets."""
    allowed = set(markets) if markets else HITTER_MARKETS
    by_year_tier = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0}))
    for r in rows:
        if r.get("prop_type") not in allowed:
            continue
        date = r.get("date") or ""
        year = date.split("-")[0] if date else "unknown"
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        tier = order_tier(order)
        if tier not in ("top_1_3", "bottom_7_9"):
            continue
        cell = by_year_tier[year][tier]
        cell["n"] += 1
        cell["hits"] += r["outcome"]
    return {
        year: {tier: {"n": c["n"], "hit_rate": _rate(c["hits"], c["n"])}
               for tier, c in sorted(tiers.items())}
        for year, tiers in sorted(by_year_tier.items())
    }


def build_report(rows):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter_rows = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]
    return {
        "n_graded_rows_total": len(graded),
        "n_hitter_market_rows": len(hitter_rows),
        "market_pa_collapse_table": market_pa_collapse_table(hitter_rows),
        "batting_order_opportunity_table": batting_order_opportunity_table(hitter_rows),
        "controlled_order_tier_by_probability_bucket_all_hitter_markets":
            controlled_order_tier_by_probability_bucket(hitter_rows),
        "controlled_order_tier_by_probability_bucket_hits_only":
            controlled_order_tier_by_probability_bucket(hitter_rows, markets={"hits"}),
        "controlled_order_tier_by_probability_bucket_total_bases_only":
            controlled_order_tier_by_probability_bucket(hitter_rows, markets={"total_bases"}),
        "year_stability_of_order_effect": year_stability_of_order_effect(hitter_rows),
        "unavailable_from_canonical_rows": [
            "confirmed_vs_assumed_lineup (LIVE/registry-only field, never on backtest rows)",
            "home_vs_away (not in backtest/SCHEMA.md's row shape)",
            "team_run_environment (not in backtest/SCHEMA.md's row shape)",
        ],
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows)
    report["_source_file"] = path
    report["_n_malformed_lines_skipped"] = n_malformed
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
