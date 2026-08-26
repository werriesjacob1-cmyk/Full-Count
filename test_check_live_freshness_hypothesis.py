#!/usr/bin/env python3
"""test_check_live_freshness_hypothesis.py -- property-based coverage for
dashboard/check_live_freshness.py using Hypothesis (Part 9 of the FULL
COUNT Claude tooling setup, 2026-08-26).

Not a replacement for test_live_freshness_watchdog.py's example-based
checks (boundary at exactly SLA_MINUTES, fail-closed on missing/unparseable
timestamps, the real 2026-08-25/2026-08-26 incident magnitudes) -- this
covers the same module's real invariants across the FULL random input
space those examples can only sample a few points of. Kept small and
targeted (Part 9's own instruction: "do not force property testing onto
every trivial module") -- one well-scoped file for the one P0-relevant
merge/freshness primitive currently Python-side and simple enough to
model precisely, not a blanket sweep.

    /tmp/mlbvenv/bin/python3 test_check_live_freshness_hypothesis.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

from hypothesis import given, settings, strategies as st

from dashboard import check_live_freshness as clf

NOW = datetime(2026, 8, 26, 20, 0, 0, tzinfo=timezone.utc)

# Ages in minutes, both sides of every real threshold this module defines
# (SLA_MINUTES=15) and the P0 doc's incident-tier boundary (30) -- generous
# range so Hypothesis can and will find true boundary cases, not just the
# ones a human happened to write down as examples.
_age_minutes = st.floats(min_value=0, max_value=24 * 60, allow_nan=False, allow_infinity=False)


@given(age=_age_minutes)
@settings(max_examples=300)
def test_staleness_is_monotonic_in_age(age):
    """INVARIANT: an older timestamp is never LESS stale than a newer one.
    If this ever regressed (e.g. a sign flip in the age computation), a
    genuinely ancient live.json could start reading as fresher than a
    barely-old one -- the exact class of bug that would make the customer-
    facing freshness bar lie in the dangerous direction."""
    ts = (NOW - timedelta(minutes=age)).isoformat()
    stale, _ = clf.is_stale({"updated_at": ts}, now=NOW)
    stale_plus_one_min, _ = clf.is_stale(
        {"updated_at": (NOW - timedelta(minutes=age + 1)).isoformat()}, now=NOW)
    # A strictly older timestamp can never be LESS stale than this one.
    assert stale_plus_one_min or not stale


@given(age=_age_minutes)
@settings(max_examples=300)
def test_staleness_minutes_matches_is_stale_boolean(age):
    """INVARIANT: is_stale()'s boolean and staleness_minutes()'s numeric
    age must never disagree about which side of SLA_MINUTES a timestamp
    falls on -- two entry points computing the same fact must stay
    consistent with each other by construction, not by coincidence."""
    ts = (NOW - timedelta(minutes=age)).isoformat()
    stale, _ = clf.is_stale({"updated_at": ts}, now=NOW)
    computed_age, _ = clf.staleness_minutes({"updated_at": ts}, now=NOW)
    assert computed_age is not None
    assert stale == (computed_age > clf.SLA_MINUTES)


@given(age=_age_minutes)
@settings(max_examples=300)
def test_never_checked_reports_stale_regardless_of_age_argument(age):
    """INVARIANT: a document with NO updated_at at all is always stale no
    matter what `now` is compared against -- fail-closed must not become
    fail-open just because the clock used for comparison happens to be
    far in the future or the past."""
    fake_now = NOW - timedelta(minutes=age)
    stale, reason = clf.is_stale({}, now=fake_now)
    assert stale is True
    assert "missing" in reason or "unparseable" in reason


# NOTE (2026-08-26): channel_staleness() -- the P0 per-channel freshness
# split (game-state/settlement vs. sportsbook price, reported
# independently) -- lives on the still-unmerged claude/live-lifecycle-p0-01
# branch, not yet on main as of this tooling branch's base commit. An
# equivalent property test for it (channel booleans must never disagree
# with is_stale()'s single-channel contract; a channel with no recorded
# check must fail closed independent of every other channel's freshness)
# is a natural follow-up once that branch merges -- intentionally not
# added here to avoid this tooling branch depending on unmerged code.


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
