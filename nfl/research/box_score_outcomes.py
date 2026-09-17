#!/usr/bin/env python3
"""nflverse weekly box-score outcomes for player-prop grading.

Sibling to `nflverse_history.py`, same source family
(`stats_player_week_{season}.csv`), different purpose: that module builds
strictly-prior-game features for the passing-yards model, this one turns the
SAME source into the `outcome` mapping `player_prop_grader.grade_player_prop_market`
needs for a specific already-played game -- `event_id`, `gsis_id`,
`final_status`, `appeared`, `stat_value`.

`player_id` in this nflverse file is already a GSIS ID (verified live,
format `00-0023459`), so no separate identity crosswalk is needed: it is the
same `gsis_id` the roster binders already produce.

WHAT "appeared" MEANS HERE, AND WHAT IT DOES NOT. This module's only signal is
row presence in nflverse's own weekly box score: a `gsis_id` with a row for
the target `game_id` appeared; a bound candidate whose `gsis_id` has no row
at all did not. This is deliberately the same definition
nfl/docs/PLAYER_PROP_SETTLEMENT_SPEC.md's VOID_DNP section already commits
to ("no appearance record at all in the final box score"), and it is
box-score truth, not full participation/snap-count truth: nflverse also
publishes a separate snap-counts file keyed by `pfr_player_id`, a different
identity namespace, and pulling that in would add a second name/team-based
identity crosswalk for a level of precision (distinguishing a real zero-stat
snap from a true absence) this module does not attempt.

WHAT "FINAL" MEANS HERE. This file has no explicit game-status column.
nflverse only publishes a season's weekly file with a game's rows once that
game's stats are finalized -- verified live, 2026-09-17: the current-season
file contains exactly week 1 (all REG games), zero rows for any week 2 game
including tonight's DET@BUF, whose kickoff is still hours away. Row presence
for a specific `game_id` is treated as sufficient evidence of finality. A
target game with zero rows is NOT_YET_FINAL, not a zero-stat outcome for
every candidate -- fails closed by raising, never silently grading empty.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)

REQUIRED_COLUMNS = frozenset({
    "player_id", "player_display_name", "position", "season", "week",
    "season_type", "team", "opponent_team", "game_id",
    "passing_yards", "passing_tds",
    "rushing_yards", "rushing_tds",
    "receiving_yards", "receiving_tds", "receptions",
    "def_sacks", "def_tds", "special_teams_tds", "fumble_recovery_tds",
})

# canonical_market -> source columns to sum for that market's stat_value.
# Touchdown-count markets deliberately sum every way nflverse credits a
# player with finding the end zone themselves (rushing, receiving, a
# defensive/return score, a fumble recovery in the end zone) but never
# passing_tds -- the passer throwing a touchdown is not the passer scoring
# one, and FanDuel's anytime/2+/3+/4+ touchdown markets settle on scores,
# not on throws.
CANONICAL_MARKET_STAT_FIELDS: dict[str, tuple[str, ...]] = {
    "passing_yards": ("passing_yards",),
    "passing_touchdowns": ("passing_tds",),
    "passing_touchdowns_alt": ("passing_tds",),
    "rushing_yards": ("rushing_yards",),
    "rushing_yards_alt": ("rushing_yards",),
    "receiving_yards": ("receiving_yards",),
    "receiving_yards_alt": ("receiving_yards",),
    "receptions": ("receptions",),
    "receptions_alt": ("receptions",),
    "rush_plus_rec_yards": ("rushing_yards", "receiving_yards"),
    "anytime_touchdown": (
        "rushing_tds", "receiving_tds", "special_teams_tds", "def_tds",
        "fumble_recovery_tds",
    ),
    "two_plus_touchdowns": (
        "rushing_tds", "receiving_tds", "special_teams_tds", "def_tds",
        "fumble_recovery_tds",
    ),
    "three_plus_touchdowns": (
        "rushing_tds", "receiving_tds", "special_teams_tds", "def_tds",
        "fumble_recovery_tds",
    ),
    "four_plus_touchdowns": (
        "rushing_tds", "receiving_tds", "special_teams_tds", "def_tds",
        "fumble_recovery_tds",
    ),
    "record_a_sack": ("def_sacks",),
}


class BoxScoreOutcomeError(ValueError):
    """Raised when box-score outcome data cannot be used safely."""


def stats_url(season: int | str) -> str:
    """Canonical nflverse weekly-player-stat CSV URL for one season."""
    try:
        year = int(season)
    except (TypeError, ValueError) as exc:
        raise BoxScoreOutcomeError(f"invalid NFL season {season!r}") from exc
    if year < 1999 or year > 2200:
        raise BoxScoreOutcomeError(f"implausible NFL season {year}")
    return STATS_URL.format(season=year)


def game_id_for(season: int, week: int, away_team: str, home_team: str) -> str:
    """nflverse's own game_id shape: {season}_{week:02d}_{away}_{home}."""
    return f"{int(season)}_{int(week):02d}_{away_team.strip().upper()}_{home_team.strip().upper()}"


def _validate_columns(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise BoxScoreOutcomeError(
                f"missing required nflverse columns at row {index}: "
                f"{', '.join(missing)}"
            )


def _to_float(value: Any, field: str) -> float:
    if value is None or value == "":
        raise BoxScoreOutcomeError(f"{field} is missing")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BoxScoreOutcomeError(f"{field} is not numeric: {value!r}") from exc


def build_player_outcomes(
    rows: Sequence[Mapping[str, Any]], *, game_id: str,
) -> dict[str, dict[str, float]]:
    """Box-score stat totals per gsis_id for exactly one nflverse game_id.

    Raises if the game has no rows at all (NOT_YET_FINAL -- see module
    docstring) or if a gsis_id appears twice for the same game (nflverse
    source-integrity incident, not something to silently sum through).
    """
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise BoxScoreOutcomeError("rows must be a sequence of mappings")
    game_id = str(game_id or "").strip()
    if not game_id:
        raise BoxScoreOutcomeError("game_id is required")

    matching = [row for row in rows if isinstance(row, Mapping)
                and str(row.get("game_id") or "").strip() == game_id]
    if not matching:
        raise BoxScoreOutcomeError(
            f"NOT_YET_FINAL: no nflverse rows for game_id={game_id!r} -- "
            "this game has not been published as final yet"
        )
    _validate_columns(matching)

    all_fields = sorted({field for fields in CANONICAL_MARKET_STAT_FIELDS.values()
                          for field in fields})

    outcomes: dict[str, dict[str, float]] = {}
    for row in matching:
        gsis_id = str(row["player_id"] or "").strip()
        if not gsis_id:
            continue
        if gsis_id in outcomes:
            raise BoxScoreOutcomeError(
                f"duplicate gsis_id {gsis_id!r} in game_id={game_id!r}"
            )
        outcomes[gsis_id] = {
            field: _to_float(row[field], field) for field in all_fields
        }
    return outcomes


def outcome_for_candidate(
    candidate: Mapping[str, Any],
    player_outcomes: Mapping[str, Mapping[str, float]],
    *,
    final_status: str = "FINAL",
) -> dict[str, Any]:
    """Build one grader-ready outcome mapping for one BOUND candidate.

    `candidate` is a bind_player_prop_candidate()/bind_passing_candidate()
    result: needs `event_id`, `gsis_id`, `market`. `player_outcomes` is
    build_player_outcomes()'s return for the matching game.
    """
    if not isinstance(candidate, Mapping):
        raise BoxScoreOutcomeError("candidate must be a mapping")
    market = str(candidate.get("market") or "")
    if market not in CANONICAL_MARKET_STAT_FIELDS:
        raise BoxScoreOutcomeError(f"unsupported market for outcomes: {market!r}")
    event_id = str(candidate.get("event_id") or "").strip()
    if not event_id:
        raise BoxScoreOutcomeError("candidate.event_id is required")
    gsis_id = str(candidate.get("gsis_id") or "").strip()
    if not gsis_id:
        raise BoxScoreOutcomeError("candidate.gsis_id is required")

    player_row = player_outcomes.get(gsis_id)
    if player_row is None:
        return {
            "event_id": event_id,
            "gsis_id": gsis_id,
            "final_status": final_status,
            "appeared": False,
            "stat_value": None,
        }

    fields = CANONICAL_MARKET_STAT_FIELDS[market]
    stat_value = sum(float(player_row[field]) for field in fields)
    return {
        "event_id": event_id,
        "gsis_id": gsis_id,
        "final_status": final_status,
        "appeared": True,
        "stat_value": stat_value,
    }
