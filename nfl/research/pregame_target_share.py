#!/usr/bin/env python3
"""Unit-consistent pregame target-share -> expected-targets stage
(Mission 10, research-only).

## The defect this fixes (found, not assumed)

`receptions_team_opportunity_challenger` computes a player's target share
as a fraction of his TEAM'S TARGETS (`targets / sum of team targets`), then
multiplies it by a predicted count of team DROPBACKS (`attempts +
sacks_suffered`). Those are different units: a dropback can end in a sack,
a throwaway, a spike, or a scramble, none of which is a target. Measured on
real 2024-2025 weeks 8+ team games, teams produce only ~0.83 targets per
dropback (p10 0.77, p90 0.88), so the existing chain over-projects
expected targets, and therefore receptions, by roughly 21% for every
player. Its mean signed error there is +0.49 receptions, against B0's
+0.02. See `engineering/nfl_pregame_target_share_20260923/`
`exploratory_diagnosis_report.json`. Six different pregame share
estimators (season-shrunk, last-4/6/10 ratio-of-sums, same-team only) all
landed within 0.001 share-MAE of each other, so the share estimate itself
was not the problem.

## What this module does

`team_targets_per_dropback` converts the team-volume prediction into the
same unit as the share: the team's own real targets-per-dropback ratio over
its last `window` games strictly before the target game (ratio of sums, not
a mean of ratios, so a short game doesn't get equal weight with a full
one). `unit_consistent_expected_receptions` then computes

    expected receptions = predicted dropbacks x targets-per-dropback
                          x target share x catch rate

with every input except the conversion factor passed in unchanged from the
existing chain. That isolates exactly one change.

## Fail-closed behavior

No prior team game with a real positive dropback count means no ratio. The
function returns `None` with a reason instead of substituting a league
constant, so an unknown ratio is never silently papered over.

## What this module does NOT do

- Does not change B0, any live selector, or `receptions_team_opportunity_
  challenger.py` (reused read-only).
- Does not change how the target share, team dropbacks, or catch rate are
  estimated.
- Does not use any target-game information: callers must pass only team
  games strictly before the target game. `team_targets_per_dropback`
  enforces this itself when given `target_season`/`target_week`.
"""
from __future__ import annotations

from typing import Any, Sequence

DEFAULT_WINDOW = 8


class PregameTargetShareError(ValueError):
    """Raised on malformed input; never used to hide a missing value."""


def team_targets_per_dropback(
    prior_team_games: Sequence[Sequence[float]],
    *,
    target_season: int,
    target_week: int,
    window: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Real ratio of team targets to team dropbacks over the team's last
    `window` games strictly before (target_season, target_week).

    `prior_team_games` rows are `(season, week, dropbacks, team_targets)`.
    Rows at or after the target week are dropped here, not trusted to the
    caller.
    """
    if window <= 0:
        raise PregameTargetShareError("window must be positive")
    games = sorted(
        (g for g in prior_team_games if (int(g[0]), int(g[1])) < (target_season, target_week)),
        key=lambda g: (g[0], g[1]),
    )[-window:]
    for g in games:
        if g[2] < 0 or g[3] < 0:
            raise PregameTargetShareError(f"negative team volume in real row {tuple(g)!r}")
    dropbacks = sum(g[2] for g in games)
    targets = sum(g[3] for g in games)
    if not games or dropbacks <= 0:
        return {"ratio": None, "games_used": len(games), "reason": "NO_REAL_PRIOR_TEAM_DROPBACKS"}
    return {"ratio": targets / dropbacks, "games_used": len(games), "reason": None}


def unit_consistent_expected_receptions(
    *,
    predicted_team_dropbacks: float | None,
    targets_per_dropback: float | None,
    target_share: float | None,
    catch_rate: float | None,
) -> dict[str, Any]:
    """Expected receptions with the share applied to predicted team
    TARGETS, not team dropbacks. Returns `projection: None` plus a reason
    whenever any real input is missing or out of range."""
    inputs = (predicted_team_dropbacks, targets_per_dropback, target_share, catch_rate)
    if any(v is None for v in inputs):
        return {"projection": None, "expected_targets": None, "reason": "MISSING_REQUIRED_INPUT"}
    if predicted_team_dropbacks <= 0:
        return {"projection": None, "expected_targets": None, "reason": "NON_POSITIVE_TEAM_DROPBACKS"}
    if not (0.0 < targets_per_dropback <= 1.5):
        return {"projection": None, "expected_targets": None, "reason": "TARGETS_PER_DROPBACK_OUT_OF_RANGE"}
    if not (0.0 < target_share <= 1.0):
        return {"projection": None, "expected_targets": None, "reason": "TARGET_SHARE_OUT_OF_RANGE"}
    if not (0.0 < catch_rate <= 1.0):
        return {"projection": None, "expected_targets": None, "reason": "CATCH_RATE_OUT_OF_RANGE"}
    expected_targets = predicted_team_dropbacks * targets_per_dropback * target_share
    return {"projection": expected_targets * catch_rate, "expected_targets": expected_targets, "reason": None}


__all__ = [
    "DEFAULT_WINDOW",
    "PregameTargetShareError",
    "team_targets_per_dropback",
    "unit_consistent_expected_receptions",
]
