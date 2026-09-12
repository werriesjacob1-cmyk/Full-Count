#!/usr/bin/env python3
"""Monotonic publication guard for the derived NFL website snapshot.

A late manual dispatch or out-of-order workflow completion must never regress
what the public site has already shown. The prospective evidence artifact is
immutable; this guard protects only the derived website view.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _time(value, label):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not valid ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{label} must carry a timezone")
    return dt


def publication_verdict(current: dict | None, candidate: dict) -> str:
    if candidate.get("publication_status") != "RESEARCH_ONLY_NOT_PUBLIC_PICKS":
        raise ValueError("candidate is not a research-only NFL website payload")
    if (candidate.get("model") or {}).get("public_selector_validated") is not False:
        raise ValueError("candidate changed the public-selector safety boundary")

    candidate_time = _time(candidate.get("created_at"), "candidate created_at")
    if candidate_time is None:
        raise ValueError("candidate created_at is required")

    if not current or current.get("created_at") is None:
        return "NEWER"
    if current.get("publication_status") != "RESEARCH_ONLY_NOT_PUBLIC_PICKS":
        raise ValueError("current NFL website payload has an unexpected publication status")

    current_time = _time(current.get("created_at"), "current created_at")
    if candidate_time > current_time:
        return "NEWER"
    if candidate_time < current_time:
        raise ValueError("candidate would regress the NFL website to an older snapshot")

    if candidate.get("snapshot_sha256") == current.get("snapshot_sha256"):
        return "SAME"
    raise ValueError("same created_at carries a different snapshot seal")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()

    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    current_path = Path(args.current)
    current = (
        json.loads(current_path.read_text(encoding="utf-8"))
        if current_path.exists()
        else None
    )
    print(publication_verdict(current, candidate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
