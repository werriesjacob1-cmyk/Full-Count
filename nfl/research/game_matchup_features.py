"""Join strictly-prior NFL team features into one home/away game row.

This module is intentionally a join/identity layer, not a predictor. It accepts
only already-temporal offense and defense feature rows and a minimal schedule
identity table. It does not consume scores, spreads, totals, prices, outcomes,
or current-game production.

The contract exists so downstream models cannot silently swap home/away teams,
join the wrong opponent, or mix feature rows from another season/week/game.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


class GameMatchupFeatureError(ValueError):
    """Raised when offense/defense/schedule identity cannot be joined safely."""


SCHEDULE_REQUIRED = frozenset({
    "game_id", "season", "week", "season_type", "home_team", "away_team"
})
FEATURE_ID_REQUIRED = frozenset({
    "game_id", "season", "week", "season_type", "team", "opponent_team",
    "prior_games_n", "feature_semantics",
})

OFFENSE_FIELDS = (
    "prior_mean_attempts",
    "prior_mean_passing_yards",
    "prior_mean_sacks_suffered",
    "prior_mean_passing_epa",
    "prior_mean_carries",
    "prior_mean_rushing_yards",
    "prior_mean_dropback_proxy",
    "prior_mean_offensive_play_proxy",
    "prior_mean_dropback_share_proxy",
    "prior_mean_yards_per_play_proxy",
)

DEFENSE_FIELDS = (
    "prior_mean_opp_attempts_allowed",
    "prior_mean_opp_passing_yards_allowed",
    "prior_mean_sacks_generated_proxy",
    "prior_mean_opp_passing_epa_allowed",
    "prior_mean_opp_carries_allowed",
    "prior_mean_opp_rushing_yards_allowed",
    "prior_mean_opp_dropback_proxy_allowed",
    "prior_mean_opp_play_proxy_allowed",
    "prior_mean_opp_yards_per_play_proxy_allowed",
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMatchupFeatureError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise GameMatchupFeatureError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GameMatchupFeatureError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise GameMatchupFeatureError(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise GameMatchupFeatureError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise GameMatchupFeatureError(f"{field} must be >= {minimum}")
    return result


def _finite_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or value == "":
        raise GameMatchupFeatureError(f"{field} must be numeric or null")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GameMatchupFeatureError(f"{field} must be numeric or null") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise GameMatchupFeatureError(f"{field} must be finite")
    return result


def _normalize_feature_rows(
    rows: Iterable[Mapping[str, Any]], *, kind: str, value_fields: tuple[str, ...]
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row_number, source in enumerate(rows):
        row = dict(source)
        missing = sorted(FEATURE_ID_REQUIRED.difference(row.keys()))
        missing.extend(field for field in value_fields if field not in row)
        if missing:
            raise GameMatchupFeatureError(
                f"{kind} feature row {row_number} missing fields: {', '.join(sorted(set(missing)))}"
            )
        game_id = _text(row["game_id"], "game_id")
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent_team"], "opponent_team").upper()
        if team == opponent:
            raise GameMatchupFeatureError(f"{kind} team and opponent must differ")
        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        season_type = _text(row["season_type"], "season_type").upper()
        if season_type != "REG":
            raise GameMatchupFeatureError(f"{kind} feature rows must be REG")
        prior_games_n = _integer(row["prior_games_n"], "prior_games_n", minimum=0)
        semantics = _text(row["feature_semantics"], "feature_semantics")

        key = (game_id, team)
        if key in index:
            raise GameMatchupFeatureError(f"duplicate {kind} feature row: {key}")

        normalized = {
            "game_id": game_id,
            "season": season,
            "week": week,
            "season_type": season_type,
            "team": team,
            "opponent_team": opponent,
            "prior_games_n": prior_games_n,
            "feature_semantics": semantics,
        }
        for field in value_fields:
            normalized[field] = _finite_or_none(row[field], field)
        index[key] = normalized
    return index


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _mean_pair(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else (left + right) / 2.0


def build_game_matchup_features(
    schedule_rows: Iterable[Mapping[str, Any]],
    offense_feature_rows: Iterable[Mapping[str, Any]],
    defense_feature_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return one leakage-safe game row from prior offense/defense features.

    Every schedule game must resolve exactly one offense and one defense feature
    row for both home and away teams, with identical season/week/opponent
    identity. Missing joins fail closed instead of silently dropping games.
    """
    offense = _normalize_feature_rows(
        offense_feature_rows, kind="offense", value_fields=OFFENSE_FIELDS
    )
    defense = _normalize_feature_rows(
        defense_feature_rows, kind="defense", value_fields=DEFENSE_FIELDS
    )

    output: list[dict[str, Any]] = []
    seen_games: set[str] = set()

    for row_number, source in enumerate(schedule_rows):
        row = dict(source)
        missing = sorted(SCHEDULE_REQUIRED.difference(row.keys()))
        if missing:
            raise GameMatchupFeatureError(
                f"schedule row {row_number} missing fields: {', '.join(missing)}"
            )
        game_id = _text(row["game_id"], "game_id")
        if game_id in seen_games:
            raise GameMatchupFeatureError(f"duplicate schedule game_id: {game_id}")
        seen_games.add(game_id)
        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        season_type = _text(row["season_type"], "season_type").upper()
        if season_type != "REG":
            raise GameMatchupFeatureError("game matchup builder accepts REG rows only")
        home = _text(row["home_team"], "home_team").upper()
        away = _text(row["away_team"], "away_team").upper()
        if home == away:
            raise GameMatchupFeatureError("home_team and away_team must differ")

        try:
            ho = offense[(game_id, home)]
            ao = offense[(game_id, away)]
            hd = defense[(game_id, home)]
            ad = defense[(game_id, away)]
        except KeyError as exc:
            raise GameMatchupFeatureError(
                f"missing offense/defense feature join for game {game_id}: {exc.args[0]}"
            ) from exc

        for kind, feature, expected_team, expected_opponent in (
            ("home offense", ho, home, away),
            ("away offense", ao, away, home),
            ("home defense", hd, home, away),
            ("away defense", ad, away, home),
        ):
            if (
                feature["season"] != season
                or feature["week"] != week
                or feature["season_type"] != season_type
                or feature["team"] != expected_team
                or feature["opponent_team"] != expected_opponent
            ):
                raise GameMatchupFeatureError(
                    f"{kind} identity mismatch for game {game_id}"
                )

        result: dict[str, Any] = {
            "game_id": game_id,
            "season": season,
            "week": week,
            "season_type": season_type,
            "home_team": home,
            "away_team": away,
            "home_offense_prior_games_n": ho["prior_games_n"],
            "away_offense_prior_games_n": ao["prior_games_n"],
            "home_defense_prior_games_n": hd["prior_games_n"],
            "away_defense_prior_games_n": ad["prior_games_n"],
            "offense_feature_semantics": ho["feature_semantics"],
            "defense_feature_semantics": hd["feature_semantics"],
            "feature_semantics": "STRICTLY_PRIOR_REG_HOME_AWAY_MATCHUP",
            "contains_current_game_outcome": False,
            "contains_market_line_or_price": False,
        }
        for field in OFFENSE_FIELDS:
            result[f"home_offense_{field}"] = ho[field]
            result[f"away_offense_{field}"] = ao[field]
        for field in DEFENSE_FIELDS:
            result[f"home_defense_{field}"] = hd[field]
            result[f"away_defense_{field}"] = ad[field]

        # Transparent matchup contrasts; still descriptive features, not forecasts.
        result["home_ypp_matchup_delta"] = _difference(
            ho["prior_mean_yards_per_play_proxy"],
            ad["prior_mean_opp_yards_per_play_proxy_allowed"],
        )
        result["away_ypp_matchup_delta"] = _difference(
            ao["prior_mean_yards_per_play_proxy"],
            hd["prior_mean_opp_yards_per_play_proxy_allowed"],
        )
        result["home_play_volume_matchup_blend"] = _mean_pair(
            ho["prior_mean_offensive_play_proxy"],
            ad["prior_mean_opp_play_proxy_allowed"],
        )
        result["away_play_volume_matchup_blend"] = _mean_pair(
            ao["prior_mean_offensive_play_proxy"],
            hd["prior_mean_opp_play_proxy_allowed"],
        )
        output.append(result)

    output.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    return output
