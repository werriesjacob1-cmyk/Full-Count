#!/usr/bin/env python3
"""History-window alignment classification for B1 attribution.

Current B1 compares:
- a player's last N appearances, and
- the current team's last N games.

Those windows are often identical, in which case B1 collapses algebraically to
B0. When they differ, the reason matters. A missed game and a team change are
not the same football mechanism and must not be pooled into one correction.

This module only classifies already-known prior-game identities. It does not
score, tune, fetch, select, or publish.
"""
from __future__ import annotations

from typing import Iterable, Sequence, Any


def _materialize_game_keys(history: Iterable[Sequence[Any]]) -> list[tuple]:
    out = []
    for index, value in enumerate(history):
        try:
            season, week, season_type, team = value
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"history key at index {index} must contain "
                "(season, week, season_type, team)"
            ) from exc
        team = str(team).strip()
        if not team:
            raise ValueError(f"history key at index {index} has empty team")
        try:
            season = int(season)
            week = int(week)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"history key at index {index} has invalid season/week"
            ) from exc
        if week <= 0:
            raise ValueError(f"history key at index {index} has nonpositive week")
        out.append((season, week, str(season_type), team))
    return out


def classify_history_alignment(
    player_history: Iterable[Sequence[Any]],
    current_team_history: Iterable[Sequence[Any]],
    *,
    current_team: str,
) -> str:
    """Classify why B1's player and team prior-game windows differ.

    Priority is intentional:
    1. exact equality => ALIGNED
    2. any prior player appearance for another team => TEAM_CHANGE
    3. unequal lengths on the same team => WINDOW_LENGTH_MISMATCH
    4. equal-length same-team but different game keys => MISSED_GAMES

    MISSED_GAMES includes any same-team appearance gap which displaces a team
    game from the player's rolling window. It does not infer an injury reason.
    """
    team = str(current_team or "").strip()
    if not team:
        raise ValueError("current_team must be non-empty")

    player = _materialize_game_keys(player_history)
    team_hist = _materialize_game_keys(current_team_history)

    if not player and not team_hist:
        return "NO_HISTORY"
    if player == team_hist:
        return "ALIGNED"

    if any(key[3] != team for key in player):
        return "TEAM_CHANGE"

    if len(player) != len(team_hist):
        return "WINDOW_LENGTH_MISMATCH"

    return "MISSED_GAMES"
