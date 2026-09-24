#!/usr/bin/env python3
"""Run the Mission 10 pre-registered Top Pick calibration holdout
replication. See DESIGN.md in this directory for the locked design (written
and committed before this script was ever executed against the real data).

Usage (from the repository root):
    python3 engineering/mlb_mission10_toppick_calibration_holdout_20260924/run_holdout_replication.py

Writes report.json next to this script. Also prints a short human-readable
summary to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
sys.path.insert(0, THIS_DIR)

from calibration_holdout_lib import (  # noqa: E402
    assert_pregame_integrity,
    cluster_bootstrap_gap_ci,
    exact_binomial_two_sided_pvalue,
    load_public_top_picks,
    summarize,
)

HOLDOUT_DATES = ["2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"]
EXCLUDED_IDS = {"fc2:822844:player-678218:hits_runs_rbis:1:over"}
MIN_N_FOR_CONFIRMATORY = 50
CI_LEVEL = 0.90
N_RESAMPLES = 10000
BOOTSTRAP_SEED = 20260924
FLAGGED_MARKETS = {"hits_runs_rbis", "pitcher_outs"}

# PR #128's own reported figures (2026-09-18, merged commit 1379fb99e4),
# reproduced here verbatim as prior context, not recomputed from raw data by
# this script (the exact live snapshot PR #128 used is not separately
# preserved from the currently-committed grades files).
PR128_CONTEXT = {
    "date": "2026-09-18",
    "n": 371,
    "mean_predicted": 0.646,
    "realized_rate": 0.536,
    "gap": 0.110,
    "p_value": 0.000009,
    "source_commit": "1379fb99e4b58765166cab9976f11eeb43d5a727",
}


def load_grades_docs(dates):
    docs = {}
    for d in dates:
        path = os.path.join(REPO_ROOT, "results", f"grades_{d}.json")
        with open(path, "r") as f:
            docs[path] = json.load(f)
    return docs


def market_slice(rows, markets, include):
    if include:
        return [r for r in rows if r.stat in markets]
    return [r for r in rows if r.stat not in markets]


def gap_block(rows, label):
    s = summarize(rows)
    block = {
        "label": label,
        "n": s.n,
        "hits": s.hits,
        "mean_predicted": s.mean_predicted,
        "realized_rate": s.realized_rate,
        "gap": s.gap,
    }
    if s.n > 0:
        block["naive_binomial_two_sided_p"] = exact_binomial_two_sided_pvalue(
            s.hits, s.n, s.mean_predicted
        )
    else:
        block["naive_binomial_two_sided_p"] = None
    return block


def main():
    docs = load_grades_docs(HOLDOUT_DATES)
    all_rows = load_public_top_picks(docs, excluded_ids=EXCLUDED_IDS)

    # Integrity check first -- fails loudly, before any statistic, if any
    # row's published probability was not genuinely frozen pregame.
    assert_pregame_integrity(all_rows)

    excluded_row_detail = None
    for path, doc in docs.items():
        for rec in doc.get("public_top_picks") or []:
            if rec.get("id") in EXCLUDED_IDS:
                excluded_row_detail = {
                    "id": rec.get("id"),
                    "name": rec.get("name"),
                    "stat": rec.get("stat"),
                    "hit_probability": rec.get("hit_probability"),
                    "grade": rec.get("grade"),
                    "reason_excluded": (
                        "incidentally inspected during pre-design schema "
                        "discovery, before DESIGN.md was locked; excluded "
                        "from the confirmatory analysis for pre-registration "
                        "integrity (see DESIGN.md)"
                    ),
                }

    primary = gap_block(all_rows, "primary: all holdout public Top Picks (2026-09-19..23)")

    if all_rows:
        ci_lo, ci_hi = cluster_bootstrap_gap_ci(
            all_rows, n_resamples=N_RESAMPLES, seed=BOOTSTRAP_SEED, ci=CI_LEVEL
        )
    else:
        ci_lo, ci_hi = (float("nan"), float("nan"))

    n = primary["n"]
    gap = primary["gap"]
    ci_excludes_zero_positive = (ci_lo > 0.0) and (ci_hi > 0.0)
    if n < MIN_N_FOR_CONFIRMATORY:
        verdict = "EXPLORATORY_UNDERPOWERED"
    elif gap > 0 and ci_excludes_zero_positive:
        verdict = "CONFIRMS_PERSISTENT_OVERCONFIDENCE"
    else:
        verdict = "DOES_NOT_REPLICATE"

    flagged_rows = market_slice(all_rows, FLAGGED_MARKETS, include=True)
    other_rows = market_slice(all_rows, FLAGGED_MARKETS, include=False)

    market_breakdown = {
        "flagged_markets_hits_runs_rbis_and_pitcher_outs": gap_block(
            flagged_rows, "exploratory: hits_runs_rbis + pitcher_outs"
        ),
        "all_other_markets": gap_block(other_rows, "exploratory: all other markets"),
    }
    # Per-individual-market detail, purely descriptive.
    per_market = {}
    for stat in sorted({r.stat for r in all_rows}):
        per_market[stat] = gap_block(
            [r for r in all_rows if r.stat == stat], f"exploratory: {stat}"
        )
    market_breakdown["per_market"] = per_market

    report = {
        "workstream_id": "MLB-MISSION10-ACCURACY",
        "experiment": "top_pick_calibration_holdout_replication",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "design_file": "DESIGN.md",
        "population": {
            "source": "results/grades_YYYY-MM-DD.json -> public_top_picks",
            "dates": HOLDOUT_DATES,
            "grade_filter": ["hit", "miss"],
            "excluded_ids": sorted(EXCLUDED_IDS),
            "excluded_row_detail": excluded_row_detail,
            "n": n,
        },
        "pregame_integrity_check": "PASSED (all published_top_pick_at strictly before game_start)",
        "primary_result": primary,
        "primary_cluster_bootstrap_ci": {
            "method": "cluster bootstrap by (slate_date, game_pk)",
            "ci_level": CI_LEVEL,
            "n_resamples": N_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "n_clusters": len({r.cluster_key for r in all_rows}),
            "lower": ci_lo,
            "upper": ci_hi,
            "excludes_zero_on_positive_side": ci_excludes_zero_positive,
        },
        "locked_success_rule": {
            "confirms_if": "gap > 0 AND 90% cluster-bootstrap CI excludes 0 (entirely positive) AND n >= 50",
            "falsifies_if": "90% CI includes 0, or gap <= 0",
            "min_n_for_confirmatory_labeling": MIN_N_FOR_CONFIRMATORY,
        },
        "verdict": verdict,
        "exploratory_market_breakdown_not_confirmatory": market_breakdown,
        "prior_context_pr128_not_recomputed_here": PR128_CONTEXT,
    }

    out_path = os.path.join(THIS_DIR, "report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=False)
        f.write("\n")

    print(f"n (primary, holdout, after 1 disclosed exclusion) = {n}")
    print(f"mean_predicted = {primary['mean_predicted']:.4f}" if n else "mean_predicted = n/a")
    print(f"realized_rate  = {primary['realized_rate']:.4f}" if n else "realized_rate  = n/a")
    print(f"gap            = {gap:.4f}" if n else "gap            = n/a")
    print(f"naive binomial two-sided p = {primary['naive_binomial_two_sided_p']}")
    print(f"cluster-bootstrap {int(CI_LEVEL*100)}% CI = [{ci_lo:.4f}, {ci_hi:.4f}]  (n_clusters={report['primary_cluster_bootstrap_ci']['n_clusters']})")
    print(f"VERDICT: {verdict}")
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
