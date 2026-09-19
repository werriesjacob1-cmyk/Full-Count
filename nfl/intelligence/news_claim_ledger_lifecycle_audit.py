#!/usr/bin/env python3
"""Read-only audit of two lifecycle behaviors PR #146's schema implies but
does not itself build: cross-run duplicate-claim merging, and
correction/retraction resolution into a "current claims" view.

PR #146's `nfl.intelligence.news_claim_ledger` defines the claim SHAPE
(`correction_of`, `corroborations`, `contradictions` fields) and a
single-batch fail-closed check (`validate_claims` raises on a duplicate
`claim_id` WITHIN one batch). It does not itself provide:

1. a merge/upsert function for combining two INDEPENDENT ingestion runs'
   claim lists into one deduplicated ledger (there is no persisted ledger
   store at all yet -- this is a real, disclosed gap, not something this
   audit assumes was silently solved elsewhere), or
2. a "current claims" query that excludes a claim once a later claim
   corrects it via `correction_of`.

This module builds both as small, independently-tested, read-only functions
-- imported and composed against PR #146's real `validate_claim`, never
editing `news_claim_ledger.py` itself.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nfl.intelligence.news_claim_ledger import NewsClaimLedgerError, validate_claim

# Fields expected to legitimately differ between two independent
# observations of the identical underlying fact (deterministic claim_id
# means "same fact", not "byte-identical record").
_VOLATILE_FIELDS = frozenset({"observed_at"})


def _content_fingerprint(claim: Mapping[str, Any]) -> tuple:
    return tuple(
        (key, claim[key]) for key in sorted(claim) if key not in _VOLATILE_FIELDS
    )


def merge_claims_by_id(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge one ledger's existing claims with a new ingestion run's claims,
    keyed by deterministic `claim_id`.

    A `claim_id` seen in both `existing` and `incoming` is treated as a
    genuine re-observation of the identical fact ONLY if every non-volatile
    field is identical; the first (existing) observation is kept and the
    duplicate is dropped. If the same `claim_id` carries DIFFERENT content
    across the two lists, that is a real integrity violation (the
    deterministic-id assumption -- same id implies same fact -- has been
    broken, e.g. by a hash-input change or a genuine data correction that
    should have used `correction_of` with a NEW id instead) and this
    function fails closed rather than silently picking one side.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for claim in existing:
        validate_claim(claim)
        cid = claim["claim_id"]
        merged[cid] = dict(claim)
        order.append(cid)

    new_claim_count = 0
    duplicate_claim_count = 0
    for claim in incoming:
        validate_claim(claim)
        cid = claim["claim_id"]
        if cid not in merged:
            merged[cid] = dict(claim)
            order.append(cid)
            new_claim_count += 1
            continue
        # Same claim_id seen again -- must be the same fact.
        if _content_fingerprint(claim) != _content_fingerprint(merged[cid]):
            raise NewsClaimLedgerError(
                f"claim_id {cid!r} repeated with materially different content -- "
                "deterministic-id integrity violated; a real correction must "
                "use a NEW claim_id with correction_of set, not reuse this one"
            )
        duplicate_claim_count += 1

    return {
        "claims": [merged[cid] for cid in order],
        "total_claim_count": len(order),
        "new_claim_count": new_claim_count,
        "duplicate_claim_count": duplicate_claim_count,
    }


def current_claims(claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Split a claim population into CURRENT (not superseded) and
    SUPERSEDED (referenced by some other claim's `correction_of`) subsets.

    Fails closed if a `correction_of` points at a `claim_id` that is not
    present in `claims` -- an orphan correction is a real data problem
    (missing context), not something to silently ignore.
    """
    by_id: dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        validate_claim(claim)
        by_id[claim["claim_id"]] = claim

    superseded_ids: set[str] = set()
    for claim in claims:
        target = claim.get("correction_of")
        if target is None:
            continue
        if target not in by_id:
            raise NewsClaimLedgerError(
                f"claim {claim['claim_id']!r} has correction_of={target!r}, "
                "which is not present in this claim population (orphan "
                "correction reference)"
            )
        superseded_ids.add(target)

    current = [c for c in claims if c["claim_id"] not in superseded_ids]
    superseded = [c for c in claims if c["claim_id"] in superseded_ids]
    return {
        "current": current,
        "superseded": superseded,
        "current_count": len(current),
        "superseded_count": len(superseded),
    }
