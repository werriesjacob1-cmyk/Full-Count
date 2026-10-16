"""Append-only, hash-chained ledger of sealed unit manifests.

Layout (small; no artifact ever embeds another artifact or an earlier link):
  <ledger>/chain.jsonl          one canonical entry per event, hash-chained (seq, prev entry hash):
                                  * CUTOFF_COMMITMENT (A4): unit, commitment hash + object, cutoff,
                                    commitment deadline, recorded_at
                                  * seal (no entry_type; A1-A3 schema unchanged): unit, manifest
                                    hashes, sealed_at, first pitch
  <ledger>/units/<unit>.json    seal uniqueness index, created with O_EXCL
  <ledger>/cutoffs/<unit>.json  cutoff-commitment index (A4: a deterministic copy of its chain
                                entry, exactly like units/), created with O_EXCL
  <ledger>/.lock                fcntl lock held for the whole check-and-append
  manifests and full cutoff commitments are content-addressed objects in the raw archive.

A4 (Codex A3 certification HOLD, PR #226 comment 6197211757): a cutoff
commitment is part of the chain, so deleting or replacing its index is a
MISSING_LINK / mismatch that fails verification closed; restore_index rewrites it
byte-identically from the chain and can never create a different first
commitment; a PROSPECTIVE commitment, like a PROSPECTIVE seal, must extend the
externally anchored head (a wholesale-deleted ledger cannot silently restart);
a PROSPECTIVE seal requires the chained commitment to be present and identical
to the manifest's; idempotent re-seals validate the commitment before NOOP.

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
CUTOFF_ENTRY_VERSION = "fc-acc-001-s1-cutoff-entry-1"
CUTOFF = "CUTOFF_COMMITMENT"
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


def is_cutoff(e):
    return e.get("entry_type") == CUTOFF


def _index_dir(ledger_dir, e):
    return os.path.join(ledger_dir, "cutoffs" if is_cutoff(e) else "units")


def _check_index(ledger_dir, entries, sub, label):
    d = os.path.join(ledger_dir, sub)
    present = {f[:-5] for f in os.listdir(d) if f.endswith(".json")} if os.path.isdir(d) else set()
    keys = [e["unit_key"] for e in entries]
    if len(keys) != len(set(keys)):
        raise LedgerError(f"a unit has two {label} entries in the chain")
    for e in entries:
        if e["unit_key"] not in present:
            raise LedgerError(f"MISSING_LINK: {label} index for {e['unit_key']} is missing (recover with restore_index)")
        with open(os.path.join(d, e["unit_key"] + ".json"), encoding="utf-8") as fh:
            if json.load(fh) != e:
                raise LedgerError(f"{label} index for {e['unit_key']} does not match its chain entry")
    orphans = present - set(keys)
    if orphans:
        raise LedgerError(f"ORPHAN {label} index without a chain entry: {sorted(orphans)}")


def _check_cutoff_entry(e, archive):
    """A chained commitment must have been recorded by its deadline and, when the archive is
    available, its full commitment object must re-derive from the archived schedule."""
    if _ts(e["recorded_at"]) > _ts(e["commitment_deadline_utc"]):
        raise LedgerError(f"{e['unit_key']}: cutoff commitment entry recorded after its deadline")
    if archive is not None:
        import cutoff as CT
        c = archive.get_json(e["commitment_object_sha256"])
        try:
            date, window = e["unit_key"].rsplit("_", 1)
            CT.verify(c, archive, {"date": date, "window": window})
        except ValueError as exc:
            raise LedgerError(f"{e['unit_key']}: chained cutoff commitment does not verify: {exc}") from exc
        if (c["commitment_sha256"], c["cutoff_utc"], c["commitment_deadline_utc"]) != (
                e["commitment_sha256"], e["cutoff_utc"], e["commitment_deadline_utc"]):
            raise LedgerError(f"{e['unit_key']}: chained cutoff entry does not match its commitment object")


def read_chain(ledger_dir, archive=None, *, kind=None):
    """Verify the whole chain (seals AND cutoff commitments, both index sets); returns its
    entries. Raises on any break."""
    chain_path, _, _ = _paths(ledger_dir)
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
    seals = [e for e in entries if not is_cutoff(e)]
    cutoffs = [e for e in entries if is_cutoff(e)]
    _check_index(ledger_dir, seals, "units", "unit")
    _check_index(ledger_dir, cutoffs, "cutoffs", "cutoff commitment")
    first = {}
    for e in entries:
        first.setdefault((e["unit_key"], is_cutoff(e)), e["seq"])
    for e in seals:
        c = first.get((e["unit_key"], True))
        if c is not None and c > e["seq"]:
            raise LedgerError(f"{e['unit_key']}: cutoff commitment chained after the unit's seal")
    for e in cutoffs:
        _check_cutoff_entry(e, archive)
    if archive is not None:
        for e in seals:
            m = archive.get_json(e["manifest_object_sha256"])
            if M.verify_manifest(m, archive) != e["manifest_sha256"]:
                raise LedgerError(f"{e['unit_key']}: manifest does not verify")
    return entries


def _cutoff_path(ledger_dir, key):
    return os.path.join(ledger_dir, "cutoffs", key + ".json")


def _cutoff_in(entries, key):
    return next((e for e in entries if is_cutoff(e) and e["unit_key"] == key), None)


def logged_cutoff(ledger_dir, key, archive=None):
    """The unit's CHAINED cutoff commitment (entry + full commitment when the archive is given), read
    from the verified chain -- never from a standalone file. None if the chain has none."""
    e = _cutoff_in(read_chain(ledger_dir, archive), key)
    if e is None:
        return None
    return {"entry": e, "commitment": archive.get_json(e["commitment_object_sha256"]) if archive else None}


def _append(ledger_dir, entries, entry):
    """Under the caller's lock: index file (O_EXCL) then the chain line, both fsynced."""
    entry["entry_sha256"] = _entry_sha(entry)
    d = _index_dir(ledger_dir, entry)
    os.makedirs(d, exist_ok=True)
    index_path = os.path.join(d, entry["unit_key"] + ".json")
    fd = os.open(index_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "wb") as fh:
        fh.write(RA.canonical_bytes(entry))
        fh.flush()
        os.fsync(fh.fileno())
    with open(_paths(ledger_dir)[0], "ab") as fh:
        fh.write(RA.canonical_bytes(entry) + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    return index_path


def _check_anchored_head(entries, anchored_head):
    current = entries[-1]["entry_sha256"] if entries else GENESIS
    if anchored_head is not None and anchored_head != current:
        raise LedgerError(f"local chain head {current} != externally anchored head {anchored_head}: "
                          "deleted, forked or unanchored local ledger; restore it, do not re-seal")
    return current


def record_cutoff(ledger_dir, commitment, archive, *, ledger_kind, anchored_head=None, now=None):
    """A3/A4: write-once cutoff commitment per unit, CHAINED into the ledger no later than its
    commitment deadline. The first commitment wins; a different later one is refused, and so is
    any commitment once the unit is sealed. A PROSPECTIVE commitment must extend the externally
    anchored head. (A local chain is not proof: the anchor of the head covering it is.)"""
    import cutoff as CT
    if ledger_kind not in KINDS:
        raise LedgerError("ledger_kind must be PROSPECTIVE or DRY_RUN")
    if ledger_kind == "PROSPECTIVE" and anchored_head is None:
        raise LedgerError("a PROSPECTIVE cutoff commitment must name the externally anchored chain head it extends")
    unit = commitment.get("unit") or {}
    CT.verify(commitment, archive, unit)
    now = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if _ts(now) > _ts(commitment["commitment_deadline_utc"]):
        raise LedgerError("cutoff commitment recorded after its commitment deadline")
    key = unit_key(unit)
    _, _, lock_path = _paths(ledger_dir)
    os.makedirs(ledger_dir, exist_ok=True)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = read_chain(ledger_dir, kind=ledger_kind)
        prior = _cutoff_in(entries, key)
        if prior is not None:
            if prior["commitment_sha256"] == commitment["commitment_sha256"]:
                return "IDEMPOTENT_NOOP"
            raise LedgerError(f"unit {key} already has a different cutoff commitment; cutoffs never move")
        if any(not is_cutoff(e) and e["unit_key"] == key for e in entries):
            raise LedgerError(f"unit {key} is already sealed; a cutoff commitment can no longer be recorded")
        current = _check_anchored_head(entries, anchored_head)
        obj_sha, _ = archive.put_json(commitment)
        _append(ledger_dir, entries, {
            "entry_version": CUTOFF_ENTRY_VERSION, "entry_type": CUTOFF, "ledger_kind": ledger_kind,
            "seq": len(entries) + 1, "prev_entry_sha256": current, "unit_key": key,
            "commitment_sha256": commitment["commitment_sha256"], "commitment_object_sha256": obj_sha,
            "cutoff_utc": commitment["cutoff_utc"], "commitment_deadline_utc": commitment["commitment_deadline_utc"],
            "recorded_at": now, "evidence_status": "LOCAL_COMMITMENT_UNANCHORED"})
    return "RECORDED"


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
    _, units_dir, lock_path = _paths(ledger_dir)
    os.makedirs(ledger_dir, exist_ok=True)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = read_chain(ledger_dir, kind=ledger_kind)
        # A4: the cutoff contract is checked BEFORE any idempotent NOOP
        logged = _cutoff_in(entries, key)
        mc = (manifest["inputs"].get("cutoff_commitment") or {}).get("commitment_sha256")
        if ledger_kind == "PROSPECTIVE" and logged is None:
            raise LedgerError(f"unit {key}: no cutoff commitment was chained before its deadline; "
                              "a PROSPECTIVE seal requires one")
        if ledger_kind == "PROSPECTIVE" and mc is None:
            raise LedgerError(f"unit {key}: manifest carries no cutoff commitment; a PROSPECTIVE seal requires one")
        if logged is not None and logged["commitment_sha256"] != mc:
            raise LedgerError(f"unit {key}: manifest does not carry the logged cutoff commitment")
        prior = next((e for e in entries if not is_cutoff(e) and e["unit_key"] == key), None)
        if prior is not None:
            if prior["manifest_sha256"] == manifest["manifest_sha256"]:
                return os.path.join(units_dir, key + ".json"), "IDEMPOTENT_NOOP"
            raise LedgerError(f"unit {key} already sealed with different content")
        current = _check_anchored_head(entries, anchored_head)
        obj_sha, _ = archive.put_json(manifest)
        index_path = _append(ledger_dir, entries, {
            "seal_version": SEAL_VERSION, "ledger_kind": ledger_kind, "seq": len(entries) + 1,
            "prev_entry_sha256": current, "unit_key": key, "manifest_sha256": manifest["manifest_sha256"],
            "manifest_object_sha256": obj_sha, "sealed_at": sealed_at,
            "first_pitch_utc_min": manifest["first_pitch_utc_min"], "evidence_status": "LOCAL_SEAL_UNANCHORED"})
    return index_path, "SEALED"


def restore_index(ledger_dir):
    """Recover a MISSING_LINK whose chain entry is intact: the index file is a
    deterministic copy of the entry, so it is rewritten byte-identically. Never
    re-seals, never touches the chain. A truncated/deleted chain is NOT locally
    recoverable: restore chain.jsonl from the external anchor's mirror."""
    chain_path, _, lock_path = _paths(ledger_dir)
    restored = []
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(chain_path, encoding="utf-8") as fh:
            entries = [json.loads(x) for x in fh.read().split("\n") if x]
        prev = GENESIS
        for i, e in enumerate(entries, 1):
            if e.get("seq") != i or e.get("prev_entry_sha256") != prev or _entry_sha(e) != e.get("entry_sha256"):
                raise LedgerError(f"chain broken at seq {i}: not locally recoverable")
            prev = e["entry_sha256"]
            d = _index_dir(ledger_dir, e)
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, e["unit_key"] + ".json")
            if not os.path.exists(p):
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(RA.canonical_bytes(e))
                restored.append(("cutoff:" if is_cutoff(e) else "") + e["unit_key"])
    read_chain(ledger_dir)
    return restored


def verify_ledger(ledger_dir, archive):
    """Sealed unit keys of a fully verified chain (cutoff commitments are verified, not listed)."""
    return [e["unit_key"] for e in read_chain(ledger_dir, archive) if not is_cutoff(e)]
