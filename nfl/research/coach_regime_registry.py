#!/usr/bin/env python3
"""Dated NFL coach/coordinator/playcaller regime registry substrate.

Pure data-substrate module. It builds no feature, scores no candidate, and is
not wired into any model, selector, or public pick. Its only job is to answer,
for a given (team, role, point-in-time), "which regime -- which person(s), on
what evidence, at what confidence -- was responsible for that role on that
date," and to fail closed (UNKNOWN) rather than guess whenever the source
record does not support a single clean answer.

## Roles

- ``HC``: head coach.
- ``OC`` / ``DC``: offensive / defensive coordinator by formal title.
- ``OFFENSIVE_PLAYCALLER`` / ``DEFENSIVE_PLAYCALLER``: who actually called
  plays, which this module treats as a *separate* fact from coordinator
  title. Title is never assumed to equal playcaller: `lookup_regime` only
  reports a playcaller as CONFIRMED when a directly-sourced playcaller
  interval exists; absent that, it reports the concurrent OC/DC as the
  playcaller with confidence downgraded to ASSUMED and the derivation
  disclosed in `LookupResult.reason`, never silently presented as fact.
  Primary QB is explicitly out of scope for this module (see the project
  brief for this workstream).

## Ingested source (Phase 1: HC role only)

`HC_GAMES_SOURCE` pins one exact nflverse/nfldata `data/games.csv` commit --
the SAME commit already pinned and used in this repository by
`game_market_b0_research.PINNED_SCHEDULE_SOURCE` (byte count and SHA-256
independently re-verified here on 2026-09-19; not imported from that module,
to keep this substrate self-contained and outside that workstream's files,
but the two pins describe the same upstream fact and can be cross-checked
against each other). That file carries `away_coach`/`home_coach` columns
naming the head coach who coached each specific game, dated by `gameday`.
Verified real coverage: REG-season games, 1999-2026 (2026 rows include
already-scheduled, not-yet-played weeks carrying nflverse's currently-known
coach for that scheduled game -- a legitimate pregame-knowable fact, not a
score or outcome, so including it is not leakage). Spot-checked against a
known real mid-season change (Jon Gruden -> Rich Bisaccia, Las Vegas
Raiders, October 2021): the source correctly attributes week 5 to Gruden and
week 6 onward to Bisaccia.

## Negative research results (disclosed, not silently worked around)

- nflverse/nflreadr has no coordinator/playcaller dataset. Verified against
  the live function reference at https://nflreadr.nflverse.com/reference/
  (HTTP 200 on 2026-09-19): `load_teams`, `load_contracts`,
  `load_depth_charts`, `load_officials`, `load_pfr_advstats`, etc. exist;
  no `load_coaches`/`load_staff`/equivalent exists.
- Pro-Football-Reference's team coaching-staff pages
  (`pro-football-reference.com/teams/<team>/<year>.htm`) are the natural
  historical source for OC/DC-by-team-season, and this repository has no
  existing PFR-scraping utility to reuse (its only existing PFR references
  are to nflverse's own PFR-*derived* closing-line/snap-count assets, not a
  scraper). The page itself returned HTTP 403 with a Cloudflare bot-challenge
  body ("Just a moment...") in this environment, not a proxy policy block --
  i.e. the site itself is refusing this environment's automated access, a
  source-access blocker rather than a coverage gap in the source itself.
  A `web.archive.org` mirror of the same URL was separately blocked by this
  environment's own egress policy ("Blocked by egress policy"), which per
  this environment's own runbook is a policy denial, not something to retry
  around.
- Wikipedia per-team-season articles (e.g. "2021 New England Patriots
  season") do carry real OC/DC staff facts, confirmed by direct fetch, but
  in at least two materially different formats across the sampled seasons
  (an `{{NFL final staff}}`-style infobox in some team-seasons, and a
  hand-authored `==Staff==` wikitable in others -- e.g. the 2010 Patriots
  page, whose table correctly shows NO true OC/DC that year, since Belichick
  ran defense by committee). Reliably parsing that inconsistency correctly
  across 32 teams x 27 seasons without misattribution risk is a
  meaningfully larger, higher-fabrication-risk effort than this pass
  safely supports, so it was not built into the ingested Phase 1 dataset.
  This is recorded here as a real, viable *candidate* future source, not as
  "no source exists" -- the honest finding is that it exists but is not yet
  safe to bulk-ingest without a much more careful, era-aware parser and
  spot-checking than fits this pass.

Net effect: Phase 1 populates `HC` only. `OC`, `DC`,
`OFFENSIVE_PLAYCALLER`, and `DEFENSIVE_PLAYCALLER` are fully supported by
this module's data model and lookup engine (see the regime-change,
shared-responsibility, and playcaller-default tests, which exercise them
against synthetic fixtures) but carry zero ingested production intervals
until a later workstream adds a real, safely-parsed source for them. A
lookup for those roles against the shipped registry therefore correctly and
honestly returns UNKNOWN with reason ``NO_COVERAGE`` for every team/date in
Phase 1 -- that is the intended, disclosed behavior, not a bug.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class RegimeRegistryError(ValueError):
    """Raised for structurally invalid registry input or API misuse.

    Reserved for programmer/data-shape errors (bad types, missing required
    columns, malformed dates). Legitimate historical uncertainty -- no
    source coverage, or conflicting source records -- is never raised; it is
    returned from `lookup_regime` as an explicit UNKNOWN `LookupResult`.
    """


# --- Roles -------------------------------------------------------------

ROLE_HC = "HC"
ROLE_OC = "OC"
ROLE_DC = "DC"
ROLE_OFFENSIVE_PLAYCALLER = "OFFENSIVE_PLAYCALLER"
ROLE_DEFENSIVE_PLAYCALLER = "DEFENSIVE_PLAYCALLER"

ALL_ROLES = frozenset({
    ROLE_HC, ROLE_OC, ROLE_DC, ROLE_OFFENSIVE_PLAYCALLER, ROLE_DEFENSIVE_PLAYCALLER,
})

# Playcaller role -> the coordinator title role it defaults to when no
# playcaller-specific evidence exists. Title is documented as a first-class
# distinct field from playcaller identity precisely so this default is
# always visible as an ASSUMPTION, never silently folded into "the fact".
PLAYCALLER_DEFAULT_ROLE = {
    ROLE_OFFENSIVE_PLAYCALLER: ROLE_OC,
    ROLE_DEFENSIVE_PLAYCALLER: ROLE_DC,
}

CONFIDENCE_CONFIRMED = "CONFIRMED"
CONFIDENCE_ASSUMED = "ASSUMED"
ALL_CONFIDENCES = frozenset({CONFIDENCE_CONFIRMED, CONFIDENCE_ASSUMED})

# Only regular-season games define a regime interval. Preseason/playoff
# rows are excluded from interval construction, matching this repository's
# existing convention of treating REG as the game-affecting season type
# (see `qb_continuity_features.GAME_AFFECTING_SEASON_TYPE`).
GAME_AFFECTING_TYPE = "REG"

REQUIRED_GAME_COLUMNS = frozenset({
    "season", "week", "game_type", "gameday",
    "away_team", "home_team", "away_coach", "home_coach",
})

# The exact nflverse/nfldata `data/games.csv` commit this module's HC
# ingestion is pinned to. Independently re-verified (byte count + SHA-256)
# against a live fetch on 2026-09-19; matches the same commit already
# pinned in this repository by `game_market_b0_research.PINNED_SCHEDULE_SOURCE`.
HC_GAMES_SOURCE = {
    "repository": "https://github.com/nflverse/nfldata",
    "commit": "8ed09b2fe3ea42332b2249a995737e13dd931ff3",
    "path": "data/games.csv",
    "bytes": 2177838,
    "sha256": "26332ae5d8d8d0481f0670cf5e3849497a415351d4026ae5bee15a5aab96d188",
    "fields_used": ["season", "week", "game_type", "gameday", "away_team", "home_team", "away_coach", "home_coach"],
    "verified_at": "2026-09-19",
    "verification_note": (
        "Re-fetched raw.githubusercontent.com/nflverse/nfldata/"
        "8ed09b2fe3ea42332b2249a995737e13dd931ff3/data/games.csv and confirmed "
        "both byte count and SHA-256 against this pin before ingesting."
    ),
}

HC_SOURCE_CITATION = (
    "nflverse/nfldata data/games.csv, commit "
    f"{HC_GAMES_SOURCE['commit']} (home_coach/away_coach columns, dated by "
    "gameday); see HC_GAMES_SOURCE for the full pin"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_hc_games_source(raw: bytes) -> None:
    """Fail closed if the fetched games.csv bytes drift from the pin."""
    n = len(raw)
    digest = sha256_bytes(raw)
    if n != HC_GAMES_SOURCE["bytes"] or digest != HC_GAMES_SOURCE["sha256"]:
        raise RegimeRegistryError(
            "nfldata games.csv digest drift: expected "
            f"bytes={HC_GAMES_SOURCE['bytes']} sha256={HC_GAMES_SOURCE['sha256']}, "
            f"got bytes={n} sha256={digest}"
        )


# --- Regime interval -----------------------------------------------------


@dataclass(frozen=True)
class RegimeInterval:
    """One dated, sourced responsibility interval for a team/role.

    `persons` is a tuple rather than a single name so that a genuinely
    real, source-agreed *shared* regime (two people jointly responsible for
    a stretch, e.g. co-coordinators) is representable as exactly one regime
    record rather than two conflicting single-person ones. `end_date` is
    always a concrete date (never None/open-ended): an interval only ever
    asserts what the source actually evidences, so an ongoing regime's
    `end_date` is its most recent observed evidence date, not an
    extrapolation into unobserved future time.
    """

    team: str
    role: str
    persons: tuple[str, ...]
    start_date: date
    end_date: date
    source: str
    confidence: str
    title: str | None = None
    transition_reason: str | None = None
    scheme: str | None = None

    def __post_init__(self) -> None:
        if not self.team or not isinstance(self.team, str):
            raise RegimeRegistryError("team must be a non-empty string")
        if self.role not in ALL_ROLES:
            raise RegimeRegistryError(f"unknown role: {self.role!r}")
        if not self.persons or any(not p or not isinstance(p, str) for p in self.persons):
            raise RegimeRegistryError("persons must be a non-empty tuple of non-empty strings")
        if len(set(self.persons)) != len(self.persons):
            raise RegimeRegistryError(f"duplicate person in persons: {self.persons!r}")
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise RegimeRegistryError("start_date/end_date must be date objects")
        if self.start_date > self.end_date:
            raise RegimeRegistryError(
                f"start_date {self.start_date} after end_date {self.end_date}"
            )
        if not self.source or not isinstance(self.source, str):
            raise RegimeRegistryError("source must be a non-empty citation string")
        if self.confidence not in ALL_CONFIDENCES:
            raise RegimeRegistryError(f"unknown confidence: {self.confidence!r}")

    @property
    def is_shared(self) -> bool:
        return len(self.persons) > 1

    def covers(self, target_date: date) -> bool:
        return self.start_date <= target_date <= self.end_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "role": self.role,
            "persons": list(self.persons),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "source": self.source,
            "confidence": self.confidence,
            "title": self.title,
            "transition_reason": self.transition_reason,
            "scheme": self.scheme,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "RegimeInterval":
        return RegimeInterval(
            team=d["team"],
            role=d["role"],
            persons=tuple(d["persons"]),
            start_date=_parse_date(d["start_date"], "start_date"),
            end_date=_parse_date(d["end_date"], "end_date"),
            source=d["source"],
            confidence=d["confidence"],
            title=d.get("title"),
            transition_reason=d.get("transition_reason"),
            scheme=d.get("scheme"),
        )


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise RegimeRegistryError(f"{field} is missing")
    try:
        year, month, day = (int(part) for part in text.split("-"))
        return date(year, month, day)
    except (TypeError, ValueError) as exc:
        raise RegimeRegistryError(f"{field} is not a valid YYYY-MM-DD date: {value!r}") from exc


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RegimeRegistryError(f"{field} is missing")
    return text


def _int_text(value: Any, field: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise RegimeRegistryError(f"{field} is missing")
    try:
        return int(text)
    except ValueError as exc:
        raise RegimeRegistryError(f"{field} must be an integer: {value!r}") from exc


# --- HC ingestion from nflverse/nfldata games.csv -------------------------


@dataclass(frozen=True)
class TeamCoachObservation:
    """One team's coach-of-record for one real, dated REG game."""

    team: str
    season: int
    week: int
    gameday: date
    coach: str


def iter_team_coach_observations(game_rows: Iterable[Mapping[str, Any]]) -> list[TeamCoachObservation]:
    """Expand nfldata games.csv rows into one HC observation per team/game.

    Each REG-season row yields two observations (home and away side); the
    coach is whoever games.csv already attributes to that team for that
    specific game (real per-game attribution, not inferred). Preseason and
    postseason rows are dropped -- see `GAME_AFFECTING_TYPE`.
    """
    rows = [dict(row) for row in game_rows]
    if not rows:
        return []

    observations: list[TeamCoachObservation] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_GAME_COLUMNS.difference(row.keys()))
        if missing:
            raise RegimeRegistryError(f"row {index} missing required columns: {', '.join(missing)}")
        if str(row["game_type"] or "").strip().upper() != GAME_AFFECTING_TYPE:
            continue

        season = _int_text(row["season"], "season")
        week = _int_text(row["week"], "week")
        gameday = _parse_date(row["gameday"], "gameday")
        away_team = _text(row["away_team"], "away_team").upper()
        home_team = _text(row["home_team"], "home_team").upper()
        away_coach = _text(row["away_coach"], "away_coach")
        home_coach = _text(row["home_coach"], "home_coach")
        if away_team == home_team:
            raise RegimeRegistryError(f"row {index}: away_team equals home_team ({away_team!r})")

        for team, coach in ((away_team, away_coach), (home_team, home_coach)):
            key = (team, gameday.isoformat())
            if key in seen:
                raise RegimeRegistryError(
                    f"duplicate REG-game observation for team/date: {team}/{gameday.isoformat()}"
                )
            seen.add(key)
            observations.append(TeamCoachObservation(
                team=team, season=season, week=week, gameday=gameday, coach=coach,
            ))

    observations.sort(key=lambda obs: (obs.team, obs.gameday, obs.season, obs.week))
    return observations


def build_hc_intervals_from_observations(
    observations: Sequence[TeamCoachObservation],
    *, source: str = HC_SOURCE_CITATION,
) -> list[RegimeInterval]:
    """Group consecutive same-coach observations per team into HC intervals.

    A regime changes exactly where the observed coach name changes between
    two chronologically adjacent games for the same team -- this is how the
    real 2021 Las Vegas Raiders Gruden->Bisaccia mid-season change (week 5
    -> week 6) is captured correctly by this function against the real
    source data, with no manual date-splitting required.
    """
    by_team: dict[str, list[TeamCoachObservation]] = {}
    for obs in observations:
        by_team.setdefault(obs.team, []).append(obs)

    intervals: list[RegimeInterval] = []
    for team, team_obs in by_team.items():
        team_obs = sorted(team_obs, key=lambda o: o.gameday)
        run_start = 0
        for i in range(1, len(team_obs) + 1):
            at_boundary = i == len(team_obs) or team_obs[i].coach != team_obs[run_start].coach
            if at_boundary:
                intervals.append(RegimeInterval(
                    team=team,
                    role=ROLE_HC,
                    persons=(team_obs[run_start].coach,),
                    start_date=team_obs[run_start].gameday,
                    end_date=team_obs[i - 1].gameday,
                    source=source,
                    confidence=CONFIDENCE_CONFIRMED,
                    title="Head Coach",
                    transition_reason="NOT_DISCLOSED_BY_SOURCE",
                ))
                run_start = i
    intervals.sort(key=lambda iv: (iv.team, iv.start_date))
    return intervals


def stretch_intervals_to_continuous(intervals: Sequence[RegimeInterval]) -> list[RegimeInterval]:
    """Bridge each team/role's known-adjacent intervals across off-game dates.

    Raw per-game intervals only cover exact game dates. Between one regime's
    last known game and the next regime's first known game (e.g. the
    offseason), nothing changed as far as the source shows, so this pushes
    each interval's `end_date` forward to the day before the *next known*
    interval's `start_date` for that team/role. The final (most recent,
    still-open-as-of-the-source) interval per team/role is left exactly as
    observed: this function only bridges gaps it can resolve on both sides,
    and never extrapolates the last known regime into unobserved future
    time. Requires its input to already be non-overlapping per (team, role);
    it raises rather than silently stretching over a genuine conflict.
    """
    groups: dict[tuple[str, str], list[RegimeInterval]] = {}
    for iv in intervals:
        groups.setdefault((iv.team, iv.role), []).append(iv)

    out: list[RegimeInterval] = []
    for (team, role), group in groups.items():
        ordered = sorted(group, key=lambda iv: iv.start_date)
        for i, iv in enumerate(ordered):
            if i > 0 and ordered[i - 1].end_date >= iv.start_date:
                raise RegimeRegistryError(
                    f"overlapping intervals for {team}/{role}; cannot stretch a conflicting timeline"
                )
            if i + 1 < len(ordered):
                new_end = ordered[i + 1].start_date - timedelta(days=1)
                out.append(replace(iv, end_date=new_end))
            else:
                out.append(iv)
    out.sort(key=lambda iv: (iv.team, iv.role, iv.start_date))
    return out


def build_game_date_index(game_rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int, int], date]:
    """team/season/week -> that game's date, for season+week lookups."""
    index: dict[tuple[str, int, int], date] = {}
    for obs in iter_team_coach_observations(game_rows):
        index[(obs.team, obs.season, obs.week)] = obs.gameday
    return index


# --- Point-in-time lookup --------------------------------------------------


@dataclass(frozen=True)
class LookupResult:
    status: str  # "RESOLVED" | "UNKNOWN"
    team: str
    role: str
    target_date: date
    persons: tuple[str, ...] | None = None
    confidence: str | None = None
    source: str | None = None
    is_shared: bool = False
    derived_from_role: str | None = None
    reason: str | None = None
    interval: RegimeInterval | None = None


def lookup_regime(
    intervals: Sequence[RegimeInterval],
    *,
    team: str,
    role: str,
    target_date: date | None = None,
    season: int | None = None,
    week: int | None = None,
    game_date_index: Mapping[tuple[str, int, int], date] | None = None,
    allow_playcaller_default: bool = True,
) -> LookupResult:
    """Resolve exactly one regime for (team, role, point-in-time), or UNKNOWN.

    The point-in-time is given directly (`target_date`) or resolved from
    (`team`, `season`, `week`) via `game_date_index`; exactly one of the two
    forms must be supplied. This function never reads wall-clock time and
    never defaults to "the latest known regime" -- it only ever filters the
    supplied `intervals` by the caller's own explicit target date, so a
    target-time lookup structurally cannot be influenced by anything the
    caller did not ask about beyond that date.

    Fail-closed behavior:
    - No interval covers the date -> UNKNOWN, reason NO_COVERAGE.
    - More than one *distinct* interval covers the date (a genuine source
      conflict) -> UNKNOWN, reason AMBIGUOUS_OVERLAPPING_INTERVALS. This is
      different from a real, source-agreed *shared* regime, which is stored
      as a single interval with multiple `persons` and resolves normally.
    - A playcaller role with no direct evidence falls back to the matching
      coordinator title (OC/DC) and is returned as CONFIRMED-title-holder
      but ASSUMED-playcaller, with the derivation disclosed in `reason`; it
      is never presented as a confirmed playcaller fact.
    """
    if role not in ALL_ROLES:
        raise RegimeRegistryError(f"unknown role: {role!r}")
    if not team or not isinstance(team, str):
        raise RegimeRegistryError("team must be a non-empty string")
    team = team.upper()

    if target_date is not None:
        if season is not None or week is not None:
            raise RegimeRegistryError("pass target_date OR season+week, not both")
        resolved_date = target_date
    else:
        if season is None or week is None:
            raise RegimeRegistryError("must supply target_date, or both season and week")
        if not game_date_index or (team, season, week) not in game_date_index:
            return LookupResult(
                status="UNKNOWN", team=team, role=role,
                target_date=date.min, reason="NO_GAME_DATE_FOR_SEASON_WEEK",
            )
        resolved_date = game_date_index[(team, season, week)]

    matches = [
        iv for iv in intervals
        if iv.team.upper() == team and iv.role == role and iv.covers(resolved_date)
    ]
    # Identical duplicate ingestion of the same fact is not an ambiguity.
    distinct = list({(iv.persons, iv.confidence, iv.source, iv.start_date, iv.end_date): iv for iv in matches}.values())

    if len(distinct) == 1:
        iv = distinct[0]
        return LookupResult(
            status="RESOLVED", team=team, role=role, target_date=resolved_date,
            persons=iv.persons, confidence=iv.confidence, source=iv.source,
            is_shared=iv.is_shared, interval=iv,
        )

    if len(distinct) > 1:
        return LookupResult(
            status="UNKNOWN", team=team, role=role, target_date=resolved_date,
            reason=f"AMBIGUOUS_OVERLAPPING_INTERVALS: {len(distinct)} conflicting source records",
        )

    # No direct coverage. Playcaller roles fall back to the concurrent
    # coordinator title, downgraded to ASSUMED, never silently upgraded.
    if allow_playcaller_default and role in PLAYCALLER_DEFAULT_ROLE:
        underlying_role = PLAYCALLER_DEFAULT_ROLE[role]
        under = lookup_regime(
            intervals, team=team, role=underlying_role, target_date=resolved_date,
            allow_playcaller_default=False,
        )
        if under.status == "RESOLVED":
            return LookupResult(
                status="RESOLVED", team=team, role=role, target_date=resolved_date,
                persons=under.persons, confidence=CONFIDENCE_ASSUMED,
                source=(
                    f"DERIVED_DEFAULT: no {role} evidence; assumed playcaller == "
                    f"concurrent {underlying_role} title-holder ({under.source})"
                ),
                is_shared=under.is_shared, derived_from_role=underlying_role,
                reason="ASSUMED_FROM_COORDINATOR_TITLE_NO_PLAYCALLER_EVIDENCE",
                interval=under.interval,
            )
        return LookupResult(
            status="UNKNOWN", team=team, role=role, target_date=resolved_date,
            reason=f"NO_PLAYCALLER_EVIDENCE_AND_UNDERLYING_{underlying_role}_{under.reason}",
        )

    return LookupResult(
        status="UNKNOWN", team=team, role=role, target_date=resolved_date, reason="NO_COVERAGE",
    )


# --- Coverage report --------------------------------------------------------


def build_coverage_report(intervals: Sequence[RegimeInterval]) -> dict[str, Any]:
    """Per role, per team: covered date range(s) and gap disclosure.

    This is the honest population statement for this substrate: it must
    never be inferred from a downstream consumer's assumptions about what
    "should" be covered, only from the intervals actually present.
    """
    report: dict[str, Any] = {}
    for role in sorted(ALL_ROLES):
        role_intervals = [iv for iv in intervals if iv.role == role]
        if not role_intervals:
            report[role] = {
                "n_teams": 0, "teams_covered": [], "per_team": {},
                "coverage_gap": "NO_INTERVALS_INGESTED_FOR_THIS_ROLE_IN_PHASE_1",
            }
            continue
        per_team: dict[str, Any] = {}
        for team in sorted({iv.team for iv in role_intervals}):
            team_intervals = sorted((iv for iv in role_intervals if iv.team == team), key=lambda iv: iv.start_date)
            per_team[team] = {
                "n_intervals": len(team_intervals),
                "earliest_start": team_intervals[0].start_date.isoformat(),
                "latest_end": team_intervals[-1].end_date.isoformat(),
                "distinct_persons": sorted({p for iv in team_intervals for p in iv.persons}),
                "confidences": sorted({iv.confidence for iv in team_intervals}),
            }
        report[role] = {
            "n_teams": len(per_team),
            "teams_covered": sorted(per_team.keys()),
            "per_team": per_team,
        }
    return report


# --- Offline CLI (mirrors game_market_b0_research.py: caller supplies the
# already-fetched, digest-verified bytes; this module never fetches a moving
# URL itself, so ingestion stays deterministic and test-independent). -------


def build_registry_from_games_csv(games_csv_path: Path) -> dict[str, Any]:
    raw = games_csv_path.read_bytes()
    verify_hc_games_source(raw)
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    observations = iter_team_coach_observations(rows)
    hc_intervals = build_hc_intervals_from_observations(observations)
    hc_intervals = stretch_intervals_to_continuous(hc_intervals)
    coverage = build_coverage_report(hc_intervals)
    return {
        "schema_version": 1,
        "source": HC_GAMES_SOURCE,
        "intervals": [iv.to_dict() for iv in hc_intervals],
        "coverage_report": coverage,
    }


def load_registry_json(path: Path) -> list[RegimeInterval]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [RegimeInterval.from_dict(d) for d in data["intervals"]]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-csv", type=Path, required=True, help="Pinned nfldata data/games.csv path")
    parser.add_argument("--out", type=Path, required=True, help="Output registry JSON path")
    args = parser.parse_args()

    registry = build_registry_from_games_csv(args.games_csv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "n_intervals": len(registry["intervals"]),
        "n_teams_hc": registry["coverage_report"][ROLE_HC]["n_teams"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
