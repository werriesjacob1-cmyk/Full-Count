#!/usr/bin/env python3
"""Append-only, on-disk layout for raw NFL captures.

LAYOUT

    nfl/raw/<slate_date>/<capture_id>/manifest.json
    nfl/raw/<slate_date>/<capture_id>/<source_id>/<artifact>.json.gz

`capture_id` is `<UTC timestamp>-<vintage>`, e.g. `20260910T221500Z-morning`.
The vintage label is part of the directory name because "which vintage is
this?" is the question a later reader asks first, and answering it should not
require opening a file.

APPEND-ONLY, MECHANICALLY. `write_capture` refuses to write into a capture_id
directory that already exists. Multiple vintages per day are separate
directories, never an overwrite of the same one. Nothing here ever edits or
deletes a prior capture: the whole point is that an archive written on Sunday
morning still says on Sunday morning's terms what was knowable then, even
after the game has been played and the answer is embarrassing.

WHY GZIP. Payloads are stored gzipped because a full slate vintage is tens of
megabytes of JSON and this repository keeps a season of them in git. The
sha256 in the manifest is computed over the UNCOMPRESSED bytes, so the digest
describes the payload as received rather than an artifact of the compressor.
`read_artifact` returns those exact bytes back.

NO NORMALIZATION. Payloads are stored as received. No field is renamed,
dropped, coerced, or reordered, and no canonical candidate identity is
attached. A schema decision made later cannot invalidate what is stored here,
which is precisely why archival was decoupled from identity.
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Iterable

from nfl.archive.provenance import (
    CAPTURE_CONTRACT_VERSION, Fetched, coverage_summary, sha256_hex, utcnow,
)

ARCHIVE_ROOT = os.path.join("nfl", "raw")

VINTAGES = (
    # Named rather than free-form so a season of directories stays greppable.
    "morning",        # first look of the day; lines have settled overnight
    "midday",
    "inactive-window",  # around the pregame inactive boundary -- the single
                        # highest-value NFL information timestamp
    "pre-lock",       # last observation before first kickoff of the window
    "post-lock",
    "ad-hoc",
)


class ArchiveExists(Exception):
    """Refusing to overwrite an existing capture. Archives are append-only."""


def capture_id(vintage: str, now: str | None = None) -> str:
    if vintage not in VINTAGES:
        raise ValueError(f"unknown vintage {vintage!r}; expected one of {VINTAGES}")
    stamp = (now or utcnow()).replace("-", "").replace(":", "")
    return f"{stamp}-{vintage}"


def capture_dir(slate_date: str, cid: str, root: str = ARCHIVE_ROOT) -> str:
    return os.path.join(root, slate_date, cid)


def _artifact_path(base: str, record: Fetched) -> str:
    safe_source = record.source_id.replace("/", "_")
    safe_artifact = record.artifact.replace("/", "_")
    return os.path.join(base, safe_source, f"{safe_artifact}.json.gz")


def write_capture(
    slate_date: str,
    vintage: str,
    records: Iterable[Fetched],
    root: str = ARCHIVE_ROOT,
    notes: dict | None = None,
    cid: str | None = None,
) -> str:
    """Write one capture vintage. Returns the capture directory path.

    Records with no body (SOURCE_FAILED, NOT_CHECKED, UNAVAILABLE_BY_POLICY)
    still get a manifest entry and no artifact file. That is the point: the
    manifest is the record of what was ATTEMPTED, not merely what succeeded.
    """
    records = list(records)
    cid = cid or capture_id(vintage)
    base = capture_dir(slate_date, cid, root)
    if os.path.exists(base):
        raise ArchiveExists(
            f"{base} already exists. Raw archives are append-only; write a new "
            "vintage rather than overwriting an observation that was already made."
        )
    os.makedirs(base)

    entries = []
    for record in records:
        entry = record.manifest_entry()
        if record.body:
            path = _artifact_path(base, record)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # mtime=0 so an identical payload compresses to identical bytes --
            # otherwise every re-run produces a spurious git diff.
            with gzip.GzipFile(path, "wb", mtime=0) as handle:
                handle.write(record.body)
            entry["artifact_path"] = os.path.relpath(path, base).replace(os.sep, "/")
        else:
            entry["artifact_path"] = None
        entries.append(entry)

    manifest = {
        "capture_contract_version": CAPTURE_CONTRACT_VERSION,
        "sport": "nfl",
        "capture_id": cid,
        "vintage": vintage,
        "slate_date": slate_date,
        "written_at": utcnow(),
        # Stated in the manifest itself so a reader who finds one of these
        # directories in isolation knows what it is and is not.
        "contains": "raw source payloads as received; no canonical candidate "
                    "identity, no normalization, no scoring, no picks",
        "coverage": coverage_summary(records),
        "artifacts": entries,
        "notes": notes or {},
    }
    manifest_path = os.path.join(base, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return base


def read_manifest(capture_path: str) -> dict:
    with open(os.path.join(capture_path, "manifest.json"), encoding="utf-8") as handle:
        return json.load(handle)


def read_artifact(capture_path: str, artifact_path: str) -> bytes:
    """Return an archived payload's original, uncompressed bytes."""
    with gzip.open(os.path.join(capture_path, artifact_path), "rb") as handle:
        return handle.read()


def verify_capture(capture_path: str) -> list[str]:
    """Re-derive every digest. Returns a list of problems; empty means intact.

    An archive nobody ever verifies is a claim, not evidence.
    """
    problems = []
    try:
        manifest = read_manifest(capture_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest unreadable: {exc}"]
    for entry in manifest.get("artifacts", []):
        path = entry.get("artifact_path")
        label = f"{entry.get('source_id')}/{entry.get('artifact')}"
        if path is None:
            if entry.get("outcome") in ("CHECKED_AND_FOUND",):
                problems.append(f"{label}: claims CHECKED_AND_FOUND with no artifact")
            continue
        try:
            body = read_artifact(capture_path, path)
        except OSError as exc:
            problems.append(f"{label}: artifact missing or unreadable ({exc})")
            continue
        if entry.get("sha256") != sha256_hex(body):
            problems.append(f"{label}: sha256 mismatch -- payload altered since capture")
        if entry.get("byte_length") != len(body):
            problems.append(f"{label}: byte_length mismatch")
    return problems
