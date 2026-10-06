#!/usr/bin/env python3
"""Byte-preserving migration of TEMPORARY (GitHub Actions artifact) V3 tapes to durable storage (Cloudflare R2).

Milestone FC-MLB-001C (Jacob, 2026-10-06): Actions artifacts are the short-term authoritative store only; every
artifact-backed tape must be copied to R2 before retention threatens it (retention_monitor.py). Contract:
  * the source is the exact sealed artifact (artifact_api.fetch: run + sha-bound name + artifact id + zip digest),
    re-verified HERE independently against the SEALED sha256 + size before anything is sent;
  * the copy is the same bytes (no rewrite, no regeneration, no recompression): tape_store.put_verified = create-only
    upload to the content address + read-back + sha256/size verification;
  * the unit's seal, manifest, CHAIN and the sealed locator are NEVER modified -- the scientific identity
    (tape sha256 + size, payload, seal) is unchanged. The durable location is ADDED as a create-only record
    STORAGE_MIGRATIONS/<unit>.json on the evidence ref, which carries the original artifact identity as provenance;
  * any failure leaves no record (and the artifact remains the authoritative copy until it expires).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tape_store as TS  # noqa: E402

SCHEMA = "fc-v3-storage-migration-1"
DIR = "STORAGE_MIGRATIONS"
PROOF = "tape_artifact.json"


class MigrationError(Exception):
    pass


def _canon_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record_path(evidence_root, unit):
    return os.path.join(evidence_root, DIR, f"{unit}.json")


def sealed_identity(evidence_root, unit):
    """(seal, sealed tape locator, upload proof or None) for a CHAINED unit; the manifest and proof must be the
    exact sealed artifacts. Raises MigrationError."""
    import verify_evidence as VE
    seals = {f"{s['date']}_{s['window']}": s for s in VE.load_chain(evidence_root)}
    if unit not in seals:
        raise MigrationError(f"{unit} is not a chained unit")
    seal = seals[unit]
    d = os.path.join(evidence_root, "seals", unit)
    arts = seal.get("artifacts_sha256") or {}
    for name in ("manifest.json.gz", PROOF):
        if name in arts and VE.sha256_bytes(open(os.path.join(d, name), "rb").read()) != arts[name]:
            raise MigrationError(f"{unit}/{name} is not the sealed file")
    man = json.load(gzip.open(os.path.join(d, "manifest.json.gz")))
    if man.get("manifest_sha256") != seal.get("manifest_sha256"):
        raise MigrationError(f"{unit}: manifest is not the sealed one")
    loc = (man.get("shadow_provenance") or {}).get("tape_store") or {}
    proof = json.load(open(os.path.join(d, PROOF))) if PROOF in arts else None
    return seal, loc, proof


def validate_record(rec, unit, seal, sealed_loc):
    """Problems with a migration record (empty list = a valid durable locator for the SAME sealed identity)."""
    p = []
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    if rec.get("record_sha256") != _canon_sha(body):
        p.append("record_sha256 does not match the record")
    if rec.get("schema") != SCHEMA or rec.get("unit") != unit or rec.get("seal_sha256") != seal.get("seal_sha256"):
        p.append("record is not for this sealed unit")
    if rec.get("original_locator") != sealed_loc:
        p.append("original_locator is not the sealed locator")
    dur = rec.get("durable_locator") or {}
    for f in ("key", "sha256", "bytes"):
        if dur.get(f) != sealed_loc.get(f):
            p.append(f"durable {f} differs from the sealed tape identity")
    if dur.get("store") != "r2" or dur.get("storage_contract") != TS.STORAGE_CONTRACT or not dur.get("verified_readback"):
        p.append("durable locator is not a read-back-verified R2 content address")
    src = rec.get("source_verification") or {}
    if (src.get("sha256"), src.get("bytes")) != (sealed_loc.get("sha256"), sealed_loc.get("bytes")):
        p.append("source verification does not match the sealed identity")
    return p


def durable_locator(evidence_root, unit, seal, sealed_loc):
    """None when not migrated; the validated durable locator when migrated; MigrationError when a record exists but
    is invalid (never silently ignored)."""
    path = record_path(evidence_root, unit)
    if not os.path.exists(path):
        return None
    rec = json.load(open(path))
    problems = validate_record(rec, unit, seal, sealed_loc)
    if problems:
        raise MigrationError(f"{unit}: invalid storage migration record: {problems}")
    return rec["durable_locator"]


def migrate_unit(evidence_root, unit, source_tape, store, now=None):
    """Copy ONE sealed artifact-backed tape to `store` (R2) byte-for-byte and write the create-only record."""
    seal, loc, proof = sealed_identity(evidence_root, unit)
    if loc.get("store") != TS.GHA_KIND:
        raise MigrationError(f"{unit}: tape store is {loc.get('store')!r}, not a temporary Actions artifact")
    if not proof or not proof.get("verified_readback"):
        raise MigrationError(f"{unit}: no sealed upload read-back proof ({PROOF})")
    path = record_path(evidence_root, unit)
    if os.path.exists(path):
        raise MigrationError(f"{unit}: already migrated (records are create-only)")
    sha, size = loc["sha256"], int(loc["bytes"])
    TS._verify(source_tape, sha, size)                       # independent source check against the SEAL
    dur = TS.put_verified(store, source_tape, sha, size)     # create-only + read-back + sha/size
    TS._fetch_via(store, dur)                                # a second, independent read-back
    t = (now or datetime.now(timezone.utc)).isoformat()
    rec = {"schema": SCHEMA, "unit": unit, "seal_sha256": seal["seal_sha256"], "original_locator": loc,
           "original_artifact": {k: proof.get(k) for k in ("repository", "run_id", "run_attempt", "artifact_id",
                                                          "artifact_name", "file_name", "zip_digest", "created_at",
                                                          "expires_at", "retention_days_configured")},
           "source_verification": {"sha256": sha, "bytes": size, "verified_at": t},
           "durable_locator": dict(dur, storage_contract=TS.STORAGE_CONTRACT),
           "byte_preserving": True, "migrated_at": t}
    rec["record_sha256"] = _canon_sha(rec)
    problems = validate_record(rec, unit, seal, loc)
    if problems:
        raise MigrationError(f"{unit}: refusing to write an invalid record: {problems}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x") as fh:                              # create-only
        json.dump(rec, fh, indent=1, sort_keys=True)
    return rec


def publish(evidence_root, unit):
    """Append-only commit of exactly one new record to the evidence ref (never a force push)."""
    import seal as SL
    rel = os.path.relpath(record_path(evidence_root, unit), evidence_root)
    for cmd in (["add", "--", rel], ["commit", "-m", f"MLB v3 storage migration {unit} -> R2 (byte-preserving)", "--", rel],
                ["push", "origin", f"HEAD:{SL.EVIDENCE_REF}"]):
        subprocess.run(["git", "-C", evidence_root, *cmd], check=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Migrate one artifact-backed V3 tape to R2 (byte-preserving)")
    ap.add_argument("--evidence-root", required=True, help="a worktree of the evidence ref")
    ap.add_argument("--unit", required=True)
    ap.add_argument("--source-tape", required=True, help="the tape fetched by artifact_api.fetch (exact artifact)")
    ap.add_argument("--publish", action="store_true", help="commit + push the record (append-only)")
    a = ap.parse_args(argv)
    rec = migrate_unit(a.evidence_root, a.unit, a.source_tape, TS.R2Store.from_env())
    if a.publish:
        publish(a.evidence_root, a.unit)
    print(json.dumps(rec, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MigrationError, TS.StoreError) as exc:
        print(f"MIGRATION REFUSED: {exc}", file=sys.stderr)
        sys.exit(3)
