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


def evaluate(*, schedule, live_state=None, freshness_health=None,
             observed_at=None):
    """Compose the integrity verdict from already-durable pipeline signals.

    ``schedule``          the dict `_game_schedule()` returned. Empty means the
                          whole-slate MLB fetch failed and game-start filtering
                          was skipped for the entire build.
    ``live_state``        parsed `docs/live.json`, for its `reconciliation`
                          block. None/unreadable => UNKNOWN, not CLEAR.
    ``freshness_health``  `check_live_freshness.health()`-shaped dict, whose
                          required channels are the two upstreams a price and a
                          settlement depend on.

    Returns a verdict dict. State is HOLD if any hold applies, UNKNOWN if the
    evaluation could not be completed, CLEAR only when it genuinely ran clean.
    """
    holds, notes = [], {}

    # Whole-slate schedule outage. The per-row gates already fail closed on a
    # missing schedule entry, but recording it explicitly is what makes
    # "0 eligible because MLB was down" distinguishable from "0 eligible
    # because nothing qualified" -- a §12 reporting requirement.
    if not schedule:
        holds.append(hold(SCOPE_SLATE, "slate", R_SCHEDULE_UNAVAILABLE,
                          observed_at=observed_at))
        notes["schedule"] = "empty schedule: whole-slate fetch failed"

    if live_state is None:
        return _verdict(UNKNOWN, holds,
                        dict(notes, reason=R_INPUTS_UNREADABLE), False)

    recon = (live_state or {}).get("reconciliation")
    if recon is None:
        # The reconciliation block is the best existing hold source; without it
        # we genuinely do not know. Not CLEAR.
        return _verdict(UNKNOWN, holds,
                        dict(notes, reason="reconciliation block absent"), False)

    for mismatch in (recon.get("mismatches") or []):
        kind = mismatch.get("kind")
        if kind == "board_age":
            holds.append(hold(SCOPE_SLATE, "slate", R_BOARD_AGE_MISMATCH,
                              observed_at=mismatch.get("observed_at")
                              or observed_at))
        elif kind == "lineup":
            # KIND_LINEUP fingerprints are game_pk:side, which maps directly
            # onto the hold registry's game key.
            game_pk = mismatch.get("game_pk")
            if game_pk is None:
                fp = str(mismatch.get("fingerprint") or "")
                parts = fp.split(":")
                game_pk = parts[1] if len(parts) > 1 else None
            if game_pk is not None:
                holds.append(hold(SCOPE_GAME, game_pk, R_LINEUP_MISMATCH,
                                  observed_at=mismatch.get("observed_at")
                                  or observed_at))
        # KIND_LINE_MOVED is deliberately NOT a source-integrity hold: it is a
        # real successful market observation and is already the price gate's
        # territory. Double-counting it would corrupt the rejection funnel.

    if freshness_health is not None:
        stale = [c for c, ok in (freshness_health.get("channels") or {}).items()
                 if ok is False]
        if stale:
            holds.append(hold(SCOPE_SLATE, "slate", R_REQUIRED_CHANNEL_STALE,
                              observed_at=observed_at))
            notes["stale_channels"] = stale

    state = HOLD if holds else CLEAR
    return _verdict(state, holds, notes, True)


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
