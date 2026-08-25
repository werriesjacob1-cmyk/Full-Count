#!/usr/bin/env python3
"""disagreement_decomposition.py -- Priority 1/2/3 of the model/context
disagreement phase (2026-08-25): can internal disagreement between
score_batter's own category components identify false confidence at the
SAME nominal predicted_prob?

COMPONENT AUDIT (real, checked against actual canonical data before
building any metric -- see backtest/disagreement_priority1_2_3_2026-08-25.md
for the full correlation map):
  - `cat_environment` is a CONSTANT 50 across every canonical row (the
    main backfill ran with --no-weather; environment inputs never
    populated). Not a bug -- excluded entirely, never used below.
  - `cat_context` is ALSO a constant 50 for `strikeouts` (pitcher) rows --
    score_pitcher's category framework does not populate a CONTEXT
    component the way score_batter does. `cat_*` fields therefore only
    carry real, varying information on `hits` and `hits_runs_rbis` --
    the only two markets this module analyzes.
  - `cat_context` correlates at r=0.97 (hits) / r=0.97 (hits_runs_rbis)
    with `score` -- NOT because CONTEXT dominates the documented
    35/25/15/15/10 weighting (it's only 10%), but because lineup_context
    (batting order, 0-100 full range) gives cat_context far higher
    VARIANCE than the other components, so it dominates the weighted
    sum's variance despite its small nominal weight. This means
    "score vs cat_context disagreement" is NOT a meaningful metric --
    they are near-collinear by construction, not independently informative.
  - `cat_matchup` is the most genuinely independent component (r<0.2 with
    every other field in both markets).
  - `cat_baseline_skill` vs `cat_context` correlate only moderately
    (r=0.21 hits, r=0.42 hits_runs_rbis) -- NOT redundant, and this is
    exactly the Weston Wilson archetype's structure (strong empirical
    history, weak situational context). This is the primary metric tested
    below.

TARGET METRIC: baseline_context_conflict = cat_baseline_skill - cat_context.
A large POSITIVE value is the Weston-like signature (empirically strong,
contextually weak); a large NEGATIVE value is the opposite (contextually
favorable, empirically thin).

LEAKAGE: every field used (cat_baseline_skill, cat_context, predicted_prob)
is a value generate_picks.py's own scoring pass computes before first
pitch -- no postgame field is used as an input anywhere in this module.

    /tmp/mlbvenv/bin/python3 backtest/disagreement_decomposition.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, load_rows

DEFAULT_PATH = "backtest/rows_canonical.jsonl"
CAT_MARKETS = ("hits", "hits_runs_rbis")  # the only markets with real, varying cat_* data


def baseline_context_conflict(row):
    b = row.get("cat_baseline_skill")
    c = row.get("cat_context")
    if b is None or c is None:
        return None
    return b - c


def conflict_tier(conflict, hi=20.0, lo=-20.0):
    """Three-tier split: strongly Weston-like (empirical >> context),
    strongly anti-Weston (context >> empirical), or balanced."""
    if conflict is None:
        return None
    if conflict >= hi:
        return "high_empirical_low_context"  # Weston-like
    if conflict <= lo:
        return "high_context_low_empirical"
    return "balanced"


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def same_probability_bucket_conflict_test(rows, market):
    market_rows = [r for r in rows if r.get("prop_type") == market]
    by_bucket_tier = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0}))
    for r in market_rows:
        bucket = prob_bucket(r.get("predicted_prob"))
        tier = conflict_tier(baseline_context_conflict(r))
        if bucket is None or tier is None:
            continue
        cell = by_bucket_tier[bucket][tier]
        cell["n"] += 1
        cell["hits"] += r["outcome"]
    return {
        bucket: {tier: {"n": c["n"], "hit_rate": _rate(c["hits"], c["n"])}
                 for tier, c in sorted(tiers.items())}
        for bucket, tiers in sorted(by_bucket_tier.items())
    }


def year_stability_of_conflict(rows, market):
    market_rows = [r for r in rows if r.get("prop_type") == market]
    by_year_tier = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hits": 0}))
    for r in market_rows:
        tier = conflict_tier(baseline_context_conflict(r))
        if tier is None:
            continue
        year = _year(r.get("date"))
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
    report = {"markets_analyzed": list(CAT_MARKETS), "per_market": {}}
    for market in CAT_MARKETS:
        market_rows = [r for r in graded if r.get("prop_type") == market
                       and r.get("cat_baseline_skill") is not None]
        pooled = defaultdict(lambda: {"n": 0, "hits": 0})
        for r in market_rows:
            tier = conflict_tier(baseline_context_conflict(r))
            if tier is None:
                continue
            pooled[tier]["n"] += 1
            pooled[tier]["hits"] += r["outcome"]
        report["per_market"][market] = {
            "n_rows_with_components": len(market_rows),
            "pooled_by_conflict_tier": {t: {"n": c["n"], "hit_rate": _rate(c["hits"], c["n"])}
                                         for t, c in sorted(pooled.items())},
            "same_probability_bucket_control": same_probability_bucket_conflict_test(graded, market),
            "year_stability": year_stability_of_conflict(graded, market),
        }
    return report


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
