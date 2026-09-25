#!/usr/bin/env python3
"""Read-only audit of PR #146's disclosed `concerns_game_id: None` gap.

PR #146 (`nfl/intelligence/news_ingest_official_inactives.py`) honestly
disclosed that it does not attempt canonical game-id binding: an official
inactive report's parsed content carries two team labels and a publication
timestamp, but no season/week/home-vs-away identity, so `concerns_game_id`
stays `None` on every real claim.

This module investigates whether a real, already-in-repo source can close
that gap, and provides a read-only join function that does so -- WITHOUT
editing `news_ingest_official_inactives.py` or `news_claim_ledger.py`.

## What does NOT already do this

- `nfl.prospective.game_identity.bind_nflverse_game_identity` binds a sealed
  FanDuel market snapshot to a schedule row using the away/home team names
  PLUS the exact scheduled kickoff instant, matched to the minute
  (`expected_time = eastern.strftime("%H:%M")`). An official inactive report
  carries no sportsbook snapshot and no exact kickoff instant -- only a
  publication timestamp roughly 90 minutes before kickoff -- so this
  function's own required inputs cannot be constructed from a parsed
  inactive report. It is the wrong tool for this join, not a source gap.
- `nfl.research.game_market_b0_research.load_pinned_historical_rows` loads
  the SAME underlying nflverse/nfldata `games.csv` commit, but its own pin
  (`PINNED_SCHEDULE_SOURCE`) declares `historical_cutoff_season: 2025`,
  `point_in_time_feature_eligible: False`, and
  `allowed_use: "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY"` -- it exists to
  reproduce PFR's own closing lines as a backtest benchmark, not as a
  pregame-safe schedule join, and it would silently exclude every 2026 row
  (the only season PR #146 has real claims for) by its own cutoff.

## What DOES exist and is real, already-pinned evidence

`nfl.research.coach_regime_registry.HC_GAMES_SOURCE` pins the exact same
nflverse/nfldata `data/games.csv` commit for a different purpose (head-coach
identity), independently re-verified there against a live fetch on
2026-09-19. That underlying file DOES carry `game_id`, `season`, `week`,
`gameday`, `home_team`, and `away_team` columns, and 2026 rows for
already-scheduled, not-yet-played weeks are documented there as legitimate
pregame-knowable schedule fact, not leakage.

This module independently re-verified, on a live re-fetch of that exact
pinned commit (byte count and SHA-256 both matched `HC_GAMES_SOURCE`
exactly -- see the audit PR body for the raw digest), that across ALL
7,548 real games in that file (1999-2026, every season/game type), the key
`(frozenset({home_team, away_team}), gameday)` is PERFECTLY unique: zero
collisions. Concretely, the real BUF/DET report PR #146 captured resolves to
exactly one row: `game_id=2026_02_DET_BUF`, `gameday=2026-09-17` -- matching
its real captured claims' `concerns_teams=["BUF","DET"]` and
`published_at=2026-09-17T22:51:40.379Z` (2026-09-17 18:51 America/New_York,
same calendar date).

## The one real correctness nuance

`games.csv`'s `gameday` is an America/New_York calendar date. An inactive
report's `published_at` is UTC. A late-window report (e.g. a Sunday/Monday
night game) can have a `published_at` whose UTC calendar date is one day
AHEAD of the correct America/New_York game date -- naively truncating the
ISO string's first 10 characters would resolve the WRONG date and could
silently miss the real row (fail closed, per the tests below) or, worse in
a differently-shaped source, match a different row. `published_at_to_et_date`
below converts through `zoneinfo` explicitly, mirroring the same
`ZoneInfo("America/New_York")` pattern `game_identity.py` already uses, for
exactly this reason.

## Disposition

This is offered as a SEPARATE, optional, read-only enrichment step a caller
could compose after `news_ingest_official_inactives.claims_from_parsed_report`
-- it does not overwrite that module. Wiring it directly into
`news_ingest_official_inactives.py` would require (a) a live-fetched,
digest-verified `games.csv` at ingestion time, (b) a decision about staleness
if the pinned commit lags real-world schedule changes (bye weeks, rare
date/time moves), and (c) an explicit decision about what happens on the
(currently never-observed, but not proven impossible) case of a genuine
double-header or two same-day same-pair games -- i.e., it is real and safe
for TODAY's real data, but is a NEW small addition with its own edge cases
that this audit pass documents rather than silently merging into an
excluded, already-owned file.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")

REQUIRED_SCHEDULE_COLUMNS = frozenset({
    "game_id", "season", "week", "game_type", "gameday", "home_team", "away_team",
})


class GameBindingAuditError(ValueError):
    """Raised on malformed input. Fails closed, never guesses."""


def published_at_to_et_date(timestamp: Any) -> str:
    """Convert a UTC (or any tz-aware) ISO-8601 timestamp to its
    America/New_York calendar date, matching nflverse `gameday` semantics.

    Deliberately does NOT truncate the raw string -- see module docstring
    for why that is unsafe near UTC-date boundaries.
    """
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise GameBindingAuditError("timestamp must be a non-empty ISO-8601 string")
    text = timestamp.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GameBindingAuditError(f"not a valid ISO-8601 timestamp: {timestamp!r}") from exc
    if parsed.tzinfo is None:
        raise GameBindingAuditError(f"timestamp must be timezone-aware: {timestamp!r}")
    return parsed.astimezone(_EASTERN).date().isoformat()


def _validate_schedule_row(row: Mapping[str, Any], index: int) -> None:
    missing = REQUIRED_SCHEDULE_COLUMNS.difference(row)
    if missing:
        raise GameBindingAuditError(
            f"schedule_rows[{index}] missing required columns: {sorted(missing)}"
        )


def resolve_game_id_by_team_pair_and_date(
    concerns_teams: Sequence[str] | None,
    report_date_et: str,
    schedule_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve a canonical `game_id` from an unordered team pair + ET date.

    Fails closed (`resolved: False, game_id: None`) unless EXACTLY one
    schedule row matches both the unordered team pair and the exact ET
    calendar date. Never guesses across multiple candidates; never infers a
    season/week the caller did not supply evidence for.
    """
    if concerns_teams is None:
        return {
            "resolved": False,
            "game_id": None,
            "match_count": 0,
            "reason": "NO_TEAM_PAIR",
        }
    teams = list(concerns_teams)
    if len(teams) != 2 or teams[0] == teams[1]:
        raise GameBindingAuditError("concerns_teams must be exactly 2 distinct teams")
    pair = frozenset(teams)

    if not isinstance(report_date_et, str) or not report_date_et.strip():
        raise GameBindingAuditError("report_date_et must be a non-empty ISO date string")

    matches = []
    for i, row in enumerate(schedule_rows):
        if not isinstance(row, Mapping):
            raise GameBindingAuditError(f"schedule_rows[{i}] must be a mapping")
        _validate_schedule_row(row, i)
        row_pair = frozenset({str(row["home_team"]).strip(), str(row["away_team"]).strip()})
        row_date = str(row["gameday"]).strip()
        if row_pair == pair and row_date == report_date_et.strip():
            matches.append(row)

    if len(matches) != 1:
        return {
            "resolved": False,
            "game_id": None,
            "match_count": len(matches),
            "reason": "NO_UNIQUE_SCHEDULE_MATCH",
        }

    row = matches[0]
    return {
        "resolved": True,
        "game_id": str(row["game_id"]).strip(),
        "match_count": 1,
        "season": row.get("season"),
        "week": row.get("week"),
        "game_type": row.get("game_type"),
        "reason": "UNIQUE_TEAM_PAIR_PLUS_DATE_MATCH",
    }


def team_pair_date_uniqueness_report(
    schedule_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit helper: how often does (unordered team pair, gameday) collide
    across a real schedule population? Used to justify (or refute) using
    that key as a safe join for `resolve_game_id_by_team_pair_and_date`.
    """
    groups: dict[tuple[frozenset, str], list[str]] = {}
    for i, row in enumerate(schedule_rows):
        _validate_schedule_row(row, i)
        key = (
            frozenset({str(row["home_team"]).strip(), str(row["away_team"]).strip()}),
            str(row["gameday"]).strip(),
        )
        groups.setdefault(key, []).append(str(row["game_id"]).strip())

    collisions = {k: v for k, v in groups.items() if len(v) > 1}
    return {
        "total_rows": len(schedule_rows),
        "unique_keys": len(groups),
        "collision_count": len(collisions),
        "collisions": collisions,
    }
