#!/usr/bin/env python3
"""Read-only certification of a durable FULL COUNT canonical run.

The certifier never repairs, regenerates, rewrites, or assembles into the run
directory. It independently re-reads durable artifacts, recomputes checksums,
and returns exactly one verdict class:

* CANONICAL CERTIFIED
* NOT CANONICAL
* CERTIFICATION BLOCKED

A run that is merely incomplete is BLOCKED, not condemned. A checksum,
identity, schema, or provenance contradiction is NOT CANONICAL.

The durable layout expected here is:

    <run_dir>/manifest.json
    <run_dir>/index.json
    <run_dir>/source/*.parquet
    <run_dir>/rows/YYYY-MM-DD.jsonl.gz
    <run_dir>/rows/YYYY-MM-DD.meta.json

This matches canonical_durability.py's remote checkpoint format.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
from collections import Counter
from datetime import date, datetime, timedelta

from accuracy_lab import manifest_identity_strength
from backtest import generation_regime as gr

ALLOWED_COMPLETE_STATUSES = {"ok", "no_games"}
IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")
SOURCE_REQUIRED_FIELDS = (
    "source",
    "request_identity",
    "retrieval_timestamp",
    "library",
    "library_version",
    "row_count",
    "content_sha256",
    "date_coverage",
    "cache_mode",
)
CANONICAL_REPOSITORY_IDENTITY = "werriesjacob1-cmyk/Full-Count"


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError(f"invalid date range: {start}..{end}")
    out = []
    current = first
    while current <= last:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _identity_fingerprint(identity):
    return _sha256_bytes(json.dumps(identity, sort_keys=True).encode())


def _environment_fingerprint(environment):
    payload = {
        key: environment.get(key)
        for key in (
            "python_version",
            "python_implementation",
            "platform",
            "machine",
            "critical_packages",
            "pip_freeze_sha256",
        )
    }
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode())


def _lineage_fingerprint(records):
    keyed = sorted(
        json.dumps(
            {
                key: record.get(key)
                for key in (
                    "source",
                    "request_identity",
                    "content_sha256",
                    "schema_fingerprint",
                    "row_count",
                )
            },
            sort_keys=True,
        )
        for record in records
    )
    return _sha256_bytes("\n".join(keyed).encode())


def _logical_fingerprint(parts):
    # Exact canonical_run.py semantics: default json separators + sort_keys.
    return _sha256_bytes(json.dumps(parts, sort_keys=True).encode())


def _finite_probability(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 <= number <= 1


def _source_schema_attestation(path):
    """Independently bind schema/coverage to exact parquet content bytes.

    The historical source-lineage record for the Aug-28 run has null
    schema_columns/schema_fingerprint. We do NOT rewrite that record. This
    attestation is additive evidence tied to the already-recorded content SHA.
    """
    try:
        import pandas as pd
    except Exception as exc:  # pragma: no cover - environment-specific
        return {"available": False, "problem": f"pandas unavailable: {exc}"}

    content_sha = _sha256_file(path)
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return {
            "available": False,
            "content_sha256": content_sha,
            "problem": f"parquet unreadable: {exc}",
        }

    columns = sorted(str(column) for column in frame.columns)
    column_fingerprint = _sha256_bytes(",".join(columns).encode())
    typed_schema = sorted(
        f"{column}:{frame[column].dtype}"
        for column in frame.columns
    )
    typed_schema_fingerprint = _sha256_bytes(
        "\n".join(typed_schema).encode()
    )

    min_date = None
    max_date = None
    if "game_date" in frame.columns and len(frame):
        parsed = pd.to_datetime(frame["game_date"], errors="coerce").dropna()
        if len(parsed):
            min_date = str(parsed.min().date())
            max_date = str(parsed.max().date())

    return {
        "available": True,
        "path": path,
        "content_sha256": content_sha,
        "row_count": int(len(frame)),
        "schema_columns": columns,
        # Matches canonical_durability.source_lineage_record semantics.
        "schema_fingerprint": column_fingerprint,
        # Stronger additive diagnostic; does not alter historical semantics.
        "typed_schema_fingerprint": typed_schema_fingerprint,
        "date_coverage": (
            f"{min_date}..{max_date}"
            if min_date is not None and max_date is not None
            else None
        ),
    }


def _find_source_attestation(run_dir, source_lineage):
    source_dir = os.path.join(run_dir, "source")
    if not os.path.isdir(source_dir):
        return None

    parquets = sorted(
        os.path.join(source_dir, name)
        for name in os.listdir(source_dir)
        if name.endswith(".parquet")
    )
    if not parquets:
        return None

    expected_shas = {
        record.get("content_sha256")
        for record in source_lineage
        if record.get("content_sha256")
    }
    for path in parquets:
        sha = _sha256_file(path)
        if sha in expected_shas:
            return _source_schema_attestation(path)

    # Exact source file exists but cannot be tied to recorded content identity.
    return {
        "available": False,
        "problem": "source parquet present but no content SHA matches stored lineage",
        "candidate_paths": parquets,
    }


def _checkpoint_paths(run_dir, day):
    base = os.path.join(run_dir, "rows")
    return (
        os.path.join(base, f"{day}.jsonl.gz"),
        os.path.join(base, f"{day}.meta.json"),
    )


def _read_checkpoint(run_dir, day):
    data_path, meta_path = _checkpoint_paths(run_dir, day)
    if not os.path.exists(data_path) or not os.path.exists(meta_path):
        return {
            "present": False,
            "data_path": data_path,
            "meta_path": meta_path,
        }

    with open(meta_path, "rb") as handle:
        meta_bytes = handle.read()
    meta = json.loads(meta_bytes.decode("utf-8"))

    try:
        with gzip.open(data_path, "rb") as handle:
            raw = handle.read()
    except Exception as exc:
        return {
            "present": True,
            "readable": False,
            "problem": f"gzip unreadable: {exc}",
            "meta": meta,
            "meta_bytes": meta_bytes,
        }

    return {
        "present": True,
        "readable": True,
        "meta": meta,
        "meta_bytes": meta_bytes,
        "raw": raw,
        "data_sha256": _sha256_bytes(raw),
        "meta_sha256": _sha256_bytes(meta_bytes),
    }


def _validate_rows(day, raw, pinned_sha):
    failures = []
    observed_shas = set()
    identities = set()
    row_count = 0

    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        row_count += 1
        try:
            row = json.loads(line)
        except Exception as exc:
            failures.append(
                f"{day}: row {line_number} invalid JSON: {exc}"
            )
            continue

        missing = [
            field for field in IDENTITY_FIELDS
            if field not in row or row.get(field) is None or row.get(field) == ""
        ]
        if missing:
            failures.append(
                f"{day}: row {line_number} incomplete identity fields {missing}"
            )
            continue

        identity = tuple(row.get(field) for field in IDENTITY_FIELDS)
        if identity in identities:
            failures.append(
                f"{day}: duplicate candidate identity {identity!r}"
            )
        identities.add(identity)

        if row.get("date") != day:
            failures.append(
                f"{day}: row {line_number} embeds date {row.get('date')!r}"
            )

        code_sha = row.get("code_git_sha")
        if not code_sha:
            failures.append(
                f"{day}: row {line_number} missing code_git_sha"
            )
        else:
            observed_shas.add(code_sha)
            if pinned_sha and code_sha != pinned_sha:
                failures.append(
                    f"{day}: row {line_number} code_git_sha={code_sha!r} "
                    f"!= manifest pinned {pinned_sha!r}"
                )

        if row.get("outcome") not in (0, 1):
            failures.append(
                f"{day}: row {line_number} non-binary outcome "
                f"{row.get('outcome')!r}"
            )

        if not _finite_probability(row.get("predicted_prob")):
            failures.append(
                f"{day}: row {line_number} invalid predicted_prob "
                f"{row.get('predicted_prob')!r}"
            )

    return {
        "row_count": row_count,
        "observed_shas": observed_shas,
        "failures": failures,
    }


def _dataset_identity_strength(assembled_sha, total_rows, start, end, code_sha):
    derived = {
        "manifest_schema_version": 2,
        "artifact_sha256": assembled_sha,
        "artifact_row_count": total_rows,
        "artifact_date_range": [start, end],
        "code_git_sha_at_lock": code_sha,
    }
    return {
        "derived_manifest": derived,
        "strength": manifest_identity_strength(derived),
    }


def certify_run(run_dir):
    """Certify one durable run directory without modifying it."""
    failures = []
    blockers = []
    warnings = []

    manifest_path = os.path.join(run_dir, "manifest.json")
    index_path = os.path.join(run_dir, "index.json")
    if not os.path.exists(manifest_path) or not os.path.exists(index_path):
        return {
            "verdict": "NOT CANONICAL",
            "failures": ["manifest.json and index.json are both required"],
            "blockers": [],
            "warnings": [],
        }

    manifest = _json(manifest_path)
    index = _json(index_path)

    run_id = manifest.get("run_id")
    if index.get("run_id") != run_id:
        failures.append(
            f"run_id mismatch: manifest={run_id!r} index={index.get('run_id')!r}"
        )

    if manifest.get("repository_identity") != CANONICAL_REPOSITORY_IDENTITY:
        failures.append(
            "manifest repository_identity is not the canonical Full-Count repository"
        )

    identity = index.get("identity") or {}
    expected_identity = {
        "run_id": manifest.get("run_id"),
        "code_git_sha": manifest.get("code_git_sha"),
        "schema_version": manifest.get("schema_version"),
        "requested_start_date": manifest.get("requested_start_date"),
        "requested_end_date": manifest.get("requested_end_date"),
        "weather_mode": manifest.get("weather_mode"),
        "repository_identity": manifest.get("repository_identity"),
        "model_artifact_versions": manifest.get("model_artifact_versions"),
        "evidence_regime": manifest.get("evidence_regime"),
        "candidate_identity_fields": manifest.get("candidate_identity_fields"),
    }
    if identity != expected_identity:
        failures.append("index identity does not exactly match manifest identity")

    recomputed_identity_fingerprint = _identity_fingerprint(identity)
    if index.get("identity_fingerprint") != recomputed_identity_fingerprint:
        failures.append(
            "index identity_fingerprint does not match recomputed identity bytes"
        )

    if index.get("cache_mode") not in ("fresh_source", "frozen_cache"):
        failures.append(
            f"invalid/missing cache_mode: {index.get('cache_mode')!r}"
        )

    environment = index.get("environment") or {}
    environment_fingerprint = environment.get("environment_fingerprint")
    if not environment:
        blockers.append("generator environment identity is absent")
    elif environment_fingerprint != _environment_fingerprint(environment):
        failures.append("environment_fingerprint does not recompute")
    if not environment.get("pip_freeze_sha256"):
        blockers.append("environment pip_freeze_sha256 is absent")
    if not environment.get("critical_packages"):
        blockers.append("environment critical package versions are absent")

    lineage = index.get("source_lineage") or []
    if not lineage:
        blockers.append("source lineage is absent")
    else:
        recomputed_lineage_fingerprint = _lineage_fingerprint(lineage)
        if index.get("source_lineage_fingerprint") != recomputed_lineage_fingerprint:
            failures.append(
                "source_lineage_fingerprint does not match stored lineage records"
            )

    for n, record in enumerate(lineage):
        missing = [
            field for field in SOURCE_REQUIRED_FIELDS
            if record.get(field) in (None, "")
        ]
        if missing:
            blockers.append(
                f"source lineage record {n} missing required fields: {missing}"
            )
        if record.get("cache_mode") != index.get("cache_mode"):
            failures.append(
                f"source lineage record {n} cache_mode differs from index cache_mode"
            )

    source_attestation = _find_source_attestation(run_dir, lineage)
    if source_attestation is None:
        blockers.append("exact source parquet unavailable for independent attestation")
    elif not source_attestation.get("available"):
        failures.append(source_attestation.get("problem", "source attestation failed"))
    else:
        matching = [
            record for record in lineage
            if record.get("content_sha256") == source_attestation["content_sha256"]
        ]
        if len(matching) != 1:
            failures.append(
                "exact source parquet does not map to exactly one lineage record"
            )
        else:
            record = matching[0]
            if record.get("row_count") != source_attestation["row_count"]:
                failures.append(
                    "source parquet row count differs from lineage row_count"
                )
            if record.get("date_coverage") != source_attestation["date_coverage"]:
                failures.append(
                    "source parquet date coverage differs from lineage date_coverage"
                )
            if record.get("schema_fingerprint") is None:
                warnings.append(
                    "stored source lineage omitted schema_fingerprint; "
                    "independent source-schema attestation supplies it additively"
                )
            elif record.get("schema_fingerprint") != source_attestation["schema_fingerprint"]:
                failures.append(
                    "source parquet schema fingerprint differs from stored lineage"
                )
            if record.get("schema_columns") is None:
                warnings.append(
                    "stored source lineage omitted schema_columns; "
                    "independent source-schema attestation supplies them additively"
                )

    start = manifest.get("requested_start_date")
    end = manifest.get("requested_end_date")
    try:
        requested_dates = _dates(start, end)
    except Exception as exc:
        return {
            "verdict": "NOT CANONICAL",
            "failures": failures + [f"invalid requested date range: {exc}"],
            "blockers": blockers,
            "warnings": warnings,
        }

    index_dates = index.get("dates") or {}
    extra_index_dates = sorted(set(index_dates) - set(requested_dates))
    if extra_index_dates:
        failures.append(
            f"index contains dates outside requested range: {extra_index_dates[:10]!r}"
        )

    status_counts = Counter()
    observed_shas = set()
    logical_parts = []
    virtual_assembly = hashlib.sha256()
    total_rows = 0
    checkpoint_failures = []

    for day in requested_dates:
        entry = index_dates.get(day)
        if not entry:
            status_counts["never_run"] += 1
            blockers.append(f"{day}: absent from durable index")
            continue

        status = entry.get("status")
        status_counts[status or "unknown"] += 1
        if status not in ALLOWED_COMPLETE_STATUSES:
            blockers.append(f"{day}: unresolved status {status!r}")
            continue

        checkpoint = _read_checkpoint(run_dir, day)
        if not checkpoint.get("present"):
            checkpoint_failures.append(
                f"{day}: index says {status} but row/meta artifact is absent"
            )
            continue
        if not checkpoint.get("readable"):
            checkpoint_failures.append(
                f"{day}: {checkpoint.get('problem')}"
            )
            continue

        meta = checkpoint["meta"]
        raw = checkpoint["raw"]
        if meta.get("date") != day:
            checkpoint_failures.append(
                f"{day}: meta embeds date {meta.get('date')!r}"
            )
        if meta.get("status") != status:
            checkpoint_failures.append(
                f"{day}: meta status {meta.get('status')!r} != index {status!r}"
            )
        if meta.get("code_git_sha") != manifest.get("code_git_sha"):
            checkpoint_failures.append(
                f"{day}: meta code_git_sha {meta.get('code_git_sha')!r} "
                f"!= manifest {manifest.get('code_git_sha')!r}"
            )

        if checkpoint["data_sha256"] != meta.get("sha256"):
            checkpoint_failures.append(
                f"{day}: recomputed decompressed data SHA != meta sha256"
            )
        if checkpoint["data_sha256"] != entry.get("data_sha256"):
            checkpoint_failures.append(
                f"{day}: recomputed decompressed data SHA != index data_sha256"
            )
        if entry.get("meta_sha256") and checkpoint["meta_sha256"] != entry.get("meta_sha256"):
            checkpoint_failures.append(
                f"{day}: meta file SHA != index meta_sha256"
            )

        row_report = _validate_rows(day, raw, manifest.get("code_git_sha"))
        checkpoint_failures.extend(row_report["failures"])
        observed_shas.update(row_report["observed_shas"])

        if row_report["row_count"] != meta.get("row_count"):
            checkpoint_failures.append(
                f"{day}: parsed row_count={row_report['row_count']} "
                f"!= meta row_count={meta.get('row_count')!r}"
            )
        if row_report["row_count"] != entry.get("rows"):
            checkpoint_failures.append(
                f"{day}: parsed row_count={row_report['row_count']} "
                f"!= index rows={entry.get('rows')!r}"
            )

        if status == "no_games" and row_report["row_count"] != 0:
            checkpoint_failures.append(
                f"{day}: no_games checkpoint contains rows"
            )
        if status == "ok" and row_report["row_count"] == 0:
            checkpoint_failures.append(
                f"{day}: ok checkpoint contains zero rows"
            )

        logical_parts.append(
            (
                day,
                status,
                int(meta.get("row_count") or 0),
                meta.get("sha256") or "",
            )
        )

        if status == "ok":
            virtual_assembly.update(raw)
            total_rows += row_report["row_count"]

    failures.extend(checkpoint_failures)

    expected_summary = dict(index.get("summary") or {})
    computed_summary = {
        "ok": status_counts.get("ok", 0),
        "no_games": status_counts.get("no_games", 0),
        "never_run": (
            len(requested_dates)
            - status_counts.get("ok", 0)
            - status_counts.get("no_games", 0)
        ),
    }
    for key in ("ok", "no_games", "never_run"):
        if expected_summary.get(key) != computed_summary[key]:
            failures.append(
                f"index summary {key}={expected_summary.get(key)!r} "
                f"!= recomputed {computed_summary[key]}"
            )

    complete = (
        not failures
        and computed_summary["never_run"] == 0
        and len(logical_parts) == len(requested_dates)
    )

    if computed_summary["never_run"] != 0:
        blockers.append(
            f"run incomplete: {computed_summary['never_run']} of "
            f"{len(requested_dates)} requested dates unresolved"
        )

    generation_regime = gr.classify_dataset_regime(observed_shas, ())
    if generation_regime["status"] == gr.MIXED_NON_EQUIVALENT:
        failures.append("generation regime is MIXED_NON_EQUIVALENT")
    elif generation_regime["status"] == gr.MIXED_UNPROVEN:
        blockers.append(
            "generation regime is MIXED_UNPROVEN and lacks equivalence evidence"
        )
    elif complete and generation_regime["status"] != gr.SINGLE_SHA:
        # This run is expected single-SHA. Generic mixed-equivalent support can
        # be added with explicit equivalence records if a future run needs it.
        blockers.append(
            f"complete dataset regime {generation_regime['status']} requires "
            "explicit equivalence-record loading before certification"
        )

    assembled_sha = virtual_assembly.hexdigest() if logical_parts else None
    logical_fingerprint = (
        _logical_fingerprint(logical_parts)
        if len(logical_parts) == len(requested_dates)
        else None
    )

    dataset_identity = None
    if complete and assembled_sha:
        dataset_identity = _dataset_identity_strength(
            assembled_sha,
            total_rows,
            start,
            end,
            manifest.get("code_git_sha"),
        )
        if not dataset_identity["strength"].get("promotion_grade"):
            blockers.append("derived dataset identity is below promotion grade")

    model_versions = manifest.get("model_artifact_versions") or {}
    if not model_versions:
        blockers.append("run-level model artifact versions are absent")
    if model_versions != (identity.get("model_artifact_versions") or {}):
        failures.append(
            "manifest and durable identity model_artifact_versions disagree"
        )
    warnings.append(
        "rows carry code_git_sha rather than repeating model/calibration/"
        "feature version strings; for a single-SHA run, run-level version "
        "bundle + per-row code SHA is the recorded provenance contract"
    )

    # De-duplicate identical blocker/warning strings while preserving order.
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    failures = list(dict.fromkeys(failures))

    if failures:
        verdict = "NOT CANONICAL"
    elif blockers:
        verdict = "CERTIFICATION BLOCKED"
    else:
        verdict = "CANONICAL CERTIFIED"

    return {
        "verdict": verdict,
        "run_id": run_id,
        "requested_date_range": [start, end],
        "total_dates": len(requested_dates),
        "summary": computed_summary,
        "total_rows": total_rows,
        "virtual_assembled_byte_sha256": assembled_sha,
        "logical_fingerprint": logical_fingerprint,
        "observed_code_shas": sorted(observed_shas),
        "generation_regime": generation_regime,
        "identity_fingerprint_recomputed": recomputed_identity_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "source_lineage_fingerprint": index.get("source_lineage_fingerprint"),
        "source_schema_attestation": source_attestation,
        "dataset_identity": dataset_identity,
        "failures": failures,
        "blockers": blockers,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = certify_run(args.run_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["verdict"])
        print(
            f"dates: ok={report.get('summary', {}).get('ok')} "
            f"no_games={report.get('summary', {}).get('no_games')} "
            f"unresolved={report.get('summary', {}).get('never_run')}"
        )
        for item in report.get("failures", []):
            print(f"FAIL: {item}")
        for item in report.get("blockers", []):
            print(f"BLOCKED: {item}")
        for item in report.get("warnings", []):
            print(f"WARN: {item}")

    return 0 if report["verdict"] == "CANONICAL CERTIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
