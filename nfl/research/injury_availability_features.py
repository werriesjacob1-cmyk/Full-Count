#!/usr/bin/env python3
"""Strictly-prior starter-availability features from nflverse weekly injury reports.

Named hypothesis (do not extend this module beyond it): a team's margin/total
prediction error against the existing B0 naive baseline (`game_market_b0.py`)
is measurably larger in games where the starting QB was listed with a
game-affecting status (Out or Doubtful) than in games with a healthy
incumbent. This module does not evaluate that hypothesis, compute any error
correlation, or wire anything into a model or selector -- it only ingests the
source and builds the strictly pregame-safe availability feature a later
evaluation harness would need.

## Ingestion / provenance

Source: nflverse's `injuries` release
(`https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv`),
the same source `nflreadr::load_injuries()` reads (see nflverse/nflreadr's
`R/load_injuries.R`). This is the weekly practice/game-status injury report:
one row per player/week with a designation, published on the NFL's normal
Wednesday-Friday reporting cadence, i.e. before that week's games. It is a
different dataset from this repo's `nfl/normalize/official_inactives.py`,
which parses live-captured, archived HTML of the official ~90-minutes-before-
kickoff inactive list. That parser only covers games this system has actively
captured going forward; it has no bulk historical archive. nflverse's weekly
injury report is therefore the only source in reach that can extend this
signal across historical seasons, at the cost of reporting the last *filed*
status (Out/Doubtful/Questionable) rather than the literal final inactive
list. That distinction is preserved explicitly in this module's output
(`source_class`) rather than being blurred into a claim of "the inactive
list."

Coverage is real and bounded, not assumed: nflreadr's own loader enforces
`seasons >= 2009` (`stopifnot(seasons >= 2009, ...)` in `load_injuries.R`),
and this was independently confirmed against the live release on 2026-09-18:
`injuries_2009.csv` returns HTTP 200 and `injuries_2008.csv` returns HTTP 404
from `github.com/nflverse/nflverse-data`. Seasons before 2009 are not silently
treated as "no game-affecting status" (a favorable zero); they are reported
as an explicit `SEASON_NOT_COVERED_BY_SOURCE` state.

Per-season files are stable, completed-season CSVs (verified 2023 file:
738,501 bytes, header `season,game_type,team,week,gsis_id,position,
full_name,first_name,last_name,report_primary_injury,report_secondary_injury,
report_status,practice_primary_injury,practice_secondary_injury,
practice_status,date_modified`); this module does not pin an exact byte/SHA-256
digest per season the way `game_market_b0_research.PINNED_SCHEDULE_SOURCE`
pins a single nfldata commit, because nflverse republishes a season's
injuries file as a single per-season asset rather than a versioned repo path,
matching the existing convention in `nflverse_history.py` (URL template +
`REQUIRED_COLUMNS` fail-closed check, not a commit/byte pin) rather than
`game_market_b0_research.py`'s convention (which pins a specific nfldata
commit because that source has no natural per-season asset boundary). Only
the current, in-progress season's file is a moving target; completed seasons
do not change shape once published.

## What "starter" means here

nflverse's injury-report rows carry no starter/depth-chart flag, and this
repo ingests no roster/depth-chart dataset. Per the project owner's explicit
instruction not to fabricate a starter designation, "starter" is defined the
same way `qb_continuity_features.py` already defines it: the incumbent QB
entering the game, inferred strictly from prior weeks' pass-attempt usage.
Extending "starter-tier" to non-QB skill positions (the hypothesis's "or
another clearly-starter-tier skill player") is explicitly deferred: it would
require either a depth-chart ingestion or a comparable prior-usage proxy for
RB/WR/TE, neither of which exists in this repo yet, and building one was not
requested. This module covers the QB case only and says so in its output
(`starter_definition`), rather than silently narrowing scope.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

EARLIEST_COVERED_SEASON = 2009

INJURY_REPORT_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "injuries/injuries_{season}.csv"
)

REQUIRED_COLUMNS = frozenset({
    "season",
    "game_type",
    "team",
    "week",
    "gsis_id",
    "position",
    "report_status",
})

# The weekly injury report's own vocabulary. "Inactive" is not a value this
# source ever emits (that is the separate official inactive-list dataset);
# Out and Doubtful are the report's own game-affecting designations.
GAME_AFFECTING_STATUSES = frozenset({"OUT", "DOUBTFUL"})
NOT_GAME_AFFECTING_STATUSES = frozenset({"QUESTIONABLE", ""})


class InjuryAvailabilityError(ValueError):
    """Raised when weekly injury-report rows are not safe to use."""


def injury_report_url(season: int | str) -> str:
    """Canonical nflverse per-season weekly-injury-report CSV URL."""
    try:
        year = int(season)
    except (TypeError, ValueError) as exc:
        raise InjuryAvailabilityError(f"invalid NFL season {season!r}") from exc
    if year < 1999 or year > 2200:
        raise InjuryAvailabilityError(f"implausible NFL season {year}")
    return INJURY_REPORT_URL.format(season=year)


def season_is_covered(season: int) -> bool:
    return season >= EARLIEST_COVERED_SEASON


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().upper()


def _validate_and_index_injury_rows(
    injury_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, int, str, str], str]:
    """Return {(season, week, team, gsis_id): normalized report_status}."""
    index: dict[tuple[int, int, str, str], str] = {}
    for row_number, row in enumerate(injury_rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise InjuryAvailabilityError(
                f"injury row {row_number} missing required columns: {', '.join(missing)}"
            )
        gsis_id = str(row["gsis_id"] or "").strip()
        if not gsis_id:
            raise InjuryAvailabilityError(f"injury row {row_number} missing gsis_id")
        try:
            season = int(row["season"])
            week = int(row["week"])
        except (TypeError, ValueError) as exc:
            raise InjuryAvailabilityError(
                f"injury row {row_number} has non-integer season/week"
            ) from exc
        game_type = str(row["game_type"] or "").strip().upper()
        if game_type != "REG":
            # This module only feeds REG-week QB-continuity rows; postseason
            # injury rows are out of scope for this feature, not fabricated.
            continue
        team = str(row["team"] or "").strip().upper()
        if not team:
            raise InjuryAvailabilityError(f"injury row {row_number} missing team")
        status = _normalize_status(row["report_status"])

        key = (season, week, team, gsis_id)
        if key in index:
            raise InjuryAvailabilityError(
                f"duplicate injury report row for player/team/week: {key}"
            )
        index[key] = status
    return index


def build_prior_starter_availability_features(
    injury_rows: Iterable[Mapping[str, Any]],
    qb_continuity_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach a strictly pregame starter-availability flag to QB-continuity rows.

    For each row produced by
    `qb_continuity_features.build_prior_qb_continuity_features`, this reads
    ONLY that row's already-strictly-prior incumbent QB identity
    (`row["features"]["prior_starter_player_id"]`) and looks it up in the
    SAME week's nflverse weekly injury report for that team. It never reads
    `row["target"]` (this game's own realized starter/attempts), which is
    current-game information.

    The weekly injury report is filed before that week's games (see module
    docstring), so using the current week's own filed report to describe
    that same week's game is not a lookahead violation -- it is the intended
    pregame signal, exactly analogous to how `scoring_prior_features.py`
    attaches a "PREGAME" row's own already-known facts to its target game
    without using that game's outcome.
    """
    injury_index = _validate_and_index_injury_rows(injury_rows)

    output: list[dict[str, Any]] = []
    for row in qb_continuity_rows:
        season = row["season"]
        week = row["week"]
        team = row["team"]
        incumbent = row["features"]["prior_starter_player_id"]

        base = {
            "season": season,
            "week": week,
            "game_type": row.get("game_type", "REG"),
            "team": team,
            "opponent_team": row["opponent_team"],
            "incumbent_starter_player_id": incumbent,
            "source_class": "NFLVERSE_WEEKLY_INJURY_REPORT",
            "starter_definition": "QB_ONLY_USAGE_PROXY_INCUMBENT_ENTERING_GAME",
            "official_inactive_list_used": False,
            "current_game_information_used": False,
            "feature_semantics": "STRICTLY_PRIOR_STARTER_AVAILABILITY_FROM_WEEKLY_INJURY_REPORT",
        }

        if not season_is_covered(season):
            output.append({
                **base,
                "availability_status": "SEASON_NOT_COVERED_BY_SOURCE",
                "report_status_raw": None,
                "starter_out_feature": None,
            })
            continue

        if incumbent is None:
            output.append({
                **base,
                "availability_status": "UNKNOWN_NO_PRIOR_STARTER_IDENTITY",
                "report_status_raw": None,
                "starter_out_feature": None,
            })
            continue

        raw_status = injury_index.get((season, week, team, incumbent))
        if raw_status is None:
            # No row filed for this player/team/week in a covered season.
            # nflverse's injury report only lists players carrying a
            # designation, so absence in a season we successfully loaded is
            # itself the signal, not a data gap. See module docstring.
            output.append({
                **base,
                "availability_status": "NOT_LISTED_GAME_AFFECTING_STATUS",
                "report_status_raw": None,
                "starter_out_feature": False,
            })
            continue

        if raw_status in GAME_AFFECTING_STATUSES:
            status_label = "LISTED_" + raw_status
            starter_out = True
        elif raw_status in NOT_GAME_AFFECTING_STATUSES:
            status_label = "NOT_LISTED_GAME_AFFECTING_STATUS"
            starter_out = False
        else:
            # An nflverse status value outside the known vocabulary (e.g. a
            # future schema change). Fail closed rather than silently
            # guessing which side of the line it belongs on.
            raise InjuryAvailabilityError(
                f"unrecognized report_status {raw_status!r} for {team} "
                f"{season}w{week} {incumbent}"
            )

        output.append({
            **base,
            "availability_status": status_label,
            "report_status_raw": raw_status,
            "starter_out_feature": starter_out,
        })

    output.sort(key=lambda r: (r["season"], r["week"], r["team"]))
    return output


__all__ = [
    "EARLIEST_COVERED_SEASON",
    "INJURY_REPORT_URL",
    "REQUIRED_COLUMNS",
    "GAME_AFFECTING_STATUSES",
    "InjuryAvailabilityError",
    "injury_report_url",
    "season_is_covered",
    "build_prior_starter_availability_features",
]
