"""Strictly-prior NFL scoring features with an explicit finality gate.

The builder accepts REG schedule/game identity rows that the caller has already
classified as either FINAL or PREGAME. It never infers finality from score
presence. FINAL rows may advance team history; PREGAME rows may receive prior
features but must not contain scores and never advance history.

This makes the same temporal substrate usable for retrospective research and a
prospective upcoming game without letting an in-progress score masquerade as a
completed historical observation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping


class ScoringPriorFeatureError(ValueError):
    """Raised when scoring-history provenance or chronology is unsafe."""


REQUIRED_COLUMNS = frozenset({
    "game_id", "season", "week", "game_type", "home_team", "away_team",
    "final_status", "home_score", "away_score",
})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringPriorFeatureError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise ScoringPriorFeatureError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ScoringPriorFeatureError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ScoringPriorFeatureError(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise ScoringPriorFeatureError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise ScoringPriorFeatureError(f"{field} must be >= {minimum}")
    return result


def _score_or_none(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return _integer(value, field, minimum=0)


def _mean(history: deque[dict[str, Any]], field: str) -> float | None:
    if not history:
        return None
    return sum(float(row[field]) for row in history) / len(history)


def build_prior_scoring_features(
    source_rows: Iterable[Mapping[str, Any]], *, rolling_window: int = 5
) -> list[dict[str, Any]]:
    """Return one team-game feature row using only explicitly FINAL prior games.

    `final_status` must be exactly FINAL or PREGAME. FINAL requires both scores;
    PREGAME forbids both scores. Once a team encounters a PREGAME row, a later
    scheduled row for that team is rejected because the intervening result is
    unknowable and history cannot be advanced safely.
    """
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise ScoringPriorFeatureError("rolling_window must be a positive integer")

    normalized: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    seen_team_week: set[tuple[int, int, str]] = set()

    for row_number, source in enumerate(source_rows):
        row = dict(source)
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise ScoringPriorFeatureError(
                f"row {row_number} missing required columns: {', '.join(missing)}"
            )
        game_id = _text(row["game_id"], "game_id")
        if game_id in seen_games:
            raise ScoringPriorFeatureError(f"duplicate game_id: {game_id}")
        seen_games.add(game_id)
        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        game_type = _text(row["game_type"], "game_type").upper()
        if game_type != "REG":
            raise ScoringPriorFeatureError("scoring prior feature builder accepts REG rows only")
        home = _text(row["home_team"], "home_team").upper()
        away = _text(row["away_team"], "away_team").upper()
        if home == away:
            raise ScoringPriorFeatureError("home_team and away_team must differ")
        final_status = _text(row["final_status"], "final_status").upper()
        if final_status not in {"FINAL", "PREGAME"}:
            raise ScoringPriorFeatureError("final_status must be FINAL or PREGAME")
        home_score = _score_or_none(row["home_score"], "home_score")
        away_score = _score_or_none(row["away_score"], "away_score")
        if final_status == "FINAL" and (home_score is None or away_score is None):
            raise ScoringPriorFeatureError("FINAL row requires both scores")
        if final_status == "PREGAME" and (home_score is not None or away_score is not None):
            raise ScoringPriorFeatureError("PREGAME row must not contain scores")

        for team in (home, away):
            key = (season, week, team)
            if key in seen_team_week:
                raise ScoringPriorFeatureError(f"duplicate team/week schedule row: {key}")
            seen_team_week.add(key)

        normalized.append({
            "game_id": game_id,
            "season": season,
            "week": week,
            "game_type": game_type,
            "home_team": home,
            "away_team": away,
            "final_status": final_status,
            "home_score": home_score,
            "away_score": away_score,
        })

    normalized.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    histories: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=rolling_window))
    blocked_after_pregame: set[str] = set()
    output: list[dict[str, Any]] = []

    for game in normalized:
        home = game["home_team"]
        away = game["away_team"]
        for team in (home, away):
            if team in blocked_after_pregame:
                raise ScoringPriorFeatureError(
                    f"cannot build later row for {team} after an unresolved PREGAME game"
                )

        team_specs = ((home, away, True), (away, home, False))
        for team, opponent, is_home in team_specs:
            history = histories[team]
            feature = {
                "game_id": game["game_id"],
                "season": game["season"],
                "week": game["week"],
                "game_type": game["game_type"],
                "team": team,
                "opponent_team": opponent,
                "is_home": is_home,
                "target_final_status": game["final_status"],
                "prior_games_n": len(history),
                "rolling_window": rolling_window,
                "prior_mean_points_for": _mean(history, "points_for"),
                "prior_mean_points_against": _mean(history, "points_against"),
                "prior_mean_margin": _mean(history, "margin"),
                "prior_mean_game_total": _mean(history, "game_total"),
                "feature_semantics": "STRICTLY_PRIOR_EXPLICIT_FINAL_SCORING",
                "finality_inferred_from_scores": False,
                "contains_target_game_score": False,
            }
            output.append(feature)

        if game["final_status"] == "PREGAME":
            blocked_after_pregame.update((home, away))
            continue

        assert game["home_score"] is not None and game["away_score"] is not None
        home_score = game["home_score"]
        away_score = game["away_score"]
        total = home_score + away_score
        histories[home].append({
            "points_for": home_score,
            "points_against": away_score,
            "margin": home_score - away_score,
            "game_total": total,
        })
        histories[away].append({
            "points_for": away_score,
            "points_against": home_score,
            "margin": away_score - home_score,
            "game_total": total,
        })

    output.sort(key=lambda r: (r["season"], r["week"], r["game_id"], r["team"]))
    return output
