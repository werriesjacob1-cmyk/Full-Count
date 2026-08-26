#!/usr/bin/env python3
"""check_live_freshness.py -- pure staleness check for docs/live.json,
used by .github/workflows/live-freshness-watchdog.yml.

2026-08-25 incident: docs/live.json went stale for roughly an hour after
Dashboard Live Update run #271 (which itself succeeded cleanly and pushed
at 01:17:44Z -- verified via that run's own job step timings) because
GitHub's own cron scheduler created no new run for dashboard-live.yml for
the following hour, while its concurrency group sat completely idle the
whole time. A separate, also-real mechanism found in the same
investigation: dashboard-live.yml's grading+repricing steps have regrown
past their 15-minute timeout budget (measured ~15m combined vs 5m19s five
days earlier), causing a large fraction of scheduled ticks to get
genuinely cancelled. Both failure modes manifest identically here: a
live.json that hasn't advanced past the freshness SLA. This module does
not care which mechanism caused it -- it exists to detect the SYMPTOM
reliably so the watchdog workflow can trigger recovery regardless of root
cause.

Deliberately does NOT write to docs/live.json -- that file keeps exactly
one semantic writer path (dashboard-live.yml's own merge_live_files.py
step). This module only reads and reports.

FRESHNESS SLA: scheduled cadence is 5 minutes (dashboard-live.yml's own
cron); 15 minutes (3x cadence) is the threshold past which staleness stops
being "the next tick hasn't landed yet" and starts being a real degraded
state worth an automatic recovery attempt.
"""
import json
import sys
from datetime import datetime, timezone

SLA_MINUTES = 15
FRESHNESS_FIELD = "updated_at"


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def staleness_minutes(live_state, now=None):
    """Returns (age_minutes: float or None, reason: str). age_minutes is
    None when the freshness field is missing/unparseable -- an unknown age
    is treated as a degraded state by the caller, same "unknown is not
    fresh" convention recommendation.freshness_check() already uses."""
    now = now or datetime.now(timezone.utc)
    dt = _parse_iso((live_state or {}).get(FRESHNESS_FIELD))
    if dt is None:
        return None, f"'{FRESHNESS_FIELD}' missing or unparseable"
    age = (now - dt).total_seconds() / 60.0
    return age, f"'{FRESHNESS_FIELD}' is {age:.1f} minutes old"


def is_stale(live_state, now=None, sla_minutes=SLA_MINUTES):
    age, reason = staleness_minutes(live_state, now=now)
    if age is None:
        return True, reason
    return age > sla_minutes, reason


# Per-channel freshness (P0 lifecycle audit, 2026-08-26): `updated_at` alone
# answers "did SOMETHING change," not "which channel is actually behind."
# The real incident this module was built for (and the 2026-08-26 Colt
# Keith incident that reconfirmed it) both trace to the same root cause --
# dashboard-live.yml not being invoked on schedule -- which stalls the
# grading/game-state channel and the pricing channel together, so `main()`
# still gates its exit code on the one combined `updated_at` SLA (the
# recovery action, re-dispatching that same workflow, is identical either
# way). This function exists purely for OBSERVABILITY: when a human or a
# future automated report looks at *why* something is stale, "grading
# hasn't checked in 43 minutes, pricing 4 minutes" is a materially
# different, more actionable fact than one undifferentiated "stale."
CHANNELS = {
    "game_state_and_settlement": "grades_checked_at",
    "sportsbook_price": "prices_checked_at",
}


def channel_staleness(live_state, now=None, sla_minutes=SLA_MINUTES):
    """Returns {channel_name: (age_minutes_or_None, stale_bool)} for each
    named channel field in CHANNELS. A channel whose field is entirely
    absent (an older live.json predating that field) reports (None, True)
    -- unknown is never treated as fresh, same convention staleness_minutes()
    already uses for the combined check."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for channel, field in CHANNELS.items():
        dt = _parse_iso((live_state or {}).get(field))
        if dt is None:
            out[channel] = (None, True)
        else:
            age = (now - dt).total_seconds() / 60.0
            out[channel] = (age, age > sla_minutes)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "docs/live.json"
    try:
        with open(path) as f:
            live_state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"STALE: could not read/parse {path}: {e}")
        sys.exit(2)
    stale, reason = is_stale(live_state)
    for channel, (age, chan_stale) in channel_staleness(live_state).items():
        age_text = f"{age:.1f}m old" if age is not None else "never checked"
        tag = "STALE" if chan_stale else "fresh"
        print(f"  [{tag}] {channel}: {age_text}")
    if stale:
        print(f"STALE: {reason} (SLA {SLA_MINUTES}m)")
        sys.exit(1)
    print(f"FRESH: {reason} (SLA {SLA_MINUTES}m)")
    sys.exit(0)


if __name__ == "__main__":
    main()
