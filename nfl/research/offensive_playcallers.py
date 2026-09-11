#!/usr/bin/env python3
"""Curated evidence for ACTUAL offensive play-caller duty.

WHY THIS EXISTS. Coordinator title and actual play-calling duty are different
football facts. FULL COUNT must preserve that distinction instead of assuming
OC == play caller.

FIRST SNAPSHOT. ESPN/NFL Nation published an all-32 offensive play-caller survey
on 2026-09-03. The rows below preserve only the factual team/caller/role claim,
not ESPN's prose or performance analysis.

POINT-IN-TIME CAVEAT. This source was discovered manually during the 2026-09-11
research session without a raw capture envelope preserving the exact first
observed timestamp. Publication time is known, but publication time is NOT an
appointment/effective time and it is NOT a substitute for FULL COUNT's own
observed_at. Therefore these rows are research evidence only and fail closed for
PIT model use.

Future prospective capture must preserve the source bytes/reference plus
observed_at and, when separately evidenced, effective_at. Historical callers
should be reconstructed from contemporaneous source assertions rather than
back-filled from present-day truth.
"""
from __future__ import annotations

from copy import deepcopy


SOURCE_ID_2026 = "espn_nfl_nation_2026_playcallers"

SOURCES = {
    SOURCE_ID_2026: {
        "publisher": "ESPN / NFL Nation",
        "title": "Who calls plays for every NFL team in 2026? What to know",
        "url": (
            "https://www.espn.com/nfl/story/_/id/49711157/"
            "nfl-playcallers-32-teams-mike-mcdaniel-sean-mcvay-mike-mccarthy"
        ),
        # ESPN renders Sep 3, 2026, 06:00 AM ET. September is EDT (UTC-4).
        "source_published_at": "2026-09-03T10:00:00Z",
        "publication_time_semantics": "source_published_at",
        "effective_time_caveat": (
            "The article publication time is not the appointment/effective time "
            "for the duty. An earlier team announcement or coach statement may "
            "establish effective_at separately."
        ),
        "observation_caveat": (
            "The source was discovered manually on 2026-09-11, but an exact "
            "first observed_at timestamp was not preserved in a raw FULL COUNT "
            "capture envelope. It is therefore not PIT-admissible."
        ),
        "scope": "all 32 teams; offensive play caller; season-entry snapshot",
        "evidence_regime": "historical_research",
    },
}


# team codes follow nflverse conventions. Washington is WAS, even though ESPN's
# article navigation displays WSH.
_2026 = (
    ("ARI", "Mike LaFleur", "head_coach"),
    ("ATL", "Tommy Rees", "offensive_coordinator"),
    ("BAL", "Declan Doyle", "offensive_coordinator"),
    ("BUF", "Joe Brady", "head_coach"),
    ("CAR", "Brad Idzik", "offensive_coordinator"),
    ("CHI", "Ben Johnson", "head_coach"),
    ("CIN", "Zac Taylor", "head_coach"),
    ("CLE", "Todd Monken", "head_coach"),
    ("DAL", "Brian Schottenheimer", "head_coach"),
    ("DEN", "Davis Webb", "offensive_coordinator"),
    ("DET", "Drew Petzing", "offensive_coordinator"),
    ("GB", "Matt LaFleur", "head_coach"),
    ("HOU", "Nick Caley", "offensive_coordinator"),
    ("IND", "Shane Steichen", "head_coach"),
    ("JAX", "Liam Coen", "head_coach"),
    ("KC", "Andy Reid", "head_coach"),
    ("LV", "Klint Kubiak", "head_coach"),
    ("LAC", "Mike McDaniel", "offensive_coordinator"),
    ("LAR", "Sean McVay", "head_coach"),
    ("MIA", "Bobby Slowik", "offensive_coordinator"),
    ("MIN", "Kevin O'Connell", "head_coach"),
    ("NE", "Josh McDaniels", "offensive_coordinator"),
    ("NO", "Kellen Moore", "head_coach"),
    ("NYG", "Matt Nagy", "offensive_coordinator"),
    ("NYJ", "Frank Reich", "offensive_coordinator"),
    ("PHI", "Sean Mannion", "offensive_coordinator"),
    ("PIT", "Mike McCarthy", "head_coach"),
    ("SF", "Kyle Shanahan", "head_coach"),
    ("SEA", "Brian Fleury", "offensive_coordinator"),
    ("TB", "Zac Robinson", "offensive_coordinator"),
    ("TEN", "Brian Daboll", "offensive_coordinator"),
    ("WAS", "David Blough", "offensive_coordinator"),
)


def _row(team: str, caller: str, role: str) -> dict:
    source = SOURCES[SOURCE_ID_2026]
    return {
        "season": 2026,
        "team": team,
        "phase": "offense",
        "play_caller": caller,
        "caller_role": role,
        "source_id": SOURCE_ID_2026,
        "source_url": source["url"],
        "source_published_at": source["source_published_at"],
        # Unknown by construction for this manually discovered historical
        # research snapshot. Never fill these with publication time.
        "observed_at": None,
        "effective_at": None,
        "pit_admissible": False,
        "pit_block_reason": (
            "Exact FULL COUNT first observed_at was not preserved in a raw "
            "capture; effective_at is also not established by this snapshot."
        ),
        "evidence_regime": "historical_research",
        "claim_scope": "season_entry_snapshot",
    }


SNAPSHOTS = {
    2026: tuple(_row(*values) for values in _2026),
}


def snapshot_for_season(season: int) -> list[dict]:
    """Return a defensive copy of the curated season snapshot.

    Unknown seasons return an empty list rather than silently carrying a caller
    backward or forward across seasons.
    """
    return deepcopy(list(SNAPSHOTS.get(int(season), ())))
