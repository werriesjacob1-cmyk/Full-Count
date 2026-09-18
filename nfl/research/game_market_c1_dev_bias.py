"""Predeclared C1 challenger: development-only additive bias correction for NFL game markets.

C1 asks one narrow question: does correcting B0's development-period mean error
improve validation/held margin and total accuracy? The correction is fit only on
2000-2019 eligible FINAL games. Closing lines are never used to fit C1.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Iterable, Mapping


C1_NAME = "GAME_MARKET_C1_DEV_BIAS_CORRECTED"
DEV_START = 2000
DEV_END = 2019
VALID_START = 2020
VALID_END = 2022
HELD_START = 2023
HELD_END = 2025


class GameMarketC1Error(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketC1Error(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise GameMarketC1Error(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GameMarketC1Error(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise GameMarketC1Error(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise GameMarketC1Error(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise GameMarketC1Error(f"{field} must be >= {minimum}")
    return result


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise GameMarketC1Error(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GameMarketC1Error(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise GameMarketC1Error(f"{field} must be finite")
    return result


def prepare_rows(
    prediction_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    for source in prediction_rows:
        row = dict(source)
        game_id = _text(row.get("game_id"), "game_id")
        if not game_id or game_id in predictions:
            raise GameMarketC1Error(f"invalid or duplicate prediction game_id: {game_id!r}")
        if row.get("uses_market_line_as_feature") is not False:
            raise GameMarketC1Error("C1 requires B0 predictions with no market-line feature")
        if row.get("uses_current_game_outcome_as_feature") is not False:
            raise GameMarketC1Error("C1 requires B0 predictions with no target-outcome feature")
        predictions[game_id] = row
    outcomes: dict[str, dict[str, Any]] = {}
    for source in outcome_rows:
        row = dict(source)
        game_id = _text(row.get("game_id"), "game_id")
        if not game_id or game_id in outcomes:
            raise GameMarketC1Error(f"invalid or duplicate outcome game_id: {game_id!r}")
        if _text(row.get("final_status"), "final_status").upper() != "FINAL":
            raise GameMarketC1Error("C1 outcome rows require explicit FINAL status")
        outcomes[game_id] = row

    paired: list[dict[str, Any]] = []
    for game_id, pred in predictions.items():
        if pred.get("eligibility") != "ELIGIBLE" or pred.get("target_final_status") != "FINAL":
            continue
        out = outcomes.get(game_id)
        if out is None:
            raise GameMarketC1Error(f"eligible FINAL prediction missing outcome: {game_id}")
        for field in ("season", "week", "game_type", "home_team", "away_team"):
            if pred.get(field) != out.get(field):
                raise GameMarketC1Error(f"prediction/outcome {field} mismatch: {game_id}")
        season = _integer(pred["season"], "season", minimum=1999)
        home_score = _integer(out.get("home_score"), "home_score", minimum=0)
        away_score = _integer(out.get("away_score"), "away_score", minimum=0)
        actual_margin = home_score - away_score
        actual_total = home_score + away_score
        b0_margin = _finite(pred.get("predicted_home_margin"), "predicted_home_margin")
        b0_total = _finite(pred.get("predicted_total"), "predicted_total")
        paired.append({
            "game_id": game_id,
            "season": season,
            "b0_margin": b0_margin,
            "b0_total": b0_total,
            "actual_margin": float(actual_margin),
            "actual_total": float(actual_total),
        })
    return sorted(paired, key=lambda r: (r["season"], r["game_id"]))


def fit_development_corrections(rows: Iterable[Mapping[str, Any]]) -> dict[str, float | int]:
    dev = [dict(r) for r in rows if DEV_START <= int(r["season"]) <= DEV_END]
    if not dev:
        raise GameMarketC1Error("no eligible development rows")
    margin_residuals = [float(r["actual_margin"]) - float(r["b0_margin"]) for r in dev]
    total_residuals = [float(r["actual_total"]) - float(r["b0_total"]) for r in dev]
    return {
        "development_games": len(dev),
        "margin_additive_correction": statistics.fmean(margin_residuals),
        "total_additive_correction": statistics.fmean(total_residuals),
    }


def apply_c1(rows: Iterable[Mapping[str, Any]], corrections: Mapping[str, Any]) -> list[dict[str, Any]]:
    margin_correction = _finite(corrections.get("margin_additive_correction"), "margin_additive_correction")
    total_correction = _finite(corrections.get("total_additive_correction"), "total_additive_correction")
    output = []
    for source in rows:
        row = dict(source)
        output.append({
            **row,
            "c1_margin": float(row["b0_margin"]) + margin_correction,
            "c1_total": float(row["b0_total"]) + total_correction,
        })
    return output


def _mae(rows: list[dict[str, Any]], prediction_key: str, actual_key: str) -> float | None:
    if not rows:
        return None
    return statistics.fmean(abs(float(r[prediction_key]) - float(r[actual_key])) for r in rows)


def _bootstrap_delta(
    rows: list[dict[str, Any]], *, c1_key: str, b0_key: str, actual_key: str,
    iterations: int = 2000, seed: int = 20260914,
) -> dict[str, Any]:
    if not rows:
        return {"games": 0, "iterations": iterations, "seed": seed, "p2_5": None, "p50": None, "p97_5": None}
    rng = random.Random(seed)
    deltas = []
    n = len(rows)
    for _ in range(iterations):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        c1 = statistics.fmean(abs(float(r[c1_key]) - float(r[actual_key])) for r in sample)
        b0 = statistics.fmean(abs(float(r[b0_key]) - float(r[actual_key])) for r in sample)
        deltas.append(c1 - b0)
    deltas.sort()
    return {
        "games": n, "iterations": iterations, "seed": seed,
        "p2_5": deltas[min(iterations - 1, int(iterations * 0.025))],
        "p50": statistics.median(deltas),
        "p97_5": deltas[min(iterations - 1, int(iterations * 0.975))],
    }


def evaluate_c1(rows: Iterable[Mapping[str, Any]], corrections: Mapping[str, Any]) -> dict[str, Any]:
    scored = apply_c1(rows, corrections)
    partitions = {
        "validation_2020_2022": (VALID_START, VALID_END),
        "held_2023_2025": (HELD_START, HELD_END),
    }
    result: dict[str, Any] = {}
    for name, (start, end) in partitions.items():
        subset = [r for r in scored if start <= int(r["season"]) <= end]
        margin_b0 = _mae(subset, "b0_margin", "actual_margin")
        margin_c1 = _mae(subset, "c1_margin", "actual_margin")
        total_b0 = _mae(subset, "b0_total", "actual_total")
        total_c1 = _mae(subset, "c1_total", "actual_total")
        block = {
            "games": len(subset),
            "margin": {
                "b0_mae": margin_b0,
                "c1_mae": margin_c1,
                "mae_delta_c1_minus_b0": None if not subset else margin_c1 - margin_b0,
            },
            "total": {
                "b0_mae": total_b0,
                "c1_mae": total_c1,
                "mae_delta_c1_minus_b0": None if not subset else total_c1 - total_b0,
            },
        }
        if name == "held_2023_2025":
            block["margin"]["game_bootstrap"] = _bootstrap_delta(
                subset, c1_key="c1_margin", b0_key="b0_margin", actual_key="actual_margin", seed=20260914
            )
            block["total"]["game_bootstrap"] = _bootstrap_delta(
                subset, c1_key="c1_total", b0_key="b0_total", actual_key="actual_total", seed=20260915
            )
        result[name] = block
    return {
        "challenger_name": C1_NAME,
        "status": "RESEARCH_CHALLENGER_NOT_PROMOTED",
        "fit_population": "eligible FINAL B0 games, development 2000-2019 only",
        "uses_closing_market_for_fit": False,
        "uses_validation_or_held_outcomes_for_fit": False,
        "corrections": dict(corrections),
        "partitions": result,
    }
