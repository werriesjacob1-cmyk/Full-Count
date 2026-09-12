#!/usr/bin/env python3
"""Deterministic prospective snapshot sealing for NFL shadow research.

This is not a cryptographic append-only ledger by itself; repository/branch
storage policy provides persistence. Its job is to make each pregame snapshot
self-identifying and tamper-evident:
- deterministic observation IDs,
- deterministic record ordering,
- a SHA-256 over canonical snapshot content,
- explicit rejection of postgame/outcome fields,
- no public PLAY decisions allowed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_DECISIONS = frozenset({"SHADOW_ONLY", "QUARANTINED"})
FORBIDDEN_OUTCOME_KEYS = frozenset({
    "actual",
    "actual_value",
    "result",
    "grade",
    "graded",
    "hit",
    "won",
    "settled",
    "outcome",
})


def validate_pregame_timing(records, sealed_at):
    """Reject a live capture that crossed kickoff before freezing decisions."""
    def clock(value):
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('invalid pregame timing') from exc
        if dt.tzinfo is None:
            raise ValueError('pregame timing requires timezone')
        return dt
    sealed = clock(sealed_at)
    for row in records:
        if not clock(row.get('captured_at')) <= sealed < clock(row.get('event_open_date')):
            raise ValueError('capture and decision sealing must precede kickoff')


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _required_text(record: Mapping[str, Any], field: str) -> str:
    value = str(record.get(field) or "").strip()
    if not value:
        raise ValueError(f"missing required shadow field: {field}")
    return value


def observation_id(record: Mapping[str, Any]) -> str:
    """Return a deterministic ID for one sportsbook observation."""
    identity = {
        "event_id": _required_text(record, "event_id"),
        "market_id": _required_text(record, "market_id"),
        "gsis_id": _required_text(record, "gsis_id"),
        "market": _required_text(record, "market"),
        "line": record.get("line"),
        "over_odds": record.get("over_odds"),
        "under_odds": record.get("under_odds"),
        "captured_at": _required_text(record, "captured_at"),
    }
    digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:24]
    return f"fcnfl-shadow1:{digest}"


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("shadow record must be a mapping")

    forbidden = sorted(FORBIDDEN_OUTCOME_KEYS.intersection(record.keys()))
    if forbidden:
        raise ValueError(
            "pregame shadow snapshot contains forbidden outcome fields: "
            + ", ".join(forbidden)
        )

    decision = str(record.get("decision_status") or "").strip().upper()
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(
            "decision_status must be SHADOW_ONLY or QUARANTINED"
        )

    for field in (
        "event_id",
        "market_id",
        "gsis_id",
        "market",
        "player_name",
        "team",
        "captured_at",
        "availability_status",
    ):
        _required_text(record, field)

    if str(record.get("market")) != "passing_yards":
        raise ValueError("shadow v1 supports passing_yards only")

    row = dict(record)
    row["decision_status"] = decision
    row["observation_id"] = observation_id(row)
    return row


def seal_snapshot(
    records: Sequence[Mapping[str, Any]],
    *,
    slate_date: str,
    code_sha: str,
    source_vintage: str,
    sealed_at: str,
) -> dict[str, Any]:
    """Validate, deterministically order, and hash one prospective snapshot."""
    slate = str(slate_date or "").strip()
    code = str(code_sha or "").strip()
    vintage = str(source_vintage or "").strip()
    sealed = str(sealed_at or "").strip()
    if not all((slate, code, vintage, sealed)):
        raise ValueError("snapshot metadata fields must be non-empty")

    normalized = [_validate_record(record) for record in records]
    normalized.sort(key=lambda row: row["observation_id"])

    ids = [row["observation_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate observation_id in snapshot")

    body = {
        "schema_version": SCHEMA_VERSION,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_SHADOW",
        "slate_date": slate,
        "code_sha": code,
        "source_vintage": vintage,
        "sealed_at": sealed,
        "record_count": len(normalized),
        "records": normalized,
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return {
        **body,
        "snapshot_sha256": digest,
    }
