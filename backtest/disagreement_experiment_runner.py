#!/usr/bin/env python3
"""disagreement_experiment_runner.py -- ONE-COMMAND reproduction of the
disagreement research thread, built 2026-08-25 while the canonical
dataset was being rebuilt after two container restarts. Wraps, never
reimplements, the already-tested modules:

  - disagreement_decomposition.py (component audit, conflict-tier tables)
  - disagreement_challenger_model.py (equal-volume challenger)
  - canonical_baseline_report.py (row loading, season_phase)

Executes exactly the LOCKED protocol in
backtest/disagreement_experiment_protocol.md, written BEFORE this runner
saw any rebuilt data -- do not change PROMOTION_BAR_MIN_Z,
CONFLICT_HI/CONFLICT_LO, or SAFE_POOL_MIN_PROB after seeing a real result;
any genuine methodological fix belongs as a dated addendum to the
protocol doc, not a silent edit here.

    /tmp/mlbvenv/bin/python3 backtest/disagreement_experiment_runner.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, season_phase, load_rows
from disagreement_decomposition import (
    CAT_MARKETS, baseline_context_conflict, conflict_tier,
    same_probability_bucket_conflict_test, year_stability_of_conflict, build_report as decomposition_report,
)
from disagreement_challenger_model import (
    fit_bucket_tier_hit_rate, challenger_probability, TRAIN_YEARS, HOLDOUT_YEARS,
)
from pa_opportunity_model import equal_volume_ranking_comparison, _rate

DEFAULT_PATH = "backtest/rows_canonical.jsonl"

# ---- Locked protocol constants (backtest/disagreement_experiment_protocol.md) ----
SAFE_POOL_MIN_PROB = 0.60
PROMOTION_BAR_MIN_Z = 1.96

# ---- Pre-restart reference numbers, from
# backtest/disagreement_priority1_2_3_2026-08-25.md's own pooled table,
# used ONLY for the reproduction check below (never for the promotion
# verdict itself). ----
PRE_RESTART_REFERENCE_POOLED = {
    "hits_runs_rbis": {
        "high_empirical_low_context": 0.622, "balanced": 0.6498, "high_context_low_empirical": 0.7103,
    },
    "hits": {
        "high_empirical_low_context": 0.5353, "balanced": 0.5987, "high_context_low_empirical": 0.6522,
    },
}
REPRODUCTION_TOLERANCE_PP = 0.03  # 3 percentage points


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def data_audit(rows):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    dates = sorted({r.get("date") for r in graded if r.get("date")})
    shas = defaultdict(int)
    for r in graded:
        shas[r.get("code_git_sha")] += 1
    required = ["date", "prop_type", "predicted_prob", "outcome", "code_git_sha"]
    field_presence = {f: sum(1 for r in graded if r.get(f) is not None) for f in required}
    cat_presence = {m: sum(1 for r in graded if r.get("prop_type") == m and r.get("cat_baseline_skill") is not None)
                     for m in CAT_MARKETS}
    return {
        "n_rows_total": len(rows), "n_rows_graded": len(graded),
        "n_dates": len(dates), "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "code_git_sha_counts": dict(shas), "single_regime": len(set(shas) - {None}) <= 1,
        "required_field_presence": field_presence,
        "cat_market_row_counts": cat_presence,
    }


def _pearson(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 10:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return round(cov / (sx * sy), 4)


COMPONENT_FIELDS = ["cat_matchup", "cat_recent_form", "cat_baseline_skill", "cat_context", "score", "predicted_prob"]


def component_dependency_map(rows):
    """Pairwise Pearson correlations among the score components, per
    cat-market. cat_environment is deliberately excluded -- confirmed
    constant (--no-weather) in the pre-restart audit; re-verified live
    below via `constant_fields`, not assumed."""
    out = {}
    for market in CAT_MARKETS:
        market_rows = [r for r in rows if r.get("prop_type") == market and r.get("cat_baseline_skill") is not None]
        data = {f: [r.get(f) for r in market_rows] for f in COMPONENT_FIELDS + ["cat_environment"]}
        constant_fields = [f for f in data if len({v for v in data[f] if v is not None}) <= 1]
        pairs = {}
        for i, a in enumerate(COMPONENT_FIELDS):
            for b in COMPONENT_FIELDS[i + 1:]:
                pairs[f"{a} x {b}"] = _pearson(data[a], data[b])
        out[market] = {"n": len(market_rows), "constant_fields": constant_fields, "pairwise_correlations": pairs}
    return out


def reproduction_check(decomp_report):
    """Compares this run's pooled conflict-tier hit rates against the
    pre-restart reference. Returns REPRODUCED / PARTIALLY_REPRODUCED /
    NOT_REPRODUCED per market, and an overall verdict (the minimum of the
    two)."""
    per_market = {}
    for market, reference in PRE_RESTART_REFERENCE_POOLED.items():
        pooled = decomp_report.get("per_market", {}).get(market, {}).get("pooled_by_conflict_tier", {})
        tier_checks = {}
        for tier, ref_rate in reference.items():
            new = pooled.get(tier, {})
            new_rate = new.get("hit_rate")
            if new_rate is None:
                tier_checks[tier] = {"status": "MISSING", "reference": ref_rate, "observed": None}
                continue
            diff = abs(new_rate - ref_rate)
            tier_checks[tier] = {
                "status": "MATCH" if diff <= REPRODUCTION_TOLERANCE_PP else "DIVERGED",
                "reference": ref_rate, "observed": new_rate, "abs_diff": round(diff, 4),
            }
        # Ordering check: Weston-like < balanced < opposite, same as the reference.
        rates = {t: pooled.get(t, {}).get("hit_rate") for t in
                  ("high_empirical_low_context", "balanced", "high_context_low_empirical")}
        ordering_holds = (rates["high_empirical_low_context"] is not None and rates["balanced"] is not None
                          and rates["high_context_low_empirical"] is not None
                          and rates["high_empirical_low_context"] < rates["balanced"] < rates["high_context_low_empirical"])
        n_match = sum(1 for c in tier_checks.values() if c["status"] == "MATCH")
        if n_match == 3 and ordering_holds:
            verdict = "REPRODUCED"
        elif ordering_holds:
            verdict = "PARTIALLY_REPRODUCED"
        else:
            verdict = "NOT_REPRODUCED"
        per_market[market] = {"tier_checks": tier_checks, "ordering_holds": ordering_holds, "verdict": verdict}
    order = {"NOT_REPRODUCED": 0, "PARTIALLY_REPRODUCED": 1, "REPRODUCED": 2}
    overall = min(per_market.values(), key=lambda v: order[v["verdict"]])["verdict"] if per_market else "NOT_REPRODUCED"
    return {"per_market": per_market, "overall": overall}


def two_proportion_z(n1, p1, n2, p2):
    if n1 == 0 or n2 == 0:
        return None
    h1, h2 = n1 * p1, n2 * p2
    pooled = (h1 + h2) / (n1 + n2)
    if pooled in (0, 1):
        return None
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    return round((p1 - p2) / se, 3)


def primary_challenger_result(rows, market):
    """The ONE challenger the promotion verdict is based on -- exactly
    disagreement_challenger_model.py's existing fit/apply/equal-volume
    pipeline, reused verbatim."""
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    market_rows = [r for r in graded if r.get("prop_type") == market and r.get("cat_baseline_skill") is not None]
    train_rows = [r for r in market_rows if _year(r.get("date")) in TRAIN_YEARS]
    holdout_rows = [r for r in market_rows if _year(r.get("date")) in HOLDOUT_YEARS]
    cell_rate, bucket_rate = fit_bucket_tier_hit_rate(train_rows)
    comparisons = []
    for r in holdout_rows:
        challenger = challenger_probability(r, cell_rate, bucket_rate)
        if challenger is None:
            continue
        comparisons.append({"current_prob": r.get("predicted_prob"), "challenger_prob": challenger,
                             "outcome": r["outcome"], "date": r.get("date")})
    equal_volume = equal_volume_ranking_comparison(comparisons, min_line_prob=SAFE_POOL_MIN_PROB)

    z = None
    if equal_volume.get("n_added_by_challenger") and equal_volume.get("n_removed_by_challenger"):
        z = two_proportion_z(
            equal_volume["n_added_by_challenger"], equal_volume["added_hit_rate"],
            equal_volume["n_removed_by_challenger"], equal_volume["removed_hit_rate"])

    # Year stability of the added-vs-removed split (protocol's item 3).
    by_year = defaultdict(lambda: {"added_n": 0, "added_hits": 0, "removed_n": 0, "removed_hits": 0})
    ranked = sorted(comparisons, key=lambda c: c["challenger_prob"], reverse=True)
    n_selected = equal_volume.get("n_current_selected", 0)
    challenger_selected_ids = {id(c) for c in ranked[:n_selected]}
    current_selected = [c for c in comparisons if (c["current_prob"] or 0) >= SAFE_POOL_MIN_PROB]
    current_selected_ids = {id(c) for c in current_selected}
    for c in comparisons:
        in_challenger = id(c) in challenger_selected_ids
        in_current = id(c) in current_selected_ids
        y = _year(c["date"])
        if in_challenger and not in_current:
            by_year[y]["added_n"] += 1
            by_year[y]["added_hits"] += c["outcome"]
        elif in_current and not in_challenger:
            by_year[y]["removed_n"] += 1
            by_year[y]["removed_hits"] += c["outcome"]

    year_stability = {y: {"added_hit_rate": _rate(v["added_hits"], v["added_n"]), "added_n": v["added_n"],
                            "removed_hit_rate": _rate(v["removed_hits"], v["removed_n"]), "removed_n": v["removed_n"]}
                       for y, v in sorted(by_year.items())}

    return {"market": market, "equal_volume": equal_volume, "z": z, "year_stability": year_stability}


def promotion_verdict(primary_results):
    """Implements backtest/disagreement_experiment_protocol.md's
    PROMOTION BAR exactly. Returns EARNS_SHADOW or CLOSED with reasons,
    per market (a market-specific challenger may earn shadow testing even
    if another does not -- Priority 9's own instruction not to force one
    universal policy)."""
    verdicts = {}
    for market, result in primary_results.items():
        ev = result["equal_volume"]
        reasons = []
        positive_gain = (ev.get("challenger_hit_rate") or 0) > (ev.get("current_hit_rate") or 0)
        if not positive_gain:
            reasons.append("no positive net equal-volume gain")
        z = result["z"]
        z_ok = z is not None and z >= PROMOTION_BAR_MIN_Z
        if not z_ok:
            reasons.append(f"added-vs-removed z={z} below bar {PROMOTION_BAR_MIN_Z}")
        # Direction consistency: added_hit_rate > removed_hit_rate in >=2/3 years with n>=200 each side.
        years_checked = 0
        years_consistent = 0
        for y, v in result["year_stability"].items():
            if v["added_n"] >= 200 and v["removed_n"] >= 200:
                years_checked += 1
                if (v["added_hit_rate"] or 0) > (v["removed_hit_rate"] or 0):
                    years_consistent += 1
        direction_ok = years_checked == 0 or years_consistent >= max(1, round(years_checked * 2 / 3))
        if not direction_ok:
            reasons.append(f"direction inconsistent across years ({years_consistent}/{years_checked})")
        verdicts[market] = {
            "verdict": "EARNS_SHADOW" if (positive_gain and z_ok and direction_ok) else "CLOSED",
            "reasons": reasons or ["all promotion-bar criteria met"],
        }
    return verdicts


def run_full_experiment(path):
    try:
        rows, n_malformed = load_rows(path)
    except FileNotFoundError:
        return {"error": f"no such file: {path}"}
    if not rows:
        return {"error": f"no rows read from {path}"}

    audit = data_audit(rows)
    deps = component_dependency_map(rows)
    decomp = decomposition_report(rows)
    repro = reproduction_check(decomp)

    primary = {market: primary_challenger_result(rows, market) for market in CAT_MARKETS}
    verdicts = promotion_verdict(primary)

    return {
        "_source_file": path, "_n_malformed_lines_skipped": n_malformed,
        "data_audit": audit,
        "component_dependency_map": deps,
        "same_probability_disagreement_tables": decomp,
        "reproduction_check": repro,
        "primary_challenger_equal_volume": primary,
        "promotion_verdict": verdicts,
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    report = run_full_experiment(path)
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
