#!/usr/bin/env python3
"""Build or update a market coverage registry from archived FanDuel payloads."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import tempfile
from pathlib import Path

from market_coverage.registry import (
    build_coverage_report,
    extract_fanduel_observations,
    new_registry,
    update_registry,
)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _payload_arg(value: str) -> tuple[str | None, Path]:
    if "=" not in value:
        return None, Path(value)
    tab, path = value.split("=", 1)
    return (tab.strip() or None), Path(path)


def _values(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"capture plan {label} must be a non-empty list")
    cleaned = [str(item).strip() for item in value]
    if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(f"capture plan {label} must contain unique non-empty values")
    return sorted(cleaned)


def _payload_event_ids(payload: dict) -> set[str]:
    attachments = payload.get("attachments")
    if not isinstance(attachments, dict):
        return set()
    event_ids: set[str] = set()
    events = attachments.get("events")
    if isinstance(events, dict):
        for key, event in events.items():
            if isinstance(event, dict):
                value = event.get("eventId", event.get("id", key))
            else:
                value = key
            if value not in (None, ""):
                event_ids.add(str(value))
    markets = attachments.get("markets")
    if isinstance(markets, dict):
        for market in markets.values():
            if isinstance(market, dict) and market.get("eventId") not in (None, ""):
                event_ids.add(str(market["eventId"]))
    return event_ids


def _verify_capture_plan(
    plan: dict, *, sport: str, observed_pairs: list[tuple[str, str]]
) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("capture plan must be a JSON object")
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported capture plan schema")
    plan_sport = str(plan.get("sport") or "").strip().upper()
    if plan_sport != sport.strip().upper():
        raise ValueError("capture plan sport does not match --sport")
    sportsbook = str(plan.get("sportsbook") or "").strip().upper()
    if sportsbook != "FANDUEL":
        raise ValueError("capture plan sportsbook must be FANDUEL")
    event_ids = _values(plan.get("event_ids"), "event_ids")
    tabs = _values(plan.get("tabs"), "tabs")
    discovery_artifact = str(plan.get("discovery_artifact") or "").strip()
    discovery_sha256 = str(plan.get("discovery_sha256") or "").strip().lower()
    if not discovery_artifact:
        raise ValueError("capture plan discovery_artifact is required")
    if len(discovery_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in discovery_sha256
    ):
        raise ValueError("capture plan discovery_sha256 must be a SHA-256 hex digest")
    expected = {(event_id, tab) for event_id in event_ids for tab in tabs}
    counts = Counter(observed_pairs)
    missing = sorted(expected.difference(counts))
    unexpected = sorted(set(counts).difference(expected))
    duplicates = sorted(pair for pair, count in counts.items() if count != 1)
    if missing or unexpected or duplicates:
        raise ValueError(
            "capture plan mismatch: "
            f"missing={missing}, unexpected={unexpected}, duplicates={duplicates}"
        )
    identity = {
        "sport": plan_sport,
        "sportsbook": sportsbook,
        "event_ids": event_ids,
        "tabs": tabs,
        "discovery_artifact": discovery_artifact,
        "discovery_sha256": discovery_sha256,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return {
        **identity,
        "scope_id": "fc-capture1:" + hashlib.sha256(raw).hexdigest()[:24],
        "expected_event_tab_pairs": len(expected),
        "observed_event_tab_pairs": len(observed_pairs),
        "verified": True,
    }


def _previous_scope_ids(
    previous_report: object, capture_scope: dict
) -> tuple[list[str], bool]:
    if not isinstance(previous_report, dict):
        return [], False
    prior_scope = previous_report.get("capture_scope")
    if not isinstance(prior_scope, dict):
        return [], False
    prior_ids = previous_report.get("observed_coverage_ids")
    if (
        previous_report.get("capture_complete") is not True
        or prior_scope.get("scope_id") != capture_scope.get("scope_id")
        or not isinstance(prior_ids, list)
    ):
        return [], False
    return [str(value) for value in prior_ids], True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--classification")
    parser.add_argument(
        "--capture-plan",
        help="JSON scope with event_ids and tabs; required to report coverage loss",
    )
    parser.add_argument(
        "--payload", action="append", required=True, metavar="[TAB=]PATH",
        help="archived raw FanDuel JSON; repeat for each event/tab payload",
    )
    args = parser.parse_args()

    registry_path = Path(args.registry)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        known_before_ids = [row["coverage_id"] for row in registry.get("markets", [])]
    else:
        registry = new_registry(generated_at=args.observed_at)
        known_before_ids = []

    previous_report = None
    report_path = Path(args.report)
    if report_path.exists():
        previous_report = json.loads(report_path.read_text(encoding="utf-8"))

    classifications = {}
    if args.classification:
        classifications = json.loads(
            Path(args.classification).read_text(encoding="utf-8")
        )

    observations = []
    observed_pairs: list[tuple[str, str]] = []
    for raw_arg in args.payload:
        tab, path = _payload_arg(raw_arg)
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"payload must be a JSON object: {path.name}")
        if args.capture_plan:
            if not tab:
                raise ValueError("every payload needs TAB=PATH with --capture-plan")
            event_ids = _payload_event_ids(payload)
            if not event_ids:
                raise ValueError(f"payload has no event identity: {path.name}")
            observed_pairs.extend((event_id, tab) for event_id in event_ids)
        observations.extend(extract_fanduel_observations(
            payload,
            sport=args.sport,
            observed_at=args.observed_at,
            source_artifact=f"{tab or 'default'}:{path.name}",
            tab=tab,
            source_payload_sha256=hashlib.sha256(raw).hexdigest(),
        ))

    registry = update_registry(
        registry,
        observations,
        generated_at=args.observed_at,
        classifications=classifications,
    )
    observed_ids = sorted({
        row["coverage_id"]
        for row in registry["markets"]
        if any(
            row["source_market_type"] == observation["source_market_type"]
            and row["sport"] == observation["sport"]
            and row["sportsbook"] == observation["sportsbook"]
            for observation in observations
        )
    })
    capture_scope = None
    previous_ids = []
    comparison_ready = False
    if args.capture_plan:
        plan = json.loads(Path(args.capture_plan).read_text(encoding="utf-8"))
        capture_scope = _verify_capture_plan(
            plan, sport=args.sport, observed_pairs=observed_pairs
        )
        previous_ids, comparison_ready = _previous_scope_ids(
            previous_report, capture_scope
        )
    elif previous_report:
        previous_ids = previous_report.get("observed_coverage_ids", [])

    report = build_coverage_report(
        registry,
        observed_coverage_ids=observed_ids,
        previous_observed_coverage_ids=previous_ids,
        known_before_coverage_ids=known_before_ids,
        capture_complete=bool(capture_scope),
        capture_scope=capture_scope,
        coverage_comparison_ready=comparison_ready,
    )
    _atomic_json(registry_path, registry)
    _atomic_json(report_path, report)
    print(json.dumps({
        "registry": str(registry_path),
        "report": args.report,
        "known_market_count": report["known_market_count"],
        "observed_market_count": report["observed_market_count"],
        "not_normalized": len(report["not_normalized"]),
        "without_graders": len(report["without_graders"]),
        "alternate_lines_not_represented": len(
            report["alternate_lines_not_represented"]
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
