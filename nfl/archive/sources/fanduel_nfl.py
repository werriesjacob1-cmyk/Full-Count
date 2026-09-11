#!/usr/bin/env python3
"""Raw FanDuel NFL sportsbook state. PRIORITY 1 of the archival order.

WHY FIRST. A price is the most perishable thing on this list and the only one
that tells us what the market knew. Play-by-play can be reacquired next year;
Sunday morning's receiving-yards line cannot be reconstructed from anything.

WHAT WAS VERIFIED EMPIRICALLY, 2026-09-10/11, AND WHAT IT COST
The MLB module odds_fanduel.py is NOT reused here and NOT modified. Its
transport shape (regional hosts, the public `_ak` application key, the
`content-managed-page` / `event-page` pair) does carry over, and that was
CHECKED rather than assumed. Two things did not carry over:

  1. TAB TOKENS ARE SLUGS, NOT THE NUMERIC IDS THE LAYOUT PUBLISHES.
     `layout.tabs` is keyed by numeric id ('217' -> 'Passing Props') and
     contains no slug field. Passing `tab=217` DOES NOT ERROR. It silently
     returns a 9-market default payload that looks like a perfectly healthy
     response and contains none of the passing props. Measured: all eight
     numeric prop-tab ids returned byte-identical market sets. Only
     title-derived slugs ('passing-props') return the real markets.
     `d-st` works; `dst` and `defense` silently return the default.
     This is the single most dangerous property of this feed. A wrong token
     produces plausible data, not an error, so `baseline_market_ids` below
     exists to make the failure loud instead of invisible.

  2. THE TAB SET IS SPORT-SPECIFIC. NFL's prop tabs are passing-props,
     receiving-props, rushing-props, td-scorer-props, scoring, d-st,
     game-specials, popular. None of MLB's tabs (pitcher-props, batter-props,
     innings, lasers, moonshots) exist here.

NO INTERPRETATION HAPPENS IN THIS MODULE. It stores payloads. It does not map
market types to Full Count stats, does not build candidates, does not attach a
canonical identity, and does not score anything. The market-type census that
informed the notes above lives in nfl/docs/, not in code.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import CHECKED_AND_FOUND, Fetched, NOT_CHECKED, PARTIAL

SOURCE_ID = "fanduel_nfl"

# Public client key from FanDuel's own web app, identical to the one MLB uses.
# Not a credential: it identifies the calling application, not a user, and no
# account is involved. Duplicated rather than imported so that this module
# creates no dependency from NFL onto an MLB production module.
AK = "FhMFpcPWXMeyZxOx"
HOSTS = ("sbapi.nj", "sbapi.pa", "sbapi.az", "sbapi.co")

# Tabs carrying player-prop and scoring markets, as slugs. Verified live.
# Quarter/half tabs are omitted: they are derivative game markets, and the
# archival budget is better spent on more vintages of the player props.
PROP_TABS = (
    "popular",
    "passing-props",
    "receiving-props",
    "rushing-props",
    "td-scorer-props",
    "scoring",
    "d-st",
    "game-specials",
)


def _url(host: str, path: str) -> str:
    return f"https://{host}.sportsbook.fanduel.com/api/{path}"


def _fetch_first_healthy_host(
    artifact: str, path: str, context: dict, session: requests.Session,
) -> Fetched:
    """Try each region in turn. One region being down is not an outage.

    The LAST failure is what gets reported when every region fails, and the
    record notes how many were tried so a single-region blip cannot be
    mistaken for a feed-wide outage.
    """
    attempts = []
    last = None
    for host in HOSTS:
        record = fetch(
            SOURCE_ID, artifact, _url(host, path),
            context={**context, "host": host}, session=session,
        )
        if record.outcome == CHECKED_AND_FOUND:
            record.context["hosts_tried"] = len(attempts) + 1
            return record
        attempts.append(f"{host}: {record.failure_reason or record.outcome}")
        last = record
    if last is not None:
        last.failure_reason = (
            f"all {len(HOSTS)} FanDuel regions failed -- " + " | ".join(attempts)
        )
        last.context["hosts_tried"] = len(HOSTS)
        return last
    # Unreachable with a non-empty HOSTS, but a silent None would be worse.
    return Fetched(
        source_id=SOURCE_ID, artifact=artifact, url=path, outcome=NOT_CHECKED,
        context={**context, "reason": "no hosts configured"},
    )


def discover_events(body: bytes) -> list[dict]:
    """Event ids and names from a root payload. Discovery only, no pricing.

    Kept separate from capture so the orchestrator can archive the raw root
    payload AND know which events to walk, without this module deciding
    anything about them. Events whose name lacks ' @ ' are futures, awards and
    season-long markets, not games.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    events = ((payload.get("attachments") or {}).get("events") or {})
    if not isinstance(events, dict):
        return []
    out = []
    for event in events.values():
        if not isinstance(event, dict):
            continue
        name = event.get("name") or ""
        event_id = event.get("eventId")
        if " @ " not in name or event_id in (None, ""):
            continue
        out.append({
            "event_id": event_id, "name": name, "open_date": event.get("openDate"),
        })
    return sorted(out, key=lambda e: (str(e["open_date"]), str(e["event_id"])))


def _market_ids(body: Optional[bytes]) -> frozenset:
    if not body:
        return frozenset()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return frozenset()
    markets = (payload.get("attachments") or {}).get("markets")
    return frozenset(markets) if isinstance(markets, dict) else frozenset()


def _slugify_tab_title(value: str) -> str:
    """Convert FanDuel's visible tab title into the empirically working token.

    Numeric layout ids are deliberately ignored elsewhere. The live feed was
    measured returning a plausible default payload for tab=217 while the
    title-derived token passing-props returned the actual passing markets.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug


def _tab_titles(body: Optional[bytes]) -> list[str]:
    """Read human tab titles from either dict- or list-shaped layout.tabs.

    FanDuel layout is not an identity contract, so this is deliberately
    tolerant of title/name/label keys. Discovery failure simply returns [] and
    the verified fallback slugs remain available.
    """
    if not body:
        return []
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    tabs = (payload.get("layout") or {}).get("tabs")
    values = tabs.values() if isinstance(tabs, dict) else tabs if isinstance(tabs, list) else []
    out = []
    for tab in values:
        if isinstance(tab, str):
            title = tab
        elif isinstance(tab, dict):
            title = tab.get("title") or tab.get("name") or tab.get("label")
        else:
            title = None
        if isinstance(title, str) and title.strip():
            out.append(title.strip())
    return out


_NON_RESEARCH_TAB_TOKENS = frozenset({
    "quick-bets",
    "parlays",
    "same-game-parlay",
})


def _research_relevant_tab(slug: str) -> bool:
    """Reject UI/period navigation without guessing away unknown market tabs.

    Live probe 2026-09-11 showed FanDuel layout discovery exposing quarter/half
    tabs plus quick-bets/parlays. Fetching them doubles archive work without
    adding pregame player-prop evidence. The filter is intentionally NARROW:
    unknown future tabs remain discoverable rather than being silently dropped.
    """
    if slug in _NON_RESEARCH_TAB_TOKENS:
        return False
    if re.fullmatch(r"\d+(?:st|nd|rd|th)-(?:quarter|half)", slug):
        return False
    return True


def _tab_slugs_for_event(
    baseline_body: Optional[bytes], fallback_tabs: Iterable[str] = PROP_TABS,
) -> tuple[str, ...]:
    """Union layout-discovered title slugs with empirically verified fallbacks.

    This solves two opposite failure modes at once:
      * hard-coded slugs alone can miss a newly exposed player-prop family;
      * layout alone is incomplete (d-st and game-specials were observed
        working while absent from the layout list).

    Numeric layout ids are NEVER emitted as request tokens.
    """
    ordered = []
    seen = set()
    for token in fallback_tabs:
        token = str(token).strip()
        if token and token not in seen:
            ordered.append(token)
            seen.add(token)
    for title in _tab_titles(baseline_body):
        slug = _slugify_tab_title(title)
        if (slug and not slug.isdigit() and _research_relevant_tab(slug)
                and slug not in seen):
            ordered.append(slug)
            seen.add(slug)
    return tuple(ordered)


def _classify_tab_payload(record: Fetched, baseline_ids: frozenset) -> Fetched:
    """Mark FanDuel's silent-default success as PARTIAL, never FOUND.

    HTTP and JSON succeeded, so SOURCE_FAILED would discard useful evidence.
    But the requested tab's semantic coverage is not established, so
    CHECKED_AND_FOUND would falsely certify the exact market family we may
    have missed. PARTIAL is the honest middle state and is non-conclusive.
    """
    if record.outcome != CHECKED_AND_FOUND or not baseline_ids:
        return record
    echoed = _market_ids(record.body) == baseline_ids
    record.context["tab_echoed_no_tab_baseline"] = echoed
    if echoed:
        record.outcome = PARTIAL
        record.context["warning"] = (
            "FanDuel silently returned the no-tab default market set for this "
            "tab token. Bytes were archived, but requested-tab coverage is "
            "ambiguous and MUST NOT be treated as CHECKED_AND_FOUND."
        )
    return record


def capture(
    session: Optional[requests.Session] = None,
    event_limit: Optional[int] = None,
    tabs: Iterable[str] = PROP_TABS,
) -> list[Fetched]:
    """Archive the NFL root feed and every prop tab of every discovered event."""
    session = session or requests.Session()
    records: list[Fetched] = []

    root = _fetch_first_healthy_host(
        "root_nfl_page",
        f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={AK}",
        {"feed": "nfl root"}, session,
    )
    records.append(root)
    if root.outcome != CHECKED_AND_FOUND:
        # Discovery failed, so every event is NOT_CHECKED rather than absent.
        # Downstream must not read this run as "no NFL games today".
        records.append(Fetched(
            source_id=SOURCE_ID, artifact="events_not_enumerated",
            url=root.url, outcome=NOT_CHECKED,
            context={"reason": "root discovery failed; event list unknown",
                     "root_outcome": root.outcome},
        ))
        return records

    events = discover_events(root.body)
    if event_limit is not None:
        events = events[:event_limit]

    # The no-tab payload is the exact thing an ignored tab token returns, so it
    # is captured once per event as the control for detecting that failure.
    for event in events:
        event_id = event["event_id"]
        base_context = {"event_id": event_id, "event_name": event["name"],
                        "open_date": event["open_date"]}
        baseline = _fetch_first_healthy_host(
            f"event_{event_id}_no_tab",
            f"event-page?eventId={event_id}&_ak={AK}",
            {**base_context, "tab": None, "role": "control for ignored-tab detection"},
            session,
        )
        records.append(baseline)
        baseline_ids = _market_ids(baseline.body)

        event_tabs = _tab_slugs_for_event(baseline.body, fallback_tabs=tabs)
        for tab in event_tabs:
            record = _fetch_first_healthy_host(
                f"event_{event_id}_tab_{tab}",
                f"event-page?eventId={event_id}&tab={tab}&_ak={AK}",
                {**base_context, "tab": tab}, session,
            )
            record = _classify_tab_payload(record, baseline_ids)
            record.context["tab_discovery"] = (
                "layout_or_verified_fallback" if tab not in tuple(tabs)
                else "verified_fallback_or_layout"
            )
            records.append(record)
    return records
