#!/usr/bin/env python3
"""Official NFL.com surfaces. PRIORITY 2 and 4 of the archival order.

THE OFFICIAL INJURY/PRACTICE REPORT IS THE AUTHORITATIVE ARTIFACT for the
Wednesday -> Thursday -> Friday DNP/Limited/Full trajectory, and that
trajectory is one of the few NFL information states that is genuinely
unreconstructable after the fact: the page shows the CURRENT designation, and
the prior day's designation is simply gone. Archiving it daily is the only way
a later experiment can honestly know what was knowable on Friday.

STORED AS RAW HTML, UNPARSED, DELIBERATELY. These are server-rendered pages
whose markup will change. Parsing now would bake today's DOM into a season of
archives and lose anything the parser did not anticipate. The bytes are kept;
extraction is a later, re-runnable step against preserved input. This is the
"do not let perfect schema design delay raw preservation" rule applied
literally.

WHAT IS NOT ATTEMPTED HERE, AND WHY. No press-conference transcript, caption
track, or audio/video transcription is fetched. Whether automated retrieval of
those is permitted has NOT been established, and this module will not
manufacture coverage by guessing. Those artifacts are recorded as
UNAVAILABLE_BY_POLICY by media_discovery.py so the gap is visible in the
coverage manifest instead of looking like an absence of news.
"""
from __future__ import annotations

from typing import Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import Fetched

SOURCE_ID = "official_nfl"

PAGES = (
    # (artifact, url, what it carries)
    ("injuries_report", "https://www.nfl.com/injuries/",
     "official injury/practice report -- DNP/Limited/Full designations"),
    ("transactions", "https://www.nfl.com/transactions/",
     "roster-changing events: signings, releases, IR, elevations"),
    ("scores", "https://www.nfl.com/scores/",
     "official schedule and game status"),
    ("standings", "https://www.nfl.com/standings/",
     "team records, an input to expected game script"),
)


def capture(session: Optional[requests.Session] = None) -> list[Fetched]:
    session = session or requests.Session()
    return [
        fetch(SOURCE_ID, artifact, url, expect="html",
              context={"carries": carries, "stored": "raw HTML, unparsed"},
              session=session)
        for artifact, url, carries in PAGES
    ]
