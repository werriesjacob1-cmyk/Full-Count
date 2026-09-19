#!/usr/bin/env python3
"""Backward-compatible re-export shim.

This module originally (PR #151, `NFL-NEWS-CLAIM-LEDGER-AUDIT-20260919`)
built `merge_claims_by_id` and `current_claims` as a read-only audit of two
lifecycle behaviors PR #146's schema implied but did not itself provide: a
merge/upsert function for combining two independent ingestion runs' claim
lists into one deduplicated ledger, and a "current claims" query that
excludes a claim once a later claim corrects it via `correction_of`.

`NFL-NEWS-BRAIN-IDENTITY-TEMPORAL-REPAIR-20260919` (the repair pass following
that audit) promoted both functions into `news_claim_ledger.py` itself as
first-class, canonical API: that module already owns the claim schema and
its temporal-safety functions, so a caller wiring real ingestion into a
persisted ledger should not need to import from a module whose name and
docstring describe it as a one-off investigation. This file is kept, unedited
in its public surface, purely so PR #151's own tests keep passing against
the same import path -- new code should import `merge_claims_by_id` and
`current_claims` directly from `nfl.intelligence.news_claim_ledger`.
"""
from __future__ import annotations

from nfl.intelligence.news_claim_ledger import (  # noqa: F401 -- re-export for compatibility
    current_claims,
    merge_claims_by_id,
)

__all__ = ["current_claims", "merge_claims_by_id"]
