#!/usr/bin/env python3
"""Same-population evaluation for NFL V0 B1 versus frozen B0.

A challenger does not get credit for lower error by silently dropping harder
rows. Each market therefore reports:
- full B0 reference population
- B1-comparable population
- coverage ratio
- B0 and B1 errors on the exact same comparison rows
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Any, Callable

from nfl.research import b1_team_opportunity as b1
from nfl.research import v0_baseline as b0


MARKETS = {
    "pass_attempts": ("attempts", b1.predict_pass_attempts),
    "rush_attempts": ("carries", b1.predict_rush_attempts),
    "receptions": ("receptions", b1.predict_receptions),
    "passing_yards": ("passing_yards", b1.predict_passing_yards),
    "rushing_yards": ("rushing_yards", b1.predict_rushing_yards),
    "receiving_yards": ("receiving_yards", b1.predict_receiving_yards),
}

B1_REQUIRED = {
    "pass_attempts": (
        "projected_team_pass_attempts",
        "prior_player_pass_attempt_share",
    ),
    "rush_attempts": (
        "projected_team_carries",
        "prior_player_carry_share",
    ),
    "receptions": (
        "projected_team_targets",
        "prior_player_target_share",
        "prior_player_catch_rate",
    ),
    "passing_yards": (
        "projected_team_pass_attempts",
        "prior_player_pass_attempt_share",
        "prior_player_pass_yards_per_attempt",
    ),
    "rushing_yards": (
        "projected_team_carries",
        "prior_player_carry_share",
        "prior_player_rush_yards_per_carry",
    ),
    "receiving_yards": (
        "projected_team_targets",
        "prior_player_target_share",
        "prior_player_yards_per_target",
    ),
}


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _reference_eligible(
    row: Mapping[str, Any],
    stat: str,
    *,
    test_season: int,
    test_season_type: str,
    min_history: int,
) -> tuple[float, float] | None:
    """Return (B0 prediction, actual) for the frozen reference population."""
    try:
        if int(row.get("season")) != int(test_season):
            return None
        if str(row.get("season_type")) != str(test_season_type):
            return None
        if int(row.get("history_n", 0)) < min_history:
            return None
    except (TypeError, ValueError):
        return None

    position = str(row.get("position") or "").strip().upper()
    if position not in b0.ELIGIBLE_POSITIONS[stat]:
        return None

    b0_features = row.get("b0_features") or {}
    role = _finite(b0_features.get(b0.ROLE_FEATURE[stat]))
    if role is None or role <= 0:
        return None

    prediction = _finite(b0_features.get(f"rolling_{stat}"))
    actual = _finite((row.get("target") or {}).get(stat))
    if prediction is None or actual is None:
        return None
    return prediction, actual


def _metrics(predictions: list[float], actuals: list[float]) -> dict:
    if not predictions:
        return {
            "n": 0,
            "mae": None,
            "rmse": None,
            "bias": None,
            "mean_prediction": None,
            "mean_actual": None,
        }
    errors = [p - a for p, a in zip(predictions, actuals)]
    n = len(errors)
    return {
        "n": n,
        "mae": sum(abs(e) for e in errors) / n,
        "rmse": math.sqrt(sum(e * e for e in errors) / n),
        "bias": sum(errors) / n,
        "mean_prediction": sum(predictions) / n,
        "mean_actual": sum(actuals) / n,
    }


def paired_error_rows(
    rows: Iterable[Mapping[str, Any]],
    market: str,
    *,
    test_season: int,
    test_season_type: str = "REG",
    min_history: int = 3,
    min_team_history: int = 3,
) -> list[dict]:
    """Return the exact row-level population on which B0 and B1 are compared."""
    if market not in MARKETS:
        raise ValueError(f"unsupported B1 market: {market}")

    stat, predictor = MARKETS[market]
    paired = []

    for row in rows:
        ref = _reference_eligible(
            row,
            stat,
            test_season=test_season,
            test_season_type=test_season_type,
            min_history=min_history,
        )
        if ref is None:
            continue

        try:
            team_history_n = int(row.get("team_history_n", 0))
        except (TypeError, ValueError):
            continue
        if team_history_n < min_team_history:
            continue

        features = row.get("features") or {}
        if any(_finite(features.get(name)) is None for name in B1_REQUIRED[market]):
            continue

        challenger = predictor(features)
        if not math.isfinite(challenger):
            raise ValueError(f"non-finite B1 prediction for {market}")

        base, actual = ref
        paired.append({
            "season": int(row.get("season")),
            "week": int(row.get("week")),
            "player_id": row.get("player_id"),
            "team": row.get("team"),
            "opponent_team": row.get("opponent_team"),
            "b0_prediction": base,
            "b1_prediction": challenger,
            "actual": actual,
            "b0_abs_error": abs(base - actual),
            "b1_abs_error": abs(challenger - actual),
            "delta_abs_error": abs(challenger - actual) - abs(base - actual),
            "b0_squared_error": (base - actual) ** 2,
            "b1_squared_error": (challenger - actual) ** 2,
        })

    return paired


def compare_market(
    rows: Iterable[Mapping[str, Any]],
    market: str,
    *,
    test_season: int,
    test_season_type: str = "REG",
    min_history: int = 3,
    min_team_history: int = 3,
) -> dict:
    if market not in MARKETS:
        raise ValueError(f"unsupported B1 market: {market}")

    materialized = list(rows)
    stat, _ = MARKETS[market]

    reference_n = sum(
        1 for row in materialized
        if _reference_eligible(
            row,
            stat,
            test_season=test_season,
            test_season_type=test_season_type,
            min_history=min_history,
        ) is not None
    )

    paired = paired_error_rows(
        materialized,
        market,
        test_season=test_season,
        test_season_type=test_season_type,
        min_history=min_history,
        min_team_history=min_team_history,
    )

    b0_predictions = [row["b0_prediction"] for row in paired]
    b1_predictions = [row["b1_prediction"] for row in paired]
    actuals = [row["actual"] for row in paired]

    base = _metrics(b0_predictions, actuals)
    challenger = _metrics(b1_predictions, actuals)
    comparison_n = challenger["n"]

    return {
        "market": market,
        "stat": stat,
        "b0_reference_n": reference_n,
        "comparison_n": comparison_n,
        "coverage_ratio": (
            comparison_n / reference_n if reference_n else None
        ),
        "b0_mae": base["mae"],
        "b1_mae": challenger["mae"],
        "delta_mae": (
            challenger["mae"] - base["mae"]
            if base["mae"] is not None and challenger["mae"] is not None
            else None
        ),
        "b0_rmse": base["rmse"],
        "b1_rmse": challenger["rmse"],
        "delta_rmse": (
            challenger["rmse"] - base["rmse"]
            if base["rmse"] is not None and challenger["rmse"] is not None
            else None
        ),
        "b0_bias": base["bias"],
        "b1_bias": challenger["bias"],
        "b0_mean_prediction": base["mean_prediction"],
        "b1_mean_prediction": challenger["mean_prediction"],
        "mean_actual": challenger["mean_actual"],
    }


def compare_first_wave(
    rows: Iterable[Mapping[str, Any]],
    *,
    test_season: int,
    test_season_type: str = "REG",
    min_history: int = 3,
    min_team_history: int = 3,
) -> dict:
    materialized = list(rows)
    return {
        "test_season": int(test_season),
        "test_season_type": str(test_season_type),
        "min_history": int(min_history),
        "min_team_history": int(min_team_history),
        "markets": {
            market: compare_market(
                materialized,
                market,
                test_season=test_season,
                test_season_type=test_season_type,
                min_history=min_history,
                min_team_history=min_team_history,
            )
            for market in MARKETS
        },
    }
