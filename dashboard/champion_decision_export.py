"""Inactive, research-only replay of the dashboard publication decision boundary.

This module never fetches data, changes the Pages artifact, or asserts that a
quoted price was executable.  An export is a record of *intended* decisions;
only the deployment registry can prove public exposure.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

try:
    from .live_state import apply_live_overlay, parse_utc, prop_identity_key, stable_prop_id, state_digest, validate_payload_identities
    from .publication_registry import build_publication_manifest, validate_manifest, validate_registry
except ImportError:  # direct dashboard/prepare_pages_artifact.py execution
    from live_state import apply_live_overlay, parse_utc, prop_identity_key, stable_prop_id, state_digest, validate_payload_identities
    from publication_registry import build_publication_manifest, validate_manifest, validate_registry

SCHEMA = "fc-acc-009-dashboard-decision-1"


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def coverage_status(expected_game_ids, observed_game_ids, n, *, discovery_complete):
    """Only an independently authenticated expected-game set can prove zero."""
    if expected_game_ids is None or discovery_complete is None:
        return "UNVERIFIED"
    expected, observed = set(map(str, expected_game_ids)), set(map(str, observed_game_ids))
    if not discovery_complete or observed != expected:
        return "MISSING" if not observed else "PARTIAL"
    return "ZERO_PICK" if n == 0 else "COMPLETE"


def build_export(pre_data, pre_live, registry, preliminary, final_data, final_live,
                 manifest, *, run_id=None):
    """Copy the exact pre-withholding dashboard state and bind it to Pages.

    The final manifest is the real staged manifest, not a guessed later
    deployment.  A subsequent verifier must supply that exact manifest.
    """
    validate_payload_identities(pre_data)
    validate_payload_identities(final_data)
    validate_registry(registry)
    validate_manifest(manifest)
    source_commit, prepared_at = manifest["source_commit"], manifest["prepared_at"]
    cutoff = manifest["publication_cutoff_at"]
    if parse_utc(prepared_at) is None or parse_utc(cutoff) is None:
        raise ValueError("invalid publication time")
    for label in ("generated_at", "odds_fetched_at"):
        observed = pre_data.get(label)
        if observed is not None and (parse_utc(observed) is None
                                     or parse_utc(observed) > parse_utc(prepared_at)):
            raise ValueError(f"dashboard {label} is invalid or later than preparation")
    rebuilt_pre = build_publication_manifest(pre_data, pre_live, registry, source_commit,
                                             prepared_at, publication_cutoff_at=cutoff)
    if rebuilt_pre != preliminary:
        raise ValueError("pre-withholding manifest does not replay")
    rebuilt_final = build_publication_manifest(final_data, final_live, registry, source_commit,
                                               prepared_at, publication_cutoff_at=cutoff)
    for field in ("artifact_id", "candidates", "files", "known_public_ids", "known_publications"):
        if rebuilt_final[field] != manifest[field]:
            raise ValueError(f"final publication manifest mismatch: {field}")
    effective = apply_live_overlay(pre_data, pre_live)
    rows = effective["props"]
    ids = [stable_prop_id(row) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate effective candidate identity")
    top_ids = sorted(stable_prop_id(row) for row in rows
                     if row.get("recommendation_status") == "top_pick")
    candidate_ids = sorted(candidate["canonical_id"] for candidate in preliminary["candidates"])
    candidate_set = set(candidate_ids)
    staged_ids = sorted(candidate["canonical_id"] for candidate in manifest["candidates"])
    if not set(staged_ids).issubset(candidate_set):
        raise ValueError("staged publication candidate was absent from original dashboard decision")
    known_set = set(registry["entries"])
    decision_rows = []
    for row in rows:
        game, subject, market, needs, wager_side = prop_identity_key(row)
        pid = stable_prop_id(row)
        decision_rows.append({
            "canonical_id": pid, "game_pk": game, "subject": list(subject),
            "player_id": row.get("player_id"), "team": row.get("team"),
            "market": market, "needs": needs, "wager_side": wager_side,
            "line": (row.get("projection") or {}).get("value"),
            "recommendation_status": row.get("recommendation_status"),
            "status_reasons": row.get("status_reasons"),
            "hit_probability": row.get("hit_probability"),
            "market_odds": row.get("market_odds"),
            "market_observed_at": row.get("market_observed_at"),
            "game_state": row.get("game_state"),
            "new_publication_candidate": pid in candidate_set,
            "already_public": pid in known_set,
        })
    # A prior published pick is not a new selection on this run.  Neither
    # count is narrowed by research quote/authentication requirements.
    record = {
        "schema_version": SCHEMA, "research_only": True, "active": False,
        "publication_status": "INTENDED_NOT_EXPOSURE_PROVEN",
        "run": {"github_run_id": run_id, "source_commit": source_commit,
                "prepared_at": prepared_at, "dashboard_generated_at": pre_data.get("generated_at"),
                "odds_fetched_at": pre_data.get("odds_fetched_at"),
                "versions": pre_data.get("recommendation_metadata")},
        "source_limitations": ["FANDUEL_RAW_RESPONSE_NOT_ARCHIVED_HERE",
                               "PER_OFFER_OBSERVATION_TIME_NOT_AUTHENTICATED",
                               "MLB_SCHEDULE_AND_LINEUP_RAW_BYTES_NOT_ARCHIVED_HERE",
                               "DEPLOYMENT_EXPOSURE_REQUIRES_PUBLIC_REGISTRY"],
        "counts": {"dashboard_rows": len(rows), "dashboard_top_picks": len(top_ids),
                   "new_publication_policy_N": len(candidate_ids),
                   "staged_publication_candidates": len(staged_ids),
                   "already_public_ids": len(registry["entries"])},
        "decision_ids": {"dashboard_top_picks": top_ids,
                         "new_publication_candidates": candidate_ids,
                         "staged_manifest_candidates": staged_ids,
                         "withheld_after_preparation": sorted(candidate_set - set(staged_ids))},
        "decision_rows": decision_rows,
        "observed_games": sorted({str(row["game_pk"]) for row in rows}),
        "coverage_status": "UNVERIFIED",
        "pre_data": pre_data, "pre_live": pre_live, "registry": registry,
        "preliminary_manifest": preliminary,
        "final_data_sha256": state_digest(final_data),
        "final_live_sha256": state_digest(final_live),
        "manifest_sha256": _sha(manifest),
        "manifest_artifact_id": manifest["artifact_id"],
    }
    record["record_sha256"] = _sha(record)
    return record


def verify_export(record, manifest, final_data, final_live):
    if record.get("schema_version") != SCHEMA or record.get("research_only") is not True:
        raise ValueError("unsupported research export")
    saved = dict(record)
    claimed = saved.pop("record_sha256", None)
    if claimed != _sha(saved):
        raise ValueError("research export hash mismatch")
    if (record["manifest_sha256"] != _sha(manifest)
            or record["final_data_sha256"] != state_digest(final_data)
            or record["final_live_sha256"] != state_digest(final_live)):
        raise ValueError("staged Pages artifact does not match export")
    rebuilt = build_export(record["pre_data"], record["pre_live"], record["registry"],
                           record["preliminary_manifest"], final_data, final_live, manifest,
                           run_id=record["run"]["github_run_id"])
    if rebuilt != record:
        raise ValueError("dashboard policy decisions do not replay")
    return True


def write_once(record, directory):
    """Atomic visibility, no overwrite; an identical retry is a no-op."""
    if record.get("record_sha256") != _sha({k: v for k, v in record.items()
                                            if k != "record_sha256"}):
        raise ValueError("invalid record seal")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, record["manifest_artifact_id"] + ".json")
    content = _bytes(record) + b"\n"
    if os.path.exists(target):
        with open(target, "rb") as handle:
            existing = handle.read()
        if existing != content:
            raise FileExistsError("a different export already exists for this manifest")
        return target
    temporary = os.path.join(directory, "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # POSIX hard-link creation and Windows rename both fail if the
            # destination exists, while making the complete temp visible at
            # one instant. Windows sandboxed runners may forbid hard links.
            if os.name == "nt":
                os.rename(temporary, target)
            else:
                os.link(temporary, target)
        except FileExistsError:
            with open(target, "rb") as handle:
                existing = handle.read()
            if existing != content:
                raise FileExistsError("concurrent conflicting export")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
