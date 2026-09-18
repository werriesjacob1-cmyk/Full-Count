"""Point-in-time-safe REG-season defensive tendency features for NFL research.

This module derives defensive history from reciprocal nflverse weekly team-stat
rows. For a defense, the observed value for a completed game is the *opponent's*
offensive production in that game. The target game's opponent production never
enters its own features: the feature row is emitted before the current game is
appended to defensive history.

This is a descriptive historical substrate, not a claim about personnel-adjusted
defensive strength, injury state, true pressure rate, or expected future points.
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

OPPONENT_OFFENSE_FIELDS = (
    "attempts",
    "passing_yards",
    "sacks_suffered",
    "passing_epa",
    "carries",
    "rushing_yards",
)


class DefensePriorFeatureError(ValueError):
    """Raised when defensive history cannot be built unambiguously."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DefensePriorFeatureError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise DefensePriorFeatureError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DefensePriorFeatureError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise DefensePriorFeatureError(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise DefensePriorFeatureError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise DefensePriorFeatureError(f"{field} must be >= {minimum}")
    return result


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise DefensePriorFeatureError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DefensePriorFeatureError(f"{field} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise DefensePriorFeatureError(f"{field} must be finite")
    return result


def _mean(history: deque[dict[str, Any]], field: str) -> float | None:
    if not history:
        return None
    return sum(float(row[field]) for row in history) / len(history)


def build_prior_defense_features(
    source_rows: Iterable[Mapping[str, Any]], *, rolling_window: int = 5
) -> list[dict[str, Any]]:
    """Build defensive target rows using only earlier REG games.

    Each game must have exactly two reciprocal team rows. If A says opponent B,
    the matching row must be B vs A with the same game/season/week. This avoids
    silently attributing one team's offense to the wrong defense.
    """
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise DefensePriorFeatureError("rolling_window must be a positive integer")

    rows = [dict(row) for row in source_rows]
    if not rows:
        return []

    normalized: list[dict[str, Any]] = []
    seen_team_week: set[tuple[int, int, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise DefensePriorFeatureError(
                f"missing required team-stat columns at row {index}: {', '.join(missing)}"
            )
        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        season_type = _text(row["season_type"], "season_type").upper()
        if season_type != "REG":
            raise DefensePriorFeatureError("defense prior feature builder accepts REG rows only")
        game_id = _text(row["game_id"], "game_id")
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent_team"], "opponent_team").upper()
        if team == opponent:
            raise DefensePriorFeatureError("team and opponent_team must differ")
        key = (season, week, team)
        if key in seen_team_week:
            raise DefensePriorFeatureError(f"duplicate team/week row: {key}")
        seen_team_week.add(key)

        numeric = {field: _number(row[field], field) for field in OPPONENT_OFFENSE_FIELDS}
        for field in ("attempts", "passing_yards", "sacks_suffered", "carries", "rushing_yards"):
            if numeric[field] < 0:
                raise DefensePriorFeatureError(f"{field} must be non-negative")

        dropback_proxy = numeric["attempts"] + numeric["sacks_suffered"]
        play_proxy = dropback_proxy + numeric["carries"]
        if play_proxy <= 0:
            raise DefensePriorFeatureError("offensive play proxy must be positive")

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
            "yards_per_play_proxy": (numeric["passing_yards"] + numeric["rushing_yards"]) / play_proxy,
        })

    by_game: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        by_game[(row["season"], row["week"], row["game_id"])].append(row)

    defensive_observations: list[dict[str, Any]] = []
    for game_key, pair in by_game.items():
        if len(pair) != 2:
            raise DefensePriorFeatureError(
                f"game must contain exactly two team rows, got {len(pair)}: {game_key}"
            )
        first, second = pair
        if first["team"] != second["opponent_team"] or second["team"] != first["opponent_team"]:
            raise DefensePriorFeatureError(f"non-reciprocal game rows: {game_key}")

        # A defense's observation is the opposing offense's actual output.
        for defense_row, offense_row in ((first, second), (second, first)):
            defensive_observations.append({
                "game_id": defense_row["game_id"],
                "season": defense_row["season"],
                "week": defense_row["week"],
                "season_type": defense_row["season_type"],
                "team": defense_row["team"],
                "opponent_team": defense_row["opponent_team"],
                "opp_attempts_allowed": offense_row["attempts"],
                "opp_passing_yards_allowed": offense_row["passing_yards"],
                "sacks_generated_proxy": offense_row["sacks_suffered"],
                "opp_passing_epa_allowed": offense_row["passing_epa"],
                "opp_carries_allowed": offense_row["carries"],
                "opp_rushing_yards_allowed": offense_row["rushing_yards"],
                "opp_dropback_proxy_allowed": offense_row["dropback_proxy"],
                "opp_play_proxy_allowed": offense_row["offensive_play_proxy"],
                "opp_yards_per_play_proxy_allowed": offense_row["yards_per_play_proxy"],
            })

    defensive_observations.sort(
        key=lambda row: (row["season"], row["week"], row["team"], row["game_id"])
    )
    histories: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=rolling_window))
    output: list[dict[str, Any]] = []
    rolling_fields = (
        "opp_attempts_allowed",
        "opp_passing_yards_allowed",
        "sacks_generated_proxy",
        "opp_passing_epa_allowed",
        "opp_carries_allowed",
        "opp_rushing_yards_allowed",
        "opp_dropback_proxy_allowed",
        "opp_play_proxy_allowed",
        "opp_yards_per_play_proxy_allowed",
    )

    for current in defensive_observations:
        history = histories[current["team"]]
        feature_row: dict[str, Any] = {
            "game_id": current["game_id"],
            "season": current["season"],
            "week": current["week"],
            "season_type": current["season_type"],
            "team": current["team"],
            "opponent_team": current["opponent_team"],
            "prior_games_n": len(history),
            "rolling_window": rolling_window,
            "feature_semantics": "STRICTLY_PRIOR_REG_DEFENSE_BOX_SCORE",
            "personnel_adjusted": False,
            "true_pressure_rate_available": False,
            "expected_points_allowed_available": False,
        }
        for field in rolling_fields:
            feature_row[f"prior_mean_{field}"] = _mean(history, field)
        output.append(feature_row)
        history.append(current)

    return output
