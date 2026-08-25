#!/usr/bin/env python3
"""test_live_freshness_watchdog.py -- coverage for dashboard/check_live_freshness.py,
the 2026-08-25 incident-hardening watchdog's SLA-checking logic. The
workflow integration itself (dispatching dashboard-live.yml via `gh workflow
run`) can't be unit tested here -- this covers the one piece of real, testable
logic: is docs/live.json's own updated_at within the freshness SLA.

    /tmp/mlbvenv/bin/python3 test_live_freshness_watchdog.py
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


from dashboard import check_live_freshness as clf

NOW = datetime(2026, 8, 25, 2, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


head("1. well within SLA -- fresh")
state = {"updated_at": iso(NOW - timedelta(minutes=3))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is False, f"3 minutes old, SLA {clf.SLA_MINUTES}m -- must be fresh, got stale={stale}", reason)

head("2. exactly at the SLA boundary -- NOT stale (strictly greater-than only)")
state = {"updated_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is False, f"exactly {clf.SLA_MINUTES}m old must not be stale (boundary is exclusive)", reason)

head("3. one second past the SLA boundary -- stale")
state = {"updated_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES, seconds=1))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is True, f"{clf.SLA_MINUTES}m0s1 old must be stale", reason)

head("4. well past SLA -- the real incident shape (~60 minutes stale)")
state = {"updated_at": iso(NOW - timedelta(minutes=60))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is True, "60 minutes old (the real 2026-08-25 incident magnitude) must be stale", reason)

head("5. FAIL-CLOSED: missing updated_at is treated as stale, never assumed fresh")
stale, reason = clf.is_stale({}, now=NOW)
check(stale is True, "an empty/missing live state must be treated as stale (unknown != fresh)", reason)

head("6. FAIL-CLOSED: unparseable timestamp is treated as stale")
stale, reason = clf.is_stale({"updated_at": "not-a-timestamp"}, now=NOW)
check(stale is True, "an unparseable updated_at must be treated as stale", reason)

head("7. naive (no-offset) timestamps are assumed UTC, not rejected")
naive_recent = (NOW - timedelta(minutes=2)).replace(tzinfo=None).isoformat()
stale, reason = clf.is_stale({"updated_at": naive_recent}, now=NOW)
check(stale is False, "a naive but recent timestamp must still be read correctly as fresh", reason)

head("8. staleness_minutes() reports a real numeric age, not just a boolean")
state = {"updated_at": iso(NOW - timedelta(minutes=42))}
age, reason = clf.staleness_minutes(state, now=NOW)
check(age is not None and abs(age - 42.0) < 0.01, f"expected age~=42.0, got {age}", reason)

head("9. staleness_minutes() returns None for a missing field, not a fabricated number")
age, reason = clf.staleness_minutes({}, now=NOW)
check(age is None, f"missing field must report age=None, not a guessed number, got {age}", reason)

print()
passed = sum(1 for ok, _, _ in _results if ok)
total = len(_results)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
