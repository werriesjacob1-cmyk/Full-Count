#!/usr/bin/env python3
"""Immutable, hash-chained event ledger for published Top Picks.

This is the "Prediction Receipts" ledger referenced (but never built) in
engineering/ENGINEERING_HANDOFF.md's roadmap -- distinct from
dashboard/publication_registry.py's lifecycle registry. The registry
guarantees a wager's first-exposure snapshot is never overwritten by
*discipline* (its own code never edits an existing entries[key]). This
ledger adds a second, independent, verifiable guarantee: every publication
is a link in a SHA-256 hash chain, so editing or deleting a past event
breaks every hash after it and is detectable by verify_ledger_integrity(),
not merely "we don't have code that would do that."

Scope of this first increment: PUBLICATION events only, sourced from the
exact same immutable snapshot dashboard/publication_registry.py already
produces -- no new data is invented, only made independently verifiable.
Recording every subsequent settlement-state transition (open ->
provisional_hit/provisional_miss -> hit/miss/void) as its own ledger event
is real future scope, deliberately deferred: as of this module's creation,
two Live Integrity PRs are open and unmerged against dashboard/
refresh_grades.py's settlement loop (the natural hook point for that second
event type), and wiring a third concurrent writer into that same hot path
risks exactly the kind of overlapping-writer conflict project discipline
prohibits. Wire it in as its own follow-up once that file stabilizes.

One append-only file, one event per publication:

    data/prediction_ledger/events.jsonl

Each line is a JSON object:

    {
      "event_seq": <int, 0-based, matches line's position in the file>,
      "prev_hash": <sha256 hex of the previous event, or null for event 0>,
      "event_hash": sha256(canonical JSON of
          {prev_hash, prop_id, event_type, payload, recorded_at}),
      "recorded_at": <UTC ISO-8601 -- the registry's own first_published_at>,
      "prop_id": <canonical wager id>,
      "event_type": "publication",
      "payload": {canonical_id, settlement_identity, slate_date,
                  first_published_at, publication_provenance, snapshot},
    }

Publication, like the registry entry it mirrors, is a first-write-wins
fact: append_publication_event() is a no-op (returns False) if this
prop_id already has a publication event on record.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

try:
    from .live_state import parse_utc
except ImportError:  # direct script execution
    from live_state import parse_utc


DEFAULT_LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "prediction_ledger", "events.jsonl",
)

PUBLICATION_SNAPSHOT_KEYS = (
    "canonical_id", "settlement_identity", "slate_date",
    "first_published_at", "publication_provenance", "snapshot",
)


def _event_hash(prev_hash, prop_id, event_type, payload, recorded_at):
    # Matches dashboard/live_state.py's state_digest() encoding exactly
    # (sort_keys canonical JSON, ensure_ascii=False) for the same reason it
    # does: a deterministic hash that doesn't escape real player names.
    canonical = json.dumps(
        {
            "prev_hash": prev_hash, "prop_id": prop_id,
            "event_type": event_type, "payload": payload,
            "recorded_at": recorded_at,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_events(path):
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _atomic_write_events(path, events):
    """fsync a temporary file and atomically replace only after success.

    A full rewrite-and-replace on every append (mirroring live_state.py's
    atomic_write_json) rather than a raw append avoids any possibility of a
    partial/torn final line surviving a crash mid-write -- this file is
    small (one entry per published wager) so the cost is negligible.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def verify_ledger_integrity(path=DEFAULT_LEDGER_PATH):
    """Walk the whole chain, recomputing every hash. Raises ValueError on
    the first break -- a missing/altered event, a reordered line, or a
    duplicate publication for one prop_id. Returns a small summary dict
    on success."""
    events = _read_events(path)
    prev_hash = None
    publication_ids = set()
    for index, event in enumerate(events):
        for key in ("event_seq", "prev_hash", "event_hash", "recorded_at",
                    "prop_id", "event_type", "payload"):
            if key not in event:
                raise ValueError(f"ledger event {index} is missing required field {key!r}")
        if event["event_seq"] != index:
            raise ValueError(
                f"ledger event at line {index} has out-of-order event_seq {event['event_seq']!r}"
            )
        if event["prev_hash"] != prev_hash:
            raise ValueError(f"ledger event {index} breaks the hash chain (tampered or reordered)")
        if parse_utc(event["recorded_at"]) is None:
            raise ValueError(f"ledger event {index} has an invalid recorded_at timestamp")
        recomputed = _event_hash(
            prev_hash, event["prop_id"], event["event_type"], event["payload"], event["recorded_at"],
        )
        if recomputed != event["event_hash"]:
            raise ValueError(f"ledger event {index} content does not match its own hash (tampered)")
        if event["event_type"] == "publication":
            if event["prop_id"] in publication_ids:
                raise ValueError(
                    f"ledger contains more than one publication event for {event['prop_id']!r}"
                )
            publication_ids.add(event["prop_id"])
        else:
            raise ValueError(f"ledger event {index} has an unknown event_type {event['event_type']!r}")
        prev_hash = event["event_hash"]
    return {"event_count": len(events), "publication_count": len(publication_ids)}


def append_publication_event(registry_entry, recorded_at=None, path=DEFAULT_LEDGER_PATH):
    """Append one immutable publication event for one registry entry.

    Returns False (no-op) if this prop_id already has a publication event
    on record -- a publication cannot be re-asserted or edited, only ever
    first recorded once, exactly like the registry entry it mirrors.
    """
    prop_id = registry_entry["canonical_id"]
    recorded_at = recorded_at or registry_entry["first_published_at"]
    if parse_utc(recorded_at) is None:
        raise ValueError(f"ledger event for {prop_id!r} requires a strict UTC recorded_at")
    events = _read_events(path)
    for event in events:
        if event["event_type"] == "publication" and event["prop_id"] == prop_id:
            return False
    payload = {key: registry_entry[key] for key in PUBLICATION_SNAPSHOT_KEYS if key in registry_entry}
    prev_hash = events[-1]["event_hash"] if events else None
    event_hash = _event_hash(prev_hash, prop_id, "publication", payload, recorded_at)
    events.append({
        "event_seq": len(events),
        "prev_hash": prev_hash,
        "event_hash": event_hash,
        "recorded_at": recorded_at,
        "prop_id": prop_id,
        "event_type": "publication",
        "payload": payload,
    })
    _atomic_write_events(path, events)
    return True


def backfill_from_registry(registry, path=DEFAULT_LEDGER_PATH):
    """One-time (idempotent) bootstrap: seed the ledger with a publication
    event for every entry already present in the registry, using the
    registry's OWN recorded first_published_at. This replays already-
    immutable registry facts into hash-chained form -- it is not
    fabricating history, since every value came from a fact the registry
    itself already committed to permanently. Safe to call repeatedly;
    already-seeded prop_ids are skipped by append_publication_event.
    Returns the number of NEW events actually appended.
    """
    added = 0
    for prop_id in sorted(registry["entries"]):
        if append_publication_event(registry["entries"][prop_id], path=path):
            added += 1
    return added


def reconstruct_wager(prop_id, ledger_path=DEFAULT_LEDGER_PATH, results_dir=None):
    """Reassemble the full lifecycle of one published wager: what was
    predicted (from the immutable ledger) and what happened (from the
    slate's graded results file, if it exists yet).

    Never fabricates an outcome. If the wager has no publication event, or
    its slate hasn't been graded yet, or the grades file doesn't contain
    it, that is reported honestly rather than guessed at.
    """
    events = _read_events(ledger_path)
    publication = next(
        (e for e in events if e["event_type"] == "publication" and e["prop_id"] == prop_id),
        None,
    )
    if publication is None:
        return {"prop_id": prop_id, "found": False, "reason": "no publication event recorded"}

    slate_date = publication["payload"].get("slate_date")
    results_dir = results_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results",
    )
    grades_path = os.path.join(results_dir, f"grades_{slate_date}.json")

    outcome = None
    outcome_status = "not_yet_graded"
    if os.path.exists(grades_path):
        with open(grades_path, encoding="utf-8") as handle:
            grades = json.load(handle)
        match = next(
            (row for row in grades.get("public_top_picks") or [] if row.get("id") == prop_id),
            None,
        )
        if match is None:
            outcome_status = "slate_graded_but_pick_not_found"
        else:
            outcome = {
                "grade": match.get("grade"),
                "settlement_state": match.get("settlement_state"),
                "settlement_authority": match.get("settlement_authority"),
                "settlement_observed_at": match.get("settlement_observed_at"),
                "settlement_source": match.get("settlement_source"),
                "reason": match.get("reason"),
            }
            outcome_status = "graded"
            snapshot = publication["payload"].get("snapshot") or {}
            mismatches = [
                field for field in ("hit_probability", "market_odds", "prop", "projection")
                if match.get(field) != snapshot.get(field)
            ]
            if mismatches:
                outcome_status = "graded_with_prediction_mismatch"
                outcome["prediction_mismatch_fields"] = mismatches

    return {
        "prop_id": prop_id,
        "found": True,
        "prediction": publication["payload"],
        "outcome_status": outcome_status,
        "outcome": outcome,
    }
