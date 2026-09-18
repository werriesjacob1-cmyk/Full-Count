"""Seal normalized NFL game markets into a deterministic research-only snapshot.

The input contract matches ``nfl.normalize.fanduel_game_lines``. A complete snapshot
accounts for each primary market family either as one normalized record or one
explicit normalization failure. Missing coverage is never silently ignored.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

MARKETS = {"moneyline", "spread", "game_total"}


class GameMarketSnapshotError(ValueError):
    """Raised when normalized market evidence cannot be sealed safely."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketSnapshotError(f"{field} must be a non-empty string")
    return value.strip()


def _dt(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GameMarketSnapshotError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GameMarketSnapshotError(f"{field} must be timezone-aware")
    return parsed


def _sha256_hex(value: str, field: str) -> str:
    normalized = _text(value, field).lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise GameMarketSnapshotError(f"{field} must be 64 lowercase hex characters")
    return normalized


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def seal_game_market_snapshot(
    records: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    sealed_at: str,
) -> dict[str, Any]:
    """Seal one event's three primary market families without inventing coverage."""
    expected_event = _text(event_id, "event_id")
    sealed_dt = _dt(sealed_at, "sealed_at")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise GameMarketSnapshotError("records must be a sequence")
    if not isinstance(failures, Sequence) or isinstance(failures, (str, bytes)):
        raise GameMarketSnapshotError("failures must be a sequence")

    normalized_records = [deepcopy(dict(row)) for row in records]
    normalized_failures = [deepcopy(dict(row)) for row in failures]

    seen_records: set[str] = set()
    seen_failures: set[str] = set()
    payload_hashes: set[str] = set()
    capture_times: set[str] = set()
    market_times: list[datetime] = []

    for row in normalized_records:
        if _text(row.get("event_id"), "record.event_id") != expected_event:
            raise GameMarketSnapshotError("record event_id mismatch")
        canonical = _text(row.get("market"), "record.market").lower()
        if canonical not in MARKETS:
            raise GameMarketSnapshotError(f"unsupported canonical_market: {canonical}")
        if canonical in seen_records:
            raise GameMarketSnapshotError(f"duplicate normalized market: {canonical}")
        seen_records.add(canonical)

        if _text(row.get("sport"), "record.sport").upper() != "NFL":
            raise GameMarketSnapshotError("record sport must be NFL")
        if _text(row.get("market_status"), "record.market_status").upper() != "OPEN" or row.get("in_play") is not False:
            raise GameMarketSnapshotError("record must be OPEN and pregame")

        payload_hashes.add(_sha256_hex(row.get("source_payload_sha256"), "record.source_payload_sha256"))
        captured_raw = _text(row.get("captured_at"), "record.captured_at")
        captured_dt = _dt(captured_raw, "record.captured_at")
        market_dt = _dt(row.get("market_time"), "record.market_time")
        if captured_dt >= market_dt:
            raise GameMarketSnapshotError("record must be captured strictly before market_time")
        if sealed_dt < captured_dt:
            raise GameMarketSnapshotError("sealed_at cannot precede captured_at")
        if sealed_dt >= market_dt:
            raise GameMarketSnapshotError("snapshot must be sealed strictly before market_time")
        capture_times.add(captured_raw)
        market_times.append(market_dt)

    for row in normalized_failures:
        row_event = _text(row.get("event_id"), "failure.event_id")
        if row_event != expected_event:
            raise GameMarketSnapshotError("failure event_id mismatch")
        canonical = _text(row.get("market"), "failure.market").lower()
        if canonical not in MARKETS:
            raise GameMarketSnapshotError(f"unsupported failure market: {canonical}")
        _text(row.get("reason"), "failure.reason")
        if canonical in seen_failures:
            raise GameMarketSnapshotError(f"duplicate failure market: {canonical}")
        if canonical in seen_records:
            raise GameMarketSnapshotError(f"market cannot be both normalized and failed: {canonical}")
        seen_failures.add(canonical)

    accounted = seen_records | seen_failures
    missing = sorted(MARKETS - accounted)
    if missing:
        raise GameMarketSnapshotError(f"unaccounted primary markets: {','.join(missing)}")
    if not normalized_records:
        raise GameMarketSnapshotError("snapshot requires at least one normalized market")
    if len(payload_hashes) != 1:
        raise GameMarketSnapshotError("normalized records must share one source payload SHA")
    if len(capture_times) != 1:
        raise GameMarketSnapshotError("normalized records must share one captured_at")

    body = {
        "schema_version": 1,
        "sport": "NFL",
        "event_id": expected_event,
        "captured_at": next(iter(capture_times)),
        "sealed_at": _text(sealed_at, "sealed_at"),
        "source_payload_sha256": next(iter(payload_hashes)),
        "records": sorted(normalized_records, key=lambda row: row["market"]),
        "failures": sorted(normalized_failures, key=lambda row: row["market"]),
        "research_only": True,
        "public_eligible": False,
    }
    return {**body, "snapshot_sha256": _canonical_hash(body)}


def verify_game_market_snapshot(snapshot: Mapping[str, Any]) -> str:
    """Rebuild a sealed snapshot and require byte-semantic equality."""
    if not isinstance(snapshot, Mapping):
        raise GameMarketSnapshotError("snapshot must be a mapping")
    expected = _sha256_hex(snapshot.get("snapshot_sha256"), "snapshot_sha256")
    rebuilt = seal_game_market_snapshot(
        snapshot.get("records"),
        snapshot.get("failures"),
        event_id=snapshot.get("event_id"),
        sealed_at=snapshot.get("sealed_at"),
    )
    if rebuilt["snapshot_sha256"] != expected:
        raise GameMarketSnapshotError("snapshot hash mismatch")
    if dict(snapshot) != rebuilt:
        raise GameMarketSnapshotError("snapshot content does not match canonical seal")
    return expected
