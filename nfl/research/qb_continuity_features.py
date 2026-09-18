#!/usr/bin/env python3
"""Strictly-prior QB continuity/tenure features from nflverse weekly player stats.

Named hypothesis (do not extend this module beyond it): a team's offensive
scoring deviates further from its own QB-agnostic rolling-average prior
scoring features (`scoring_prior_features.py`) when the current game's
starting QB differs from the team's incumbent starter entering the game, or
when that incumbent has a short run of consecutive starts. This module does
not evaluate that hypothesis, correlate it with anything, or wire it into any
model or selector. It only builds the strictly-prior QB-identity substrate a
later challenger or evaluation harness would need, and proves that substrate
never uses current-game information.

No new external data source is ingested here. Per the project owner's
explicit instruction to check existing ingestion before pulling anything new:
this repo has no depth-chart/roster "starter" flag ingested anywhere, so
"starter" is inferred exactly the way `passing_yards_baseline_research.py`
already treats QB starters -- from nflverse's own already-ingested weekly
player-stat rows (`nflverse_history.PLAYER_STATS_URL`,
`nflverse_history.REQUIRED_COLUMNS`): the QB with the most pass attempts
recorded for a team in a given week. That source's own docstring is explicit
that "current-game attempts are not used for eligibility" for exactly this
reason -- a game's own attempts are current-game information, unknowable
before kickoff, and this module enforces the same rule structurally.

Every observed team/week starter identity is usable as a PRIOR fact only once
that week is history. A target game's own attempts populate that row's
`target` block (current-game information, useful only to a later evaluation
harness that grades against realized outcomes) and are never copied into that
same row's `features` block. This mirrors `nflverse_history.build_prior_only_rows`
and `scoring_prior_features.build_prior_scoring_features`: emit the row before
advancing history with the row's own facts.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping


class QBContinuityError(ValueError):
    """Raised when weekly QB usage rows are not safe to use."""


REQUIRED_COLUMNS = frozenset({
    "player_id",
    "position",
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "attempts",
})

# nflverse_history.py already treats blank/"0" player_id rows as structural
# scaffolding, not players. This module additionally ignores non-QB and
# non-REG rows rather than rejecting them, because callers are expected to
# pass whatever general weekly player-stat population they already have
# (e.g. the full nflverse_history source rows), not a QB-only, REG-only file.
GAME_AFFECTING_SEASON_TYPE = "REG"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QBContinuityError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise QBContinuityError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise QBContinuityError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise QBContinuityError(f"{field} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise QBContinuityError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise QBContinuityError(f"{field} must be >= {minimum}")
    return result


def _attempts(value: Any) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise QBContinuityError("attempts must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise QBContinuityError("attempts must be numeric") from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise QBContinuityError("attempts must be finite")
    if parsed < 0:
        raise QBContinuityError("attempts must be non-negative")
    return parsed


def infer_team_week_starters(
    source_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return one row per (season, week, team) with that week's inferred starter.

    "Starter" is a usage proxy, not a roster/depth-chart designation: the QB
    with the most pass attempts that week for that team. Ties (equal maximum
    attempts) are broken by the lexicographically smallest `player_id` so the
    result is deterministic and reproducible, never by insertion order.

    A team/week with no QB row carrying `attempts > 0` (bye week, or a source
    gap) produces no row at all -- it is not fabricated as a zero-attempt
    "starter". This function performs no temporal ordering itself; it is a
    pure aggregation step consumed by `build_prior_qb_continuity_features`,
    which enforces the no-lookahead ordering.
    """
    rows = [dict(row) for row in source_rows]
    seen_player_week: set[tuple[str, int, int, str]] = set()
    buckets: dict[tuple[int, int, str], dict[str, Any]] = {}

    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise QBContinuityError(
                f"row {index} missing required columns: {', '.join(missing)}"
            )
        position = str(row["position"] or "").strip().upper()
        season_type = str(row["season_type"] or "").strip().upper()
        if position != "QB" or season_type != GAME_AFFECTING_SEASON_TYPE:
            continue

        player_id = str(row["player_id"] or "").strip()
        if not player_id or player_id == "0":
            # Structural/missing-identity scaffolding row (see
            # nflverse_history._missing_id_row_is_audited_structural_zero);
            # never a real QB observation.
            continue

        season = _integer(row["season"], "season", minimum=1999)
        week = _integer(row["week"], "week", minimum=1)
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent_team"], "opponent_team").upper()
        if team == opponent:
            raise QBContinuityError("team and opponent_team must differ")
        attempts = _attempts(row["attempts"])

        dup_key = (player_id, season, week, team)
        if dup_key in seen_player_week:
            raise QBContinuityError(f"duplicate QB/team/week row: {dup_key}")
        seen_player_week.add(dup_key)

        if attempts <= 0:
            continue

        key = (season, week, team)
        bucket = buckets.setdefault(key, {
            "season": season, "week": week, "team": team,
            "opponents": set(), "candidates": [],
        })
        bucket["opponents"].add(opponent)
        bucket["candidates"].append((player_id, attempts))

    starters: list[dict[str, Any]] = []
    for (season, week, team), bucket in buckets.items():
        if len(bucket["opponents"]) != 1:
            raise QBContinuityError(
                f"team/week has ambiguous opponent identity: {team} {season}w{week}"
            )
        best = sorted(bucket["candidates"], key=lambda item: (-item[1], item[0]))[0]
        starters.append({
            "season": season,
            "week": week,
            "team": team,
            "opponent_team": next(iter(bucket["opponents"])),
            "starter_player_id": best[0],
            "starter_attempts": best[1],
        })

    starters.sort(key=lambda r: (r["season"], r["week"], r["team"]))
    return starters


def build_prior_qb_continuity_features(
    source_rows: Iterable[Mapping[str, Any]],
    *,
    rolling_window: int = 5,
) -> list[dict[str, Any]]:
    """Build one team-game row per observed starter with strictly prior features.

    `features` contains only facts knowable from games completed before the
    target game: the incumbent starter entering this game (the most recent
    PRIOR week's inferred starter) and how many consecutive prior starts that
    incumbent has made. It never contains this game's own attempts.

    `target` records this game's own realized starter identity -- current-
    game information -- for a later, separate evaluation harness only. No
    field in `target` is copied into `features`, and this function performs
    no correlation, scoring, or model wiring of its own.
    """
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise QBContinuityError("rolling_window must be a positive integer")

    starters = infer_team_week_starters(source_rows)

    histories: dict[str, deque[dict[str, Any]]] = defaultdict(
        lambda: deque(maxlen=rolling_window)
    )
    output: list[dict[str, Any]] = []

    for current in starters:
        team = current["team"]
        history = histories[team]

        if history:
            prior_starter_id = history[-1]["starter_player_id"]
            tenure = 1
            for past in reversed(list(history)[:-1]):
                if past["starter_player_id"] == prior_starter_id:
                    tenure += 1
                else:
                    break
        else:
            prior_starter_id = None
            tenure = None

        features = {
            "prior_starter_player_id": prior_starter_id,
            "qb_tenure_starts": tenure,
            "games_since_qb_change": tenure,
            "prior_starters_last_n": [h["starter_player_id"] for h in history],
            "current_game_attempts_used": False,
            "starter_source": "NFLVERSE_WEEKLY_PLAYER_STAT_ATTEMPTS_PROXY",
            "depth_chart_or_official_starter_designation_used": False,
        }

        actual_starter_id = current["starter_player_id"]
        target = {
            "actual_starter_player_id": actual_starter_id,
            "actual_starter_attempts": current["starter_attempts"],
            "starter_changed_from_prior": (
                None if prior_starter_id is None
                else actual_starter_id != prior_starter_id
            ),
        }

        output.append({
            "season": current["season"],
            "week": current["week"],
            "game_type": GAME_AFFECTING_SEASON_TYPE,
            "team": team,
            "opponent_team": current["opponent_team"],
            "prior_games_n": len(history),
            "rolling_window": rolling_window,
            "feature_semantics": "STRICTLY_PRIOR_QB_STARTER_CONTINUITY",
            "features": features,
            "target": target,
        })

        history.append(current)

    output.sort(key=lambda r: (r["season"], r["week"], r["team"]))
    return output
