"""Write-once, idempotent ledger of sealed unit manifests.

A seal records sealed_at (wall clock at sealing). A PROSPECTIVE seal is refused
unless decision_at <= sealed_at < the unit's earliest covered first pitch, so a
manifest can never be sealed (backfilled) after games begin. DRY_RUN manifests
can never enter a prospective ledger. Re-sealing identical content is a no-op;
different content for an already-sealed unit is refused.
"""
import json
import os
from datetime import datetime, timezone

import fanduel as FDC
import manifest as M


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


SEAL_VERSION = "fc-acc-001-s1-seal-1"


class LedgerError(ValueError):
    pass


def _ts(v):
    return M._ts(v)


def unit_key(unit):
    if unit.get("window") not in ("DAY", "NIGHT"):
        raise LedgerError("unit window must be DAY or NIGHT")
    return f"{unit['date']}_{unit['window']}"


def seal(ledger_dir, manifest, archive, *, ledger_kind, now=None):
    if ledger_kind not in ("PROSPECTIVE", "DRY_RUN"):
        raise LedgerError("ledger_kind must be PROSPECTIVE or DRY_RUN")
    if ledger_kind == "PROSPECTIVE" and manifest["mode"] != "PROSPECTIVE":
        raise LedgerError("dry-run manifests can never enter the prospective ledger")
    M.verify_manifest(manifest, archive)
    sealed_at = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if ledger_kind == "PROSPECTIVE":
        if not (_ts(manifest["unit"]["decision_at"]) <= _ts(sealed_at) < _ts(manifest["first_pitch_utc_min"])):
            raise LedgerError("prospective seal must be at/after the decision and before the first covered pitch")
    record = {"seal_version": SEAL_VERSION, "ledger_kind": ledger_kind, "unit_key": unit_key(manifest["unit"]),
              "manifest_sha256": manifest["manifest_sha256"], "sealed_at": sealed_at, "manifest": manifest}
    record["seal_sha256"] = FDC.canonical_sha256({k: v for k, v in record.items() if k != "seal_sha256"})
    os.makedirs(ledger_dir, exist_ok=True)
    path = os.path.join(ledger_dir, record["unit_key"] + ".json")
    if os.path.exists(path):
        prior = _load_json(path)
        if prior["manifest_sha256"] == manifest["manifest_sha256"]:
            return path, "IDEMPOTENT_NOOP"
        raise LedgerError(f"unit {record['unit_key']} already sealed with different content")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)
    return path, "SEALED"


def verify_ledger(ledger_dir, archive):
    out = []
    for f in sorted(os.listdir(ledger_dir)):
        if not f.endswith(".json"):
            continue
        rec = _load_json(os.path.join(ledger_dir, f))
        if FDC.canonical_sha256({k: v for k, v in rec.items() if k != "seal_sha256"}) != rec["seal_sha256"]:
            raise LedgerError(f"{f}: seal hash does not rebuild")
        if rec["manifest"]["manifest_sha256"] != rec["manifest_sha256"] or f != rec["unit_key"] + ".json":
            raise LedgerError(f"{f}: seal does not bind its manifest/unit")
        M.verify_manifest(rec["manifest"], archive)
        out.append(rec["unit_key"])
    return out
