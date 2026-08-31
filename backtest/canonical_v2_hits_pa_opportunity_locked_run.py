#!/usr/bin/env python3
"""Locked canonical-v2 Hits PA/opportunity experiment.

Protocol: backtest/canonical_v2_hits_pa_opportunity_protocol.md

Primary challenger is the pre-existing residual opportunity model
(order + days_rest + getaway_day). Secondary order-only results are descriptive
only. The decisive comparison preserves champion pick count independently on
EVERY date, preventing a challenger from shifting volume onto easier slates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backtest.canonical_baseline_report import load_rows, season_phase
from backtest.opportunity_decomposition import derive_batting_order, HITTER_MARKETS
from backtest.pa_opportunity_model import (
    candidate_key,
    challenger_probability as order_challenger_probability,
    dedupe_player_games,
    fit_hit_rate_given_pa,
    fit_pa_distribution,
)
from backtest.residual_challenger_model import (
    challenger_probability_joint,
    fit_joint_pa_distribution,
)

PROTOCOL_VERSION = "canonical-v2-hits-pa-opportunity-v1"
SAFE_POOL_MIN_PROB = 0.60
PRIMARY_MODEL = "residual_order_days_rest_getaway"
SECONDARY_MODEL = "order_only"
MIN_PROMOTION_Z = 1.96
BOOTSTRAP_SEED = 20260831
BOOTSTRAP_REPS = 4000


class ExperimentError(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _year(day):
    return str(day or "").split("-")[0]


def _rate(hits, n):
    return hits / n if n else None


def _round(value, digits=6):
    return round(value, digits) if value is not None else None


def wilson_interval(hits, n, z=1.96):
    if not n:
        return [None, None]
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (
        z
        * math.sqrt((p * (1 - p) / n) + (z * z / (4 * n * n)))
        / denom
    )
    return [_round(max(0.0, center - half)), _round(min(1.0, center + half))]


def two_proportion_z(h1, n1, h2, n2):
    if not n1 or not n2:
        return {"z": None, "p_two_sided": None}
    p1 = h1 / n1
    p2 = h2 / n2
    pooled = (h1 + h2) / (n1 + n2)
    if pooled in (0, 1):
        return {"z": None, "p_two_sided": None}
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return {"z": None, "p_two_sided": None}
    z = (p1 - p2) / se
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {"z": _round(z, 4), "p_two_sided": _round(p, 6)}


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def date_cluster_bootstrap(date_summaries, reps=BOOTSTRAP_REPS, seed=BOOTSTRAP_SEED):
    """Bootstrap dates, not individual correlated player/market rows."""
    usable = [d for d in date_summaries if d["current_n"] > 0]
    if not usable:
        return {
            "reps": reps,
            "seed": seed,
            "delta_95pct": [None, None],
        }
    rng = random.Random(seed)
    deltas = []
    for _ in range(reps):
        sample = [usable[rng.randrange(len(usable))] for _ in usable]
        cn = sum(x["current_n"] for x in sample)
        chn = sum(x["challenger_n"] for x in sample)
        if cn <= 0 or chn <= 0:
            continue
        cr = sum(x["current_hits"] for x in sample) / cn
        chr_ = sum(x["challenger_hits"] for x in sample) / chn
        deltas.append(chr_ - cr)
    deltas.sort()
    return {
        "reps": reps,
        "seed": seed,
        "successful_reps": len(deltas),
        "delta_95pct": [
            _round(_quantile(deltas, 0.025)),
            _round(_quantile(deltas, 0.975)),
        ],
    }


def _semantic_key(row):
    key = candidate_key(row)
    if key is None:
        raise ExperimentError(
            "canonical row lacks required candidate identity for experiment"
        )
    return tuple(key)


def _key_string(key):
    return json.dumps(key, separators=(",", ":"), default=str)


def date_matched_compare(comparisons):
    """Exact same N on every date over the exact same candidate universe."""
    by_date = defaultdict(list)
    seen_keys = set()
    for c in comparisons:
        key = tuple(c["candidate_key"])
        if key in seen_keys:
            raise ExperimentError(f"duplicate experiment candidate {key!r}")
        seen_keys.add(key)
        by_date[c["date"]].append(c)

    all_current = []
    all_challenger = []
    date_summaries = []
    selection_mismatches = []

    for day, items in sorted(by_date.items()):
        current = [
            c for c in items
            if float(c["current_prob"]) >= SAFE_POOL_MIN_PROB
        ]
        n = len(current)
        ranked = sorted(
            items,
            key=lambda c: (
                -float(c["rank_prob"]),
                -float(c["current_prob"]),
                _key_string(c["candidate_key"]),
            ),
        )
        challenger = ranked[:n]
        if len(challenger) != n:
            selection_mismatches.append({
                "date": day,
                "current_n": n,
                "challenger_n": len(challenger),
            })

        current_ids = {tuple(c["candidate_key"]) for c in current}
        challenger_ids = {tuple(c["candidate_key"]) for c in challenger}
        overlap_ids = current_ids & challenger_ids
        added = [
            c for c in challenger
            if tuple(c["candidate_key"]) not in current_ids
        ]
        removed = [
            c for c in current
            if tuple(c["candidate_key"]) not in challenger_ids
        ]
        if len(added) != len(removed):
            selection_mismatches.append({
                "date": day,
                "added_n": len(added),
                "removed_n": len(removed),
            })

        date_summaries.append({
            "date": day,
            "candidate_n": len(items),
            "current_n": len(current),
            "challenger_n": len(challenger),
            "current_hits": sum(int(c["outcome"]) for c in current),
            "challenger_hits": sum(int(c["outcome"]) for c in challenger),
            "overlap_n": len(overlap_ids),
            "added_n": len(added),
            "added_hits": sum(int(c["outcome"]) for c in added),
            "removed_n": len(removed),
            "removed_hits": sum(int(c["outcome"]) for c in removed),
        })
        all_current.extend(current)
        all_challenger.extend(challenger)

    current_ids = {tuple(c["candidate_key"]) for c in all_current}
    challenger_ids = {tuple(c["candidate_key"]) for c in all_challenger}
    overlap_ids = current_ids & challenger_ids
    added = [
        c for c in all_challenger
        if tuple(c["candidate_key"]) not in current_ids
    ]
    removed = [
        c for c in all_current
        if tuple(c["candidate_key"]) not in challenger_ids
    ]

    current_hits = sum(int(c["outcome"]) for c in all_current)
    challenger_hits = sum(int(c["outcome"]) for c in all_challenger)
    added_hits = sum(int(c["outcome"]) for c in added)
    removed_hits = sum(int(c["outcome"]) for c in removed)

    current_rate = _rate(current_hits, len(all_current))
    challenger_rate = _rate(challenger_hits, len(all_challenger))
    added_rate = _rate(added_hits, len(added))
    removed_rate = _rate(removed_hits, len(removed))

    phase = defaultdict(
        lambda: {"added_n": 0, "added_hits": 0, "removed_n": 0, "removed_hits": 0}
    )
    for c in added:
        p = season_phase(c["date"])
        phase[p]["added_n"] += 1
        phase[p]["added_hits"] += int(c["outcome"])
    for c in removed:
        p = season_phase(c["date"])
        phase[p]["removed_n"] += 1
        phase[p]["removed_hits"] += int(c["outcome"])

    phase_report = {}
    for p, v in sorted(phase.items()):
        phase_report[p] = {
            **v,
            "added_hit_rate": _round(_rate(v["added_hits"], v["added_n"])),
            "removed_hit_rate": _round(_rate(v["removed_hits"], v["removed_n"])),
        }

    direction = {"positive": 0, "equal": 0, "negative": 0}
    for d in date_summaries:
        if d["added_n"] == 0:
            continue
        diff = d["added_hits"] - d["removed_hits"]
        if diff > 0:
            direction["positive"] += 1
        elif diff < 0:
            direction["negative"] += 1
        else:
            direction["equal"] += 1

    z = two_proportion_z(
        added_hits, len(added), removed_hits, len(removed)
    )
    return {
        "candidate_dates": len(by_date),
        "candidate_n": len(comparisons),
        "selected_n_current": len(all_current),
        "selected_n_challenger": len(all_challenger),
        "current_hits": current_hits,
        "challenger_hits": challenger_hits,
        "current_hit_rate": _round(current_rate),
        "challenger_hit_rate": _round(challenger_rate),
        "hit_rate_delta": (
            _round(challenger_rate - current_rate)
            if current_rate is not None and challenger_rate is not None
            else None
        ),
        "current_hit_rate_wilson_95": wilson_interval(
            current_hits, len(all_current)
        ),
        "challenger_hit_rate_wilson_95": wilson_interval(
            challenger_hits, len(all_challenger)
        ),
        "overlap_n": len(overlap_ids),
        "added_n": len(added),
        "added_hits": added_hits,
        "added_hit_rate": _round(added_rate),
        "added_hit_rate_wilson_95": wilson_interval(added_hits, len(added)),
        "removed_n": len(removed),
        "removed_hits": removed_hits,
        "removed_hit_rate": _round(removed_rate),
        "removed_hit_rate_wilson_95": wilson_interval(
            removed_hits, len(removed)
        ),
        "added_vs_removed": z,
        "date_cluster_bootstrap": date_cluster_bootstrap(date_summaries),
        "churn_date_direction": direction,
        "season_phase_added_removed": phase_report,
        "selection_count_mismatches": selection_mismatches,
        "date_summaries": date_summaries,
    }


def _fit(rows, train_years, model):
    graded_hitter = [
        r for r in rows
        if r.get("outcome") in (0, 1)
        and r.get("prop_type") in HITTER_MARKETS
        and _year(r.get("date")) in train_years
    ]
    if not graded_hitter:
        raise ExperimentError(f"no training rows for years {sorted(train_years)}")
    player_games = dedupe_player_games(graded_hitter)
    order_dist = fit_pa_distribution(player_games)
    hit_rate = fit_hit_rate_given_pa(graded_hitter, "hits")
    if model == PRIMARY_MODEL:
        return {
            "order_dist": order_dist,
            "hit_rate": hit_rate,
            "joint_dist": fit_joint_pa_distribution(player_games),
            "train_player_games": len(player_games),
        }
    if model == SECONDARY_MODEL:
        return {
            "order_dist": order_dist,
            "hit_rate": hit_rate,
            "joint_dist": None,
            "train_player_games": len(player_games),
        }
    raise ExperimentError(f"unknown model {model}")


def _raw_challenger(row, fitted, model):
    if model == PRIMARY_MODEL:
        return challenger_probability_joint(
            row,
            fitted["joint_dist"],
            fitted["order_dist"],
            fitted["hit_rate"],
        )
    order = derive_batting_order(
        (row.get("signals") or {}).get("lineup_slot")
    )
    if order is None:
        return None
    return order_challenger_probability(
        order, fitted["order_dist"], fitted["hit_rate"]
    )


def evaluate_walk_forward(rows, train_years, eval_year, model):
    fitted = _fit(rows, set(train_years), model)
    eval_rows = [
        r for r in rows
        if r.get("outcome") in (0, 1)
        and r.get("prop_type") == "hits"
        and _year(r.get("date")) == str(eval_year)
    ]
    if not eval_rows:
        raise ExperimentError(f"no Hits rows for evaluation year {eval_year}")

    comparisons = []
    fallback = 0
    for row in eval_rows:
        current = float(row["predicted_prob"])
        raw = _raw_challenger(row, fitted, model)
        if raw is None:
            fallback += 1
            rank_prob = current
        else:
            rank_prob = float(raw)
        comparisons.append({
            "candidate_key": _semantic_key(row),
            "date": row["date"],
            "current_prob": current,
            "challenger_prob_raw": raw,
            "rank_prob": rank_prob,
            "outcome": int(row["outcome"]),
        })

    result = date_matched_compare(comparisons)
    result.update({
        "model": model,
        "train_years": sorted(str(y) for y in train_years),
        "evaluation_year": str(eval_year),
        "eval_hits_rows": len(eval_rows),
        "challenger_unavailable_neutral_fallback_n": fallback,
        "challenger_direct_coverage": _round(
            (len(eval_rows) - fallback) / len(eval_rows)
        ),
        "train_player_games": fitted["train_player_games"],
        "joint_cells_fit": (
            len(fitted["joint_dist"])
            if fitted["joint_dist"] is not None
            else None
        ),
    })
    return result


def promotion_verdict(primary_2025, primary_2026):
    reasons = []
    if not (
        primary_2026.get("hit_rate_delta") is not None
        and primary_2026["hit_rate_delta"] > 0
    ):
        reasons.append("2026 date-matched equal-volume gain is not positive")

    z = (primary_2026.get("added_vs_removed") or {}).get("z")
    if z is None or z < MIN_PROMOTION_Z:
        reasons.append(
            f"2026 added-vs-removed z={z} below {MIN_PROMOTION_Z}"
        )

    ci = (
        (primary_2026.get("date_cluster_bootstrap") or {})
        .get("delta_95pct")
        or [None, None]
    )
    if ci[0] is None or ci[0] <= 0:
        reasons.append(
            f"2026 date-cluster bootstrap lower bound {ci[0]} is not > 0"
        )

    if not (
        primary_2025.get("hit_rate_delta") is not None
        and primary_2025["hit_rate_delta"] >= 0
    ):
        reasons.append("2025 walk-forward direction is negative")

    adverse_phases = []
    for phase, v in (
        primary_2026.get("season_phase_added_removed") or {}
    ).items():
        if v["added_n"] >= 200 and v["removed_n"] >= 200:
            if (v["added_hit_rate"] or 0) < (v["removed_hit_rate"] or 0):
                adverse_phases.append(phase)
    if adverse_phases:
        reasons.append(
            "2026 adequate-sample season phase reversal: "
            + ", ".join(adverse_phases)
        )

    mismatches = (
        (primary_2025.get("selection_count_mismatches") or [])
        + (primary_2026.get("selection_count_mismatches") or [])
    )
    if mismatches:
        reasons.append("per-date equal-volume invariant failed")

    return {
        "verdict": "CLOSED" if reasons else "EARNS_PROSPECTIVE_SHADOW",
        "reasons": reasons or [
            "all locked historical criteria met; prospective shadow only"
        ],
        "production_promotion_authorized": False,
    }


def validate_certified_input(rows_path, cert_path, manifest_path):
    cert = json.load(open(cert_path, encoding="utf-8"))
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    rows_sha = sha256_file(rows_path)
    if cert.get("verdict") != "CANONICAL CERTIFIED":
        raise ExperimentError(
            f"research certification verdict is {cert.get('verdict')!r}"
        )
    if cert.get("research_rows_sha256") != rows_sha:
        raise ExperimentError("certification research_rows_sha256 mismatch")
    if manifest.get("research_rows_sha256") != rows_sha:
        raise ExperimentError("research-view manifest rows SHA mismatch")
    return cert, manifest, rows_sha


def run(rows_path, cert_path, manifest_path):
    cert, manifest, rows_sha = validate_certified_input(
        rows_path, cert_path, manifest_path
    )
    rows, malformed = load_rows(rows_path)
    if malformed:
        raise ExperimentError(
            f"certified research rows unexpectedly contain {malformed} malformed lines"
        )
    if not rows:
        raise ExperimentError("certified research view contains no rows")

    primary_2025 = evaluate_walk_forward(
        rows, {"2024"}, "2025", PRIMARY_MODEL
    )
    primary_2026 = evaluate_walk_forward(
        rows, {"2024", "2025"}, "2026", PRIMARY_MODEL
    )
    secondary_2025 = evaluate_walk_forward(
        rows, {"2024"}, "2025", SECONDARY_MODEL
    )
    secondary_2026 = evaluate_walk_forward(
        rows, {"2024", "2025"}, "2026", SECONDARY_MODEL
    )

    protocol_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "canonical_v2_hits_pa_opportunity_protocol.md",
    )
    source_paths = {
        "protocol": protocol_path,
        "runner": os.path.abspath(__file__),
        "primary_challenger_source": os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "residual_challenger_model.py",
        ),
        "secondary_challenger_source": os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "pa_opportunity_model.py",
        ),
    }

    return {
        "experiment": "canonical_v2_hits_pa_opportunity",
        "protocol_version": PROTOCOL_VERSION,
        "evidence_regime": "canonical_historical_model_data",
        "market": "hits",
        "market_mix": {"hits": 1.0},
        "historical_safe_pool_proxy": {
            "rule": f"predicted_prob >= {SAFE_POOL_MIN_PROB}",
            "real_historical_production_eligibility_claimed": False,
        },
        "research_input": {
            "rows_sha256": rows_sha,
            "research_row_count": len(rows),
            "certification_report_sha256": cert.get(
                "certification_report_sha256"
            ),
            "research_view_manifest_sha256": manifest.get(
                "manifest_sha256"
            ),
            "quarantined_dates": (
                (manifest.get("quarantine") or {}).get("excluded_dates")
                or []
            ),
            "quarantine_counts": (
                (manifest.get("quarantine") or {}).get("counts") or {}
            ),
        },
        "locked_source_sha256": {
            name: sha256_file(path) for name, path in source_paths.items()
        },
        "primary_challenger": {
            "model": PRIMARY_MODEL,
            "2025_walk_forward": primary_2025,
            "2026_holdout": primary_2026,
        },
        "secondary_context_only": {
            "model": SECONDARY_MODEL,
            "cannot_rescue_primary": True,
            "2025_walk_forward": secondary_2025,
            "2026_holdout": secondary_2026,
        },
        "promotion_interpretation": promotion_verdict(
            primary_2025, primary_2026
        ),
        "caveats": [
            (
                "2026 has been inspected in earlier exploratory work; even a "
                "historical pass earns prospective shadow testing only."
            ),
            (
                "Canonical historical model data does not reconstruct exact "
                "sportsbook prices/publication timing or true production eligibility."
            ),
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rows")
    ap.add_argument("--certification", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output")
    args = ap.parse_args()

    result = run(args.rows, args.certification, args.manifest)
    raw = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    print(raw, end="")
    if args.output:
        if os.path.exists(args.output):
            raise FileExistsError(f"refusing to overwrite {args.output}")
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
