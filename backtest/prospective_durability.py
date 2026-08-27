#!/usr/bin/env python3
"""Content-addressed durable storage for FULL COUNT prospective snapshots.

This module deliberately does NOT publish picks, mutate production output, or
decide which model is better. It turns one already-observed prospective
candidate universe into immutable files that are cheap to commit to a dedicated
research branch.

Why content-addressed candidate blobs:
- the same candidate state may be observed repeatedly without changing;
- rewriting one giant per-day JSONL file every observation would make git
  history grow quadratically;
- candidate_funnel_logger.content_hash() already defines the substantive,
  timestamp-neutral candidate state;
- each snapshot manifest records WHEN that exact universe was observed.

Storage schema v1:

  prospective/v1/candidates/ab/<content_sha256>.json.gz
  prospective/v1/snapshots/YYYY-MM-DD/<snapshot_id>.json

A candidate blob is canonical JSON of
candidate_funnel_logger.canonical_content_record(record), gzip-compressed with
mtime=0. Its SHA-256 over the UNCOMPRESSED bytes is exactly the content hash in
the snapshot manifest.

This module only materializes files. A workflow or operator may commit/push the
result to a dedicated append-only branch after independent verification. That
separation keeps git/network side effects out of the measurement primitive.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile

try:
    from backtest import candidate_funnel_logger as cfl
    from backtest import prospective_reporting as pr
except ImportError:
    import candidate_funnel_logger as cfl
    import prospective_reporting as pr


STORAGE_SCHEMA_VERSION = 1
STORAGE_ROOT = os.path.join("prospective", "v1")


class ProspectiveDurabilityError(RuntimeError):
    pass


def _canonical_json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".tmp-prospective-", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def candidate_blob_relpath(content_hash):
    if not isinstance(content_hash, str) or len(content_hash) != 64:
        raise ProspectiveDurabilityError(
            f"invalid candidate content hash {content_hash!r}")
    return os.path.join(
        STORAGE_ROOT, "candidates", content_hash[:2],
        f"{content_hash}.json.gz")


def snapshot_relpath(date, snapshot_id):
    if not date or not snapshot_id:
        raise ProspectiveDurabilityError(
            "snapshot date and snapshot_id are required")
    return os.path.join(
        STORAGE_ROOT, "snapshots", str(date), f"{snapshot_id}.json")


def candidate_content_bytes(record):
    """Canonical substantive bytes whose SHA is cfl.content_hash(record)."""
    raw = _canonical_json_bytes(cfl.canonical_content_record(record))
    expected = cfl.content_hash(record)
    got = _sha256_bytes(raw)
    if got != expected:
        raise ProspectiveDurabilityError(
            f"candidate canonicalization drift: bytes sha {got} != content_hash {expected}")
    return raw


def _verify_existing_candidate_blob(path, expected_hash):
    try:
        with open(path, "rb") as fh:
            compressed = fh.read()
        raw = gzip.decompress(compressed)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ProspectiveDurabilityError(
            f"existing prospective candidate blob is unreadable: {path}: {exc}")
    got = _sha256_bytes(raw)
    if got != expected_hash:
        raise ProspectiveDurabilityError(
            f"existing prospective candidate blob conflicts with its path: "
            f"{path}: sha256 {got} != {expected_hash}")
    return raw


def materialize_snapshot(changelog_records, snapshot_manifest, destination_root):
    """Write one exact snapshot into immutable content-addressed storage.

    Fails closed before writing snapshot metadata if any referenced candidate
    state is missing or inconsistent. Existing immutable files are verified,
    never blindly trusted or overwritten.
    """
    resolved = pr.resolve_snapshot(changelog_records, snapshot_manifest)
    snapshot_id = snapshot_manifest.get("snapshot_id")
    date = snapshot_manifest.get("date")
    if not snapshot_id or not date:
        raise ProspectiveDurabilityError(
            "snapshot manifest lacks snapshot_id/date")

    written_candidates = 0
    reused_candidates = 0
    for record in resolved:
        h = cfl.content_hash(record)
        raw = candidate_content_bytes(record)
        rel = candidate_blob_relpath(h)
        path = os.path.join(destination_root, rel)
        if os.path.exists(path):
            existing = _verify_existing_candidate_blob(path, h)
            if existing != raw:
                # Same sha with different bytes is cryptographically implausible,
                # but equality keeps the contract explicit instead of assuming.
                raise ProspectiveDurabilityError(
                    f"candidate hash collision/conflict at {path}")
            reused_candidates += 1
            continue
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        _atomic_write(path, compressed)
        written_candidates += 1

    stored_manifest = {
        **snapshot_manifest,
        "storage_schema_version": STORAGE_SCHEMA_VERSION,
        "candidate_blob_layout": (
            f"{STORAGE_ROOT}/candidates/<sha[:2]>/<sha>.json.gz"
        ),
    }
    snapshot_bytes = _canonical_json_bytes(stored_manifest)
    snapshot_path = os.path.join(
        destination_root, snapshot_relpath(date, snapshot_id))

    snapshot_written = False
    if os.path.exists(snapshot_path):
        with open(snapshot_path, "rb") as fh:
            existing = fh.read()
        if existing != snapshot_bytes:
            raise ProspectiveDurabilityError(
                f"immutable snapshot id {snapshot_id} already exists with different bytes")
    else:
        _atomic_write(snapshot_path, snapshot_bytes)
        snapshot_written = True

    return {
        "snapshot_id": snapshot_id,
        "date": date,
        "n_candidates": len(resolved),
        "candidate_blobs_written": written_candidates,
        "candidate_blobs_reused": reused_candidates,
        "snapshot_written": snapshot_written,
        "snapshot_path": snapshot_path,
    }


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ProspectiveDurabilityError(
                    f"{path}:{lineno}: invalid JSON: {exc}")
    return rows


def materialize_from_spool(*, candidate_path, snapshot_path,
                           destination_root, snapshot_id=None):
    """Materialize one snapshot selected from the logger's local spool."""
    candidates = load_jsonl(candidate_path)
    snapshots = load_jsonl(snapshot_path)
    if snapshot_id is None:
        if not snapshots:
            raise ProspectiveDurabilityError("snapshot spool is empty")
        manifest = snapshots[-1]
    else:
        matches = [s for s in snapshots if s.get("snapshot_id") == snapshot_id]
        if len(matches) != 1:
            raise ProspectiveDurabilityError(
                f"expected exactly one snapshot {snapshot_id}, found {len(matches)}")
        manifest = matches[0]
    return materialize_snapshot(candidates, manifest, destination_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-path", required=True)
    parser.add_argument("--snapshot-path", required=True)
    parser.add_argument("--destination-root", required=True)
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    result = materialize_from_spool(
        candidate_path=args.candidate_path,
        snapshot_path=args.snapshot_path,
        destination_root=args.destination_root,
        snapshot_id=args.snapshot_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
