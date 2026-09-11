#!/usr/bin/env python3
"""Coaching staff identity for all 32 teams. Free, licensed, and point-in-time.

WHY THIS MODULE EXISTS, AND A CORRECTION IT ENCODES. NFL-01's first pass
concluded that coordinator identity did not exist in free structured data. That
conclusion was WRONG, and the way it was wrong is worth keeping: `nfldata`'s
games.csv carries only `away_coach`/`home_coach`, and that one absence was
generalised into "no OC/DC/play-caller anywhere". One file was checked and a
whole category was written off.

Wikipedia maintains `Template:<Team> staff` for every club, carrying the entire
staff by role -- head coach, offensive coordinator, defensive coordinator,
special teams, passing/run game coordinators and position coaches. Measured
2026-09-11: 30 of 32 templates resolved on a single pass (the two misses were
transient rate limits, not missing pages), yielding HC 30/30, OC 30/30, DC
29/30.

POINT-IN-TIME IS THE PART THAT MATTERS. The MediaWiki API serves any page AS OF
A TIMESTAMP via revision history, so this source satisfies the hardest of the
fifteen inventory fields -- historical point-in-time reconstructability -- which
most intelligence sources fail. Verified against the Bengals:

    asked as of 2021-09-01 -> OC Brian Callahan, DC Lou Anarumo
    asked as of 2024-09-01 -> OC Dan Pitcher,    DC Lou Anarumo
    asked as of 2026-09-11 -> OC Dan Pitcher,    DC Al Golden

That is real coaching history reconstructed from revisions, not a snapshot.

ONE REQUEST, NOT THIRTY-TWO. The current-staff snapshot is fetched as a single
multi-title query. This is not only politeness toward a free source -- it was
forced by measurement. Fetching the 32 pages individually inside the full
capture produced 23 SOURCE_FAILED out of 32 through rate limiting; adding retry
with backoff recovered it only to 27 of 32. Batching returns 32 of 32 in one
request, every time, and the failure mode disappears rather than being retried
around. A per-page burst against a source generously letting us read it was a
self-inflicted wound.

A batched query also yields ONE artifact describing one instant, which is more
honest than 32 artifacts fetched seconds apart and later treated as simultaneous.

Point-in-time reconstruction still goes one page at a time, because MediaWiki
does not accept rvstart/rvdir alongside multiple titles. That is a research
operation run rarely, not the daily path.

THE HONEST CAVEAT, AND IT IS NOT SMALL. A revision timestamp is WHEN WIKIPEDIA
WAS EDITED, not when the appointment happened. Wikipedia lags reality, usually
by hours and occasionally by much longer. So this reconstructs "what was
publicly recorded at time T", not "what was true at time T". For a pregame
cutoff that is arguably the RIGHT semantic -- it is a proxy for public knowledge
-- but it must never be described as the appointment date. Both timestamps are
therefore archived separately: `asked_as_of` and the revision's own timestamp.
A capture asking for today can legitimately return a six-week-old revision, so
freshness is recorded rather than assumed.

LICENCE. Wikipedia text is CC BY-SA 4.0 and the MediaWiki Action API is
provided for programmatic use. Attribution and share-alike are real obligations
that attach to any redistribution of derived text, not footnotes.

WHAT THIS MODULE DOES NOT DO. It archives raw wikitext. It does not parse into
production state, does not score, and does not decide who calls plays -- see
nfl/docs/PLAY_CALLER.md for why the play-caller question is genuinely harder
than coordinator identity and is not answered by this source alone.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import CHECKED_AND_FOUND, Fetched, SOURCE_FAILED

SOURCE_ID = "coaching_staff_wikipedia"
API = "https://en.wikipedia.org/w/api.php"

RETRIES = 4
BACKOFF_BASE = 1.5
# MediaWiki accepts up to 50 titles per query. All 32 clubs fit in ONE request.
BATCH_LIMIT = 50

TEAMS = (
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
    "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
    "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
    "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
    "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers",
    "Los Angeles Rams", "Miami Dolphins", "Minnesota Vikings",
    "New England Patriots", "New Orleans Saints", "New York Giants",
    "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers",
    "San Francisco 49ers", "Seattle Seahawks", "Tampa Bay Buccaneers",
    "Tennessee Titans", "Washington Commanders",
)


def _batch_url(pages: list[str]) -> str:
    """One query for every club's current staff template."""
    titles = "%7C".join(requests.utils.quote(p) for p in pages)
    return (f"{API}?action=query&prop=revisions&titles={titles}"
            "&rvprop=content%7Ctimestamp%7Cids&rvslots=main&format=json"
            "&redirects=1&formatversion=2")


def _point_in_time_url(page: str, as_of: str) -> str:
    """One page, as Wikipedia recorded it at or before `as_of`.

    Single-title by necessity: MediaWiki refuses rvstart/rvdir with multiple
    titles. `rvdir=older` is what makes this point-in-time rather than nearest.
    """
    return (f"{API}?action=query&prop=revisions"
            f"&titles={requests.utils.quote(page)}"
            "&rvprop=content%7Ctimestamp%7Cids&rvslots=main&format=json"
            f"&rvlimit=1&redirects=1&rvstart={requests.utils.quote(as_of)}"
            "&rvdir=older")


def _context(as_of, **extra):
    base = {
        "asked_as_of": as_of,
        "licence": "CC BY-SA 4.0 (Wikipedia); attribution and share-alike "
                   "attach to redistributed derived text",
        "carries": "HC, OC, DC, special teams and position coaches",
        "caveat": "revision timestamp is when Wikipedia was EDITED, not when "
                  "the appointment happened",
    }
    base.update(extra)
    return base


def _validate(record: Fetched) -> Fetched:
    """A 200 carrying no revision is not a successful observation."""
    if record.outcome == CHECKED_AND_FOUND and record.body:
        if b'"revisions"' not in record.body:
            record.outcome = SOURCE_FAILED
            record.failure_reason = (
                "HTTP 200 but the response carries no `revisions` array: the "
                "template page is missing or was renamed. Not a transport "
                "failure, and not evidence that the team has no staff page."
            )
            record.body = None
    return record


def capture(
    session: Optional[requests.Session] = None,
    as_of: Optional[str] = None,
    teams=TEAMS,
    pause: float = 0.4,
) -> list[Fetched]:
    """Archive coaching staff as raw wikitext.

    Without `as_of`: one batched request covering every club.
    With `as_of`: one request per club, reconstructing what Wikipedia recorded
    at that instant.

    Both the requested instant and each revision's own timestamp reach the
    manifest, because conflating them would turn "when we asked" into a false
    claim about when a coordinator changed.
    """
    session = session or requests.Session()
    pages = [f"Template:{team} staff" for team in teams]

    if as_of is None:
        records = []
        for start in range(0, len(pages), BATCH_LIMIT):
            chunk = pages[start:start + BATCH_LIMIT]
            record = None
            for attempt in range(RETRIES):
                record = fetch(
                    SOURCE_ID, f"staff_all_teams_batch{start // BATCH_LIMIT}",
                    _batch_url(chunk),
                    context=_context(
                        as_of, teams=list(teams[start:start + BATCH_LIMIT]),
                        page_count=len(chunk), mode="batched current snapshot"),
                    session=session,
                )
                if record.outcome == CHECKED_AND_FOUND:
                    break
                if attempt < RETRIES - 1:
                    time.sleep(BACKOFF_BASE * (2 ** attempt))
            record.context["attempts"] = attempt + 1
            records.append(_validate(record))
        return records

    records = []
    for team, page in zip(teams, pages):
        record = None
        for attempt in range(RETRIES):
            record = fetch(
                SOURCE_ID, f"staff_{team.replace(' ', '_')}",
                _point_in_time_url(page, as_of),
                context=_context(as_of, team=team, wiki_page=page,
                                 mode="point-in-time reconstruction"),
                session=session,
            )
            if record.outcome == CHECKED_AND_FOUND:
                break
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))
        record.context["attempts"] = attempt + 1
        records.append(_validate(record))
        time.sleep(pause)
    return records
