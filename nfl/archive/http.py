#!/usr/bin/env python3
"""One HTTP helper for every raw NFL source. Never raises; always records.

FAIL-OPEN IS A HARD REQUIREMENT. A capture error must never block MLB
production or customer output, so nothing here propagates an exception to the
orchestrator. Every outcome -- success, timeout, DNS failure, 403, HTML where
JSON was expected -- comes back as a Fetched record carrying its own verdict.

THE DISTINCTION THAT MATTERS. A 403 and an empty-but-valid payload are not the
same fact, and collapsing them is how "we checked and there was no news"
silently comes to mean "we never found out". So:

    transport/status/structure problem -> SOURCE_FAILED, with the reason
    understood payload, nothing in it -> CHECKED_AND_NONE_FOUND
    understood payload with content    -> CHECKED_AND_FOUND

A 403 in particular is ambiguous in a sandboxed environment: it may be the
origin refusing automation, or an egress policy refusing the host. The reason
string says so rather than guessing, because the two imply opposite follow-ups
(drop the source vs. run it somewhere else).
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from nfl.archive.provenance import (
    CHECKED_AND_FOUND, CHECKED_AND_NONE_FOUND, SOURCE_FAILED, Fetched, utcnow,
)

# Identifies the archiver honestly. Not a spoof of a browser we are not:
# a source operator reading their logs should be able to tell what this is
# and who to contact, which is also what makes the traffic defensible.
UA = {
    "User-Agent": (
        "FullCount-NFL-Archiver/1 (+https://github.com/werriesjacob1-cmyk/Full-Count) "
        "python-requests"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
}

TIMEOUT = 30


def _ambiguity_note(status: int) -> str:
    if status in (401, 403, 407):
        return (
            f"HTTP {status} -- AMBIGUOUS: cannot distinguish the origin refusing "
            "automated access from an egress/network policy refusing the host. "
            "Requires review before concluding the source is unusable."
        )
    return f"HTTP {status}"


def fetch(
    source_id: str,
    artifact: str,
    url: str,
    expect: str = "json",
    context: Optional[dict] = None,
    session: Optional[requests.Session] = None,
    timeout: int = TIMEOUT,
    source_timestamp_key: Optional[str] = None,
) -> Fetched:
    """Fetch one artifact. Returns a Fetched record; never raises.

    `expect` is "json", "html", or "bytes" and controls only what counts as a
    STRUCTURAL failure -- the body is stored as received either way. Nothing
    here parses a payload for meaning; `source_timestamp_key` reads at most one
    top-level timestamp field, because "when did the source say this was true?"
    is provenance, not interpretation.
    """
    observed_at = utcnow()
    context = dict(context or {})
    getter = (session or requests).get
    try:
        response = getter(url, headers=UA, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 -- fail-open by contract
        return Fetched(
            source_id=source_id, artifact=artifact, url=url,
            outcome=SOURCE_FAILED, observed_at=observed_at, context=context,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    body = response.content
    if response.status_code != 200:
        return Fetched(
            source_id=source_id, artifact=artifact, url=url,
            outcome=SOURCE_FAILED, observed_at=observed_at, context=context,
            http_status=response.status_code,
            failure_reason=_ambiguity_note(response.status_code),
        )

    source_timestamp = None
    if expect == "json":
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return Fetched(
                source_id=source_id, artifact=artifact, url=url,
                outcome=SOURCE_FAILED, observed_at=observed_at, context=context,
                http_status=200,
                failure_reason=(
                    f"expected JSON, got unparseable body ({exc}). "
                    f"First 120 bytes: {body[:120]!r}"
                ),
            )
        if source_timestamp_key and isinstance(parsed, dict):
            value = parsed.get(source_timestamp_key)
            if isinstance(value, (str, int, float)):
                source_timestamp = str(value)
        empty = parsed in ({}, [], None)
    elif expect == "html":
        empty = len(body.strip()) == 0
    else:
        empty = len(body) == 0

    return Fetched(
        source_id=source_id, artifact=artifact, url=url,
        outcome=CHECKED_AND_NONE_FOUND if empty else CHECKED_AND_FOUND,
        observed_at=observed_at, http_status=200,
        body=body if not empty else None,
        source_timestamp=source_timestamp, context=context,
    )
