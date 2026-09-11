#!/usr/bin/env python3
"""Minimal coach/player media DISCOVERY, plus an honest record of what is not obtained.

SCOPE DISCIPLINE. The NFL Intelligence Engine -- recurring search across all 32
teams, press-conference review every day, source tiering, claim extraction with
provenance -- is a future subsystem. It is NOT built here. This module is the
minimum plumbing the raw-archival deliverable needs: note which official media
index pages exist and respond, and record precisely which media artifacts were
NOT obtained and why.

WHY RECORDING A NON-FETCH IS THE POINT. The requirement is that a missing
expected source must never be indistinguishable from "no relevant news". The
failure mode is not a crash; it is a coverage manifest that quietly omits press
conferences entirely, so that six weeks later nobody can tell whether a coach
said nothing or whether we never looked. Every row below therefore appears in
the manifest with an explicit outcome, including the rows that were deliberately
not fetched.

WHAT IS DELIBERATELY NOT DONE, AND WHY -- none of this is a capability gap to
paper over:

  * No transcript, caption track, or audio/video transcription is retrieved.
    Whether automated retrieval and machine processing of official team video
    and its caption tracks is PERMITTED has not been established. Terms status
    is UNKNOWN-REQUIRES-REVIEW, and an unknown is not an implied yes.
  * No authentication is used, attempted, or worked around; no paywall,
    access control, or DRM is circumvented. A subscription that lets a person
    watch something does not imply a right to automate it.
  * No quote, statement, or summary is generated. Fabricating a transcript
    would be worse than having none, because a fabricated quote is
    indistinguishable from evidence downstream.

HONEST UNAVAILABLE BEATS FABRICATED COVERAGE. That is the whole design of this
module.
"""
from __future__ import annotations

from typing import Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import Fetched, UNAVAILABLE_BY_POLICY

SOURCE_ID = "media_discovery"

# Official league index pages that are plain public reads. Discovery only: we
# record that the page responded and keep its bytes. We do not follow it into
# video assets.
INDEX_PAGES = (
    ("league_news_index", "https://www.nfl.com/news/",
     "official league news index"),
    ("league_videos_index", "https://www.nfl.com/videos/",
     "official league video index -- index metadata only, no media retrieval"),
)

# Each row becomes an UNAVAILABLE_BY_POLICY manifest entry. The reason strings
# are the deliverable: they tell a future session exactly what question to
# answer before this coverage can exist.
NOT_ATTEMPTED = (
    ("head_coach_press_conference_transcripts",
     "Automated retrieval/processing of official team press-conference video or "
     "caption tracks is UNKNOWN-REQUIRES-REVIEW. Not attempted. Jacob's "
     "requirement that coach and player press conferences be reviewed daily is "
     "recorded as an unmet, blocked requirement -- not silently dropped."),
    ("offensive_coordinator_press_conference_transcripts",
     "Same terms question as head-coach media. Not attempted."),
    ("defensive_coordinator_press_conference_transcripts",
     "Same terms question as head-coach media. Not attempted."),
    ("player_press_conference_transcripts",
     "Same terms question as head-coach media. Not attempted."),
    ("official_team_site_media_per_team",
     "32 team media ecosystems each with their own terms. No per-team terms "
     "review has been done, so no per-team automated read is performed."),
    ("beat_reporter_practice_observations",
     "Requires recurring web search plus a source-tier model and per-outlet "
     "terms review. That is the NFL Intelligence Engine, which NFL-01 "
     "deliberately does not build."),
    ("audio_video_transcription",
     "Would require retrieving media this mission has not established the right "
     "to retrieve. Not attempted."),
    ("pff_and_other_paywalled_charting",
     "Paywalled. Not fetched, and must never be presented as a free source."),
    ("next_gen_stats_api",
     "https://nextgenstats.nfl.com/api/... returned HTTP 401 on 2026-09-11 "
     "under two different User-Agents: it requires authentication. Not "
     "automatable without credentials whose use has not been authorised. "
     "Distinct from pro-football-reference.com, which returned 403 with a real "
     "HTML error body under both UAs -- that is an origin refusing automation, "
     "and its terms are UNKNOWN-REQUIRES-REVIEW rather than simply closed."),
)


def capture(session: Optional[requests.Session] = None) -> list[Fetched]:
    session = session or requests.Session()
    records = [
        fetch(SOURCE_ID, artifact, url, expect="html",
              context={"carries": carries, "scope": "discovery only"},
              session=session)
        for artifact, url, carries in INDEX_PAGES
    ]
    records += [
        Fetched(source_id=SOURCE_ID, artifact=artifact, url="",
                outcome=UNAVAILABLE_BY_POLICY,
                context={"reason": reason,
                         "terms_status": "UNKNOWN-REQUIRES-REVIEW"})
        for artifact, reason in NOT_ATTEMPTED
    ]
    return records
