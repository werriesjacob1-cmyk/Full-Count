#!/usr/bin/env python3
"""Point-in-time B1 team-opportunity feature builder.

B1 composes two prior-only histories:
1. current team's prior offensive volume
2. current player's prior share/efficiency, using each prior game's team totals

Player source normalization is delegated to nflverse_history.build_prior_only_rows
so B0 and B1 share one identity/schema/empty-row contract.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Mapping, Any

from nfl.research import nflverse_history as nh


TEAM_REQUIRED_COLUMNS = frozenset({
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "attempts",
    "carries",
})


def _int(value: Any, name: str) -> int:
    if value in (None, ""):
        raise ValueError(f"missing team field: {name}")
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid team field {name}: {value!r}") from exc


def _float(value: Any, name: str) -> float:
    if value in (None, ""):
        raise ValueError(f"missing team field: {name}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid team field {name}: {value!r}") from exc


def _key(row: Mapping[str, Any]) -> tuple:
    return (
        int(row["season"]),
        int(row["week"]),
        str(row["season_type"]),
        str(row["team"]),
    )


def _mean(history: Iterable[Mapping[str, float]], field: str) -> float | None:
    vals = [float(row[field]) for row in history]
    return sum(vals) / len(vals) if vals else None


def _ratio_of_sums(
    history: Iterable[Mapping[str, float]],
    numerator: str,
    denominator: str,
) -> float | None:
    rows = list(history)
    den = sum(float(row[denominator]) for row in rows)
    if den <= 0:
        return None
    num = sum(float(row[numerator]) for row in rows)
    return num / den


def _normalize_team_rows(
    source_rows: Iterable[Mapping[str, Any]],
) -> list[dict]:
    out = []
    seen = set()
    for index, source in enumerate(source_rows):
        row = dict(source)
        missing = sorted(TEAM_REQUIRED_COLUMNS - row.keys())
        if missing:
            raise ValueError(
                f"missing required team columns at row {index}: {', '.join(missing)}"
            )
        season = _int(row["season"], "season")
        week = _int(row["week"], "week")
        team = str(row["team"]).strip()
        if not team:
            raise ValueError("team is empty")
        norm = {
            "season": season,
            "week": week,
            "season_type": str(row["season_type"]),
            "team": team,
            "opponent_team": str(row["opponent_team"]),
            "attempts": _float(row["attempts"], "attempts"),
            "carries": _float(row["carries"], "carries"),
        }
        key = _key(norm)
        if key in seen:
            raise ValueError(f"duplicate team-week row: {key}")
        seen.add(key)
        out.append(norm)
    out.sort(
        key=lambda row: (
            row["season"], row["week"], row["team"], row["season_type"]
        )
    )
    return out


def build_b1_rows(
    player_source_rows: Iterable[Mapping[str, Any]],
    team_source_rows: Iterable[Mapping[str, Any]],
    rolling_window: int = 5,
) -> list[dict]:
    """Build B1 features with no current-game information in any feature.

    Team target volume is derived from the sum of player targets for each
    historical team-week because the team weekly source does not need to carry
    a target field for B1.

    All current outcomes remain only in target. Team and player histories are
    appended after their corresponding prediction-time features are emitted.
    """
    if rolling_window <= 0:
        raise ValueError("rolling_window must be a positive integer")

    player_rows = nh.build_prior_only_rows(
        player_source_rows,
        rolling_window=rolling_window,
    )
    team_rows = _normalize_team_rows(team_source_rows)

    team_targets = defaultdict(float)
    for row in player_rows:
        team_targets[_key(row)] += float(row["target"]["targets"])

    team_actual = {}
    for row in team_rows:
        key = _key(row)
        if key not in team_targets:
            raise ValueError(f"team-week row has no player target rows: {key}")
        team_actual[key] = {
            "attempts": row["attempts"],
            "carries": row["carries"],
            "targets": team_targets[key],
        }

    team_histories = defaultdict(lambda: deque(maxlen=rolling_window))
    team_prior = {}
    for row in team_rows:
        key = _key(row)
        hist = team_histories[row["team"]]
        team_prior[key] = {
            "team_history_n": len(hist),
            "projected_team_pass_attempts": _mean(hist, "attempts"),
            "projected_team_carries": _mean(hist, "carries"),
            "projected_team_targets": _mean(hist, "targets"),
        }
        hist.append(team_actual[key])

    player_histories = defaultdict(lambda: deque(maxlen=rolling_window))
    built = []

    for row in player_rows:
        key = _key(row)
        if key not in team_actual:
            raise ValueError(f"missing team-week row for player row: {key}")
        if key not in team_prior:
            raise ValueError(f"missing prior team projection for player row: {key}")

        hist = player_histories[row["player_id"]]
        team_features = team_prior[key]

        features = {
            "projected_team_pass_attempts":
                team_features["projected_team_pass_attempts"],
            "projected_team_carries":
                team_features["projected_team_carries"],
            "projected_team_targets":
                team_features["projected_team_targets"],
            "prior_player_pass_attempt_share":
                _ratio_of_sums(hist, "attempts", "team_attempts"),
            "prior_player_carry_share":
                _ratio_of_sums(hist, "carries", "team_carries"),
            "prior_player_target_share":
                _ratio_of_sums(hist, "targets", "team_targets"),
            "prior_player_catch_rate":
                _ratio_of_sums(hist, "receptions", "targets"),
            "prior_player_yards_per_target":
                _ratio_of_sums(hist, "receiving_yards", "targets"),
            "prior_player_pass_yards_per_attempt":
                _ratio_of_sums(hist, "passing_yards", "attempts"),
            "prior_player_rush_yards_per_carry":
                _ratio_of_sums(hist, "rushing_yards", "carries"),
        }

        built.append({
            "player_id": row["player_id"],
            "player_display_name": row["player_display_name"],
            "position": row["position"],
            "season": row["season"],
            "week": row["week"],
            "season_type": row["season_type"],
            "team": row["team"],
            "opponent_team": row["opponent_team"],
            "history_n": len(hist),
            "team_history_n": team_features["team_history_n"],
            "rolling_window": rolling_window,
            "features": features,
            "b0_features": row["features"],
            "target": row["target"],
        })

        current_team = team_actual[key]
        target = row["target"]
        hist.append({
            "attempts": float(target["attempts"]),
            "passing_yards": float(target["passing_yards"]),
            "carries": float(target["carries"]),
            "rushing_yards": float(target["rushing_yards"]),
            "targets": float(target["targets"]),
            "receptions": float(target["receptions"]),
            "receiving_yards": float(target["receiving_yards"]),
            "team_attempts": float(current_team["attempts"]),
            "team_carries": float(current_team["carries"]),
            "team_targets": float(current_team["targets"]),
        })

    return built
