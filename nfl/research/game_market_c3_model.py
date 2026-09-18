"""Predeclared C3 challenger: C2's margin features plus QB-continuity and
starter-availability, testing the project owner's named hypothesis that
C2's margin weakness is partly explained by missing starter/regime/
availability information.

C3 is margin-only by explicit instruction (do not add these features to a
totals model unless separately justified). It is fit *only* on
`development_2000_2019` `c3_eligibility == "ELIGIBLE"` rows built by
`game_market_c3_features.build_c3_game_rows`. Because
`injury_availability_features.py` only covers seasons 2009+, the real
development population fit here is a strict subset of C2's own
development_2000_2019 population -- see `game_market_c3_research.py` for the
exact eligible counts. Closing lines are never used to fit C3.

Promotion gate (predeclared here, before any validation/held partition is
evaluated -- see `PROMOTION_GATE_DESCRIPTION` and `evaluate_promotion_gate`).
The gate is written against BOTH B0 and C2, because the entire point of C3
is testing whether availability information closes the *specific* margin gap
C2 left open, not merely re-litigating C2 vs B0:

1. held margin MAE(C3) < held margin MAE(B0)                    (strict win vs B0)
2. held margin MAE(C3) < held margin MAE(C2)                    (strict win vs C2,
   on the identical C3-eligible paired population)
3. paired bootstrap 97.5th percentile of (C3-B0) held margin
   MAE delta is < 0                                    (not attributable to chance)
4. paired bootstrap 97.5th percentile of (C3-C2) held margin
   MAE delta is < 0                                    (not attributable to chance)
5. validation margin MAE(C3) <= validation margin MAE(B0)        (directionally
   consistent second check, not just a held-only fluke)
6. validation margin MAE(C3) <= validation margin MAE(C2)        (same, vs C2)
7. the held (C3-C2) margin MAE improvement survives leaving out any single
   held-partition season (2023, 2024, or 2025) -- i.e. no one held season
   alone explains the entire improvement. This directly targets the exact
   failure mode that sank C2's own margin result (driven largely by 2022):
   an aggregate improvement that disappears once the one favorable season is
   removed is not treated as a real, stable signal here either.

All seven must hold for `promotion_eligible = True`. Any failure is reported
with the specific unmet condition(s); a negative result is preserved exactly
like C1's/C2's, not softened or re-tuned after the fact.
"""
from __future__ import annotations

import random
import statistics
from typing import Any, Iterable, Mapping

from nfl.research import game_market_c2_ridge as ridge

C3_NAME = "GAME_MARKET_C3_MARGIN_QB_AVAILABILITY_RIDGE"

DEV_START, DEV_END = 2000, 2019
VALID_START, VALID_END = 2020, 2022
HELD_START, HELD_END = 2023, 2025

# Predeclared, untuned hyperparameter: never selected by looking at
# validation/held MAE. C3 follows the exact same "unit information"
# heuristic C2 used (penalty magnitude equal to the number of standardized
# features) rather than reusing C2's literal 8.0 unchanged, because that
# heuristic is mechanically a function of feature count: C3 fits 11
# features (C2's 8 plus the 3 new ones in
# `game_market_c3_features.NEW_MARGIN_FEATURES`), so the same rule gives
# 11.0. This is a mechanical application of C2's own already-predeclared
# rule, not a new tuning choice, and is fixed here before any held/
# validation evaluation runs.
RIDGE_LAMBDA = 11.0

PROMOTION_GATE_DESCRIPTION = (
    "held margin MAE(C3) < held margin MAE(B0) AND held margin MAE(C3) < "
    "held margin MAE(C2) AND the paired bootstrap 97.5th percentile of "
    "(C3-B0) held margin MAE delta is < 0 AND the paired bootstrap 97.5th "
    "percentile of (C3-C2) held margin MAE delta is < 0 AND validation "
    "margin MAE(C3) <= validation margin MAE(B0) AND validation margin "
    "MAE(C3) <= validation margin MAE(C2) AND the held (C3-C2) margin MAE "
    "improvement survives leaving out any single held-partition season "
    "(2023, 2024, or 2025) individually -- no one held season alone may "
    "explain the entire improvement, the exact failure mode that sank C2's "
    "own margin result (driven largely by 2022)."
)


class GameMarketC3ModelError(ValueError):
    pass


def fit_c3_model(
    c3_rows: Iterable[Mapping[str, Any]],
    *,
    margin_features: Iterable[str],
    ridge_lambda: float = RIDGE_LAMBDA,
) -> dict[str, Any]:
    """Fit the C3 margin ridge model on development-partition rows only."""
    dev_rows = [
        row for row in c3_rows
        if row.get("c3_eligibility") == "ELIGIBLE"
        and row.get("actual_margin") is not None
        and DEV_START <= int(row["season"]) <= DEV_END
    ]
    if not dev_rows:
        raise GameMarketC3ModelError("no eligible development rows to fit C3")

    margin_rows = [
        {**row["c3_margin_features"], "actual_margin": row["actual_margin"]}
        for row in dev_rows
    ]
    margin_model = ridge.fit_ridge(
        margin_rows,
        feature_names=list(margin_features),
        target_key="actual_margin",
        ridge_lambda=ridge_lambda,
    )
    return {
        "challenger_name": C3_NAME,
        "development_games": len(dev_rows),
        "development_seasons": (DEV_START, DEV_END),
        "ridge_lambda": ridge_lambda,
        "margin_model": margin_model,
    }


def apply_c3(
    c3_rows: Iterable[Mapping[str, Any]], model: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Attach a `c3_margin` prediction to every `c3_eligibility == "ELIGIBLE"` row."""
    margin_model = model["margin_model"]
    output = []
    for source in c3_rows:
        row = dict(source)
        if row.get("c3_eligibility") == "ELIGIBLE":
            row["c3_margin"] = ridge.predict_ridge(row["c3_margin_features"], margin_model)
        else:
            row["c3_margin"] = None
        output.append(row)
    return output


def pair_predictions(
    c3_rows: Iterable[Mapping[str, Any]], b0_rows: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Join B0/C2/C3 margin predictions on the C3-eligible population.

    `c3_rows` must already carry both `c2_margin` (from applying C2's own,
    separately fit `game_market_c2_model` on `row["margin_features"]`) and
    `c3_margin` (from `apply_c3` above) -- this function does not fit or
    apply either model itself, it only joins already-computed predictions
    for a fair, paired comparison on one identical population, matching
    `game_market_c2_model.pair_with_b0`'s own convention.
    """
    b0_by_id = {
        row["game_id"]: row
        for row in b0_rows
        if row.get("eligibility") == "ELIGIBLE" and row.get("target_final_status") == "FINAL"
    }
    paired = []
    for row in c3_rows:
        if row.get("c3_eligibility") != "ELIGIBLE" or row.get("actual_margin") is None:
            continue
        if row.get("c2_margin") is None or row.get("c3_margin") is None:
            raise GameMarketC3ModelError(
                f"C3-eligible row missing an applied model prediction: {row['game_id']}"
            )
        b0_row = b0_by_id.get(row["game_id"])
        if b0_row is None:
            continue
        for field in ("season", "week", "game_type", "home_team", "away_team"):
            if row.get(field) != b0_row.get(field):
                raise GameMarketC3ModelError(f"C3/B0 identity mismatch: {row['game_id']}")
        paired.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "actual_margin": row["actual_margin"],
            "b0_margin": float(b0_row["predicted_home_margin"]),
            "c2_margin": float(row["c2_margin"]),
            "c3_margin": float(row["c3_margin"]),
        })
    return sorted(paired, key=lambda r: (r["season"], r["game_id"]))


def _mae(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return statistics.fmean(abs(float(r[key]) - float(r["actual_margin"])) for r in rows)


def _bootstrap_delta(
    rows: list[dict[str, Any]], *, challenger_key: str, baseline_key: str,
    iterations: int = 2000, seed: int = 20260918,
) -> dict[str, Any]:
    if not rows:
        return {"games": 0, "iterations": iterations, "seed": seed, "p2_5": None, "p50": None, "p97_5": None}
    rng = random.Random(seed)
    deltas = []
    n = len(rows)
    for _ in range(iterations):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        challenger = statistics.fmean(abs(float(r[challenger_key]) - float(r["actual_margin"])) for r in sample)
        baseline = statistics.fmean(abs(float(r[baseline_key]) - float(r["actual_margin"])) for r in sample)
        deltas.append(challenger - baseline)
    deltas.sort()
    return {
        "games": n, "iterations": iterations, "seed": seed,
        "p2_5": deltas[min(iterations - 1, int(iterations * 0.025))],
        "p50": statistics.median(deltas),
        "p97_5": deltas[min(iterations - 1, int(iterations * 0.975))],
    }


def _partition_block(subset: list[dict[str, Any]], *, with_bootstrap: bool, seed_base: int) -> dict[str, Any]:
    b0_mae = _mae(subset, "b0_margin")
    c2_mae = _mae(subset, "c2_margin")
    c3_mae = _mae(subset, "c3_margin")
    block = {
        "games": len(subset),
        "b0_mae": b0_mae,
        "c2_mae": c2_mae,
        "c3_mae": c3_mae,
        "mae_delta_c3_minus_b0": None if not subset else c3_mae - b0_mae,
        "mae_delta_c3_minus_c2": None if not subset else c3_mae - c2_mae,
    }
    if with_bootstrap:
        block["bootstrap_c3_vs_b0"] = _bootstrap_delta(
            subset, challenger_key="c3_margin", baseline_key="b0_margin", seed=seed_base,
        )
        block["bootstrap_c3_vs_c2"] = _bootstrap_delta(
            subset, challenger_key="c3_margin", baseline_key="c2_margin", seed=seed_base + 1,
        )
    return block


def _leave_one_held_season_out(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    held = [r for r in paired_rows if HELD_START <= r["season"] <= HELD_END]
    seasons = sorted({r["season"] for r in held})
    results: dict[str, Any] = {}
    for excluded in seasons:
        subset = [r for r in held if r["season"] != excluded]
        results[str(excluded)] = {
            "games_remaining": len(subset),
            "mae_delta_c3_minus_c2_excluding_this_season": (
                None if not subset else _mae(subset, "c3_margin") - _mae(subset, "c2_margin")
            ),
            "mae_delta_c3_minus_b0_excluding_this_season": (
                None if not subset else _mae(subset, "c3_margin") - _mae(subset, "b0_margin")
            ),
        }
    return results


def evaluate_c3(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
    partitions = {
        "development_2000_2019": (DEV_START, DEV_END),
        "validation_2020_2022": (VALID_START, VALID_END),
        "held_2023_2025": (HELD_START, HELD_END),
    }
    result: dict[str, Any] = {}
    for name, (start, end) in partitions.items():
        subset = [r for r in paired_rows if start <= int(r["season"]) <= end]
        result[name] = _partition_block(
            subset,
            with_bootstrap=name in ("validation_2020_2022", "held_2023_2025"),
            seed_base=20260918 if name == "validation_2020_2022" else 20260920,
        )

    seasons = sorted({r["season"] for r in paired_rows})
    by_season = {}
    for season in seasons:
        subset = [r for r in paired_rows if r["season"] == season]
        by_season[str(season)] = _partition_block(subset, with_bootstrap=False, seed_base=0)
    result["season_by_season_all_covered"] = by_season
    result["held_leave_one_season_out"] = _leave_one_held_season_out(paired_rows)
    return result


def evaluate_promotion_gate(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the predeclared gate in `PROMOTION_GATE_DESCRIPTION` to `evaluation`."""
    held = evaluation["held_2023_2025"]
    validation = evaluation["validation_2020_2022"]
    leave_one_out = evaluation["held_leave_one_season_out"]

    failures = []
    if held["c3_mae"] is None or held["b0_mae"] is None or not held["c3_mae"] < held["b0_mae"]:
        failures.append("held margin MAE(C3) is not strictly better than B0")
    if held["c3_mae"] is None or held["c2_mae"] is None or not held["c3_mae"] < held["c2_mae"]:
        failures.append("held margin MAE(C3) is not strictly better than C2")

    bootstrap_vs_b0 = held.get("bootstrap_c3_vs_b0", {})
    if bootstrap_vs_b0.get("p97_5") is None or not bootstrap_vs_b0["p97_5"] < 0:
        failures.append("held (C3-B0) margin bootstrap 97.5th percentile does not stay below zero")
    bootstrap_vs_c2 = held.get("bootstrap_c3_vs_c2", {})
    if bootstrap_vs_c2.get("p97_5") is None or not bootstrap_vs_c2["p97_5"] < 0:
        failures.append("held (C3-C2) margin bootstrap 97.5th percentile does not stay below zero")

    if (
        validation["c3_mae"] is None or validation["b0_mae"] is None
        or not validation["c3_mae"] <= validation["b0_mae"]
    ):
        failures.append("validation margin MAE(C3) is worse than B0 (not directionally consistent)")
    if (
        validation["c3_mae"] is None or validation["c2_mae"] is None
        or not validation["c3_mae"] <= validation["c2_mae"]
    ):
        failures.append("validation margin MAE(C3) is worse than C2 (not directionally consistent)")

    if not leave_one_out:
        failures.append("no held seasons available to run the leave-one-season-out stability check")
    else:
        unstable_seasons = [
            season for season, block in leave_one_out.items()
            if block["mae_delta_c3_minus_c2_excluding_this_season"] is None
            or block["mae_delta_c3_minus_c2_excluding_this_season"] >= 0
        ]
        if unstable_seasons:
            failures.append(
                "held (C3-C2) margin improvement does not survive excluding season(s) "
                + ", ".join(sorted(unstable_seasons))
                + " -- the improvement is not broadly stable"
            )

    promotion_eligible = not failures
    reason = (
        "All predeclared gate conditions met on held 2023-2025 and validation "
        "2020-2022 data, including the leave-one-season-out stability check."
        if promotion_eligible
        else "Gate failed: " + "; ".join(failures) + "."
    )
    return {
        "promotion_eligible": promotion_eligible,
        "gate_description": PROMOTION_GATE_DESCRIPTION,
        "reason": reason,
        "failed_conditions": failures,
    }
