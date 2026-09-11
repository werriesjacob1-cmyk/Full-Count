#!/usr/bin/env python3
"""ESPN public NFL feeds: schedule/venue state, injury state, per-game context.

WHAT THIS COVERS in the archival priority order: official-adjacent practice and
injury state (2), roster/depth context (8), and the market's own consensus
numbers as a cross-check on the FanDuel capture (1).

HOST NOTE, AND A CORRECTED MISTAKE, 2026-09-11. An early probe recorded
`site.api.espn.com` as refusing automation with HTTP 403 while
`site.web.api.espn.com` served the same API. That conclusion was WRONG, and the
way it was wrong is worth keeping: the 403 was caused by the probe sending a
bare `User-Agent: Mozilla/5.0`. ESPN's edge refuses that truncated browser
string and serves the same endpoint perfectly to the archiver's own honest,
self-identifying UA. Measured both ways on the same minute: bare-Mozilla 403,
archiver UA 200.

The lesson generalises past ESPN: a 403 gathered under a UA you would not
actually ship is not evidence that a source is unavailable, and recording it as
such would have retired a working feed for a season. `site.web.api` is still the
host used, because it is the one with continuous verified service in this
codebase, and `host_probe` below keeps measuring the other per run rather than
trusting either conclusion.

ESPN IS NOT AUTHORITATIVE FOR INACTIVES. Its injury feed is a useful, timely
aggregation, not the official league report -- the official report is captured
separately in official_nfl.py. When the two disagree, BOTH are archived and the
disagreement is preserved; nothing here resolves it. Contradiction is
information, and the pipeline must not silently adopt whichever source it
happened to read last.
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import CHECKED_AND_FOUND, Fetched

SOURCE_ID = "espn_nfl"

BASE = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl"
# Kept as a recorded, deliberately-attempted alternative, not dead code: the
# capture probes it so each run carries its own evidence of whether the refusal
# still holds on the network actually running the job.
BASE_REFUSED_2026_09_11 = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"


def pregame_event_ids(body: bytes) -> list[dict]:
    """Events whose status is still `pre`. Discovery only.

    Pregame is what matters for prospective archival: a game already in
    progress has begun leaking its own outcome into every feed that describes
    it, which is exactly the information a point-in-time reconstruction must
    not be able to see.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        state = (((event.get("status") or {}).get("type") or {}).get("state"))
        out.append({
            "event_id": event.get("id"),
            "short_name": event.get("shortName"),
            "date": event.get("date"),
            "state": state,
        })
    return out


def capture(
    session: Optional[requests.Session] = None,
    summary_event_limit: Optional[int] = None,
    include_completed: bool = False,
) -> list[Fetched]:
    session = session or requests.Session()
    records: list[Fetched] = []

    scoreboard = fetch(
        SOURCE_ID, "scoreboard", f"{BASE}/scoreboard",
        context={"feed": "scoreboard", "carries": "schedule, venue, status, broadcast"},
        session=session,
    )
    records.append(scoreboard)

    # Whole-league injury/practice state. Large (measured ~8.9 MB) and carries
    # its own server-side `timestamp`, which is why source_timestamp_key is set:
    # the feed's own notion of freshness is provenance we should not have to
    # infer from when we happened to call it.
    records.append(fetch(
        SOURCE_ID, "injuries_league_wide", f"{BASE}/injuries",
        context={"feed": "injuries", "authority": "aggregator, NOT the official "
                 "league injury report -- see official_nfl.py"},
        session=session, source_timestamp_key="timestamp",
    ))

    # Preserve whether the refused host is still refusing, per run.
    records.append(fetch(
        SOURCE_ID, "host_probe_site_api_espn", f"{BASE_REFUSED_2026_09_11}/scoreboard",
        context={"role": "recorded probe of the host that returned 403 on "
                 "2026-09-11; not relied upon"},
        session=session,
    ))

    events = pregame_event_ids(scoreboard.body) if scoreboard.outcome == CHECKED_AND_FOUND else []
    wanted = [e for e in events if include_completed or e["state"] == "pre"]
    if summary_event_limit is not None:
        wanted = wanted[:summary_event_limit]

    for event in wanted:
        event_id = event["event_id"]
        records.append(fetch(
            SOURCE_ID, f"summary_{event_id}", f"{BASE}/summary?event={event_id}",
            context={"event_id": event_id, "short_name": event["short_name"],
                     "date": event["date"], "state": event["state"],
                     "carries": "venue/surface, per-team injuries, consensus "
                                "spread and total, matchup projection"},
            session=session,
        ))
    return records
