"""Point-in-time-safe REG-season team feature substrate for NFL research.

This module turns nflverse weekly team-stat rows into strictly-prior rolling
features. It intentionally exposes *proxies* for play volume and pass tendency;
box-score rows are not sufficient to claim neutral-situation pass rate, pace,
or a true expected-plays estimate.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping


REQUIRED_COLUMNS = frozenset({
    "game_id",
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "attempts",
    "passing_yards",
    "sacks_suffered",
    "passing_epa",
    "carries",
    "rushing_yards",
})

NUMERIC_FIELDS = (
    "attempts",
    "passing_yards",
    "sacks_suffered",
    "passing_epa",
    "carries",
    "rushing_yards",
)


class TeamPriorFeatureError(ValueError):
    """Raised when weekly team-stat history is not safe to use."""


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise TeamPriorFeatureError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TeamPriorFeatureError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise TeamPriorFeatureError(f"{field} must be an integer")
    if isinstance(value, str) and str(parsed) != value.strip():
        raise TeamPriorFeatureError(f"{field} must be an integer")
    if minimum is not None and parsed < minimum:
        raise TeamPriorFeatureError(f"{field} must be >= {minimum}")
    return parsed


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise TeamPriorFeatureError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TeamPriorFeatureError(f"{field} must be numeric") from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise TeamPriorFeatureError(f"{field} must be finite")
    return parsed


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TeamPriorFeatureError(f"{field} must be a non-empty string")
    return value.strip()


def _mean(history: deque[dict[str, Any]], field: str) -> float | None:
    if not history:
        return None
    return sum(float(row[field]) for row in history) / len(history)


def build_prior_team_features(
    source_rows: Iterable[Mapping[str, Any]], *, rolling_window: int = 5
) -> list[dict[str, Any]]:
    """Build one target row per team-game using only earlier REG games.

    Rows may span season boundaries, so Week 1 can legitimately use prior-season
    history. Current-game derived values are appended to history only *after*
    the target feature row is emitted. Postseason rows are rejected rather than
    mixed into ambiguous week-number chronology.
    """
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise TeamPriorFeatureError("rolling_window must be a positive integer")

    rows = [dict(row) for row in source_rows]
    if not rows:
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise TeamPriorFeatureError(
                f"missing required team-stat columns at row {index}: {', '.join(missing)}"
            )

        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        season_type = _text(row["season_type"], "season_type").upper()
        if season_type != "REG":
            raise TeamPriorFeatureError("team prior feature builder accepts REG rows only")
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent_team"], "opponent_team").upper()
        if team == opponent:
            raise TeamPriorFeatureError("team and opponent_team must differ")
        game_id = _text(row["game_id"], "game_id")

        key = (season, week, team)
        if key in seen:
            raise TeamPriorFeatureError(f"duplicate team/week row: {key}")
        seen.add(key)

        numeric = {field: _number(row[field], field) for field in NUMERIC_FIELDS}
        for field in ("attempts", "passing_yards", "sacks_suffered", "carries", "rushing_yards"):
            if numeric[field] < 0:
                raise TeamPriorFeatureError(f"{field} must be non-negative")

        dropback_proxy = numeric["attempts"] + numeric["sacks_suffered"]
        play_proxy = dropback_proxy + numeric["carries"]
        if play_proxy <= 0:
            raise TeamPriorFeatureError("offensive play proxy must be positive")

        normalized.append({
            "game_id": game_id,
            "season": season,
            "week": week,
            "season_type": season_type,
            "team": team,
            "opponent_team": opponent,
            **numeric,
            "dropback_proxy": dropback_proxy,
            "offensive_play_proxy": play_proxy,
            "dropback_share_proxy": dropback_proxy / play_proxy,
            "yards_per_play_proxy": (numeric["passing_yards"] + numeric["rushing_yards"]) / play_proxy,
        })

    normalized.sort(key=lambda row: (row["season"], row["week"], row["team"], row["game_id"]))
    histories: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=rolling_window))
    built: list[dict[str, Any]] = []

    rolling_fields = (
        "attempts",
        "passing_yards",
        "sacks_suffered",
        "passing_epa",
        "carries",
        "rushing_yards",
        "dropback_proxy",
        "offensive_play_proxy",
        "dropback_share_proxy",
        "yards_per_play_proxy",
    )

    for row in normalized:
        history = histories[row["team"]]
        feature_row: dict[str, Any] = {
            "game_id": row["game_id"],
            "season": row["season"],
            "week": row["week"],
            "season_type": row["season_type"],
            "team": row["team"],
            "opponent_team": row["opponent_team"],
            "prior_games_n": len(history),
            "rolling_window": rolling_window,
            "feature_semantics": "STRICTLY_PRIOR_REG_TEAM_BOX_SCORE",
            "neutral_pass_rate_available": False,
            "true_pace_available": False,
            "expected_plays_available": False,
        }
        for field in rolling_fields:
            feature_row[f"prior_mean_{field}"] = _mean(history, field)
        built.append(feature_row)

        history.append(row)

    return built
