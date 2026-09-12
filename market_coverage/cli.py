#!/usr/bin/env python3
"""Build or update a market coverage registry from archived FanDuel payloads."""
from __future__ import annotations

import argparse
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
    return (tab or None), Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--classification")
    parser.add_argument(
        "--payload", action="append", required=True, metavar="[TAB=]PATH",
        help="archived raw FanDuel JSON; repeat for each event/tab payload",
    )
    parser.add_argument(
        "--capture-complete", action="store_true",
        help="allow missing prior families to appear as coverage loss",
    )
    args = parser.parse_args()

    registry_path = Path(args.registry)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        previous_ids = [row["coverage_id"] for row in registry.get("markets", [])]
    else:
        registry = new_registry(generated_at=args.observed_at)
        previous_ids = []

    classifications = {}
    if args.classification:
        classifications = json.loads(
            Path(args.classification).read_text(encoding="utf-8")
        )

    observations = []
    for raw_arg in args.payload:
        tab, path = _payload_arg(raw_arg)
        raw = path.read_bytes()
        payload = json.loads(raw)
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
    report = build_coverage_report(
        registry,
        observed_coverage_ids=observed_ids,
        previous_observed_coverage_ids=previous_ids,
        capture_complete=args.capture_complete,
    )
    _atomic_json(registry_path, registry)
    _atomic_json(Path(args.report), report)
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
