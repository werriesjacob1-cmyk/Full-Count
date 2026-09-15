"""Strict retrospective controls for nflverse/PFR closing spread and total lines.

These rows are useful as market-efficiency benchmarks. They are NOT timestamped
sportsbook observations and therefore must never be relabeled as historical
FanDuel data or assumed available at an earlier decision time.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


class ClosingMarketControlError(ValueError):
    """Raised when a historical closing-market row is internally inconsistent."""


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClosingMarketControlError(f"{key} must be a non-empty string")
    return value.strip()


def _integer(row: Mapping[str, Any], key: str, *, minimum: int | None = None) -> int:
    value = row.get(key)
    if isinstance(value, bool):
        raise ClosingMarketControlError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ClosingMarketControlError(f"{key} must be an integer") from exc
    if str(parsed) != str(value).strip() and not isinstance(value, int):
        raise ClosingMarketControlError(f"{key} must be an integer")
    if minimum is not None and parsed < minimum:
        raise ClosingMarketControlError(f"{key} must be >= {minimum}")
    return parsed


def _number(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or value in (None, ""):
        raise ClosingMarketControlError(f"{key} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ClosingMarketControlError(f"{key} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ClosingMarketControlError(f"{key} must be finite")
    return parsed


def _settlement(delta: float) -> str:
    if math.isclose(delta, 0.0, abs_tol=1e-12):
        return "PUSH"
    return "HOME_OR_OVER" if delta > 0 else "AWAY_OR_UNDER"


def build_closing_market_control(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one played nflverse schedule row and expose closing-line controls.

    nflfastR documents ``spread_line`` as the closing spread from the home-team
    perspective: positive means the home team was favored. Therefore
    ``home_margin - spread_line`` is positive when the home team covered.
    ``actual_total - total_line`` is positive when the game went over.
    """
    if not isinstance(row, Mapping):
        raise ClosingMarketControlError("row must be a mapping")

    game_id = _text(row, "game_id")
    season = _integer(row, "season", minimum=1999)
    week = _integer(row, "week", minimum=1)
    game_type = _text(row, "game_type").upper()
    home_team = _text(row, "home_team").upper()
    away_team = _text(row, "away_team").upper()
    if home_team == away_team:
        raise ClosingMarketControlError("home_team and away_team must differ")

    home_score = _integer(row, "home_score", minimum=0)
    away_score = _integer(row, "away_score", minimum=0)
    reported_result = _number(row, "result")
    reported_total = _number(row, "total")
    calculated_result = home_score - away_score
    calculated_total = home_score + away_score
    if not math.isclose(reported_result, calculated_result, abs_tol=1e-12):
        raise ClosingMarketControlError("result does not equal home_score-away_score")
    if not math.isclose(reported_total, calculated_total, abs_tol=1e-12):
        raise ClosingMarketControlError("total does not equal home_score+away_score")

    closing_spread = _number(row, "spread_line")
    closing_total = _number(row, "total_line")
    if closing_total <= 0:
        raise ClosingMarketControlError("total_line must be positive")

    spread_residual = float(calculated_result) - closing_spread
    total_residual = float(calculated_total) - closing_total
    spread_side = _settlement(spread_residual)
    total_side = _settlement(total_residual)

    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "game_type": game_type,
        "away_team": away_team,
        "home_team": home_team,
        "away_score": away_score,
        "home_score": home_score,
        "home_margin": calculated_result,
        "actual_total": calculated_total,
        "closing_spread_line": closing_spread,
        "closing_total_line": closing_total,
        "home_margin_minus_close": spread_residual,
        "actual_total_minus_close": total_residual,
        "spread_settlement": (
            "PUSH" if spread_side == "PUSH" else "HOME_COVER" if spread_side == "HOME_OR_OVER" else "AWAY_COVER"
        ),
        "total_settlement": (
            "PUSH" if total_side == "PUSH" else "OVER" if total_side == "HOME_OR_OVER" else "UNDER"
        ),
        "market_vintage": "CLOSING",
        "market_source": "PRO_FOOTBALL_REFERENCE_VIA_NFLVERSE",
        "allowed_use": "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY",
        "point_in_time_feature_eligible": False,
        "historical_sportsbook_identity": None,
    }
