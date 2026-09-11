#!/usr/bin/env python3
"""Point-in-time-safe NFL weekly history builder for V0 research.

This module is intentionally small and boring. Its job is to turn nflverse
weekly player-stat rows into RESEARCH rows whose features contain only games
that occurred before the target row.

It does NOT:
- score a prop
- use sportsbook prices
- choose candidates
- publish anything
- infer missing football facts
- use current-week outcomes as features

A later model is only as honest as this temporal substrate.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Mapping, Any


PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)

# Fail closed if nflverse changes the weekly player-stat contract we rely on.
# These are deliberately the minimum fields needed for V0 opportunity/outcome
# research, not an attempt to mirror the entire upstream schema.
REQUIRED_COLUMNS = frozenset({
    "player_id",
    "player_display_name",
    "position",
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "attempts",
    "completions",
    "passing_yards",
    "passing_tds",
})

NUMERIC_STATS = (
    "attempts",
    "completions",
    "passing_yards",
    "passing_tds",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_tds",
)


def player_stats_url(season: int | str) -> str:
    """Canonical nflverse weekly-player-stat CSV URL for one season."""
    try:
        year = int(season)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid NFL season {season!r}") from exc
    if year < 1999 or year > 2200:
        raise ValueError(f"implausible NFL season {year}")
    return PLAYER_STATS_URL.format(season=year)


def _to_int(value: Any, field: str) -> int:
    if value is None or value == "":
        raise ValueError(f"{field} is missing")
    try:
        parsed = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric: {value!r}") from exc
    return parsed


def _to_float(value: Any, field: str) -> float:
    if value is None or value == "":
        raise ValueError(f"{field} is missing")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric: {value!r}") from exc


def _validate_columns(rows: list[Mapping[str, Any]]) -> None:
    """Require the V0 source contract on every row.

    Checking only the first row would allow a malformed later record to turn
    into a favorable zero after parsing. Every source row must carry the same
    required facts or the dataset build stops.
    """
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise ValueError(
                "missing required nflverse columns "
                f"at row {index}: {', '.join(missing)}"
            )


def _empty_id_row_is_audited_structural_zero(row: Mapping[str, Any]) -> bool:
    """Return True only for the audited anonymous zero rows in nflverse stats.

    Live source audit on 2026-09-11 found exactly 22 such rows in each of
    2023/2024/2025: blank player_id, blank display name, blank position, and
    zero tracked offensive production. Those are source scaffolding, not player
    games, and may be excluded.

    The exception is intentionally narrow. A future empty-ID row with a name,
    a position, or any non-zero tracked offense is a source-integrity incident,
    not something we silently discard.
    """
    if str(row.get("player_display_name") or "").strip():
        return False
    if str(row.get("position") or "").strip():
        return False
    for stat in NUMERIC_STATS:
        value = row.get(stat)
        if value in (None, ""):
            numeric = 0.0
        else:
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"empty player_id row has non-numeric {stat}: {value!r}"
                ) from exc
        if numeric != 0.0:
            return False
    return True


def _mean(history: Iterable[Mapping[str, float]], stat: str) -> float | None:
    vals = [float(row[stat]) for row in history]
    return (sum(vals) / len(vals)) if vals else None


def build_prior_only_rows(
    source_rows: Iterable[Mapping[str, Any]],
    rolling_window: int = 5,
) -> list[dict]:
    """Build chronological player-game rows with strictly prior-game features.

    Rows are grouped by stable `player_id`, then ordered by season/week.
    The current game's target is emitted BEFORE that row is appended to the
    player's history. This order is the core no-lookahead invariant.

    History may cross season boundaries, which is legitimate because a Week 1
    prediction can know last season's games. Future rows can never enter prior
    history because ordering is chronological.

    Duplicate player/week rows are refused rather than guessed through. Weekly
    player stats are expected to contain one aggregate row per player/week; a
    duplicate would make temporal semantics ambiguous.
    """
    if rolling_window <= 0:
        raise ValueError("rolling_window must be a positive integer")

    rows = [dict(row) for row in source_rows]
    if not rows:
        return []
    _validate_columns(rows)

    normalized = []
    seen_keys = set()
    for row in rows:
        player_id = str(row["player_id"]).strip()
        if not player_id:
            if _empty_id_row_is_audited_structural_zero(row):
                continue
            has_offense = False
            for stat in NUMERIC_STATS:
                value = row.get(stat)
                if value in (None, ""):
                    continue
                try:
                    if float(value) != 0.0:
                        has_offense = True
                        break
                except (TypeError, ValueError):
                    has_offense = True
                    break
            if has_offense:
                raise ValueError("empty player_id row carries offense")
            raise ValueError(
                "empty player_id row is not an audited structural zero"
            )
        season = _to_int(row["season"], "season")
        week = _to_int(row["week"], "week")
        if week <= 0:
            raise ValueError(f"week must be positive, got {week}")

        key = (player_id, season, week, str(row["season_type"]))
        if key in seen_keys:
            raise ValueError(f"duplicate player/week row: {key}")
        seen_keys.add(key)

        stats = {stat: _to_float(row[stat], stat) for stat in NUMERIC_STATS}
        normalized.append({
            "player_id": player_id,
            "player_display_name": str(row["player_display_name"]),
            "position": str(row["position"]),
            "season": season,
            "week": week,
            "season_type": str(row["season_type"]),
            "team": str(row["team"]),
            "opponent_team": str(row["opponent_team"]),
            **stats,
        })

    normalized.sort(
        key=lambda row: (
            row["season"],
            row["week"],
            row["player_id"],
            row["season_type"],
        )
    )

    histories: dict[str, deque] = defaultdict(
        lambda: deque(maxlen=rolling_window)
    )
    built = []

    for row in normalized:
        history = histories[row["player_id"]]

        features = {
            f"rolling_{stat}": _mean(history, stat)
            for stat in NUMERIC_STATS
        }

        target = {stat: row[stat] for stat in NUMERIC_STATS}

        built.append({
            "player_id": row["player_id"],
            "player_display_name": row["player_display_name"],
            "position": row["position"],
            "season": row["season"],
            "week": row["week"],
            "season_type": row["season_type"],
            "team": row["team"],
            "opponent_team": row["opponent_team"],
            "history_n": len(history),
            "rolling_window": rolling_window,
            "features": features,
            "target": target,
        })

        # Append only AFTER emitting this prediction row. Moving this line
        # above the output construction leaks the current game's outcome.
        history.append(row)

    return built
