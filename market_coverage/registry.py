#!/usr/bin/env python3
"""Durable cross-sport registry for every observed sportsbook market type.

This layer answers a coverage question only: what markets did a book expose,
and how far has each family progressed through Full Count's research lifecycle?
It does not normalize wagers, assign probabilities, select bets, grade results,
or alter either sport's production pipeline.

Unknown and malformed market families are records, never dropped rows. A
successful capture can therefore reveal an implementation gap without turning
that gap into a candidate or favorable evidence.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1
LIFECYCLE_STATUSES = frozenset({
    "DISCOVERED",
    "INGESTED",
    "NORMALIZED",
    "HISTORICAL_DATA_AVAILABLE",
    "MODEL_RESEARCH",
    "HISTORICAL_VALIDATED",
    "PROSPECTIVE_SHADOW",
    "SELECTOR_RESEARCH",
    "PUBLIC_ELIGIBLE",
    "REJECTED",
    "BLOCKED_DATA",
    "BLOCKED_IDENTITY",
    "BLOCKED_GRADING",
    "UNSUPPORTED",
})

CAPABILITY_FIELDS = (
    "ingested",
    "normalized",
    "historical_data_available",
    "model_research",
    "historical_validated",
    "prospective_capture",
    "grader",
    "selector_research",
    "public_eligible",
)


def _text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _utc(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO-8601: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def coverage_id(sport: str, sportsbook: str, source_market_type: str) -> str:
    identity = {
        "sport": _text(sport, "sport").upper(),
        "sportsbook": _text(sportsbook, "sportsbook").upper(),
        "source_market_type": _text(source_market_type, "source_market_type"),
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "fc-market1:" + hashlib.sha256(raw).hexdigest()[:24]


def _capabilities(value: Mapping[str, Any] | None = None) -> dict[str, bool]:
    source = value or {}
    unknown = sorted(set(source).difference(CAPABILITY_FIELDS))
    if unknown:
        raise ValueError(f"unknown capability fields: {', '.join(unknown)}")
    return {field: bool(source.get(field, False)) for field in CAPABILITY_FIELDS}


def new_registry(*, generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc(generated_at, "generated_at"),
        "markets": [],
    }


def _runner_count(market: Mapping[str, Any]) -> int:
    runners = market.get("runners")
    return len(runners) if isinstance(runners, list) else 0


def extract_fanduel_observations(
    payload: Mapping[str, Any],
    *,
    sport: str,
    observed_at: str,
    source_artifact: str,
    tab: str | None = None,
    source_payload_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Census every market family in one raw FanDuel payload.

    A missing ``marketType`` is retained under a sentinel family and marked as
    an identity blocker by ``update_registry``. The raw market ID is preserved
    as an example, but never promoted into a canonical market identity.
    """
    attachments = payload.get("attachments")
    if not isinstance(attachments, Mapping):
        raise ValueError("FanDuel payload is missing attachments")
    markets = attachments.get("markets")
    if not isinstance(markets, Mapping):
        raise ValueError("FanDuel payload is missing attachments.markets")

    seen_at = _utc(observed_at, "observed_at")
    source_digest = str(source_payload_sha256 or "").strip().lower() or None
    if source_digest is not None and (
        len(source_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in source_digest)
    ):
        raise ValueError("source_payload_sha256 must be a lowercase SHA-256 hex digest")
    groups: dict[str, dict[str, Any]] = {}
    for raw_key, market in markets.items():
        if not isinstance(market, Mapping):
            continue
        market_type = str(market.get("marketType") or "").strip()
        missing_type = not market_type
        if missing_type:
            market_type = "__MISSING_MARKET_TYPE__"
        name = str(market.get("marketName") or "").strip()
        event_id = market.get("eventId")
        market_id = market.get("marketId", raw_key)
        alternate = "ALT" in market_type.upper() or "ALTERNATE" in (
            market_type + " " + name
        ).upper()
        row = groups.setdefault(market_type, {
            "sport": _text(sport, "sport").upper(),
            "sportsbook": "FANDUEL",
            "source_market_type": market_type,
            "observed_at": seen_at,
            "source_artifact": _text(source_artifact, "source_artifact"),
            "source_artifacts": {_text(source_artifact, "source_artifact")},
            "source_payload_sha256s": ({source_digest} if source_digest else set()),
            "source_market_names": set(),
            "tabs": set(),
            "example_event_ids": set(),
            "example_market_ids": set(),
            "market_instances": 0,
            "runner_instances": 0,
            "alternate": False,
            "missing_market_type": False,
        })
        if name:
            row["source_market_names"].add(name)
        if tab:
            row["tabs"].add(str(tab))
        if event_id not in (None, ""):
            row["example_event_ids"].add(str(event_id))
        if market_id not in (None, ""):
            row["example_market_ids"].add(str(market_id))
        row["market_instances"] += 1
        row["runner_instances"] += _runner_count(market)
        row["alternate"] = row["alternate"] or alternate
        row["missing_market_type"] = row["missing_market_type"] or missing_type

    observations = []
    for row in groups.values():
        observations.append({
            **row,
            "source_market_names": sorted(row["source_market_names"]),
            "tabs": sorted(row["tabs"]),
            "example_event_ids": sorted(row["example_event_ids"]),
            "example_market_ids": sorted(row["example_market_ids"]),
            "source_artifacts": sorted(row["source_artifacts"]),
            "source_payload_sha256s": sorted(row["source_payload_sha256s"]),
        })
    return sorted(observations, key=lambda row: row["source_market_type"])


def _classification_for(
    classifications: Mapping[str, Mapping[str, Any]], entry_id: str,
    market_type: str,
) -> Mapping[str, Any] | None:
    return classifications.get(entry_id) or classifications.get(market_type)


def update_registry(
    registry: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    *,
    generated_at: str,
    classifications: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge a complete or partial census without implying model support.

    Lifecycle and capability changes occur only through an explicit
    classification. Ordinary new observations update coverage history while
    preserving the prior research decision.
    """
    if registry.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported market coverage registry schema")
    existing_rows = registry.get("markets")
    if not isinstance(existing_rows, list):
        raise ValueError("registry markets must be a list")
    by_id = {str(row.get("coverage_id")): dict(row) for row in existing_rows}
    if len(by_id) != len(existing_rows) or "None" in by_id or "" in by_id:
        raise ValueError("registry contains duplicate or missing coverage_id")
    explicit = classifications or {}

    for observation in observations:
        sport = _text(observation.get("sport"), "observation sport").upper()
        book = _text(observation.get("sportsbook"), "observation sportsbook").upper()
        market_type = _text(
            observation.get("source_market_type"), "observation source_market_type"
        )
        seen_at = _utc(observation.get("observed_at"), "observation observed_at")
        entry_id = coverage_id(sport, book, market_type)
        row = by_id.get(entry_id)
        if row is None:
            missing_type = bool(observation.get("missing_market_type"))
            row = {
                "coverage_id": entry_id,
                "sport": sport,
                "sportsbook": book,
                "source_market_type": market_type,
                "source_market_names": [],
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
                "observation_count": 0,
                "market_instances": 0,
                "runner_instances": 0,
                "tabs": [],
                "example_event_ids": [],
                "example_market_ids": [],
                "source_artifacts": [],
                "source_payload_sha256s": [],
                "alternate": bool(observation.get("alternate")),
                "lifecycle_status": (
                    "BLOCKED_IDENTITY" if missing_type else "DISCOVERED"
                ),
                "canonical_market": None,
                "capabilities": _capabilities({"ingested": True}),
                "blockers": (["SOURCE_MARKET_TYPE_MISSING"] if missing_type else []),
                "status_reason": (
                    "FanDuel marketType was missing" if missing_type
                    else "Observed in sportsbook payload; not yet classified"
                ),
            }
            by_id[entry_id] = row

        row["last_seen_at"] = max(_utc(row["last_seen_at"], "last_seen_at"), seen_at)
        row["first_seen_at"] = min(_utc(row["first_seen_at"], "first_seen_at"), seen_at)
        row["observation_count"] = int(row.get("observation_count") or 0) + 1
        row["market_instances"] = int(row.get("market_instances") or 0) + int(
            observation.get("market_instances") or 0
        )
        row["runner_instances"] = int(row.get("runner_instances") or 0) + int(
            observation.get("runner_instances") or 0
        )
        row["alternate"] = bool(row.get("alternate")) or bool(observation.get("alternate"))
        for field, limit in (
            ("source_market_names", 25),
            ("tabs", 25),
            ("example_event_ids", 20),
            ("example_market_ids", 20),
            ("source_artifacts", 50),
            ("source_payload_sha256s", 50),
        ):
            values = {str(x) for x in row.get(field, []) if str(x)}
            values.update(str(x) for x in observation.get(field, []) if str(x))
            row[field] = sorted(values)[:limit]

        classification = _classification_for(explicit, entry_id, market_type)
        if classification is not None:
            status = _text(classification.get("lifecycle_status"), "lifecycle_status")
            if status not in LIFECYCLE_STATUSES:
                raise ValueError(f"unknown lifecycle status {status!r}")
            row["lifecycle_status"] = status
            row["canonical_market"] = classification.get("canonical_market")
            row["capabilities"] = _capabilities(classification.get("capabilities"))
            row["blockers"] = sorted({str(x) for x in classification.get("blockers", [])})
            row["status_reason"] = _text(
                classification.get("status_reason"), "status_reason"
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc(generated_at, "generated_at"),
        "markets": sorted(
            by_id.values(),
            key=lambda row: (
                row["sport"], row["sportsbook"], row["source_market_type"]
            ),
        ),
    }


def build_coverage_report(
    registry: Mapping[str, Any],
    *,
    observed_coverage_ids: Iterable[str],
    previous_observed_coverage_ids: Iterable[str] = (),
    capture_complete: bool,
) -> dict[str, Any]:
    """Summarize actionable gaps without treating failed capture as loss."""
    rows = registry.get("markets")
    if not isinstance(rows, list):
        raise ValueError("registry markets must be a list")
    current = set(observed_coverage_ids)
    previous = set(previous_observed_coverage_ids)
    known = {row["coverage_id"]: row for row in rows}
    unknown_ids = sorted(current.difference(known))
    if unknown_ids:
        raise ValueError("observed IDs are absent from registry: " + ", ".join(unknown_ids))

    def ids_where(predicate) -> list[str]:
        return sorted(row["coverage_id"] for row in rows if predicate(row))

    loss = sorted(previous.difference(current)) if capture_complete else []
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_generated_at": registry.get("generated_at"),
        "capture_complete": bool(capture_complete),
        "known_market_count": len(rows),
        "observed_market_count": len(current),
        "newly_discovered": sorted(current.difference(previous)),
        "not_normalized": ids_where(
            lambda row: not row.get("capabilities", {}).get("normalized")
        ),
        "without_historical_data": ids_where(
            lambda row: not row.get("capabilities", {}).get("historical_data_available")
        ),
        "without_graders": ids_where(
            lambda row: not row.get("capabilities", {}).get("grader")
        ),
        "alternate_lines_not_represented": ids_where(
            lambda row: bool(row.get("alternate"))
            and not row.get("capabilities", {}).get("normalized")
        ),
        "unexplained_coverage_loss": loss,
        "coverage_loss_suppressed_due_to_incomplete_capture": (
            sorted(previous.difference(current)) if not capture_complete else []
        ),
    }
