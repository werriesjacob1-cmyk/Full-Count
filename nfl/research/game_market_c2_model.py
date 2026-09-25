"""Predeclared C2 challenger: strictly-prior context ridge regression for NFL
game markets.

C2 asks whether the merged-but-unused team/defense/matchup/PBP feature
substrate improves on B0's naive prior-scoring baseline. It is fit *only* on
`development_2000_2019` eligible FINAL games using the feature contrasts
built by `game_market_c2_features.build_c2_game_rows`. Closing lines are
never used to fit C2; they are read only afterwards, as a retrospective
benchmark, exactly like B0/C1.

Promotion gate (predeclared here, before any validation/held partition is
evaluated -- see `PROMOTION_GATE_DESCRIPTION` and `evaluate_promotion_gate`):

1. held margin MAE(C2) < held margin MAE(B0)                    (strict win)
2. paired bootstrap 97.5th percentile of (C2 - B0) held margin
   MAE delta is < 0                                    (not attributable to chance)
3. held total MAE(C2) <= held total MAE(B0)                      (not worse)
4. validation margin MAE(C2) <= validation margin MAE(B0)        (directionally
   consistent second check, not just a held-only fluke)

All four must hold for `promotion_eligible = True`. Any failure is reported
with the specific unmet condition(s); a negative result is preserved exactly
like C1's, not softened or re-tuned after the fact.
"""
from __future__ import annotations

import random
import statistics
from typing import Any, Iterable, Mapping

from nfl.research import game_market_c2_ridge as ridge

C2_NAME = "GAME_MARKET_C2_PRIOR_CONTEXT_RIDGE"

DEV_START, DEV_END = 2000, 2019
VALID_START, VALID_END = 2020, 2022
HELD_START, HELD_END = 2023, 2025

# Predeclared, untuned hyperparameter: never selected by looking at
# validation/held MAE. `RIDGE_LAMBDA` uses the common "unit information"
# heuristic (penalty magnitude equal to the number of standardized
# features), which is a mild regularizer given ~5,000 development rows for
# 8 features -- there is no plausible choice of this value in that
# neighborhood that would flip the qualitative dev-partition fit.
RIDGE_LAMBDA = 8.0

PROMOTION_GATE_DESCRIPTION = (
    "held margin MAE(C2) < held margin MAE(B0) AND the paired bootstrap "
    "97.5th percentile of (C2-B0) held margin MAE delta is < 0 AND held "
    "total MAE(C2) <= held total MAE(B0) AND validation margin MAE(C2) <= "
    "validation margin MAE(B0)."
)


class GameMarketC2ModelError(ValueError):
    pass


def fit_c2_model(
    c2_rows: Iterable[Mapping[str, Any]],
    *,
    margin_features: Iterable[str],
    total_features: Iterable[str],
    ridge_lambda: float = RIDGE_LAMBDA,
) -> dict[str, Any]:
    """Fit margin and total ridge models on development-partition rows only."""
    dev_rows = [
        row for row in c2_rows
        if row.get("eligibility") == "ELIGIBLE"
        and row.get("actual_margin") is not None
        and DEV_START <= int(row["season"]) <= DEV_END
    ]
    if not dev_rows:
        raise GameMarketC2ModelError("no eligible development rows to fit C2")

    margin_rows = [
        {**row["margin_features"], "actual_margin": row["actual_margin"]}
        for row in dev_rows
    ]
    total_rows = [
        {**row["total_features"], "actual_total": row["actual_total"]}
        for row in dev_rows
    ]
    margin_model = ridge.fit_ridge(
        margin_rows,
        feature_names=list(margin_features),
        target_key="actual_margin",
        ridge_lambda=ridge_lambda,
    )
    total_model = ridge.fit_ridge(
        total_rows,
        feature_names=list(total_features),
        target_key="actual_total",
        ridge_lambda=ridge_lambda,
    )
    return {
        "challenger_name": C2_NAME,
        "development_games": len(dev_rows),
        "development_seasons": (DEV_START, DEV_END),
        "ridge_lambda": ridge_lambda,
        "margin_model": margin_model,
        "total_model": total_model,
    }


def apply_c2(
    c2_rows: Iterable[Mapping[str, Any]], model: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Attach `c2_margin`/`c2_total` predictions to every ELIGIBLE row."""
    margin_model = model["margin_model"]
    total_model = model["total_model"]
    output = []
    for source in c2_rows:
        row = dict(source)
        if row.get("eligibility") == "ELIGIBLE":
            row["c2_margin"] = ridge.predict_ridge(row["margin_features"], margin_model)
            row["c2_total"] = ridge.predict_ridge(row["total_features"], total_model)
        else:
            row["c2_margin"] = None
            row["c2_total"] = None
        output.append(row)
    return output


def pair_with_b0(
    c2_rows: Iterable[Mapping[str, Any]], b0_rows: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Join C2 predictions with B0 predictions on the common eligible population.

    Only games where *both* B0 and C2 have an ELIGIBLE, FINAL prediction are
    kept -- this is the documented common-population intersection (see the
    C2 research runner for the exact, small, honestly-reported exclusion
    this produces).
    """
    b0_by_id = {
        row["game_id"]: row
        for row in b0_rows
        if row.get("eligibility") == "ELIGIBLE" and row.get("target_final_status") == "FINAL"
    }
    paired = []
    for row in c2_rows:
        if row.get("eligibility") != "ELIGIBLE" or row.get("actual_margin") is None:
            continue
        b0_row = b0_by_id.get(row["game_id"])
        if b0_row is None:
            continue
        for field in ("season", "week", "game_type", "home_team", "away_team"):
            if row.get(field) != b0_row.get(field):
                raise GameMarketC2ModelError(f"C2/B0 identity mismatch: {row['game_id']}")
        paired.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "actual_margin": row["actual_margin"],
            "actual_total": row["actual_total"],
            "b0_margin": float(b0_row["predicted_home_margin"]),
            "b0_total": float(b0_row["predicted_total"]),
            "c2_margin": row["c2_margin"],
            "c2_total": row["c2_total"],
        })
    return sorted(paired, key=lambda r: (r["season"], r["game_id"]))


def _mae(rows: list[dict[str, Any]], key: str, actual_key: str) -> float | None:
    if not rows:
        return None
    return statistics.fmean(abs(float(r[key]) - float(r[actual_key])) for r in rows)


def _bootstrap_delta(
    rows: list[dict[str, Any]], *, challenger_key: str, baseline_key: str, actual_key: str,
    iterations: int = 2000, seed: int = 20260918,
) -> dict[str, Any]:
    if not rows:
        return {"games": 0, "iterations": iterations, "seed": seed, "p2_5": None, "p50": None, "p97_5": None}
    rng = random.Random(seed)
    deltas = []
    n = len(rows)
    for _ in range(iterations):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        challenger = statistics.fmean(abs(float(r[challenger_key]) - float(r[actual_key])) for r in sample)
        baseline = statistics.fmean(abs(float(r[baseline_key]) - float(r[actual_key])) for r in sample)
        deltas.append(challenger - baseline)
    deltas.sort()
    return {
        "games": n, "iterations": iterations, "seed": seed,
        "p2_5": deltas[min(iterations - 1, int(iterations * 0.025))],
        "p50": statistics.median(deltas),
        "p97_5": deltas[min(iterations - 1, int(iterations * 0.975))],
    }


def _partition_block(subset: list[dict[str, Any]], *, with_bootstrap: bool, seed_base: int) -> dict[str, Any]:
    margin_b0 = _mae(subset, "b0_margin", "actual_margin")
    margin_c2 = _mae(subset, "c2_margin", "actual_margin")
    total_b0 = _mae(subset, "b0_total", "actual_total")
    total_c2 = _mae(subset, "c2_total", "actual_total")
    block = {
        "games": len(subset),
        "margin": {
            "b0_mae": margin_b0,
            "c2_mae": margin_c2,
            "mae_delta_c2_minus_b0": None if not subset else margin_c2 - margin_b0,
        },
        "total": {
            "b0_mae": total_b0,
            "c2_mae": total_c2,
            "mae_delta_c2_minus_b0": None if not subset else total_c2 - total_b0,
        },
    }
    if with_bootstrap:
        block["margin"]["game_bootstrap"] = _bootstrap_delta(
            subset, challenger_key="c2_margin", baseline_key="b0_margin", actual_key="actual_margin",
            seed=seed_base,
        )
        block["total"]["game_bootstrap"] = _bootstrap_delta(
            subset, challenger_key="c2_total", baseline_key="b0_total", actual_key="actual_total",
            seed=seed_base + 1,
        )
    return block


def evaluate_c2(paired_rows: list[dict[str, Any]]) -> dict[str, Any]:
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

    seasons = sorted({r["season"] for r in paired_rows if VALID_START <= r["season"] <= HELD_END})
    by_season = {}
    for season in seasons:
        subset = [r for r in paired_rows if r["season"] == season]
        by_season[str(season)] = _partition_block(subset, with_bootstrap=False, seed_base=0)
    result["season_by_season_2020_2025"] = by_season
    return result


def evaluate_promotion_gate(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the predeclared gate in `PROMOTION_GATE_DESCRIPTION` to `evaluation`."""
    held = evaluation["held_2023_2025"]
    validation = evaluation["validation_2020_2022"]

    held_margin_b0 = held["margin"]["b0_mae"]
    held_margin_c2 = held["margin"]["c2_mae"]
    held_total_b0 = held["total"]["b0_mae"]
    held_total_c2 = held["total"]["c2_mae"]
    held_margin_bootstrap_p97_5 = held["margin"]["game_bootstrap"]["p97_5"]
    val_margin_b0 = validation["margin"]["b0_mae"]
    val_margin_c2 = validation["margin"]["c2_mae"]

    failures = []
    if held_margin_c2 is None or held_margin_b0 is None or not held_margin_c2 < held_margin_b0:
        failures.append("held margin MAE is not strictly better than B0")
    if held_margin_bootstrap_p97_5 is None or not held_margin_bootstrap_p97_5 < 0:
        failures.append("held margin bootstrap 97.5th percentile does not stay below zero")
    if held_total_c2 is None or held_total_b0 is None or not held_total_c2 <= held_total_b0:
        failures.append("held total MAE is worse than B0")
    if val_margin_c2 is None or val_margin_b0 is None or not val_margin_c2 <= val_margin_b0:
        failures.append("validation margin MAE is worse than B0 (not directionally consistent)")

    promotion_eligible = not failures
    reason = (
        "All predeclared gate conditions met on held 2023-2025 and validation "
        "2020-2022 data."
        if promotion_eligible
        else "Gate failed: " + "; ".join(failures) + "."
    )
    return {
        "promotion_eligible": promotion_eligible,
        "gate_description": PROMOTION_GATE_DESCRIPTION,
        "reason": reason,
        "failed_conditions": failures,
    }
