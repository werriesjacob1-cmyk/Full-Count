#!/usr/bin/env python3
"""The capture envelope: what was fetched, from where, and when it was seen.

THE CONTRACT. A raw archive is worthless as evidence unless a later reader can
answer, without trusting anyone's memory:

    WHAT did we ask for?          -> url, request method, source_id
    WHAT came back?               -> raw bytes, byte length, sha256, http status
    WHEN did we observe it?       -> observed_at (when THIS process saw it)
    WHEN did the source say it    -> source_timestamp, when the payload itself
    was true?                        carries one; None when it does not
    WHAT code observed it?        -> capturer_version
    DID it work?                  -> outcome, plus the reason when it did not

`observed_at` and `source_timestamp` are separate fields on purpose and must
never be collapsed. `observed_at` is the only one this process can vouch for,
and it is the field a point-in-time reconstruction has to key on: information
first observed after a prediction cutoff cannot be used at that cutoff, no
matter what timestamp the payload claims for itself.

NO CANONICAL CANDIDATE IDENTITY APPEARS HERE, DELIBERATELY. Raw archival is
decoupled from candidate identity so that nothing a later identity design
decides can contradict a season of already-written archives. Raw archives can
be reparsed. A source state never fetched is gone forever.

OUTCOMES. These are the coverage states the completeness watchdog consumes.
A missing expected source must never be indistinguishable from a source that
was checked and legitimately had nothing to report:

    CHECKED_AND_FOUND       fetched, parsed as the expected container, non-empty
    CHECKED_AND_NONE_FOUND  fetched and understood; the source genuinely has
                            nothing for this entity right now
    SOURCE_FAILED           transport, status, or structure failure -- we do
                            NOT know what the source would have said
    PARTIAL                 bytes were preserved, but expected semantic coverage
                            is incomplete or ambiguous; never evidence of absence
    NOT_CHECKED             never attempted this run (budget, ordering, or an
                            upstream dependency failed first)
    UNAVAILABLE_BY_POLICY   deliberately not fetched: terms unclear, requires
                            authentication, or automation not established as
                            permitted. Recorded, not silently skipped.
    STALE                   served from a prior capture; no fresh observation
    UNRESOLVED_CONTRADICTION  sources disagree and the disagreement is
                            preserved rather than resolved by write order

ABSENCE OF EVIDENCE MUST NOT MASQUERADE AS EVIDENCE OF ABSENCE. That is the
entire reason SOURCE_FAILED and CHECKED_AND_NONE_FOUND are different values.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

# Bumped when the meaning of an archived field changes, so a later reparse can
# tell which contract produced a given directory. Additive layout changes do
# not need a bump; a changed field MEANING does.
CAPTURE_CONTRACT_VERSION = 1

CHECKED_AND_FOUND = "CHECKED_AND_FOUND"
CHECKED_AND_NONE_FOUND = "CHECKED_AND_NONE_FOUND"
SOURCE_FAILED = "SOURCE_FAILED"
PARTIAL = "PARTIAL"
NOT_CHECKED = "NOT_CHECKED"
UNAVAILABLE_BY_POLICY = "UNAVAILABLE_BY_POLICY"
STALE = "STALE"
UNRESOLVED_CONTRADICTION = "UNRESOLVED_CONTRADICTION"

OUTCOMES = frozenset((
    CHECKED_AND_FOUND, CHECKED_AND_NONE_FOUND, SOURCE_FAILED, PARTIAL,
    NOT_CHECKED, UNAVAILABLE_BY_POLICY, STALE, UNRESOLVED_CONTRADICTION,
))

# Outcomes that assert the source was successfully understood. Only these may
# ever support a claim that the source had nothing to say.
CONCLUSIVE_OUTCOMES = frozenset((CHECKED_AND_FOUND, CHECKED_AND_NONE_FOUND))


def utcnow() -> str:
    """ISO-8601 UTC to whole seconds, with an explicit Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass
class Fetched:
    """One raw observation of one source artifact.

    `body` holds the bytes exactly as received and is NOT written into the
    manifest -- it is written to its own artifact file, and the manifest
    records its digest. That keeps the manifest small and readable while
    leaving the payload byte-exact and independently verifiable.
    """
    source_id: str
    artifact: str
    url: str
    outcome: str
    observed_at: str = field(default_factory=utcnow)
    http_status: Optional[int] = None
    body: Optional[bytes] = None
    source_timestamp: Optional[str] = None
    failure_reason: Optional[str] = None
    # Free-form, source-specific, NON-INTERPRETIVE context: which event id,
    # which team, which tab. Enough to find this artifact again later without
    # parsing it. Never a derived football judgement.
    context: dict = field(default_factory=dict)
    capturer_version: int = CAPTURE_CONTRACT_VERSION

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"unknown capture outcome {self.outcome!r}; "
                f"expected one of {sorted(OUTCOMES)}"
            )
        if self.outcome in (CHECKED_AND_FOUND, PARTIAL) and not self.body:
            raise ValueError(
                f"{self.source_id}/{self.artifact}: {self.outcome} asserts bytes "
                "were preserved, but no body was captured. Use SOURCE_FAILED "
                "when the fetch did not produce one -- a missing payload must "
                "never read as a successful or partial observation."
            )
        if self.outcome == SOURCE_FAILED and not self.failure_reason:
            raise ValueError(
                f"{self.source_id}/{self.artifact}: SOURCE_FAILED requires a "
                "failure_reason. An unexplained failure cannot be triaged."
            )

    def manifest_entry(self) -> dict:
        entry = {k: v for k, v in asdict(self).items() if k != "body"}
        entry["byte_length"] = len(self.body) if self.body is not None else 0
        entry["sha256"] = sha256_hex(self.body) if self.body else None
        return entry


def coverage_summary(records: list["Fetched"]) -> dict:
    """Count outcomes per source, for the coverage manifest.

    Reported per source rather than pooled: a run in which the sportsbook
    succeeded and the injury report failed is NOT 50% healthy, it is a run
    with a specific known hole, and the hole is the actionable part.
    """
    per_source: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = per_source.setdefault(record.source_id, {})
        bucket[record.outcome] = bucket.get(record.outcome, 0) + 1
    return {
        "by_source": per_source,
        "totals": {
            outcome: sum(b.get(outcome, 0) for b in per_source.values())
            for outcome in sorted(OUTCOMES)
            if any(outcome in b for b in per_source.values())
        },
        "sources_with_failures": sorted(
            s for s, b in per_source.items() if b.get(SOURCE_FAILED)
        ),
        "sources_with_partial_observation": sorted(
            s for s, b in per_source.items() if b.get(PARTIAL)
        ),
        # A source that produced NO conclusive observation at all is the
        # dangerous case: downstream, its silence is indistinguishable from
        # "nothing to report" unless it is named here.
        "sources_with_no_conclusive_observation": sorted(
            s for s, b in per_source.items()
            if not any(b.get(o) for o in CONCLUSIVE_OUTCOMES)
        ),
    }
