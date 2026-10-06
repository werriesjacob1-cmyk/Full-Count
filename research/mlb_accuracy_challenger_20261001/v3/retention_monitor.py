#!/usr/bin/env python3
"""Retention monitor for TEMPORARY (GitHub Actions artifact) V3 tapes. Evidence must never expire unnoticed.

For every chained unit whose sealed tape lives in an Actions artifact, report the artifact's creation time, the
configured retention, the expiry (GitHub's `expires_at`, recorded by artifact_api.confirm_upload before the seal and
optionally re-read live), and the days remaining. Thresholds (conservative; documented in STORAGE_TEMPORARY.md):

  OK                 > WARN_DAYS remaining
  MIGRATE_WARNING    <= WARN_DAYS (45) remaining: migrate to R2 now (exit 10, ::warning:: + #91 alert)
  MIGRATE_CRITICAL   <= CRITICAL_DAYS (30) remaining: evidence at risk (exit 20, ::error:: + #91 alert)
  EXPIRED / PROOF_MISSING / MIGRATION_INVALID / LIVE_MISMATCH: evidence lost or unprovable (exit 20)
  MIGRATED_DURABLE   a valid byte-preserving R2 migration record exists (no expiry risk)
  NOT_TEMPORARY      the unit's tape is not artifact-backed (legacy / R2)

With the repository's 90-day retention the warning fires 45 days after recording, leaving >= 45 days to migrate.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tape_migration as MG  # noqa: E402
import tape_store as TS  # noqa: E402

WARN_DAYS = 45
CRITICAL_DAYS = 30
EXIT = {"OK": 0, "MIGRATED_DURABLE": 0, "NOT_TEMPORARY": 0, "MIGRATE_WARNING": 10, "MIGRATE_CRITICAL": 20,
        "EXPIRED": 20, "PROOF_MISSING": 20, "MIGRATION_INVALID": 20, "LIVE_MISMATCH": 20}


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def classify(days_remaining):
    if days_remaining <= 0:
        return "EXPIRED"
    if days_remaining <= CRITICAL_DAYS:
        return "MIGRATE_CRITICAL"
    if days_remaining <= WARN_DAYS:
        return "MIGRATE_WARNING"
    return "OK"


def unit_status(evidence_root, seal, now, live=None):
    unit = f"{seal['date']}_{seal['window']}"
    d = os.path.join(evidence_root, "seals", unit)
    man = json.load(gzip.open(os.path.join(d, "manifest.json.gz")))
    loc = (man.get("shadow_provenance") or {}).get("tape_store") or {}
    row = {"unit": unit, "store": loc.get("store")}
    if loc.get("store") != TS.GHA_KIND:
        return dict(row, status="NOT_TEMPORARY")
    try:
        if MG.durable_locator(evidence_root, unit, seal, loc):
            return dict(row, status="MIGRATED_DURABLE", durable=MG.record_path(evidence_root, unit))
    except MG.MigrationError as exc:
        return dict(row, status="MIGRATION_INVALID", detail=str(exc))
    proof = (seal.get("artifacts_sha256") or {}).get(MG.PROOF) and json.load(open(os.path.join(d, MG.PROOF)))
    if not proof or not proof.get("expires_at"):
        return dict(row, status="PROOF_MISSING")
    exp = _t(proof["expires_at"])
    row.update(artifact_id=proof.get("artifact_id"), run_id=proof.get("run_id"), created_at=proof.get("created_at"),
               retention_days_configured=proof.get("retention_days_configured"), expires_at=proof["expires_at"],
               sha256=loc.get("sha256"), bytes=loc.get("bytes"))
    if live is not None:                                         # optional: re-read GitHub's current view
        try:
            a = live(dict(loc, artifact_id=proof.get("artifact_id")))
            row["live_expires_at"] = a.get("expires_at")
            exp = min(exp, _t(a["expires_at"]))                  # the earlier expiry governs
        except TS.TapeExpired as exc:
            return dict(row, status="EXPIRED", detail=str(exc))
        except TS.StoreError as exc:
            return dict(row, status="LIVE_MISMATCH", detail=f"{exc.code}: {exc}")
    days = (exp - now).total_seconds() / 86400
    row["days_remaining"] = round(days, 2)
    return dict(row, status=classify(days))


def scan(evidence_root, now=None, live=None):
    import verify_evidence as VE
    now = now or datetime.now(timezone.utc)
    rows = [unit_status(evidence_root, s, now, live) for s in VE.load_chain(evidence_root)]
    worst = max((EXIT[r["status"]] for r in rows), default=0)
    return {"checked_at": now.isoformat(), "warn_days": WARN_DAYS, "critical_days": CRITICAL_DAYS,
            "units": rows, "exit_code": worst,
            "summary": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})}}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Temporary-tape retention monitor (Actions artifacts)")
    ap.add_argument("--evidence-root", required=True)
    ap.add_argument("--live", action="store_true", help="also re-read each artifact's expiry from the GitHub API")
    a = ap.parse_args(argv)
    live = None
    if a.live:
        import artifact_api as AA
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        live = lambda loc: AA.find_artifact(loc, token)  # noqa: E731
    rep = scan(a.evidence_root, live=live)
    for r in rep["units"]:
        if EXIT[r["status"]]:
            level = "error" if EXIT[r["status"]] >= 20 else "warning"
            print(f"::{level}::V3 TAPE {r['status']} {r['unit']} days_remaining={r.get('days_remaining')} "
                  f"expires_at={r.get('expires_at')} -- migrate to R2 (FC-MLB-001C)")
    print(json.dumps(rep, indent=1, sort_keys=True))
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
