"""First auditable NFL spread/total research baseline (B0).

B0 intentionally uses only strictly-prior scoring features. It does not ingest a
market line, price, current-game score, weather, injury, roster, or PBP feature.
The closing spread/total may be supplied later *only* to the retrospective
evaluator as a benchmark on the identical game population.

Prediction rule for each eligible game (minimum three prior games per team):
- home points = mean(home prior points-for, away prior points-against)
- away points = mean(away prior points-for, home prior points-against)
- margin = home points - away points
- total = home points + away points

This is a falsifiable control, not a production model or betting selector.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping


BASELINE_NAME = "GAME_MARKET_B0_PRIOR_SCORING_BLEND"
DEFAULT_MIN_PRIOR_GAMES = 3
FIXED_PARTITIONS = {
    "development_2000_2019": (2000, 2019),
    "validation_2020_2022": (2020, 2022),
    "held_2023_2025": (2023, 2025),
}


class GameMarketB0Error(ValueError):
    """Raised when the baseline/evaluation population is ambiguous."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketB0Error(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise GameMarketB0Error(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GameMarketB0Error(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise GameMarketB0Error(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise GameMarketB0Error(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise GameMarketB0Error(f"{field} must be >= {minimum}")
    return result


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise GameMarketB0Error(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GameMarketB0Error(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise GameMarketB0Error(f"{field} must be finite")
    return result


def _finite_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _finite(value, field)


def build_b0_predictions(
    scoring_feature_rows: Iterable[Mapping[str, Any]],
    *,
    min_prior_games: int = DEFAULT_MIN_PRIOR_GAMES,
) -> list[dict[str, Any]]:
    """Build one B0 prediction row per game from strictly-prior scoring features."""
    if isinstance(min_prior_games, bool) or not isinstance(min_prior_games, int) or min_prior_games < 1:
        raise GameMarketB0Error("min_prior_games must be a positive integer")

    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_team_game: set[tuple[str, str]] = set()
    for row_number, source in enumerate(scoring_feature_rows):
        row = dict(source)
        required = {
            "game_id", "season", "week", "game_type", "team", "opponent_team",
            "is_home", "target_final_status", "prior_games_n",
            "prior_mean_points_for", "prior_mean_points_against",
            "feature_semantics", "contains_target_game_score",
        }
        missing = sorted(required.difference(row.keys()))
        if missing:
            raise GameMarketB0Error(
                f"scoring feature row {row_number} missing fields: {', '.join(missing)}"
            )
        game_id = _text(row["game_id"], "game_id")
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent_team"], "opponent_team").upper()
        if team == opponent:
            raise GameMarketB0Error("team and opponent_team must differ")
        if not isinstance(row["is_home"], bool):
            raise GameMarketB0Error("is_home must be boolean")
        if row["contains_target_game_score"] is not False:
            raise GameMarketB0Error("B0 refuses scoring features containing target-game score")
        semantics = _text(row["feature_semantics"], "feature_semantics")
        if semantics != "STRICTLY_PRIOR_EXPLICIT_FINAL_SCORING":
            raise GameMarketB0Error(f"unexpected scoring feature semantics: {semantics}")
        target_status = _text(row["target_final_status"], "target_final_status").upper()
        if target_status not in {"FINAL", "PREGAME"}:
            raise GameMarketB0Error("target_final_status must be FINAL or PREGAME")
        normalized = {
            "game_id": game_id,
            "season": _integer(row["season"], "season", minimum=1999),
            "week": _integer(row["week"], "week", minimum=1),
            "game_type": _text(row["game_type"], "game_type").upper(),
            "team": team,
            "opponent_team": opponent,
            "is_home": row["is_home"],
            "target_final_status": target_status,
            "prior_games_n": _integer(row["prior_games_n"], "prior_games_n", minimum=0),
            "prior_mean_points_for": _finite_or_none(row["prior_mean_points_for"], "prior_mean_points_for"),
            "prior_mean_points_against": _finite_or_none(row["prior_mean_points_against"], "prior_mean_points_against"),
        }
        if normalized["game_type"] != "REG":
            raise GameMarketB0Error("B0 accepts REG rows only")
        key = (game_id, team)
        if key in seen_team_game:
            raise GameMarketB0Error(f"duplicate team/game scoring feature row: {key}")
        seen_team_game.add(key)
        by_game[game_id].append(normalized)

    output: list[dict[str, Any]] = []
    for game_id, pair in by_game.items():
        if len(pair) != 2:
            raise GameMarketB0Error(
                f"game must have exactly two scoring feature rows, got {len(pair)}: {game_id}"
            )
        home_rows = [row for row in pair if row["is_home"]]
        away_rows = [row for row in pair if not row["is_home"]]
        if len(home_rows) != 1 or len(away_rows) != 1:
            raise GameMarketB0Error(f"game must have one home and one away row: {game_id}")
        home = home_rows[0]
        away = away_rows[0]
        if home["opponent_team"] != away["team"] or away["opponent_team"] != home["team"]:
            raise GameMarketB0Error(f"non-reciprocal team identity: {game_id}")
        for field in ("season", "week", "game_type", "target_final_status"):
            if home[field] != away[field]:
                raise GameMarketB0Error(f"home/away {field} mismatch: {game_id}")

        sufficient = (
            home["prior_games_n"] >= min_prior_games
            and away["prior_games_n"] >= min_prior_games
            and home["prior_mean_points_for"] is not None
            and home["prior_mean_points_against"] is not None
            and away["prior_mean_points_for"] is not None
            and away["prior_mean_points_against"] is not None
        )
        predicted_home = predicted_away = predicted_margin = predicted_total = None
        if sufficient:
            predicted_home = (
                home["prior_mean_points_for"] + away["prior_mean_points_against"]
            ) / 2.0
            predicted_away = (
                away["prior_mean_points_for"] + home["prior_mean_points_against"]
            ) / 2.0
            predicted_margin = predicted_home - predicted_away
            predicted_total = predicted_home + predicted_away

        output.append({
            "game_id": game_id,
            "season": home["season"],
            "week": home["week"],
            "game_type": home["game_type"],
            "home_team": home["team"],
            "away_team": away["team"],
            "target_final_status": home["target_final_status"],
            "home_prior_games_n": home["prior_games_n"],
            "away_prior_games_n": away["prior_games_n"],
            "eligibility": "ELIGIBLE" if sufficient else "INSUFFICIENT_HISTORY",
            "predicted_home_points": predicted_home,
            "predicted_away_points": predicted_away,
            "predicted_home_margin": predicted_margin,
            "predicted_total": predicted_total,
            "baseline_name": BASELINE_NAME,
            "min_prior_games": min_prior_games,
            "uses_market_line_as_feature": False,
            "uses_current_game_outcome_as_feature": False,
            "home_field_adjustment": 0.0,
        })

    output.sort(key=lambda row: (row["season"], row["week"], row["game_id"]))
    return output


def _metrics(errors: list[float]) -> dict[str, Any]:
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "bias_prediction_minus_actual": None}
    absolute = [abs(value) for value in errors]
    return {
        "n": len(errors),
        "mae": statistics.fmean(absolute),
        "rmse": math.sqrt(statistics.fmean(value * value for value in errors)),
        "bias_prediction_minus_actual": statistics.fmean(errors),
    }


def _bootstrap_mae_delta(
    rows: list[dict[str, Any]],
    *,
    model_error_key: str,
    market_error_key: str,
    iterations: int = 2000,
    seed: int = 20260914,
) -> dict[str, Any]:
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 0:
        raise GameMarketB0Error("bootstrap iterations must be a positive integer")
    if not rows:
        return {
            "unit": "game_id", "games": 0, "iterations": iterations, "seed": seed,
            "mae_delta_model_minus_close_p2_5": None,
            "mae_delta_model_minus_close_p50": None,
            "mae_delta_model_minus_close_p97_5": None,
        }
    rng = random.Random(seed)
    deltas = []
    n = len(rows)
    for _ in range(iterations):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        model_mae = statistics.fmean(abs(row[model_error_key]) for row in sample)
        market_mae = statistics.fmean(abs(row[market_error_key]) for row in sample)
        deltas.append(model_mae - market_mae)
    deltas.sort()
    return {
        "unit": "game_id",
        "games": n,
        "iterations": iterations,
        "seed": seed,
        "mae_delta_model_minus_close_p2_5": deltas[min(iterations - 1, int(iterations * 0.025))],
        "mae_delta_model_minus_close_p50": statistics.median(deltas),
        "mae_delta_model_minus_close_p97_5": deltas[min(iterations - 1, int(iterations * 0.975))],
    }


def evaluate_against_closing_market(
    prediction_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
    *,
    bootstrap_iterations: int = 2000,
    bootstrap_seed: int = 20260914,
) -> dict[str, Any]:
    """Evaluate B0 and closing lines on exactly the same eligible historical games.

    `spread_line` is interpreted using nflverse/nflfastR semantics: positive
    means the home team was favored, so it is directly comparable to actual
    home margin (`home_score-away_score`). Closing data are benchmark controls
    only and never flow back into B0 predictions.
    """
    predictions: dict[str, dict[str, Any]] = {}
    for source in prediction_rows:
        row = dict(source)
        game_id = _text(row.get("game_id"), "game_id")
        if game_id in predictions:
            raise GameMarketB0Error(f"duplicate prediction game_id: {game_id}")
        predictions[game_id] = row

    outcomes: dict[str, dict[str, Any]] = {}
    for source in outcome_rows:
        row = dict(source)
        game_id = _text(row.get("game_id"), "game_id")
        if game_id in outcomes:
            raise GameMarketB0Error(f"duplicate outcome game_id: {game_id}")
        home = _text(row.get("home_team"), "home_team").upper()
        away = _text(row.get("away_team"), "away_team").upper()
        home_score = _integer(row.get("home_score"), "home_score", minimum=0)
        away_score = _integer(row.get("away_score"), "away_score", minimum=0)
        spread = _finite(row.get("spread_line"), "spread_line")
        total_line = _finite(row.get("total_line"), "total_line")
        if total_line <= 0:
            raise GameMarketB0Error("total_line must be positive")
        outcomes[game_id] = {
            "game_id": game_id,
            "season": _integer(row.get("season"), "season", minimum=1999),
            "week": _integer(row.get("week"), "week", minimum=1),
            "game_type": _text(row.get("game_type"), "game_type").upper(),
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
            "spread_line": spread,
            "total_line": total_line,
        }

    paired_rows: list[dict[str, Any]] = []
    for game_id, prediction in predictions.items():
        if prediction.get("eligibility") != "ELIGIBLE":
            continue
        if prediction.get("target_final_status") != "FINAL":
            continue
        outcome = outcomes.get(game_id)
        if outcome is None:
            raise GameMarketB0Error(f"eligible FINAL prediction missing outcome: {game_id}")
        for field in ("season", "week", "game_type", "home_team", "away_team"):
            if prediction.get(field) != outcome[field]:
                raise GameMarketB0Error(f"prediction/outcome {field} mismatch: {game_id}")
        actual_margin = outcome["home_score"] - outcome["away_score"]
        actual_total = outcome["home_score"] + outcome["away_score"]
        model_margin = _finite(prediction.get("predicted_home_margin"), "predicted_home_margin")
        model_total = _finite(prediction.get("predicted_total"), "predicted_total")
        paired_rows.append({
            "game_id": game_id,
            "season": outcome["season"],
            "actual_margin": actual_margin,
            "actual_total": actual_total,
            "model_margin_error": model_margin - actual_margin,
            "market_margin_error": outcome["spread_line"] - actual_margin,
            "model_total_error": model_total - actual_total,
            "market_total_error": outcome["total_line"] - actual_total,
        })

    partitions: dict[str, Any] = {}
    for name, (start, end) in FIXED_PARTITIONS.items():
        rows = [row for row in paired_rows if start <= row["season"] <= end]
        margin_model = _metrics([row["model_margin_error"] for row in rows])
        margin_close = _metrics([row["market_margin_error"] for row in rows])
        total_model = _metrics([row["model_total_error"] for row in rows])
        total_close = _metrics([row["market_total_error"] for row in rows])
        partition = {
            "games": len(rows),
            "margin": {
                "b0": margin_model,
                "closing_market": margin_close,
                "mae_delta_b0_minus_close": (
                    margin_model["mae"] - margin_close["mae"] if rows else None
                ),
            },
            "total": {
                "b0": total_model,
                "closing_market": total_close,
                "mae_delta_b0_minus_close": (
                    total_model["mae"] - total_close["mae"] if rows else None
                ),
            },
        }
        if name == "held_2023_2025":
            partition["margin"]["game_bootstrap"] = _bootstrap_mae_delta(
                rows,
                model_error_key="model_margin_error",
                market_error_key="market_margin_error",
                iterations=bootstrap_iterations,
                seed=bootstrap_seed,
            )
            partition["total"]["game_bootstrap"] = _bootstrap_mae_delta(
                rows,
                model_error_key="model_total_error",
                market_error_key="market_total_error",
                iterations=bootstrap_iterations,
                seed=bootstrap_seed + 1,
            )
        partitions[name] = partition

    return {
        "baseline_name": BASELINE_NAME,
        "status": "RESEARCH_BASELINE_CHARACTERIZATION_ONLY",
        "prediction_uses_market_line_as_feature": False,
        "closing_market_use": "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY",
        "fixed_partitions": dict(FIXED_PARTITIONS),
        "eligible_paired_games": len(paired_rows),
        "partitions": partitions,
    }
