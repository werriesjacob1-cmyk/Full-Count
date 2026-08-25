#!/usr/bin/env python3
"""Minimal durable registry of Top Picks proven to have reached Pages.

This is lifecycle infrastructure, not the future Prediction Receipts event
ledger. It records one immutable first-exposure snapshot per canonical wager.
Only the Pages deployment workflow confirms exposure after deploy-pages
succeeds; local qualification never writes a public record.
"""
from __future__ import annotations

import copy
import json
import os
import re

try:
    from .live_state import (
        IDENTITY_SCHEMA_VERSION,
        apply_live_overlay,
        atomic_write_json,
        before_betting_cutoff,
        canonical_prop_id,
        parse_utc,
        stable_prop_id,
        state_digest,
        validate_payload_identities,
    )
except ImportError:  # direct script execution
    from live_state import (
        IDENTITY_SCHEMA_VERSION,
        apply_live_overlay,
        atomic_write_json,
        before_betting_cutoff,
        canonical_prop_id,
        parse_utc,
        stable_prop_id,
        state_digest,
        validate_payload_identities,
    )

try:
    from .settlement_rules import supports_public_settlement
except ImportError:  # direct script execution
    from settlement_rules import supports_public_settlement


REGISTRY_SCHEMA_VERSION = 1
PUBLICATION_MANIFEST_SCHEMA_VERSION = 1
ROLLOUT_VERSION = "pre-phase-v-live-lifecycle-v1"
PUBLICATION_DEPLOYMENT_LEAD_SECONDS = 15 * 60
DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "public_top_picks", "registry.json",
)

SNAPSHOT_FIELDS = (
    "id", "identity_version", "game_pk", "player_id", "combo_player_ids",
    "type", "name", "team", "matchup", "game_start", "stat", "market_side",
    "bet_side", "direction", "lean", "projection", "prop", "hit_probability",
    "market_odds", "market_implied", "market_edge", "market_hold", "price_clears",
    "recommendation_status", "status_reasons", "why", "watchouts", "prob_ci",
    "reliability", "reliability_note", "sample_n", "lineup_assumed",
    # 2026-08-25 registry-integrity reconciliation: these two were the real,
    # concrete gap found -- docs/data.json's own `props` rows (the exact
    # `row` this function reads) already carry both `lift` (hit_probability
    # minus the market/league-rate baseline) and `base_rate` (that baseline
    # itself), but neither was ever copied into the immutable snapshot, so
    # the one durable, first-exposure record of a published Top Pick could
    # never answer "was this a positive- or negative-lift pick" after the
    # fact -- exactly the field a lift-vs-outcome accuracy comparison needs.
    # Purely additive: immutable_snapshot() already skips any field absent
    # from a given row, so this changes nothing about already-written
    # registry entries (which simply won't have these two keys -- the same
    # graceful degradation this project already applies to every other
    # historical-data-integrity boundary), only what NEW entries capture
    # going forward.
    "base_rate", "lift",
    # 2026-08-25 stable-lift-reference rollout: the shrinkage prior
    # (base_rate, above -- unchanged, still feeds predicted_prob) and the
    # LIFT REFERENCE (this pair) are now two separate concepts -- see
    # stable_base_rate.py's own docstring. Additive, same graceful
    # degradation as base_rate/lift above: absent on every entry published
    # before this rollout and on every stat other than hits_runs_rbis/
    # runs/rbis, present going forward wherever a real season-to-date
    # reference existed at publication time. Lets a future audit answer
    # "did this pick's ACTUAL Lean-gating lift differ from its slate-scoped
    # display lift" from the immutable record itself, without recomputing
    # anything.
    "lift_reference_rate", "stable_lift",
)
VERSION_FIELDS = (
    "model_version", "selection_policy_version", "calibration_version",
    "recommendation_policy_version", "feature_version", "data_version",
    "git_sha", "prediction_timestamp", "odds_fetched_at", "board_generated_at",
)


def default_registry():
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "rollout_version": ROLLOUT_VERSION,
        "updated_at": None,
        "migration": {
            "version": ROLLOUT_VERSION,
            "completed_at": None,
            "source_artifact_id": None,
        },
        "entries": {},
    }


def validate_registry(registry):
    if not isinstance(registry, dict) or not isinstance(registry.get("entries"), dict):
        raise ValueError("publication registry must contain an entries object")
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError("unsupported publication registry schema")
    if registry.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported publication identity schema")
    if registry.get("rollout_version") != ROLLOUT_VERSION:
        raise ValueError("unsupported publication rollout version")
    if registry.get("updated_at") is not None and parse_utc(registry["updated_at"]) is None:
        raise ValueError("registry updated_at must be strict UTC")
    migration = registry.get("migration")
    if not isinstance(migration, dict) or migration.get("version") != ROLLOUT_VERSION:
        raise ValueError("registry migration contract is missing")
    if migration.get("completed_at") is not None and parse_utc(migration["completed_at"]) is None:
        raise ValueError("registry migration completion time must be strict UTC")
    seen_identities = set()
    for key, entry in registry["entries"].items():
        if not isinstance(entry, dict) or entry.get("canonical_id") != key:
            raise ValueError(f"registry entry key/canonical id mismatch: {key!r}")
        if parse_utc(entry.get("first_published_at")) is None:
            raise ValueError(f"registry entry {key!r} has no valid first publication time")
        snapshot = entry.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"registry entry {key!r} has no immutable snapshot")
        if stable_prop_id(snapshot) != key:
            raise ValueError(f"registry entry {key!r} snapshot identity is inconsistent")
        if snapshot.get("recommendation_status") != "top_pick":
            raise ValueError(f"registry entry {key!r} was not exposed as a Top Pick")
        identity = tuple(entry.get("settlement_identity") or ())
        if not identity:
            raise ValueError(f"registry entry {key!r} has no settlement identity")
        encoded = json.dumps(entry["settlement_identity"], sort_keys=True)
        if encoded in seen_identities:
            raise ValueError("publication registry contains a duplicate settlement identity")
        seen_identities.add(encoded)
        provenance = entry.get("publication_provenance")
        if (not isinstance(provenance, dict)
                or re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("artifact_id") or "")) is None):
            raise ValueError(f"registry entry {key!r} has no deployment provenance")
        for label in ("artifact_prepared_at",):
            if provenance.get(label) is not None and parse_utc(provenance[label]) is None:
                raise ValueError(f"registry entry {key!r} has invalid {label}")
        for label in ("data_hash", "live_hash"):
            if (provenance.get(label) is not None
                    and re.fullmatch(r"[0-9a-f]{64}", str(provenance[label])) is None):
                raise ValueError(f"registry entry {key!r} has invalid {label}")
    return True


def load_registry(path=DEFAULT_REGISTRY_PATH):
    if not os.path.exists(path):
        return default_registry()
    try:
        with open(path, encoding="utf-8") as handle:
            registry = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"publication registry is unreadable: {path}: {exc}") from exc
    try:
        validate_registry(registry)
    except ValueError as exc:
        raise RuntimeError(f"publication registry is invalid: {path}: {exc}") from exc
    return registry


def write_registry(path, registry):
    validate_registry(registry)
    atomic_write_json(path, registry, indent=2)


def immutable_snapshot(row, payload):
    snapshot = {field: copy.deepcopy(row[field]) for field in SNAPSHOT_FIELDS if field in row}
    snapshot["id"] = stable_prop_id(row)
    snapshot["identity_version"] = IDENTITY_SCHEMA_VERSION
    metadata = payload.get("recommendation_metadata") or {}
    snapshot["versions"] = {}
    for field in VERSION_FIELDS:
        value = row.get(field)
        if value is None:
            value = metadata.get(field)
        if value is not None:
            snapshot["versions"][field] = copy.deepcopy(value)
    snapshot["qualification_timestamp"] = (
        row.get("prediction_timestamp") or metadata.get("prediction_timestamp")
    )
    snapshot["board_timestamp"] = payload.get("generated_at")
    snapshot["slate_date"] = payload.get("date")
    return snapshot


def _identity_json(row):
    try:
        from .live_state import prop_identity_key
    except ImportError:  # direct script execution through dashboard/*.py
        from live_state import prop_identity_key
    game, subject, stat, needs, side = prop_identity_key(row)
    return [game, list(subject), stat, needs, side]


def _candidate(row, payload):
    return {
        "canonical_id": stable_prop_id(row),
        "settlement_identity": _identity_json(row),
        "slate_date": payload.get("date"),
        "snapshot": immutable_snapshot(row, payload),
    }


def publication_candidate(row, payload):
    """Snapshot proven legacy exposure during the bounded rollout migration.

    This deliberately does not apply the prospective settleability gate: if an
    unsupported market was already public, the registry must preserve that
    truth and leave it ungraded rather than erase exposure retroactively.
    """
    return _candidate(row, payload)


def build_publication_manifest(data, live, registry, source_commit, prepared_at,
                               publication_cutoff_at=None):
    """Describe exactly what a prospective Pages artifact would expose.

    Calling this function does not mutate the registry. That mutation is
    permitted only after a successful deployment.
    """
    if parse_utc(prepared_at) is None:
        raise ValueError("manifest prepared_at must be strict UTC")
    publication_cutoff_at = publication_cutoff_at or prepared_at
    cutoff_dt = parse_utc(publication_cutoff_at)
    if cutoff_dt is None or cutoff_dt < parse_utc(prepared_at):
        raise ValueError("manifest publication_cutoff_at must be strict UTC and not precede preparation")
    validate_registry(registry)
    validate_payload_identities(data)
    effective = apply_live_overlay(data, live)
    candidates = []
    for row in effective.get("props") or []:
        prop_id = stable_prop_id(row)
        if prop_id in registry["entries"]:
            continue
        if row.get("recommendation_status") != "top_pick":
            continue
        if not supports_public_settlement(row):
            continue
        if row.get("game_state") not in (None, "pregame"):
            continue
        if not before_betting_cutoff(row, publication_cutoff_at):
            continue
        candidates.append(_candidate(row, effective))
    files = {
        "data.json": state_digest(data),
        "live.json": state_digest(live),
    }
    artifact_id = state_digest({
        "source_commit": source_commit,
        "prepared_at": prepared_at,
        "files": files,
        "candidate_ids": sorted(item["canonical_id"] for item in candidates),
    })
    known_publications = {}
    for prop_id, entry in registry["entries"].items():
        provenance = entry["publication_provenance"]
        known_publications[prop_id] = {
            "published_at": entry["first_published_at"],
            "artifact_id": provenance["artifact_id"],
            "snapshot_hash": state_digest(entry["snapshot"]),
            "source_commit": provenance.get("source_commit"),
            "workflow_run_id": provenance.get("workflow_run_id"),
            "deployment_id": provenance.get("deployment_id"),
        }
    return {
        "schema_version": PUBLICATION_MANIFEST_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "prepared_at": prepared_at,
        "publication_cutoff_at": publication_cutoff_at,
        "source_commit": source_commit,
        "files": files,
        "known_public_ids": sorted(registry["entries"]),
        "known_publications": known_publications,
        "candidates": candidates,
        "rollout_migration": None,
        "prior_deployment_recovery": None,
    }


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PUBLICATION_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported publication manifest schema")
    if parse_utc(manifest.get("prepared_at")) is None:
        raise ValueError("publication manifest prepared_at is invalid")
    cutoff = parse_utc(manifest.get("publication_cutoff_at") or manifest.get("prepared_at"))
    if cutoff is None or cutoff < parse_utc(manifest["prepared_at"]):
        raise ValueError("publication manifest cutoff is invalid")
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or len(artifact_id) != 64:
        raise ValueError("publication manifest artifact_id is invalid")
    if not isinstance(manifest.get("files"), dict):
        raise ValueError("publication manifest file hashes are missing")
    for required_file in ("data.json", "live.json"):
        if re.fullmatch(r"[0-9a-f]{64}", str(manifest["files"].get(required_file) or "")) is None:
            raise ValueError(f"publication manifest {required_file} hash is invalid")
    known_ids = manifest.get("known_public_ids")
    known = manifest.get("known_publications")
    if not isinstance(known_ids, list) or not isinstance(known, dict):
        raise ValueError("publication manifest known-publication proof is missing")
    if sorted(known) != sorted(known_ids) or len(set(known_ids)) != len(known_ids):
        raise ValueError("publication manifest known-publication ids are inconsistent")
    for prop_id, proof in known.items():
        if not isinstance(prop_id, str) or not prop_id.startswith("fc2:"):
            raise ValueError("publication manifest contains an invalid known public id")
        if not isinstance(proof, dict) or parse_utc(proof.get("published_at")) is None:
            raise ValueError("publication manifest known publication timestamp is invalid")
        proof_artifact = proof.get("artifact_id")
        if not isinstance(proof_artifact, str) or len(proof_artifact) != 64:
            raise ValueError("publication manifest known publication artifact is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", str(proof.get("snapshot_hash") or "")) is None:
            raise ValueError("publication manifest known publication snapshot hash is invalid")
    seen = set()
    for candidate in manifest.get("candidates") or []:
        prop_id = candidate.get("canonical_id")
        if not isinstance(prop_id, str) or prop_id in seen:
            raise ValueError("publication manifest contains a duplicate/invalid candidate id")
        snapshot = candidate.get("snapshot")
        if not isinstance(snapshot, dict) or stable_prop_id(snapshot) != prop_id:
            raise ValueError("publication candidate snapshot identity is invalid")
        if (snapshot.get("recommendation_status") != "top_pick"
                or candidate.get("settlement_identity") != _identity_json(snapshot)):
            raise ValueError("publication candidate is not a canonical Top Pick snapshot")
        if not supports_public_settlement(snapshot):
            raise ValueError("publication candidate has no supported settlement path")
        if not before_betting_cutoff(snapshot, cutoff):
            raise ValueError("publication candidate crosses its artifact betting cutoff")
        seen.add(prop_id)
    migration = manifest.get("rollout_migration")
    if migration is not None:
        if not isinstance(migration, dict) or parse_utc(migration.get("published_at")) is None:
            raise ValueError("publication manifest rollout migration is invalid")
        if not isinstance(migration.get("provenance"), dict):
            raise ValueError("rollout migration provenance is missing")
        for candidate in migration.get("candidates") or []:
            snapshot = candidate.get("snapshot")
            if (not isinstance(snapshot, dict)
                    or stable_prop_id(snapshot) != candidate.get("canonical_id")
                    or snapshot.get("recommendation_status") != "top_pick"
                    or candidate.get("settlement_identity") != _identity_json(snapshot)):
                raise ValueError("rollout migration candidate identity is invalid")
    recovery = manifest.get("prior_deployment_recovery")
    if recovery is not None:
        if not isinstance(recovery, dict) or not isinstance(recovery.get("batches"), list):
            raise ValueError("prior deployment recovery is invalid")
        for batch in recovery["batches"]:
            if not isinstance(batch, dict) or parse_utc(batch.get("published_at")) is None:
                raise ValueError("prior deployment recovery batch is invalid")
            if not isinstance(batch.get("provenance"), dict):
                raise ValueError("prior deployment recovery provenance is missing")
            for candidate in batch.get("candidates") or []:
                snapshot = candidate.get("snapshot")
                if (not isinstance(snapshot, dict)
                        or stable_prop_id(snapshot) != candidate.get("canonical_id")
                        or snapshot.get("recommendation_status") != "top_pick"
                        or candidate.get("settlement_identity") != _identity_json(snapshot)):
                    raise ValueError("prior deployment recovery candidate identity is invalid")
    return True


def confirm_publication(registry, manifest, deployed_at, provenance=None):
    """Idempotently establish first exposure after deploy-pages succeeds."""
    validate_registry(registry)
    validate_manifest(manifest)
    if parse_utc(deployed_at) is None:
        raise ValueError("deployment confirmation timestamp must be strict UTC")
    for candidate in manifest.get("candidates") or []:
        if not before_betting_cutoff(candidate["snapshot"], deployed_at):
            raise ValueError(
                f"candidate {candidate['canonical_id']} was not deployed before first pitch"
            )
    provenance = dict(provenance or {})
    artifact_id = manifest["artifact_id"]
    changed = False
    for candidate in manifest.get("candidates") or []:
        prop_id = candidate["canonical_id"]
        if prop_id in registry["entries"]:
            continue
        deployment_provenance = {
            "artifact_id": artifact_id,
            "source_commit": manifest.get("source_commit") or provenance.get("source_commit"),
            "workflow_run_id": provenance.get("run_id"),
            "deployment_id": provenance.get("deployment_id"),
            "deployment_url": provenance.get("deployment_url"),
            "artifact_prepared_at": manifest.get("prepared_at"),
            "data_hash": (manifest.get("files") or {}).get("data.json"),
            "live_hash": (manifest.get("files") or {}).get("live.json"),
        }
        registry["entries"][prop_id] = {
            "canonical_id": prop_id,
            "settlement_identity": copy.deepcopy(candidate["settlement_identity"]),
            "slate_date": candidate.get("slate_date"),
            "first_published_at": deployed_at,
            "publication_provenance": deployment_provenance,
            "snapshot": copy.deepcopy(candidate["snapshot"]),
        }
        changed = True
    if changed:
        previous = parse_utc(registry.get("updated_at"))
        observed = parse_utc(deployed_at)
        registry["updated_at"] = (
            registry.get("updated_at") if previous is not None and previous >= observed
            else deployed_at
        )
    validate_registry(registry)
    return changed


def confirm_recovery_batches(registry, recovery):
    """Apply proven prior-deployment batches in memory, idempotently."""
    changed = False
    for batch in (recovery or {}).get("batches") or []:
        recovery_manifest = {
            "schema_version": PUBLICATION_MANIFEST_SCHEMA_VERSION,
            "artifact_id": batch["provenance"]["artifact_id"],
            "prepared_at": batch["published_at"],
            "publication_cutoff_at": batch["published_at"],
            "source_commit": (
                batch["provenance"].get("source_commit")
                or "recovered-public-pages"
            ),
            "files": batch["provenance"].get("files") or {},
            "known_public_ids": [],
            "known_publications": {},
            "candidates": batch.get("candidates") or [],
            "rollout_migration": None,
            "prior_deployment_recovery": None,
        }
        changed |= confirm_publication(
            registry, recovery_manifest, batch["published_at"], batch["provenance"],
        )
    return changed


def confirm_verified_rollout_artifact(registry, candidates, published_at, provenance):
    """One-time migration from only the verified currently deployed artifact."""
    if registry["migration"].get("completed_at"):
        return False
    manifest = {
        "schema_version": PUBLICATION_MANIFEST_SCHEMA_VERSION,
        "artifact_id": provenance["artifact_id"],
        "prepared_at": published_at,
        "publication_cutoff_at": published_at,
        "source_commit": provenance.get("source_commit") or "pre-rollout-public-pages",
        "files": provenance.get("files") or {},
        "known_public_ids": [],
        "known_publications": {},
        "candidates": candidates,
    }
    confirm_publication(registry, manifest, published_at, provenance)
    registry["migration"] = {
        "version": ROLLOUT_VERSION,
        "completed_at": published_at,
        "source_artifact_id": provenance["artifact_id"],
    }
    registry["updated_at"] = published_at
    validate_registry(registry)
    return True


def published_snapshots_for_date(registry, slate_date):
    validate_registry(registry)
    out = []
    for entry in registry["entries"].values():
        if entry.get("slate_date") != slate_date:
            continue
        snapshot = copy.deepcopy(entry["snapshot"])
        snapshot.update({
            "published_top_pick_at": entry["first_published_at"],
            "publication_artifact_id": entry["publication_provenance"]["artifact_id"],
            "publication_source_commit": entry["publication_provenance"].get("source_commit"),
            "publication_run_id": entry["publication_provenance"].get("workflow_run_id"),
            "publication_deployment_id": entry["publication_provenance"].get("deployment_id"),
        })
        out.append(snapshot)
    return sorted(out, key=lambda row: row["id"])


def all_published_snapshots(registry):
    dates = sorted({entry.get("slate_date") for entry in registry["entries"].values()
                    if entry.get("slate_date")})
    return [row for date in dates for row in published_snapshots_for_date(registry, date)]
