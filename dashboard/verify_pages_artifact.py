#!/usr/bin/env python3
"""Validate the complete lifecycle contract before a Pages upload."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import timedelta

try:
    from .live_state import (
        GAME_STATES, RECOMMENDATION_STATES, SETTLEMENT_STATES,
        SETTLEMENT_FIELDS, parse_utc, state_digest, validate_live_state,
        validate_payload_identities,
    )
    from .publication_registry import PUBLICATION_DEPLOYMENT_LEAD_SECONDS, validate_manifest
except ImportError:
    from live_state import (
        GAME_STATES, RECOMMENDATION_STATES, SETTLEMENT_STATES,
        SETTLEMENT_FIELDS, parse_utc, state_digest, validate_live_state,
        validate_payload_identities,
    )
    from publication_registry import PUBLICATION_DEPLOYMENT_LEAD_SECONDS, validate_manifest


REQUIRED_FILES = (
    "index.html", "app.css", "app.js", "data.json", "live.json",
    "publication_manifest.json",
)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{os.path.basename(path)} is unreadable: {exc}") from exc


def _validate_row(row, known_publications, candidate_ids):
    recommendation = row.get("recommendation_status")
    if recommendation not in RECOMMENDATION_STATES:
        raise ValueError(f"prop {row.get('id')!r} has invalid recommendation_status {recommendation!r}")
    current_game = row.get("game_state")
    if current_game not in GAME_STATES:
        raise ValueError(f"prop {row.get('id')!r} has invalid game_state {current_game!r}")
    settlement = row.get("settlement_state")
    if settlement not in SETTLEMENT_STATES:
        raise ValueError(f"prop {row.get('id')!r} has invalid settlement_state {settlement!r}")
    for field in ("game_start", "game_state_observed_at", "settlement_observed_at"):
        if parse_utc(row.get(field)) is None:
            raise ValueError(f"prop {row.get('id')!r} has invalid strict UTC {field}")
    # Reuse live-state fact validation by constructing a minimal document.
    fact = {field: row.get(field) for field in SETTLEMENT_FIELDS}
    probe = {
        "schema_version": 3, "identity_schema_version": 2,
        "updated_at": row["settlement_observed_at"],
        "prices_updated_at": None, "grades_updated_at": row["settlement_observed_at"],
        "props": {row["id"]: fact},
    }
    validate_live_state(probe, strict_ids=True)
    if current_game == "final" and settlement == "provisional_hit":
        raise ValueError(f"prop {row['id']!r} is Final with only a provisional settlement")
    if current_game == "pregame" and settlement != "open":
        raise ValueError(f"prop {row['id']!r} has a non-open pregame settlement")
    marker = row.get("published_top_pick_at")
    artifact = row.get("publication_artifact_id")
    if bool(marker) != bool(artifact):
        raise ValueError(f"prop {row['id']!r} has partial publication provenance")
    if marker:
        proof = known_publications.get(row["id"])
        snapshot = row.get("publication_snapshot")
        if (parse_utc(marker) is None or proof is None
                or proof.get("published_at") != marker
                or proof.get("artifact_id") != artifact
                or not isinstance(snapshot, dict)
                or state_digest(snapshot) != proof.get("snapshot_hash")):
            raise ValueError(f"prop {row['id']!r} has unproven publication provenance")
    token = row.get("publication_candidate_token")
    if token is not None:
        if (row["id"] not in candidate_ids
                or re.fullmatch(r"[0-9a-f]{64}", str(token)) is None):
            raise ValueError(f"prop {row['id']!r} has an invalid publication candidate token")


def verify(root):
    missing = [name for name in REQUIRED_FILES if not os.path.isfile(os.path.join(root, name))]
    if missing:
        raise ValueError(f"Pages artifact is missing: {', '.join(missing)}")
    data = _load_json(os.path.join(root, "data.json"))
    live = _load_json(os.path.join(root, "live.json"))
    manifest = _load_json(os.path.join(root, "publication_manifest.json"))
    try:
        with open(os.path.join(root, "app.js"), encoding="utf-8") as handle:
            app = handle.read()
    except OSError as exc:
        raise ValueError(f"app.js is unreadable: {exc}") from exc

    if data.get("schema_version") != 3 or data.get("identity_schema_version") != 2:
        raise ValueError("data.json does not use the supported lifecycle/identity schema")
    for field in ("generated_at", "odds_fetched_at"):
        if data.get(field) is not None and parse_utc(data[field]) is None:
            raise ValueError(f"data.json {field} must be strict UTC")
    if parse_utc(data.get("lifecycle_prepared_at")) is None:
        raise ValueError("data.json lifecycle_prepared_at must be strict UTC")
    validate_payload_identities(data)
    validate_live_state(live, strict_ids=True)
    validate_manifest(manifest)
    prepared_at = parse_utc(manifest["prepared_at"])
    cutoff_at = parse_utc(manifest.get("publication_cutoff_at"))
    if (cutoff_at is None
            or cutoff_at < prepared_at + timedelta(seconds=PUBLICATION_DEPLOYMENT_LEAD_SECONDS)):
        raise ValueError("publication manifest does not reserve the required deployment window")
    if (manifest.get("files") or {}).get("data.json") != state_digest(data):
        raise ValueError("publication manifest data.json hash does not match the staged artifact")
    if (manifest.get("files") or {}).get("live.json") != state_digest(live):
        raise ValueError("publication manifest live.json hash does not match the staged artifact")

    known_publications = manifest.get("known_publications") or {}
    known_public_ids = set(known_publications)
    candidate_ids = {candidate.get("canonical_id") for candidate in manifest.get("candidates") or []}
    data_ids = {row["id"] for row in data["props"]}
    rows_by_id = {row["id"]: row for row in data["props"]}
    for row in data["props"]:
        _validate_row(row, known_publications, candidate_ids)

    legal_orphans = known_public_ids | candidate_ids
    for prop_id, delta in live["props"].items():
        if prop_id not in data_ids and prop_id not in legal_orphans:
            raise ValueError(f"live.json contains an orphan outside the retention contract: {prop_id}")
        board_row = rows_by_id.get(prop_id) or {}
        if (board_row.get("settlement_authority") == "official_final"
                and delta.get("settlement_state")
                and delta.get("settlement_authority") != "official_final"):
            raise ValueError(
                f"live.json contains a lower-authority settlement over final prop {prop_id}"
            )
        if (board_row.get("game_state") == "final" and delta.get("game_state")
                and delta.get("game_state") != "final"):
            raise ValueError(f"live.json attempts to regress final game state for {prop_id}")
    if 'fetchJSON("live.json")' not in app:
        raise ValueError("app.js does not poll the live overlay")
    if "settlement_state" not in app or "game_state" not in app:
        raise ValueError("app.js does not consume the separated lifecycle schema")
    return {
        "props": len(data["props"]),
        "live_deltas": len(live["props"]),
        "publication_candidates": len(candidate_ids),
        "artifact_id": manifest["artifact_id"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="docs")
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except ValueError as exc:
        print(f"Pages artifact verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Pages artifact verified: {result['props']} props, "
        f"{result['live_deltas']} live deltas, "
        f"{result['publication_candidates']} new exposure candidate(s), "
        f"artifact {result['artifact_id']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
