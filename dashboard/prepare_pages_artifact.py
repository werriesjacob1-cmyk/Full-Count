#!/usr/bin/env python3
"""Stage, normalize, and version the exact directory uploaded to Pages."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from . import build_dashboard as bd
    from .live_state import (
        IDENTITY_SCHEMA_VERSION, SCHEMA_VERSION, atomic_write_json,
        before_betting_cutoff, canonical_prop_id, default_live_state, market_side_token,
        merge_live_states, migrate_legacy_live, parse_legacy_utc, parse_utc,
        stable_prop_id, state_digest, utc_now, validate_live_state,
        validate_payload_identities,
    )
    from .publication_registry import (
        DEFAULT_REGISTRY_PATH, build_publication_manifest,
        confirm_recovery_batches, confirm_verified_rollout_artifact, load_registry,
        PUBLICATION_DEPLOYMENT_LEAD_SECONDS,
        publication_candidate, validate_manifest,
    )
except ImportError:
    import build_dashboard as bd
    from live_state import (
        IDENTITY_SCHEMA_VERSION, SCHEMA_VERSION, atomic_write_json,
        before_betting_cutoff, canonical_prop_id, default_live_state, market_side_token,
        merge_live_states, migrate_legacy_live, parse_legacy_utc, parse_utc,
        stable_prop_id, state_digest, utc_now, validate_live_state,
        validate_payload_identities,
    )
    from publication_registry import (
        DEFAULT_REGISTRY_PATH, build_publication_manifest,
        confirm_recovery_batches, confirm_verified_rollout_artifact, load_registry,
        PUBLICATION_DEPLOYMENT_LEAD_SECONDS,
        publication_candidate, validate_manifest,
    )


DEFAULT_PUBLIC_BASE_URL = "https://werriesjacob1-cmyk.github.io/Full-Count"


def _strict_or_legacy_timestamp(value, label, *, allow_legacy=False):
    parsed = parse_utc(value)
    if parsed is None and allow_legacy:
        parsed = parse_legacy_utc(value)
    if parsed is None:
        raise ValueError(f"{label} is not a valid ISO timestamp: {value!r}")
    return parsed.isoformat()


def normalize_payload(data):
    """One-way identity/state normalization for the bounded rollout."""
    if not isinstance(data, dict) or not isinstance(data.get("props"), list):
        raise ValueError("dashboard data must contain a props list")
    normalized = copy.deepcopy(data)
    source_schema = normalized.get("schema_version")
    if source_schema not in (None, 1, 2, SCHEMA_VERSION):
        raise ValueError(f"unsupported dashboard schema version {source_schema!r}")
    legacy = source_schema in (None, 1, 2)
    id_map = {}
    for label in ("generated_at", "odds_fetched_at"):
        if normalized.get(label) is not None:
            normalized[label] = _strict_or_legacy_timestamp(
                normalized[label], label, allow_legacy=legacy,
            )
    for row in normalized["props"]:
        old_id = row.get("id")
        if legacy:
            row["identity_version"] = IDENTITY_SCHEMA_VERSION
            row["market_side"] = market_side_token(row)
            row["id"] = canonical_prop_id(row)
        else:
            # Current-schema corruption is not migration input. A claimed ID
            # inconsistent with the row must fail deployment, not be silently
            # rewritten into a different wager.
            stable_prop_id(row)
        if old_id:
            id_map[old_id] = row["id"]
        legacy_state = row.pop("lifecycle_state", None) or row.pop("grade", None)
        if legacy_state is not None and not legacy:
            raise ValueError("current dashboard schema contains a legacy lifecycle field")
        if legacy_state == "hit":
            row.update({
                "settlement_state": "provisional_hit",
                "settlement_authority": "live_observation",
                "settlement_observed_at": normalized.get("generated_at"),
                "settlement_source": "legacy_pages_rollout",
            })
        elif legacy_state in ("miss", "void", "ungraded"):
            row.update({
                "settlement_state": legacy_state,
                "settlement_authority": "official_final",
                "settlement_observed_at": normalized.get("generated_at"),
                "settlement_source": "legacy_pages_rollout_final_path",
            })
        for field in ("published_top_pick_at", "game_state_observed_at", "settlement_observed_at"):
            if row.get(field) is not None:
                row[field] = _strict_or_legacy_timestamp(
                    row[field], field, allow_legacy=legacy,
                )
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    validate_payload_identities(normalized)
    return normalized, id_map


def normalize_live(live, id_map):
    if not isinstance(live, dict):
        raise ValueError("live state must be an object")
    if live.get("schema_version") in (None, 1, 2):
        normalized = migrate_legacy_live(live)
    else:
        normalized = copy.deepcopy(live)
    remapped = default_live_state()
    for key in ("updated_at", "prices_updated_at", "grades_updated_at"):
        remapped[key] = normalized.get(key)
    for old_id, delta in (normalized.get("props") or {}).items():
        new_id = id_map.get(old_id, old_id)
        if not new_id.startswith("fc2:"):
            raise ValueError(f"cannot safely migrate orphan legacy live id {old_id!r}")
        one = default_live_state()
        one["updated_at"] = normalized.get("updated_at")
        one["prices_updated_at"] = normalized.get("prices_updated_at")
        one["grades_updated_at"] = normalized.get("grades_updated_at")
        one["props"][new_id] = copy.deepcopy(delta)
        remapped = merge_live_states(remapped, one)
    validate_live_state(remapped, strict_ids=True)
    return remapped


def _http_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Full-Count-lifecycle-rollout/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        last_modified = response.headers.get("Last-Modified")
    return json.loads(raw), raw, last_modified


def _http_optional_json(url):
    try:
        return _http_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None, None
        raise


def fetch_game_contexts_light(game_pks):
    """Fetch only MLB game feeds using stdlib for the deploy-time gate.

    Pages deployment runs every few minutes and should not initialize the
    research/grading dependency graph merely to revalidate game status.
    A failed game lookup is omitted, which the publication gate treats as
    unknown: existing public state survives and new exposure fails closed.
    """
    contexts = {}
    for raw_game_pk in sorted({value for value in game_pks if value is not None}, key=str):
        try:
            game_pk = int(raw_game_pk)
            feed, _, _ = _http_json(
                f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
            )
            contexts[game_pk] = {
                "status": ((feed.get("gameData") or {}).get("status") or {}),
                "feed": feed,
            }
        except Exception as exc:
            print(f"Game {raw_game_pk} deploy-time status failed; preserving/failing closed: {exc}")
    return contexts


def fetch_verified_rollout(base_url):
    """Read only the currently deployed artifact for one-time migration."""
    data, raw_data, modified_data = _http_json(base_url.rstrip("/") + "/data.json")
    live, raw_live, modified_live = _http_json(base_url.rstrip("/") + "/live.json")
    old_manifest, _, modified_manifest = _http_optional_json(
        base_url.rstrip("/") + "/publication_manifest.json"
    )
    modified = modified_manifest or modified_data or modified_live
    if not modified:
        raise RuntimeError("currently deployed artifact omitted Last-Modified provenance")
    published_at = parsedate_to_datetime(modified).astimezone(timezone.utc).isoformat()
    normalized_data, id_map = normalize_payload(data)
    normalized_live = normalize_live(live, id_map)
    artifact_id = hashlib.sha256(raw_data + b"\0" + raw_live).hexdigest()

    if old_manifest:
        validate_manifest(old_manifest)
        artifact_id = old_manifest["artifact_id"]
    effective = bd.apply_live_overlay(normalized_data, normalized_live)
    candidates = [
        publication_candidate(row, effective)
        for row in effective.get("props") or []
        if row.get("recommendation_status") == "top_pick"
        and before_betting_cutoff(row, published_at)
    ]
    return {
        "published_at": published_at,
        "candidates": candidates,
        "provenance": {
            "artifact_id": artifact_id,
            "source_commit": "verified-pre-rollout-public-pages",
            "files": {
                "data.json": hashlib.sha256(raw_data).hexdigest(),
                "live.json": hashlib.sha256(raw_live).hexdigest(),
            },
            "migration_source_url": base_url,
        },
        "id_map": id_map,
    }


def fetch_prior_deployment_recovery(base_url, registry):
    """Return deployment-proven exposure missing after a registry-write failure.

    The public manifest is part of the artifact that actually reached Pages.
    Its Last-Modified value is therefore a conservative deployment timestamp.
    Repository candidates and timestamp archives are deliberately not used.
    """
    url = base_url.rstrip("/") + "/publication_manifest.json"
    manifest, _, modified = _http_optional_json(url)
    if manifest is None:
        raise RuntimeError(
            "publication registry rollout is complete but the deployed Pages "
            "artifact has no publication manifest"
        )
    validate_manifest(manifest)
    if not modified:
        raise RuntimeError("deployed publication manifest omitted Last-Modified provenance")
    deployed_at = parsedate_to_datetime(modified).astimezone(timezone.utc).isoformat()

    batches = []
    if manifest.get("candidates"):
        valid_candidates = []
        for candidate in manifest["candidates"]:
            if before_betting_cutoff(candidate["snapshot"], deployed_at):
                valid_candidates.append(candidate)
            else:
                print(
                    "::error::Ignoring an artifact candidate first exposed at/after its "
                    f"betting cutoff: {candidate['canonical_id']}"
                )
    else:
        valid_candidates = []
    if valid_candidates:
        batches.append({
            "published_at": deployed_at,
            "provenance": {
                "artifact_id": manifest["artifact_id"],
                "source_commit": manifest.get("source_commit"),
                "files": copy.deepcopy(manifest.get("files") or {}),
                "recovery_source_url": url,
            },
            "candidates": copy.deepcopy(valid_candidates),
        })
    migration = manifest.get("rollout_migration")
    if migration:
        batches.append(copy.deepcopy(migration))
    prior = manifest.get("prior_deployment_recovery")
    if prior:
        batches.extend(copy.deepcopy(prior.get("batches") or []))

    # Preserve the earliest deployment proof for a wager, and omit facts the
    # durable registry already contains. This makes retry/replay idempotent.
    batches.sort(key=lambda item: parse_utc(item["published_at"]))
    seen = set(registry["entries"])
    filtered = []
    for batch in batches:
        candidates = []
        for candidate in batch.get("candidates") or []:
            prop_id = candidate["canonical_id"]
            if prop_id in seen:
                continue
            seen.add(prop_id)
            candidates.append(candidate)
        if candidates:
            item = copy.deepcopy(batch)
            item["candidates"] = candidates
            filtered.append(item)
    return {"batches": filtered} if filtered else None


def prepare(source, destination, registry_path=DEFAULT_REGISTRY_PATH,
            source_commit="unknown", prepared_at=None,
            public_base_url=DEFAULT_PUBLIC_BASE_URL, contexts=None):
    prepared_at = prepared_at or utc_now()
    if parse_utc(prepared_at) is None:
        raise ValueError("prepared_at must be strict UTC")
    if os.path.exists(destination):
        raise RuntimeError(f"refusing to overwrite existing staging directory: {destination}")
    shutil.copytree(source, destination)
    try:
        with open(os.path.join(destination, "data.json"), encoding="utf-8") as handle:
            raw_data = json.load(handle)
        with open(os.path.join(destination, "live.json"), encoding="utf-8") as handle:
            raw_live = json.load(handle)

        data, id_map = normalize_payload(raw_data)
        registry = load_registry(registry_path)
        rollout = None
        recovery = None
        if not registry["migration"].get("completed_at"):
            rollout = fetch_verified_rollout(public_base_url)
            id_map.update(rollout["id_map"])
            confirm_verified_rollout_artifact(
                registry, rollout["candidates"], rollout["published_at"], rollout["provenance"],
            )
        else:
            recovery = fetch_prior_deployment_recovery(public_base_url, registry)
            # Treat the deployed manifest as publication proof immediately in
            # this staged artifact. Persistence is still deferred until this
            # deployment succeeds, but a game crossing first pitch during a
            # prior registry-write outage must not make the public pick vanish.
            confirm_recovery_batches(registry, recovery)
        live = normalize_live(raw_live, id_map)

        if contexts is None:
            contexts = fetch_game_contexts_light(
                [row.get("game_pk") for row in data.get("props") or []]
            )
        schedule = {game_pk: {"status": value.get("status") or {}}
                    for game_pk, value in (contexts or {}).items()}
        data = bd.reconcile_public_lifecycle(
            data, prior_payload=None, live=live, schedule=schedule,
            now=prepared_at, registry=registry,
        )
        data["lifecycle_prepared_at"] = prepared_at

        publication_cutoff_at = (
            parse_utc(prepared_at)
            + timedelta(seconds=PUBLICATION_DEPLOYMENT_LEAD_SECONDS)
        ).isoformat()

        preliminary = build_publication_manifest(
            data, live, registry, source_commit, prepared_at,
            publication_cutoff_at=publication_cutoff_at,
        )
        candidate_ids = {candidate["canonical_id"] for candidate in preliminary["candidates"]}
        known_ids = set(registry["entries"])
        # A local Top Pick too close to first pitch cannot be proven to finish
        # deployment before the wagering boundary. Omit it from this staged
        # artifact instead of exposing a recommendation that cannot enter the
        # durable public population safely. Source docs/ remains untouched.
        data["props"] = [
            row for row in data.get("props") or []
            if row.get("recommendation_status") != "top_pick"
            or row.get("id") in candidate_ids
            or row.get("id") in known_ids
        ]
        bd._recount_payload(data)
        public_overlay_ids = {row.get("id") for row in data["props"]} | known_ids
        live["props"] = {
            prop_id: delta for prop_id, delta in live.get("props", {}).items()
            if prop_id in public_overlay_ids
        }
        for row in data.get("props") or []:
            if row.get("id") in candidate_ids:
                row["publication_candidate_token"] = state_digest({
                    "source_commit": source_commit, "prepared_at": prepared_at, "id": row["id"],
                })
        manifest = build_publication_manifest(
            data, live, registry, source_commit, prepared_at,
            publication_cutoff_at=publication_cutoff_at,
        )
        if rollout is not None:
            manifest["rollout_migration"] = {
                "published_at": rollout["published_at"],
                "provenance": rollout["provenance"],
                "candidates": rollout["candidates"],
            }
        if recovery is not None:
            manifest["prior_deployment_recovery"] = recovery
        validate_manifest(manifest)
        atomic_write_json(os.path.join(destination, "data.json"), data)
        atomic_write_json(os.path.join(destination, "live.json"), live)
        atomic_write_json(os.path.join(destination, "publication_manifest.json"), manifest, indent=2)
        return manifest
    except Exception:
        # The caller's source tree and last deployed Pages artifact remain
        # untouched. Leave staging for diagnostics; it is never authoritative.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.path.join(REPO_ROOT, "docs"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--prepared-at", default=None)
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    args = parser.parse_args()
    manifest = prepare(
        args.source, args.out, registry_path=args.registry,
        source_commit=args.source_commit, prepared_at=args.prepared_at,
        public_base_url=args.public_base_url,
    )
    print(
        f"Prepared Pages artifact {manifest['artifact_id']} with "
        f"{len(manifest['candidates'])} new exposure candidate(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
