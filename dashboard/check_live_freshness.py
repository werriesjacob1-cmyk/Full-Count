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

# 2026-08-28 P0 follow-up -- CLOCK MASKING.
#
# This module used to gate its health decision on the single global
# `updated_at`. That was survivable while only the price and grade channels
# wrote it, because both stall together when dashboard-live.yml stops
# running. Reconciliation broke that assumption: it runs inside the same
# workflow, succeeds on its own, and would have advanced `updated_at` every
# five minutes while sportsbook pricing or game-state observation was dead.
# A healthy observer must never make an unhealthy source channel look
# healthy, so the gate is now per-channel and `updated_at` is not it.
#
# SEMANTICS, stated once so no field can quietly stand in for another:
#
#   prices_checked_at        a real sportsbook observation ATTEMPT completed
#   grades_checked_at        a real MLB game-state/settlement ATTEMPT completed
#   reconciliation.checked_at  publication-vs-authoritative reconciliation ran
#   *_updated_at             the corresponding FACTS actually changed
#   updated_at               "something in the document changed" -- retained
#                            for the overlay's own recency ordering, and
#                            deliberately NOT a health signal
#
# Only the first two are REQUIRED for the product to be healthy: they are
# the two upstreams a customer's price and settlement depend on.
# Reconciliation is reported, never substituted -- it answers a different
# question and cannot vouch for either upstream.
REQUIRED_CHANNELS = {
    "sportsbook_price": "prices_checked_at",
    "game_state_and_settlement": "grades_checked_at",
}
RECONCILIATION_CHANNEL = "reconciliation"


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


def _channel_age(live_state, field, now):
    """Age of one named channel clock. `reconciliation` is nested."""
    if field == RECONCILIATION_CHANNEL:
        raw = ((live_state or {}).get("reconciliation") or {}).get("checked_at")
    else:
        raw = (live_state or {}).get(field)
    dt = _parse_iso(raw)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 60.0


def staleness_minutes(live_state, now=None):
    """Age of the OLDEST required channel -- the honest summary number.

    Deliberately the worst channel rather than the newest write anywhere in
    the document: a document is only as fresh as the least-recently
    verified thing a customer depends on. Returns (age_minutes or None,
    reason); None means at least one required channel has never reported,
    which the caller treats as degraded, never as fresh.
    """
    now = now or datetime.now(timezone.utc)
    ages = {}
    for channel, field in REQUIRED_CHANNELS.items():
        ages[channel] = _channel_age(live_state, field, now)
    missing = [c for c, a in ages.items() if a is None]
    if missing:
        return None, ("required channel(s) never reported: "
                      + ", ".join(sorted(missing)))
    worst_channel = max(ages, key=lambda c: ages[c])
    age = ages[worst_channel]
    return age, f"oldest required channel {worst_channel!r} is {age:.1f} minutes old"


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
CHANNELS = dict(REQUIRED_CHANNELS)
# Reported alongside the required channels so a human can see that
# reconciliation is running -- but it is NOT in REQUIRED_CHANNELS, so it can
# never make a stale price or grade channel pass the gate.
CHANNELS[RECONCILIATION_CHANNEL] = RECONCILIATION_CHANNEL


def channel_staleness(live_state, now=None, sla_minutes=SLA_MINUTES):
    """Returns {channel_name: (age_minutes_or_None, stale_bool)} for each
    named channel field in CHANNELS. A channel whose field is entirely
    absent (an older live.json predating that field) reports (None, True)
    -- unknown is never treated as fresh, same convention staleness_minutes()
    already uses for the combined check."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for channel, field in CHANNELS.items():
        age = _channel_age(live_state, field, now)
        out[channel] = (None, True) if age is None else (age, age > sla_minutes)
    return out


def health(live_state, now=None, sla_minutes=SLA_MINUTES):
    """Machine-readable health, with the degraded channels NAMED.

    `healthy` is true only when every REQUIRED channel is inside the SLA.
    Reconciliation's own state is reported but never counted -- it cannot
    vouch for an upstream it does not observe.
    """
    now = now or datetime.now(timezone.utc)
    channels = channel_staleness(live_state, now=now, sla_minutes=sla_minutes)
    degraded = sorted(c for c in REQUIRED_CHANNELS if channels[c][1])
    return {
        "healthy": not degraded,
        "degraded_channels": degraded,
        "channels": {c: {"age_minutes": a, "stale": st} for c, (a, st) in channels.items()},
        "reconciliation_stale": channels[RECONCILIATION_CHANNEL][1],
        "sla_minutes": sla_minutes,
    }


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
