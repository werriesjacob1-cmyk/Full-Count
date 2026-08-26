"""live_brain/ordering.py -- small, pure ordering/monotonicity primitives.

No I/O, no network, no MLB/FanDuel fetch -- these are the primitives the
Live Brain data plane will eventually be built on top of, testable in
isolation today. Reuses Full Count's EXISTING settlement-authority naming
(none < live_observation < official_final, as already documented in
`.claude/agents/fc-live-sre.md` and implemented informally in
`dashboard/merge_live_files.py`) rather than inventing a second ranking.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from live_brain.envelopes import DeltaEnvelope, EventEnvelope

SETTLEMENT_AUTHORITY_RANK: dict[str, int] = {
    "none": 0,
    "live_observation": 1,
    "official_final": 2,
}

GAME_STATE_RANK: dict[str, int] = {
    "unknown": 0,
    "pregame": 1,
    "live": 2,
    "delayed": 2,
    "suspended": 2,
    "postponed": 2,
    "cancelled": 3,
    "final": 3,
}


class LogicalDuplicateError(ValueError):
    """Raised when two different candidate_ids claim the same real-world
    identity, or the same candidate_id is registered with a conflicting
    identity -- either way, a bug upstream, not something to silently
    resolve."""


def accept_settlement(current_authority: str, incoming_authority: str) -> bool:
    """Settlement authority must never regress. Equal authority is accepted
    (a later observation at the same authority tier can still carry a real
    field update) -- only a STRICTLY lower incoming authority is rejected."""
    return SETTLEMENT_AUTHORITY_RANK[incoming_authority] >= SETTLEMENT_AUTHORITY_RANK[current_authority]


def accept_game_state(current_state: str, incoming_state: str) -> bool:
    """`final` (and other terminal states) never regress to `live`/`pregame`."""
    return GAME_STATE_RANK[incoming_state] >= GAME_STATE_RANK[current_state]


def accept_price(current_observed_at: str, incoming_observed_at: str) -> bool:
    """A strictly older price observation can never replace a newer one.
    ISO8601 strings compare correctly lexicographically when all values use
    the same fixed-offset/UTC format -- callers are responsible for that
    normalization; this function does not silently coerce timezones."""
    return incoming_observed_at >= current_observed_at


def impact_set(event: EventEnvelope) -> frozenset[int]:
    """The core Live Brain invariant: one game's event must never require
    whole-slate recomputation. Returns exactly the game_pk(s) this event
    can affect -- today, always exactly one (Full Count has no cross-game
    event type yet; a doubleheader shared-roster case would be FUTURE
    CAPABILITY, not simulated here)."""
    return frozenset({event.game_pk})


def dedupe_events(events: list[EventEnvelope]) -> list[EventEnvelope]:
    """First-seen wins for a given dedupe_key -- a duplicate observation of
    the same real event (e.g. two overlapping polls) collapses to one."""
    seen: set[tuple] = set()
    out: list[EventEnvelope] = []
    for e in events:
        k = e.dedupe_key()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def apply_delta(state: dict[str, dict[str, Any]], delta: DeltaEnvelope) -> dict[str, dict[str, Any]]:
    """Pure merge: apply `delta` to `state` (a dict keyed by candidate_id,
    each value holding at least {"event_version": int, "fields": {...}}),
    returning a NEW state dict (never mutates the input -- required for the
    replay-determinism test, which applies the same sequence twice from a
    fresh copy each time).

    Idempotent and order-tolerant for out-of-order delivery: a delta whose
    event_version is not strictly greater than the candidate's current
    event_version is a no-op (covers both exact duplicates and stale
    reordered deliveries in one rule)."""
    current = state.get(delta.candidate_id)
    if current is not None and delta.event_version <= current["event_version"]:
        return state
    new_state = dict(state)
    fields = dict(current["fields"]) if current else {}
    for k, change in delta.changed_fields.items():
        fields[k] = change["new"]
    new_state[delta.candidate_id] = {"event_version": delta.event_version, "fields": fields}
    return new_state


def register_candidate_identity(registry: dict[str, str], candidate_id: str, identity_key: str) -> dict[str, str]:
    """Pure: returns a NEW registry mapping candidate_id -> identity_key.
    Raises LogicalDuplicateError if this candidate_id was already
    registered under a DIFFERENT identity_key, or if this identity_key is
    already claimed by a DIFFERENT candidate_id -- either direction is a
    real bug (identity is supposed to be a stable 1:1 mapping; see
    candidate_dataset.py's own existing dedupe-identity contract, which
    this reuses the spirit of rather than reimplementing)."""
    existing_key = registry.get(candidate_id)
    if existing_key is not None and existing_key != identity_key:
        raise LogicalDuplicateError(
            f"candidate_id {candidate_id!r} already registered under identity "
            f"{existing_key!r}, cannot re-register under {identity_key!r}")
    for cid, key in registry.items():
        if key == identity_key and cid != candidate_id:
            raise LogicalDuplicateError(
                f"identity {identity_key!r} already claimed by candidate_id "
                f"{cid!r}, cannot also claim it for {candidate_id!r}")
    new_registry = dict(registry)
    new_registry[candidate_id] = identity_key
    return new_registry
