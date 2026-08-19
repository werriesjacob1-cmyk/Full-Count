#!/usr/bin/env python3
"""Persist first exposure only after GitHub Pages deployment succeeds."""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .live_state import utc_now
    from .publication_registry import (
        DEFAULT_REGISTRY_PATH, confirm_publication, confirm_recovery_batches,
        confirm_verified_rollout_artifact, load_registry, validate_manifest,
        write_registry,
    )
    from .prediction_ledger import DEFAULT_LEDGER_PATH, append_publication_event
except ImportError:
    from live_state import utc_now
    from publication_registry import (
        DEFAULT_REGISTRY_PATH, confirm_publication, confirm_recovery_batches,
        confirm_verified_rollout_artifact, load_registry, validate_manifest,
        write_registry,
    )
    from prediction_ledger import DEFAULT_LEDGER_PATH, append_publication_event


def confirm(manifest_path, registry_path=DEFAULT_REGISTRY_PATH,
            deployed_at=None, provenance=None, ledger_path=DEFAULT_LEDGER_PATH):
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"publication manifest is unreadable: {manifest_path}: {exc}") from exc
    validate_manifest(manifest)
    registry = load_registry(registry_path)
    before_ids = set(registry["entries"])
    deployed_at = deployed_at or utc_now()
    changed = False
    migration = manifest.get("rollout_migration")
    if migration is not None and not registry["migration"].get("completed_at"):
        changed |= confirm_verified_rollout_artifact(
            registry, migration.get("candidates") or [], migration["published_at"],
            migration["provenance"],
        )
    recovery = manifest.get("prior_deployment_recovery")
    if recovery is not None:
        changed |= confirm_recovery_batches(registry, recovery)
    changed |= confirm_publication(registry, manifest, deployed_at, provenance)
    if changed:
        write_registry(registry_path, registry)
        # Every newly confirmed registry entry -- regardless of which of the
        # three paths above created it -- also becomes one immutable,
        # hash-chained ledger event, using the registry's own just-written
        # entry as the event payload so the two can never drift apart.
        for prop_id in sorted(set(registry["entries"]) - before_ids):
            append_publication_event(registry["entries"][prop_id], path=ledger_path)
    return changed, registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--deployed-at", default=None)
    parser.add_argument("--deployment-id", default=os.environ.get("PAGES_DEPLOYMENT_ID"))
    parser.add_argument("--deployment-url", default=os.environ.get("PAGES_DEPLOYMENT_URL"))
    args = parser.parse_args()
    provenance = {
        "source_commit": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "deployment_id": args.deployment_id,
        "deployment_url": args.deployment_url,
    }
    changed, registry = confirm(
        args.manifest, registry_path=args.registry,
        deployed_at=args.deployed_at, provenance=provenance, ledger_path=args.ledger,
    )
    print(
        f"Publication registry {'updated' if changed else 'already current'}: "
        f"{len(registry['entries'])} public Top Pick(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
