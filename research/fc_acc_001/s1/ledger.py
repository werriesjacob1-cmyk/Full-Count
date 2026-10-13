"""Append-only, hash-chained ledger of sealed unit manifests.

Layout (small; no artifact ever embeds another artifact or an earlier link):
  <ledger>/chain.jsonl          one canonical entry per seal: seq, prev entry hash,
                                unit, manifest hashes, sealed_at, first pitch
  <ledger>/units/<unit>.json    uniqueness index, created with O_EXCL
  <ledger>/.lock                fcntl lock held for the whole check-and-append
  manifests themselves are content-addressed objects in the raw archive.

Guarantees (local):
  * one manifest per unit, ever: a unit already in the chain can only be
    re-sealed with identical content (IDEMPOTENT_NOOP);
  * deleting a unit index file does not reopen the unit (the chain still holds
    it); a chain entry without its index file, or an index file without its
    chain entry, is a MISSING_LINK / ORPHAN and blocks all further sealing;
  * concurrent sealers are serialized by the lock; the O_EXCL index create is a
    second barrier.

Local files can still be deleted wholesale. That is why a PROSPECTIVE seal must
name ``anchored_head``: the chain head most recently published to an external
append-only anchor (anchor.py). If the local head differs (a deleted or forked
chain), sealing is refused. No seal is "prospective immutable" until its entry
is covered by an independently verified external anchor observed before first
pitch -- see anchor.verify_anchors. Every entry is recorded as
LOCAL_SEAL_UNANCHORED.
"""
import fcntl
import json
import os
from datetime import datetime, timezone

import fanduel as FDC
import manifest as M
import raw_archive as RA

SEAL_VERSION = "fc-acc-001-s1-seal-2"
GENESIS = "GENESIS"
KINDS = ("PROSPECTIVE", "DRY_RUN")


class LedgerError(ValueError):
    pass


def _ts(v):
    return M._ts(v)


def unit_key(unit):
    if unit.get("window") not in ("DAY", "NIGHT") or not unit.get("date"):
        raise LedgerError("unit must have a date and a DAY or NIGHT window")
    return f"{unit['date']}_{unit['window']}"


def _entry_sha(e):
    return FDC.canonical_sha256({k: v for k, v in e.items() if k != "entry_sha256"})


def _paths(ledger_dir):
    return (os.path.join(ledger_dir, "chain.jsonl"), os.path.join(ledger_dir, "units"),
            os.path.join(ledger_dir, ".lock"))


def read_chain(ledger_dir, archive=None, *, kind=None):
    """Verify the whole chain; returns its entries. Raises on any break."""
    chain_path, units_dir, _ = _paths(ledger_dir)
    entries = []
    if os.path.exists(chain_path):
        with open(chain_path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        else:
            raise LedgerError("chain.jsonl does not end with a complete entry")
        prev = GENESIS
        for i, line in enumerate(lines, 1):
            e = json.loads(line)
            if e.get("seq") != i or e.get("prev_entry_sha256") != prev or _entry_sha(e) != e.get("entry_sha256"):
                raise LedgerError(f"chain broken at seq {i}")
            if kind and e.get("ledger_kind") != kind:
                raise LedgerError(f"seq {i} is a {e.get('ledger_kind')} entry in a {kind} ledger")
            prev = e["entry_sha256"]
            entries.append(e)
    keys = [e["unit_key"] for e in entries]
    if len(keys) != len(set(keys)):
        raise LedgerError("a unit appears twice in the chain")
    present = {f[:-5] for f in os.listdir(units_dir) if f.endswith(".json")} if os.path.isdir(units_dir) else set()
    for e in entries:
        if e["unit_key"] not in present:
            raise LedgerError(f"MISSING_LINK: unit index for {e['unit_key']} is missing (recover with restore_index)")
        with open(os.path.join(units_dir, e["unit_key"] + ".json"), encoding="utf-8") as fh:
            if json.load(fh) != e:
                raise LedgerError(f"unit index for {e['unit_key']} does not match its chain entry")
    orphans = present - set(keys)
    if orphans:
        raise LedgerError(f"ORPHAN unit index without a chain entry: {sorted(orphans)}")
    if archive is not None:
        for e in entries:
            m = archive.get_json(e["manifest_object_sha256"])
            if M.verify_manifest(m, archive) != e["manifest_sha256"]:
                raise LedgerError(f"{e['unit_key']}: manifest does not verify")
    return entries


def head(ledger_dir):
    entries = read_chain(ledger_dir)
    return entries[-1]["entry_sha256"] if entries else GENESIS


def seal(ledger_dir, manifest, archive, *, ledger_kind, anchored_head=None, now=None):
    if ledger_kind not in KINDS:
        raise LedgerError("ledger_kind must be PROSPECTIVE or DRY_RUN")
    if ledger_kind == "PROSPECTIVE" and manifest["mode"] != "PROSPECTIVE":
        raise LedgerError("dry-run manifests can never enter the prospective ledger")
    if ledger_kind == "PROSPECTIVE" and anchored_head is None:
        raise LedgerError("a PROSPECTIVE seal must name the externally anchored chain head it extends")
    M.verify_manifest(manifest, archive)
    sealed_at = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if ledger_kind == "PROSPECTIVE" and not (
            _ts(manifest["unit"]["decision_at"]) <= _ts(sealed_at) < _ts(manifest["first_pitch_utc_min"])):
        raise LedgerError("prospective seal must be at/after the decision and before the first covered pitch")
    key = unit_key(manifest["unit"])
    chain_path, units_dir, lock_path = _paths(ledger_dir)
    os.makedirs(units_dir, exist_ok=True)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = read_chain(ledger_dir, kind=ledger_kind)
        prior = next((e for e in entries if e["unit_key"] == key), None)
        if prior is not None:
            if prior["manifest_sha256"] == manifest["manifest_sha256"]:
                return os.path.join(units_dir, key + ".json"), "IDEMPOTENT_NOOP"
            raise LedgerError(f"unit {key} already sealed with different content")
        current = entries[-1]["entry_sha256"] if entries else GENESIS
        if anchored_head is not None and anchored_head != current:
            raise LedgerError(f"local chain head {current} != externally anchored head {anchored_head}: "
                              "deleted, forked or unanchored local ledger; restore it, do not re-seal")
        obj_sha, _ = archive.put_json(manifest)
        entry = {"seal_version": SEAL_VERSION, "ledger_kind": ledger_kind, "seq": len(entries) + 1,
                 "prev_entry_sha256": current, "unit_key": key, "manifest_sha256": manifest["manifest_sha256"],
                 "manifest_object_sha256": obj_sha, "sealed_at": sealed_at,
                 "first_pitch_utc_min": manifest["first_pitch_utc_min"], "evidence_status": "LOCAL_SEAL_UNANCHORED"}
        entry["entry_sha256"] = _entry_sha(entry)
        index_path = os.path.join(units_dir, key + ".json")
        fd = os.open(index_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as fh:
            fh.write(RA.canonical_bytes(entry))
            fh.flush()
            os.fsync(fh.fileno())
        with open(chain_path, "ab") as fh:
            fh.write(RA.canonical_bytes(entry) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
    return index_path, "SEALED"


def restore_index(ledger_dir):
    """Recover a MISSING_LINK whose chain entry is intact: the index file is a
    deterministic copy of the entry, so it is rewritten byte-identically. Never
    re-seals, never touches the chain. A truncated/deleted chain is NOT locally
    recoverable: restore chain.jsonl from the external anchor's mirror."""
    chain_path, units_dir, lock_path = _paths(ledger_dir)
    restored = []
    os.makedirs(units_dir, exist_ok=True)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(chain_path, encoding="utf-8") as fh:
            entries = [json.loads(x) for x in fh.read().split("\n") if x]
        prev = GENESIS
        for i, e in enumerate(entries, 1):
            if e.get("seq") != i or e.get("prev_entry_sha256") != prev or _entry_sha(e) != e.get("entry_sha256"):
                raise LedgerError(f"chain broken at seq {i}: not locally recoverable")
            prev = e["entry_sha256"]
            p = os.path.join(units_dir, e["unit_key"] + ".json")
            if not os.path.exists(p):
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(RA.canonical_bytes(e))
                restored.append(e["unit_key"])
    read_chain(ledger_dir)
    return restored


def verify_ledger(ledger_dir, archive):
    return [e["unit_key"] for e in read_chain(ledger_dir, archive)]
