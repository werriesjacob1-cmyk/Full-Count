"""Source-integrity contract for the prospective Hits PA-v1 shadow.

Mission 1 correctly admitted there is no production "source-integrity hold"
mechanism, and implemented the §5 gate as a real check over a registry that is
empty by default. An empty set default is a functioning gate over nothing; it is
not a satisfied integrity requirement. This is the smallest explicit versioned
contract that makes it one.

THREE STATES, AND THE MIDDLE ONE IS THE POINT
=============================================

  CLEAR    an integrity evaluation ACTUALLY RAN and found no applicable hold.
  HOLD     an evaluation ran and found a specific, scoped, reasoned hold.
  UNKNOWN  no evaluation ran, or its inputs were unreadable.

UNKNOWN FAILS CLOSED for primary prospective eligibility, and a missing
evaluation must NEVER default to CLEAR. That asymmetry is the entire contract:
"we did not look" and "we looked and it was fine" are different facts, and
collapsing them is how an integrity gate becomes decorative.

NO NEW SOURCE OF TRUTH
======================

Every input below is already durably written by an existing sole writer. This
module reads them and composes a verdict; it writes no state file of its own. A
fourth writer would violate the one-semantic-writer discipline the pipeline
enforces elsewhere.

WHAT IS DELIBERATELY *NOT* A HOLD
=================================

Getting this wrong in the permissive direction lets bad evidence count. Getting
it wrong in the strict direction is worse in a different way: it zeroes the
experiment while looking rigorous. Explicitly NOT holds:

  * FanGraphs 403. It happens on essentially every real run and is handled by
    documented graceful degradation to Statcast. Holding on it would put every
    slate on hold forever.
  * Any optional enrichment failure (Statcast, weather, umpire, bullpen). These
    degrade the champion's own score, which is already reflected in
    `reliability` and `sample_n` -- both already hard gates.
  * `lineup-watch` not having run. Its own workflow header retires it as a
    correctness dependency (9% of declared cadence, 11-hour worst gap). Its
    silence proves nothing, so it can be neither HOLD nor CLEAR.
  * `NOT_POSTED` and `LINE_MOVED`. These are real, SUCCESSFUL observations, and
    are already handled by the price gate. Counting them twice would attribute
    one rejection to two gates and corrupt the funnel.
"""

from __future__ import annotations

CONTRACT_VERSION = "prospective-source-integrity-v1"

CLEAR = "CLEAR"
HOLD = "HOLD"
UNKNOWN = "UNKNOWN"

SCOPE_SLATE = "slate"
SCOPE_GAME = "game"
SCOPE_TEAM = "team"
SCOPE_CANDIDATE = "candidate"

# Reason codes. Stable strings, because they end up in permanent evidence and
# in §12's reporting.
R_SCHEDULE_UNAVAILABLE = "schedule_fetch_failed"
R_REQUIRED_CHANNEL_STALE = "required_freshness_channel_stale"
R_BOARD_AGE_MISMATCH = "reconciliation_board_age_mismatch"
R_LINEUP_MISMATCH = "reconciliation_lineup_mismatch"
R_NO_EVALUATION = "no_integrity_evaluation_ran"
R_INPUTS_UNREADABLE = "integrity_inputs_unreadable"


def _verdict(state, holds, notes, evaluated):
    return {
        "contract_version": CONTRACT_VERSION,
        "state": state,
        "evaluated": evaluated,
        "holds": list(holds),
        "notes": dict(notes),
    }


def unknown(reason):
    """An explicit UNKNOWN. Never silently CLEAR."""
    return _verdict(UNKNOWN, [], {"reason": reason or R_NO_EVALUATION}, False)


def hold(scope, key, reason_code, *, observed_at=None, authority=None,
         expires_at=None):
    """One scoped hold record.

    Every hold carries scope, key, reason code, observation time and the
    originating check, so a later reader can tell an automated integrity
    finding from a manual intervention.
    """
    return {
        "scope": scope,
        "key": key,
        "reason_code": reason_code,
        "observed_at": observed_at,
        "authority": authority or "automated_check",
        "expires_at": expires_at,
    }


def evaluate(*, schedule, live_state=None, now=None, observed_at=None,
             sla_minutes=None):
    """Compose the integrity verdict from already-durable pipeline signals.

    WHAT "CLEAR" MEANS HERE, PRECISELY. An evaluation genuinely ran over the
    signals that EXIST, and none of them raised a hold. The verdict records
    which signals were evaluated and which could not be, so a reader can see
    exactly what was and was not checked rather than inferring it.

    UNKNOWN is reserved for "we could not look at all": live.json unreadable or
    absent, or the required freshness channels missing. It fails closed.

    WHY RECONCILIATION IS OPTIONAL RATHER THAN REQUIRED. An earlier version of
    this contract required the `reconciliation` block and returned UNKNOWN
    without it. Measured against real committed state, that block is **null on
    every board**: `live_state.default_live_state()` initialises it to None and
    only `run_reconciliation.py` ever replaces it, and it is null across twelve
    consecutive live.json commits on main spanning an hour. Requiring it would
    therefore have returned UNKNOWN forever and silently nulled the whole
    experiment -- the same fatal outcome as defaulting to CLEAR, reached from
    the opposite direction.

    So reconciliation is treated as an ENHANCER: when present its mismatches
    become holds, and when absent that fact is recorded in
    `unevaluated_signals` rather than either ignored or fatal. That
    reconciliation never populates is itself a production defect, reported
    separately; fixing it strengthens this gate without changing its shape.
    """
    from datetime import datetime, timedelta, timezone

    holds, notes = [], {}
    evaluated, unevaluated = [], []

    # --- whole-slate schedule outage -----------------------------------
    if not schedule:
        holds.append(hold(SCOPE_SLATE, "slate", R_SCHEDULE_UNAVAILABLE,
                          observed_at=observed_at))
        notes["schedule"] = "empty schedule: whole-slate fetch failed"
    evaluated.append("schedule_availability")

    if live_state is None:
        # We could not look at anything live-side at all.
        return _verdict(UNKNOWN, holds,
                        dict(notes, reason=R_INPUTS_UNREADABLE,
                             evaluated_signals=evaluated,
                             unevaluated_signals=["freshness_channels",
                                                  "reconciliation"]), False)

    # --- required freshness channels ------------------------------------
    try:
        from dashboard.check_live_freshness import (REQUIRED_CHANNELS,
                                                    SLA_MINUTES)
    except Exception:                       # pragma: no cover - import guard
        REQUIRED_CHANNELS, SLA_MINUTES = {}, 15
    sla = sla_minutes if sla_minutes is not None else SLA_MINUTES
    now_dt = now or datetime.now(timezone.utc)

    missing, stale = [], []
    for channel, field in (REQUIRED_CHANNELS or {}).items():
        raw = live_state.get(field)
        stamp = _parse_utc(raw)
        if stamp is None:
            missing.append(channel)
        elif now_dt - stamp > timedelta(minutes=sla):
            stale.append(channel)
    if REQUIRED_CHANNELS:
        evaluated.append("required_freshness_channels")
    if missing:
        # A required channel with no timestamp at all is not a stale channel,
        # it is an unobserved one. Fail closed.
        return _verdict(UNKNOWN, holds,
                        dict(notes, reason=R_INPUTS_UNREADABLE,
                             missing_channels=missing,
                             evaluated_signals=evaluated,
                             unevaluated_signals=["reconciliation"]), False)
    if stale:
        holds.append(hold(SCOPE_SLATE, "slate", R_REQUIRED_CHANNEL_STALE,
                          observed_at=observed_at))
        notes["stale_channels"] = stale

    # --- reconciliation: an ENHANCER, not a precondition -----------------
    recon = live_state.get("reconciliation")
    if recon is None:
        unevaluated.append("reconciliation")
        notes["reconciliation"] = (
            "absent on this board (its default value); reconciliation-derived "
            "holds could not be evaluated")
    else:
        evaluated.append("reconciliation")
        for mismatch in (recon.get("mismatches") or []):
            kind = mismatch.get("kind")
            if kind == "board_age":
                holds.append(hold(SCOPE_SLATE, "slate", R_BOARD_AGE_MISMATCH,
                                  observed_at=mismatch.get("observed_at")
                                  or observed_at))
            elif kind == "lineup":
                game_pk = mismatch.get("game_pk")
                if game_pk is None:
                    parts = str(mismatch.get("fingerprint") or "").split(":")
                    game_pk = parts[1] if len(parts) > 1 else None
                if game_pk is not None:
                    holds.append(hold(SCOPE_GAME, game_pk, R_LINEUP_MISMATCH,
                                      observed_at=mismatch.get("observed_at")
                                      or observed_at))
            # KIND_LINE_MOVED is deliberately NOT a source-integrity hold: it
            # is a real successful market observation and is already the price
            # gate's territory. Double-counting corrupts the funnel.

    notes["evaluated_signals"] = evaluated
    notes["unevaluated_signals"] = unevaluated
    state = HOLD if holds else CLEAR
    return _verdict(state, holds, notes, True)


def _parse_utc(value):
    from datetime import datetime, timezone
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc))


def applies_to(verdict, *, game_pk=None, team=None, canonical_prop_id=None):
    """Does this verdict block this specific candidate?

    UNKNOWN blocks everything -- fail closed. HOLD blocks by scope match.
    CLEAR blocks nothing.
    """
    state = (verdict or {}).get("state")
    if state == UNKNOWN or state is None:
        return True, [R_NO_EVALUATION if state is None else "state_unknown"]
    if state == CLEAR:
        return False, []
    reasons = []
    for h in (verdict.get("holds") or []):
        scope, key = h.get("scope"), h.get("key")
        if scope == SCOPE_SLATE:
            reasons.append(h.get("reason_code"))
        elif scope == SCOPE_GAME and game_pk is not None and str(key) == str(game_pk):
            reasons.append(h.get("reason_code"))
        elif scope == SCOPE_TEAM and team is not None and str(key) == str(team):
            reasons.append(h.get("reason_code"))
        elif (scope == SCOPE_CANDIDATE and canonical_prop_id is not None
              and str(key) == str(canonical_prop_id)):
            reasons.append(h.get("reason_code"))
    return bool(reasons), reasons
