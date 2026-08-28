#!/usr/bin/env python3
"""check_board_freshness.py -- staleness check for the BOARD itself.

2026-08-28 incident. docs/live.json was 2 minutes old while
docs/data.json.generated_at was 10.11 HOURS old. The existing live
watchdog (check_live_freshness.py) was green the entire time and correctly
so: prices really were updating every five minutes. What it cannot see is
that the model probabilities, lineup assumptions and candidate set those
prices were being painted onto came from 06:31:57Z, because three
consecutive Dashboard Refresh runs (07:11, 12:35, 14:43 UTC) had died in
_clean_candidate_rows() on a single unidentifiable candidate.

A fresh price overlay on a ten-hour-old board is not a fresh board. It is
a stale board wearing a fresh timestamp, which is worse than an obviously
broken one because it looks actionable.

So this checks the board's OWN generation timestamp -- not git commit age,
which advances on every live price push and would have read "seconds old"
throughout the entire incident.

Like the live checker, this NEVER writes. docs/data.json keeps exactly one
semantic writer (dashboard-refresh.yml -> build_dashboard.py). This module
reads and reports; the workflow decides what to do about it.

SLA. Dashboard Refresh runs roughly hourly during the pregame window, so a
single missed tick is not an incident. 180 minutes is three missed cycles:
past that, the model/lineup basis is materially obsolete relative to a
slate that is still being priced, and a recovery dispatch is warranted.
"""
import json
import sys
from datetime import datetime, timezone

SLA_MINUTES = 180
FRESHNESS_FIELD = "generated_at"
# Beyond this the board is not merely late, it is untrustworthy as an
# actionable surface, and the product must say so rather than keep
# rendering normal cards. Consumed by build_payload()'s board_freshness
# block and asserted by test.
HARD_FAIL_MINUTES = 360


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def board_age_minutes(payload, *, now=None):
    """Minutes since the board was BUILT. None when unknowable.

    Unknowable is never treated as fresh: an absent or malformed
    generated_at is exactly what a half-written payload looks like.
    """
    generated = _parse_iso((payload or {}).get(FRESHNESS_FIELD))
    if generated is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - generated).total_seconds() / 60.0


def board_freshness(payload, *, now=None, sla_minutes=SLA_MINUTES,
                    hard_fail_minutes=HARD_FAIL_MINUTES):
    """Machine-readable board state. The product renders from this, so it
    reports the age it measured rather than a bare boolean."""
    age = board_age_minutes(payload, now=now)
    if age is None:
        return {"state": "unknown", "age_minutes": None,
                "stale": True, "actionable": False,
                "reason": "board carries no parseable generated_at"}
    if age > hard_fail_minutes:
        return {"state": "hard_stale", "age_minutes": round(age, 1),
                "stale": True, "actionable": False,
                "reason": f"board basis is {age/60:.1f}h old (hard limit "
                          f"{hard_fail_minutes/60:.0f}h); prices may be current "
                          f"but the model and lineup basis are not"}
    if age > sla_minutes:
        return {"state": "stale", "age_minutes": round(age, 1),
                "stale": True, "actionable": False,
                "reason": f"board basis is {age:.0f} min old (SLA {sla_minutes})"}
    return {"state": "fresh", "age_minutes": round(age, 1),
            "stale": False, "actionable": True, "reason": None}


def main(argv=None):
    argv = argv or sys.argv[1:]
    path = argv[0] if argv else "docs/data.json"
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BOARD STALE: {path} unreadable ({exc})")
        return 1
    rep = board_freshness(payload)
    print(f"board {rep['state']}: age={rep['age_minutes']} min "
          f"(SLA {SLA_MINUTES}, hard {HARD_FAIL_MINUTES})"
          + (f" -- {rep['reason']}" if rep["reason"] else ""))
    return 1 if rep["stale"] else 0


if __name__ == "__main__":
    sys.exit(main())
