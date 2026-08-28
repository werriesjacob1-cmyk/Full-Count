#!/usr/bin/env python3
"""test_live_freshness_watchdog.py -- coverage for dashboard/check_live_freshness.py,
the 2026-08-25 incident-hardening watchdog's SLA-checking logic. The
workflow integration itself (dispatching dashboard-live.yml via `gh workflow
run`) can't be unit tested here -- this covers the one piece of real, testable
logic: are docs/live.json's REQUIRED CHANNEL clocks within the freshness
SLA. (2026-08-28 P0 follow-up: the gate moved off the single global
updated_at, which reconciliation could advance while a real upstream was
dead -- see test_live_health_channels.py. These fixtures now set the
channel clocks the decision actually reads.)

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
state = {"prices_checked_at": iso(NOW - timedelta(minutes=3)), "grades_checked_at": iso(NOW - timedelta(minutes=3))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is False, f"3 minutes old, SLA {clf.SLA_MINUTES}m -- must be fresh, got stale={stale}", reason)

head("2. exactly at the SLA boundary -- NOT stale (strictly greater-than only)")
state = {"prices_checked_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES)), "grades_checked_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is False, f"exactly {clf.SLA_MINUTES}m old must not be stale (boundary is exclusive)", reason)

head("3. one second past the SLA boundary -- stale")
state = {"prices_checked_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES, seconds=1)), "grades_checked_at": iso(NOW - timedelta(minutes=clf.SLA_MINUTES, seconds=1))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is True, f"{clf.SLA_MINUTES}m0s1 old must be stale", reason)

head("4. well past SLA -- the real incident shape (~60 minutes stale)")
state = {"prices_checked_at": iso(NOW - timedelta(minutes=60)), "grades_checked_at": iso(NOW - timedelta(minutes=60))}
stale, reason = clf.is_stale(state, now=NOW)
check(stale is True, "60 minutes old (the real 2026-08-25 incident magnitude) must be stale", reason)

head("5. FAIL-CLOSED: a missing required-channel clock is stale, never assumed fresh")
stale, reason = clf.is_stale({}, now=NOW)
check(stale is True, "an empty/missing live state must be treated as stale (unknown != fresh)", reason)

head("6. FAIL-CLOSED: unparseable timestamp is treated as stale")
stale, reason = clf.is_stale({"prices_checked_at": "not-a-timestamp", "grades_checked_at": "not-a-timestamp"}, now=NOW)
check(stale is True, "an unparseable channel clock must be treated as stale", reason)

head("7. naive (no-offset) timestamps are assumed UTC, not rejected")
naive_recent = (NOW - timedelta(minutes=2)).replace(tzinfo=None).isoformat()
stale, reason = clf.is_stale({"prices_checked_at": naive_recent, "grades_checked_at": naive_recent}, now=NOW)
check(stale is False, "a naive but recent timestamp must still be read correctly as fresh", reason)

head("8. staleness_minutes() reports a real numeric age, not just a boolean")
state = {"prices_checked_at": iso(NOW - timedelta(minutes=42)), "grades_checked_at": iso(NOW - timedelta(minutes=42))}
age, reason = clf.staleness_minutes(state, now=NOW)
check(age is not None and abs(age - 42.0) < 0.01, f"expected age~=42.0, got {age}", reason)

head("9. staleness_minutes() returns None for a missing field, not a fabricated number")
age, reason = clf.staleness_minutes({}, now=NOW)
check(age is None, f"missing field must report age=None, not a guessed number, got {age}", reason)

head("10. channel_staleness() reports game-state/settlement and pricing SEPARATELY "
     "(P0 lifecycle audit, 2026-08-26) -- the real Colt Keith/live-freshness "
     "incident showed grading and pricing can be behind by different amounts; "
     "one combined blob hides that. Grading fresh, pricing stale.")
state = {
    "grades_checked_at": iso(NOW - timedelta(minutes=2)),
    "prices_checked_at": iso(NOW - timedelta(minutes=43)),
}
channels = clf.channel_staleness(state, now=NOW)
check(channels["game_state_and_settlement"][1] is False,
      "grading channel (2m old) is fresh", str(channels["game_state_and_settlement"]))
check(channels["sportsbook_price"][1] is True,
      "pricing channel (43m old) is stale", str(channels["sportsbook_price"]))

head("11. channel_staleness() fail-closed: a channel field entirely absent "
     "(older live.json shape) reports stale, never silently fresh")
channels = clf.channel_staleness({}, now=NOW)
check(channels["game_state_and_settlement"] == (None, True),
      "missing grades_checked_at reports (None, stale=True)", str(channels["game_state_and_settlement"]))
check(channels["sportsbook_price"] == (None, True),
      "missing prices_checked_at reports (None, stale=True)", str(channels["sportsbook_price"]))

print()
passed = sum(1 for ok, _, _ in _results if ok)
total = len(_results)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
