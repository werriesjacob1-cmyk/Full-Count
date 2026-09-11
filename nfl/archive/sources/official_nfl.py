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

GAME-DAY INACTIVES. NFL Football Operations documents that each club's inactive
list is part of the Game Day Administration Report exchanged at the 90-minute
officiating meeting. NFL.com exposes a public /inactives/ index whose linked
game-specific /news/ articles carry the actual player lists. We archive the
index as raw HTML and, when it exposes same-origin inactive-report links, archive
each linked article as raw HTML in the same capture. A reachable index with zero
current report links is evidence of that page state at that instant, NOT
permission to infer that nobody is inactive. Player/team parsing, game binding,
and football semantic validation remain downstream.

WHAT IS NOT ATTEMPTED HERE, AND WHY. No press-conference transcript, caption
track, or audio/video transcription is fetched. Whether automated retrieval of
those is permitted has NOT been established, and this module will not
manufacture coverage by guessing. Those artifacts are recorded as
UNAVAILABLE_BY_POLICY by media_discovery.py so the gap is visible in the
coverage manifest instead of looking like an absence of news.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import Fetched

SOURCE_ID = "official_nfl"
INACTIVES_INDEX_URL = "https://www.nfl.com/inactives/"


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if str(tag).lower() != "a":
            return
        for key, value in attrs:
            if str(key).lower() == "href" and value:
                self.hrefs.append(str(value))
                break


def _inactive_report_urls(body: bytes) -> tuple[str, ...]:
    """Discover linked official NFL inactive-report articles.

    This is URL discovery only, not football semantic parsing. Query strings
    and fragments are stripped so tracking parameters cannot duplicate one
    archived report. Only same-origin NFL.com /news/ paths containing the
    literal inactive-report token are eligible.
    """
    parser = _HrefParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:
        return ()

    seen = set()
    out = []
    for href in parser.hrefs:
        absolute = urljoin(INACTIVES_INDEX_URL, href)
        parts = urlsplit(absolute)
        if parts.scheme.lower() != "https":
            continue
        if parts.netloc.lower() != "www.nfl.com":
            continue
        path = parts.path or ""
        if not path.startswith("/news/"):
            continue
        if "inactive" not in path.lower():
            continue
        normalized = urlunsplit(("https", "www.nfl.com", path, "", ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return tuple(out)


def _inactive_report_artifact(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"inactive_report_{digest}"


PAGES = (
    # (artifact, url, what it carries)
    ("injuries_report", "https://www.nfl.com/injuries/",
     "official injury/practice report -- DNP/Limited/Full designations"),
    ("inactives", INACTIVES_INDEX_URL,
     "official game-day inactive report index -- discovers linked game reports"),
    ("transactions", "https://www.nfl.com/transactions/",
     "roster-changing events: signings, releases, IR, elevations"),
    ("scores", "https://www.nfl.com/scores/",
     "official schedule and game status"),
    ("standings", "https://www.nfl.com/standings/",
     "team records, an input to expected game script"),
)


def capture(session: Optional[requests.Session] = None) -> list[Fetched]:
    session = session or requests.Session()
    records = [
        fetch(
            SOURCE_ID,
            artifact,
            url,
            expect="html",
            context={"carries": carries, "stored": "raw HTML, unparsed"},
            session=session,
        )
        for artifact, url, carries in PAGES
    ]

    inactive_index = next(
        record for record in records if record.artifact == "inactives"
    )
    if inactive_index.body:
        report_urls = _inactive_report_urls(inactive_index.body)
        inactive_index.context["discovered_report_count"] = len(report_urls)
        inactive_index.context["report_discovery_status"] = (
            "completed from archived index bytes"
        )
        inactive_index.context["report_discovery_rule"] = (
            "same-origin https://www.nfl.com/news/* links whose path "
            "contains 'inactive'; query/fragment stripped"
        )

        for url in report_urls:
            records.append(
                fetch(
                    SOURCE_ID,
                    _inactive_report_artifact(url),
                    url,
                    expect="html",
                    context={
                        "carries": (
                            "official game-specific inactive report; "
                            "player/team semantics intentionally unparsed"
                        ),
                        "stored": "raw HTML, unparsed",
                        "discovered_from": INACTIVES_INDEX_URL,
                        "discovery": "linked from current inactive-report index",
                    },
                    session=session,
                )
            )
    else:
        inactive_index.context["discovered_report_count"] = 0
        inactive_index.context["report_discovery_status"] = (
            "skipped because inactive-report index bytes were unavailable"
        )

    return records
