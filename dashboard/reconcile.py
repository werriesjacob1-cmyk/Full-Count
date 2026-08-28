#!/usr/bin/env python3
"""Reconciliation: does what we PUBLISHED still match what is TRUE?

The 2026-08-28 outage was not detected by anything, and the first fix
attempt added another GitHub-cron watchdog. That was the wrong shape twice
over.

Wrong shape #1 -- scheduling. GitHub's `schedule` trigger is throttled in
this repo: Lineup Watch declares */10 and delivered 12.4 runs/day (9%),
median gap 51 min, worst 11.0 h. A recovery mechanism on that same queue
cannot bound anything. infra/live-heartbeat already solves this: a
Cloudflare cron dispatches dashboard-live.yml every 5 minutes, independent
of GitHub's scheduler. Reconciliation belongs THERE, on the observer that
already runs reliably -- not in a new cron that inherits the same defect.

Wrong shape #2 -- event acknowledgment. A watchdog that dispatches a
rebuild and then considers itself done is acknowledging an EVENT. But the
thing we care about is not "was a rebuild requested", it is "does the
published board match reality". Those come apart exactly when it matters:
the dispatch can be dropped, the rebuild can fail, or it can succeed and
still not fix the mismatch. So this module never acknowledges. It
re-derives the mismatch set from authoritative state every cycle, and a
mismatch disappears only when publication actually matches -- never
because we asked for a rebuild.

Three cheap checks, all against data the live observer already has or can
get for one extra request:

  board age      docs/data.json's own generated_at
  lineups        MLB's confirmed lineup vs the one we published
  line moved     a prop whose threshold FanDuel no longer offers

RECOVERY vs ACTIONABILITY. These are different questions and they get
different thresholds. Recovery asks "should we start rebuilding?" and
fires EARLY, because rebuilding takes time and a rebuild started at 90
minutes is finished before the board stops being actionable at 4 hours.
Actionability asks "may a customer bet this?" and is owned by
recommendation.py (4h board / 45m price), unchanged here. An earlier
recovery threshold is not a stricter product rule; it is the lead time
that keeps the product rule from ever being hit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Start recovering well before the board stops being actionable. Dashboard
# Refresh takes ~10-15 minutes end to end, and the heartbeat observes every
# 5, so 90 minutes leaves several full attempts before recommendation.py's
# 4-hour actionability limit would suppress anything.
RECOVERY_BOARD_AGE_MINUTES = 90

KIND_BOARD_AGE = "board_age"
KIND_LINEUP = "lineup"
KIND_LINE_MOVED = "line_moved"


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def board_age_minutes(payload, *, now=None):
    """Minutes since the board was built. None when unknowable.

    Unknowable is never treated as fresh: an absent or malformed
    generated_at is what a half-written payload looks like.
    """
    built = _parse((payload or {}).get("generated_at"))
    if built is None:
        return None
    return ((now or _now()) - built).total_seconds() / 60.0


# ── the three checks ─────────────────────────────────────────────────────

def board_age_mismatch(payload, *, now=None, limit_minutes=RECOVERY_BOARD_AGE_MINUTES):
    age = board_age_minutes(payload, now=now)
    if age is None:
        return {
            "kind": KIND_BOARD_AGE,
            "fingerprint": f"{KIND_BOARD_AGE}:unknown",
            "detail": "board carries no parseable generated_at",
            "authoritative": None,
            "published": (payload or {}).get("generated_at"),
        }
    if age > limit_minutes:
        return {
            "kind": KIND_BOARD_AGE,
            # Fingerprint carries the STALE basis, so a rebuild that moves
            # generated_at forward produces a different (or no) mismatch
            # rather than silently reusing this one's bookkeeping.
            "fingerprint": f"{KIND_BOARD_AGE}:{payload.get('generated_at')}",
            "detail": f"board basis is {age:.0f} min old (recovery limit {limit_minutes})",
            "authoritative": "a board built within the recovery window",
            "published": payload.get("generated_at"),
        }
    return None


def _published_lineup(payload, game_pk):
    """The batting order this board actually published for a game, as
    {order: player_id}. Only rows that carry a real slot contribute."""
    out = {}
    for row in (payload or {}).get("props") or []:
        if row.get("game_pk") != game_pk:
            continue
        order = row.get("batting_order")
        pid = row.get("player_id")
        if order and pid:
            out[int(order)] = int(pid)
    return out


def _published_assumed(payload, game_pk):
    """True when every row we published for this game is lineup_assumed."""
    rows = [r for r in ((payload or {}).get("props") or []) if r.get("game_pk") == game_pk]
    if not rows:
        return False
    return all(bool(r.get("lineup_assumed")) for r in rows)


def lineup_mismatches(payload, confirmed_lineups):
    """A confirmed MLB lineup that our publication does not reflect.

    `confirmed_lineups` maps game_pk -> {order: player_id}, and contains an
    entry ONLY for games MLB has actually posted. A game absent from it is
    not a mismatch -- nobody has posted yet, and our assumed order is the
    honest best available.

    Two distinct failures are caught:
      * we published an ASSUMED lineup and MLB has since confirmed one
      * we published a confirmed order that no longer matches MLB's
    """
    out = []
    for game_pk, confirmed in sorted((confirmed_lineups or {}).items()):
        if not confirmed:
            continue
        published = _published_lineup(payload, game_pk)
        if not published:
            continue
        assumed = _published_assumed(payload, game_pk)
        differs = any(published.get(slot) != pid for slot, pid in confirmed.items()
                      if slot in published)
        if not assumed and not differs:
            continue
        # Fingerprint the AUTHORITATIVE state, so the mismatch clears only
        # when publication matches this exact lineup -- and a later lineup
        # change opens a new, separate mismatch rather than reusing this one.
        sig = ",".join(f"{s}:{confirmed[s]}" for s in sorted(confirmed))
        out.append({
            "kind": KIND_LINEUP,
            "fingerprint": f"{KIND_LINEUP}:{game_pk}:{sig}",
            "detail": ("published lineup is still ASSUMED but MLB has confirmed one"
                       if assumed else "published batting order differs from MLB's confirmed lineup"),
            "game_pk": game_pk,
            "authoritative": confirmed,
            "published": published,
        })
    return out


def line_moved_mismatches(payload):
    """Props the book no longer offers at the threshold we published.

    Read off the effective board rather than re-fetched: refresh_prices has
    already done the market work this cycle and recorded LINE_MOVED with the
    line actually posted. Reconciliation's job is to notice that a
    correction is owed, not to redo the fetch.
    """
    out = []
    for row in (payload or {}).get("props") or []:
        if row.get("market_fetch_state") != "LINE_MOVED":
            continue
        prop_id = row.get("id")
        posted = row.get("market_posted_line")
        out.append({
            "kind": KIND_LINE_MOVED,
            "fingerprint": f"{KIND_LINE_MOVED}:{prop_id}:{posted}",
            "detail": (f"published threshold {(row.get('projection') or {}).get('value')} "
                       f"is not offered; FanDuel posts {posted}"),
            "prop_id": prop_id,
            "name": row.get("name"),
            "stat": row.get("stat"),
            "authoritative": posted,
            "published": (row.get("projection") or {}).get("value"),
        })
    return out


# ── the reconciliation pass ──────────────────────────────────────────────

def reconcile(payload, *, confirmed_lineups=None, now=None, prior=None,
              recovery_limit_minutes=RECOVERY_BOARD_AGE_MINUTES):
    """Re-derive the whole mismatch set from authoritative state.

    Deliberately stateless with respect to whether a rebuild was requested.
    `prior` contributes only bookkeeping -- when a mismatch was first seen,
    and when we last asked for a rebuild -- and can never keep a mismatch
    open or closed on its own. A mismatch exists this cycle if and only if
    the check finds it this cycle.
    """
    now = now or _now()
    found = []
    age = board_age_mismatch(payload, now=now, limit_minutes=recovery_limit_minutes)
    if age:
        found.append(age)
    found.extend(lineup_mismatches(payload, confirmed_lineups))
    found.extend(line_moved_mismatches(payload))

    prior_open = ((prior or {}).get("open") or {})
    stamp = now.isoformat()
    open_now = {}
    for m in found:
        fp = m["fingerprint"]
        was = prior_open.get(fp) or {}
        entry = dict(m)
        entry["first_seen_at"] = was.get("first_seen_at") or stamp
        entry["last_seen_at"] = stamp
        entry["rebuild_requests"] = int(was.get("rebuild_requests") or 0)
        entry["last_rebuild_request_at"] = was.get("last_rebuild_request_at")
        open_now[fp] = entry

    # Anything previously open and NOT re-derived is genuinely resolved:
    # publication now matches authoritative state. This is the only way a
    # mismatch clears.
    resolved = sorted(set(prior_open) - set(open_now))
    return {
        "checked_at": stamp,
        "open": open_now,
        "resolved_this_cycle": resolved,
        "counts": {k: sum(1 for e in open_now.values() if e["kind"] == k)
                   for k in (KIND_BOARD_AGE, KIND_LINEUP, KIND_LINE_MOVED)},
    }


def needs_rebuild(state):
    """Any open mismatch is a request for a canonical rebuild.

    All three kinds resolve the same way -- only a full Dashboard Refresh
    re-derives lineups, thresholds and probabilities together. A price
    refresh cannot fix any of them, which is precisely why the live overlay
    kept looking healthy while the board rotted underneath it.
    """
    return bool((state or {}).get("open"))


def mark_rebuild_requested(state, *, at=None):
    """Record that we asked. Does NOT clear or acknowledge anything.

    The bookkeeping exists for observability and for the stampede guard's
    backoff, never as evidence the mismatch is handled. A request that is
    dropped, or a rebuild that fails, leaves every mismatch exactly as open
    as it was -- which is the entire point.
    """
    stamp = (at or _now()).isoformat()
    for entry in (state.get("open") or {}).values():
        entry["rebuild_requests"] = int(entry.get("rebuild_requests") or 0) + 1
        entry["last_rebuild_request_at"] = stamp
    state["last_rebuild_request_at"] = stamp
    return state


# ── stampede guard ───────────────────────────────────────────────────────

REBUILD_WORKFLOW = "dashboard-refresh.yml"


def rebuild_in_flight(*, repo=None, token=None, runner=None):
    """Is a Dashboard Refresh already queued or running?

    Reconciliation runs every 5 minutes and a rebuild takes 10-15, so
    without this every cycle during a long mismatch would dispatch another
    one -- a stampede that makes recovery slower, not faster, by contending
    for the same runners and the same git push.

    Returns None when it cannot be determined. The caller must treat None as
    "assume in flight" and skip: a duplicate rebuild is a worse failure than
    a delayed one, and the next tick is only five minutes away.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or "werriesjacob1-cmyk/Full-Count"
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
           f"{REBUILD_WORKFLOW}/runs?per_page=20")
    run = runner or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30))
    proc = run(["curl", "-sS", "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/vnd.github+json",
                "-H", "X-GitHub-Api-Version: 2022-11-28", url])
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return None
    return any(r.get("status") in ("queued", "in_progress", "requested", "waiting")
               for r in runs)


def should_dispatch_rebuild(state, *, repo=None, token=None, runner=None):
    """(dispatch: bool, reason: str). Fails closed on an unknown run state."""
    if not needs_rebuild(state):
        return False, "no open mismatch"
    in_flight = rebuild_in_flight(repo=repo, token=token, runner=runner)
    if in_flight is None:
        return False, ("cannot determine whether Dashboard Refresh is already running; "
                       "skipping to avoid a duplicate rebuild (retry next cycle)")
    if in_flight:
        return False, "Dashboard Refresh already queued or in progress"
    return True, "open mismatch and no rebuild in flight"
