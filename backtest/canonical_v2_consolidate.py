#!/usr/bin/env python3
"""Independent consolidation/validation for canonical-v2 shard artifacts."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import shutil
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlparse


class ConsolidationError(RuntimeError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path, raw):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_json(path, payload):
    atomic_write(
        path,
        (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode(),
    )


def dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    out = []
    current = first
    while current <= last:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def contiguous_shard(items, shard_index, shard_count):
    n = len(items)
    q, r = divmod(n, shard_count)
    start = shard_index * q + min(shard_index, r)
    size = q + (1 if shard_index < r else 0)
    return items[start:start + size]


def logical_http_entry(entry):
    return {
        "observed_date": entry.get("observed_date"),
        "scientific_phase": entry.get("scientific_phase"),
        "method": entry.get("method"),
        "url": entry.get("url"),
        "request_body_sha256": entry.get("request_body_sha256"),
        "status_code": entry.get("status_code"),
        "response_sha256": entry.get("response_sha256"),
        "response_bytes": entry.get("response_bytes"),
        "exception_type": entry.get("exception_type"),
    }


def http_logical_fingerprint(entries):
    encoded = sorted(
        json.dumps(logical_http_entry(entry), sort_keys=True, separators=(",", ":"))
        for entry in entries
    )
    return sha256_bytes("\n".join(encoded).encode())


def scientific_environment_signature(environment):
    payload = {
        key: environment.get(key)
        for key in (
            "python_version",
            "python_implementation",
            "critical_packages",
            "pip_freeze_sha256",
        )
    }
    return sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )


def validate_http_ledger(shard_dir, day, meta, require_body_archive=False):
    http = meta.get("http_provenance") or {}
    rel = http.get("ledger_file")
    if not rel:
        raise ConsolidationError(f"{day}: missing HTTP ledger filename")
    path = os.path.join(shard_dir, "http", rel)
    if not os.path.exists(path):
        raise ConsolidationError(f"{day}: missing HTTP ledger {path}")
    raw = open(path, "rb").read()
    if sha256_bytes(raw) != http.get("ledger_file_sha256"):
        raise ConsolidationError(f"{day}: HTTP ledger byte SHA mismatch")

    entries = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception as exc:
            raise ConsolidationError(
                f"{day}: invalid HTTP ledger JSON line {line_no}: {exc}"
            ) from exc
        if entry.get("observed_date") != day:
            raise ConsolidationError(
                f"{day}: HTTP entry carries observed_date={entry.get('observed_date')!r}"
            )
        host = (urlparse(str(entry.get("url") or "")).hostname or "").lower()
        allowed = set(http.get("allowed_hosts") or [])
        if host not in allowed:
            raise ConsolidationError(
                f"{day}: HTTP ledger contains unapproved host {host!r}"
            )
        response_sha = entry.get("response_sha256")
        archived = entry.get("archived_body")
        if require_body_archive and response_sha and not archived:
            raise ConsolidationError(
                f"{day}: response {response_sha} lacks required archived body"
            )
        if archived:
            body_path = os.path.join(shard_dir, "http", archived)
            if not os.path.exists(body_path):
                raise ConsolidationError(
                    f"{day}: archived HTTP body missing: {body_path}"
                )
            with gzip.open(body_path, "rb") as handle:
                body = handle.read()
            if sha256_bytes(body) != response_sha:
                raise ConsolidationError(
                    f"{day}: archived HTTP body SHA mismatch for {response_sha}"
                )
        entries.append(entry)

    if len(entries) != int(http.get("request_count", -1)):
        raise ConsolidationError(
            f"{day}: HTTP request_count mismatch "
            f"{len(entries)} != {http.get('request_count')}"
        )
    if http_logical_fingerprint(entries) != http.get("logical_fingerprint"):
        raise ConsolidationError(f"{day}: HTTP logical fingerprint mismatch")
    if http.get("strict_host_firewall") is not True:
        raise ConsolidationError(f"{day}: HTTP source firewall was not enabled")
    return entries


def validate_rows(shard_dir, day, meta):
    data_path = os.path.join(shard_dir, "rows", f"{day}.jsonl.gz")
    if not os.path.exists(data_path):
        raise ConsolidationError(f"{day}: missing row artifact")
    with gzip.open(data_path, "rb") as handle:
        raw = handle.read()
    if sha256_bytes(raw) != meta.get("decompressed_rows_sha256"):
        raise ConsolidationError(f"{day}: decompressed row SHA mismatch")

    rows = []
    ids = set()
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            raise ConsolidationError(
                f"{day}: invalid row JSON line {line_no}: {exc}"
            ) from exc
        if row.get("date") != day:
            raise ConsolidationError(
                f"{day}: row embeds date {row.get('date')!r}"
            )
        identity = (
            row.get("date"),
            row.get("game_pk"),
            row.get("player_id"),
            row.get("prop_type"),
            row.get("line"),
        )
        if any(value is None for value in identity):
            raise ConsolidationError(f"{day}: incomplete candidate identity {identity!r}")
        if identity in ids:
            raise ConsolidationError(f"{day}: duplicate candidate identity {identity!r}")
        ids.add(identity)
        if row.get("outcome") not in (0, 1):
            raise ConsolidationError(f"{day}: non-binary outcome")
        probability = row.get("predicted_prob")
        try:
            p = float(probability)
        except (TypeError, ValueError):
            raise ConsolidationError(f"{day}: invalid predicted_prob {probability!r}")
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ConsolidationError(f"{day}: predicted_prob outside [0,1]")
        if row.get("code_git_sha") != meta.get("generation_code_sha"):
            raise ConsolidationError(
                f"{day}: row code SHA differs from date metadata"
            )
        rows.append(row)

    if len(rows) != int(meta.get("row_count", -1)):
        raise ConsolidationError(
            f"{day}: row_count mismatch {len(rows)} != {meta.get('row_count')}"
        )
    status = meta.get("status")
    if status == "no_games" and rows:
        raise ConsolidationError(f"{day}: no_games contains rows")
    if status == "ok" and not rows:
        raise ConsolidationError(f"{day}: ok date contains zero rows")
    if status not in ("ok", "no_games"):
        raise ConsolidationError(f"{day}: unresolved status {status!r}")

    access = meta.get("point_in_time_access") or {}
    if access.get("violations"):
        raise ConsolidationError(f"{day}: point-in-time violations recorded")
    return raw, rows, ids


def build_source_lineage(all_http_entries, identity, source_sha, source_schema, start, end):
    stats = []
    mlbcom = []
    for entry in all_http_entries:
        host = (urlparse(str(entry.get("url") or "")).hostname or "").lower()
        if host == "statsapi.mlb.com":
            stats.append(entry)
        elif host in ("www.mlb.com", "mlb.com"):
            mlbcom.append(entry)

    def record(name, entries):
        logical_rows = [
            logical_http_entry(entry)
            for entry in entries
        ]
        raw = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in sorted(
                logical_rows,
                key=lambda r: json.dumps(r, sort_keys=True, separators=(",", ":")),
            )
        )
        return {
            "source": name,
            "request_identity": f"{name}:{identity['run_id']}",
            "retrieval_timestamp": None,
            "library": "requests",
            "library_version": (
                (identity.get("environment") or {})
                .get("critical_packages", {})
                .get("requests")
            ),
            "row_count": len(entries),
            "schema_columns": sorted([
                "method",
                "url",
                "request_headers",
                "request_body_sha256",
                "status_code",
                "response_sha256",
                "response_bytes",
                "exception_type",
            ]),
            "schema_fingerprint": sha256_bytes(
                ",".join(sorted([
                    "method",
                    "url",
                    "request_headers",
                    "request_body_sha256",
                    "status_code",
                    "response_sha256",
                    "response_bytes",
                    "exception_type",
                ])).encode()
            ),
            "content_sha256": sha256_bytes(raw),
            "date_coverage": f"{start}..{end}",
            "cache_mode": "generation_time_content_hash_ledger",
            "logical_rows": logical_rows,
        }

    return [
        {
            "source": "statcast_leaguewide",
            "request_identity": "statcast:2024:through=2026-08-24",
            "retrieval_timestamp": None,
            "library": "pybaseball",
            "library_version": None,
            "row_count": source_schema.get("row_count"),
            "schema_columns": source_schema.get("schema_columns"),
            "schema_fingerprint": source_schema.get("schema_fingerprint"),
            "content_sha256": source_sha,
            "date_coverage": source_schema.get("date_coverage"),
            "cache_mode": "frozen_exact_artifact",
        },
        record("mlb_statsapi_request_ledger", stats),
        record("mlbcom_dated_lineup_request_ledger", mlbcom),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shards-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--shard-count", type=int, required=True)
    ap.add_argument("--source-parquet", required=True)
    ap.add_argument("--outcome-parquet", required=True)
    ap.add_argument("--require-body-archive", action="store_true")
    args = ap.parse_args()

    expected_dates = dates(args.start, args.end)
    if not os.path.exists(args.source_parquet):
        raise ConsolidationError(
            f"exact Statcast source parquet missing: {args.source_parquet}"
        )
    provided_source_sha = sha256_file(args.source_parquet)
    if not os.path.exists(args.outcome_parquet):
        raise ConsolidationError(
            f"exact outcome-only Statcast parquet missing: {args.outcome_parquet}"
        )
    provided_outcome_sha = sha256_file(args.outcome_parquet)

    manifests = []
    summaries = []
    shard_dirs = []

    for shard_index in range(args.shard_count):
        shard_dir = os.path.join(args.shards_root, f"shard-{shard_index}")
        shard_dirs.append(shard_dir)
        manifest_path = os.path.join(shard_dir, "shard_manifest.json")
        summary_path = os.path.join(shard_dir, "shard_summary.json")
        if not os.path.exists(manifest_path) or not os.path.exists(summary_path):
            raise ConsolidationError(
                f"missing manifest/summary for shard {shard_index}"
            )
        manifests.append(load_json(manifest_path))
        summaries.append(load_json(summary_path))

    identity_fingerprints = {
        manifest["identity"]["identity_fingerprint"]
        for manifest in manifests
    }
    if len(identity_fingerprints) != 1:
        raise ConsolidationError(
            f"shards have multiple run identities: {identity_fingerprints!r}"
        )
    identities = [manifest["identity"] for manifest in manifests]
    identity = identities[0]
    if identity.get("run_id") != args.run_id:
        raise ConsolidationError("run_id differs from requested consolidation run")
    if identity.get("requested_start_date") != args.start or identity.get("requested_end_date") != args.end:
        raise ConsolidationError("date range differs from requested consolidation range")

    code_shas = {identity_item.get("generation_code_sha") for identity_item in identities}
    source_shas = {
        (identity_item.get("statcast_source") or {}).get("content_sha256")
        for identity_item in identities
    }
    outcome_shas = {
        (identity_item.get("outcome_statcast_source") or {}).get("content_sha256")
        for identity_item in identities
    }
    if len(code_shas) != 1 or None in code_shas:
        raise ConsolidationError(f"mixed generation code SHA across shards: {code_shas!r}")
    if len(source_shas) != 1 or None in source_shas:
        raise ConsolidationError(f"mixed source SHA across shards: {source_shas!r}")
    if provided_source_sha != next(iter(source_shas)):
        raise ConsolidationError(
            "provided exact Statcast parquet differs from shard-bound source SHA"
        )
    if len(outcome_shas) != 1 or None in outcome_shas:
        raise ConsolidationError(
            f"mixed outcome-only source SHA across shards: {outcome_shas!r}"
        )
    if provided_outcome_sha != next(iter(outcome_shas)):
        raise ConsolidationError(
            "provided outcome-only Statcast parquet differs from shard-bound source SHA"
        )

    env_signatures = {
        scientific_environment_signature(manifest.get("environment") or {})
        for manifest in manifests
    }
    if len(env_signatures) != 1:
        raise ConsolidationError(
            "shards differ in Python/package/pip-freeze scientific environment"
        )

    seen_dates = set()
    all_rows_raw = []
    date_metadata_dir = os.path.join(args.out_dir, "date_metadata")
    os.makedirs(date_metadata_dir, exist_ok=True)
    global_ids = set()
    all_http_entries = []
    response_blob_sources = {}
    status_counts = Counter()
    total_requests = 0
    total_response_bytes = 0
    total_exceptions = 0
    total_non_2xx = 0

    for shard_index, (shard_dir, manifest, summary) in enumerate(
        zip(shard_dirs, manifests, summaries)
    ):
        expected_shard = contiguous_shard(
            expected_dates,
            shard_index,
            args.shard_count,
        )
        if manifest.get("shard_dates") != expected_shard:
            raise ConsolidationError(
                f"shard {shard_index} date assignment differs from deterministic partition"
            )
        if summary.get("errors"):
            raise ConsolidationError(
                f"shard {shard_index} contains errors: {summary['errors']!r}"
            )
        if summary.get("completed_dates") != expected_shard:
            raise ConsolidationError(
                f"shard {shard_index} did not complete its exact date set"
            )

        for day in expected_shard:
            if day in seen_dates:
                raise ConsolidationError(f"date {day} appears in multiple shards")
            seen_dates.add(day)
            meta_path = os.path.join(shard_dir, "rows", f"{day}.meta.json")
            if not os.path.exists(meta_path):
                raise ConsolidationError(f"{day}: missing metadata")
            meta = load_json(meta_path)
            atomic_json(
                os.path.join(date_metadata_dir, f"{day}.json"),
                meta,
            )
            if meta.get("generation_code_sha") not in code_shas:
                raise ConsolidationError(f"{day}: generation code SHA mismatch")
            if meta.get("source_content_sha256") not in source_shas:
                raise ConsolidationError(f"{day}: source SHA mismatch")
            if meta.get("outcome_source_content_sha256") not in outcome_shas:
                raise ConsolidationError(f"{day}: outcome-only source SHA mismatch")

            raw, rows, ids = validate_rows(shard_dir, day, meta)
            overlap = global_ids.intersection(ids)
            if overlap:
                raise ConsolidationError(
                    f"{day}: duplicate candidate identity across dates/shards: "
                    f"{next(iter(overlap))!r}"
                )
            global_ids.update(ids)
            if meta["status"] == "ok":
                all_rows_raw.append(raw)
            status_counts[meta["status"]] += 1

            entries = validate_http_ledger(
                shard_dir,
                day,
                meta,
                require_body_archive=args.require_body_archive,
            )
            for entry in entries:
                response_sha = entry.get("response_sha256")
                archived = entry.get("archived_body")
                if response_sha and archived:
                    source_path = os.path.join(shard_dir, "http", archived)
                    prior = response_blob_sources.get(response_sha)
                    if prior is None:
                        response_blob_sources[response_sha] = source_path
                    else:
                        # Content-addressed identity makes either file valid;
                        # both were independently validated above.
                        pass
            all_http_entries.extend(entries)
            hp = meta["http_provenance"]
            total_requests += int(hp.get("request_count") or 0)
            total_response_bytes += int(hp.get("response_bytes_total") or 0)
            total_exceptions += int(hp.get("exceptions") or 0)
            total_non_2xx += int(hp.get("http_non_2xx") or 0)

    if seen_dates != set(expected_dates):
        missing = sorted(set(expected_dates) - seen_dates)
        extra = sorted(seen_dates - set(expected_dates))
        raise ConsolidationError(
            f"global date accounting mismatch: missing={missing[:10]} extra={extra[:10]}"
        )

    assembled = b"".join(all_rows_raw)
    assembled_sha = sha256_bytes(assembled)
    os.makedirs(args.out_dir, exist_ok=True)
    rows_path = os.path.join(args.out_dir, "rows.jsonl")
    atomic_write(rows_path, assembled)

    final_source_dir = os.path.join(args.out_dir, "source")
    os.makedirs(final_source_dir, exist_ok=True)
    final_source_path = os.path.join(
        final_source_dir,
        "statcast_2024_through_2026-08-24.parquet",
    )
    shutil.copyfile(args.source_parquet, final_source_path)
    if sha256_file(final_source_path) != provided_source_sha:
        raise ConsolidationError("copied Statcast source SHA changed")
    outcome_name = os.path.basename(args.outcome_parquet)
    final_outcome_path = os.path.join(final_source_dir, outcome_name)
    shutil.copyfile(args.outcome_parquet, final_outcome_path)
    if sha256_file(final_outcome_path) != provided_outcome_sha:
        raise ConsolidationError("copied outcome-only Statcast source SHA changed")

    # Source schema from the common shard identity.
    statcast = identity["statcast_source"]
    source_schema = {
        "row_count": statcast.get("row_count"),
        "schema_fingerprint": statcast.get("schema_fingerprint"),
        "date_coverage": statcast.get("date_coverage"),
        "schema_columns": statcast.get("schema_columns"),
    }
    outcome_statcast = identity["outcome_statcast_source"]
    outcome_schema = {
        "row_count": outcome_statcast.get("row_count"),
        "schema_fingerprint": outcome_statcast.get("schema_fingerprint"),
        "date_coverage": outcome_statcast.get("date_coverage"),
        "schema_columns": outcome_statcast.get("schema_columns"),
    }
    # Recover full columns from any manifest's source-independent attestation
    # if a future shard schema adds them; current identity intentionally keeps
    # only the compact fields needed to compare shards.

    # Bind HTTP scientific content independent of thread/timestamp order.
    stats_rows = []
    mlbcom_rows = []
    for entry in all_http_entries:
        logical = logical_http_entry(entry)
        host = (urlparse(str(entry.get("url") or "")).hostname or "").lower()
        if host == "statsapi.mlb.com":
            stats_rows.append(logical)
        elif host in ("www.mlb.com", "mlb.com"):
            mlbcom_rows.append(logical)

    def write_ledger(name, rows):
        ordered = sorted(
            rows,
            key=lambda r: json.dumps(r, sort_keys=True, separators=(",", ":")),
        )
        raw = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in ordered
        )
        path = os.path.join(args.out_dir, name)
        atomic_write(path, raw)
        return {
            "path": name,
            "row_count": len(rows),
            "content_sha256": sha256_bytes(raw),
        }

    stats_ledger = write_ledger(
        "mlb_statsapi_request_ledger.jsonl",
        stats_rows,
    )
    mlbcom_ledger = write_ledger(
        "mlbcom_dated_lineup_request_ledger.jsonl",
        mlbcom_rows,
    )

    # Preserve the exact external response bytes in the final canonical
    # package, deduplicated by content SHA across dates and shards.
    final_blob_dir = os.path.join(args.out_dir, "http_blobs")
    os.makedirs(final_blob_dir, exist_ok=True)
    archive_bytes = 0
    for response_sha, source_path in sorted(response_blob_sources.items()):
        target = os.path.join(final_blob_dir, f"{response_sha}.gz")
        if not os.path.exists(target):
            shutil.copyfile(source_path, target)
        with gzip.open(target, "rb") as handle:
            body = handle.read()
        if sha256_bytes(body) != response_sha:
            raise ConsolidationError(
                f"consolidated response body SHA mismatch: {response_sha}"
            )
        archive_bytes += os.path.getsize(target)

    response_shas_in_ledgers = {
        row.get("response_sha256")
        for row in stats_rows + mlbcom_rows
        if row.get("response_sha256")
    }
    if args.require_body_archive and response_shas_in_ledgers != set(response_blob_sources):
        missing = sorted(response_shas_in_ledgers - set(response_blob_sources))
        extra = sorted(set(response_blob_sources) - response_shas_in_ledgers)
        raise ConsolidationError(
            f"response archive coverage mismatch: missing={missing[:5]} extra={extra[:5]}"
        )

    lineage = [
        {
            "source": "statcast_leaguewide",
            "request_identity": "statcast:2024:through=2026-08-24",
            "content_sha256": next(iter(source_shas)),
            "row_count": statcast.get("row_count"),
            "schema_columns": statcast.get("schema_columns"),
            "schema_fingerprint": statcast.get("schema_fingerprint"),
            "date_coverage": statcast.get("date_coverage"),
            "cache_mode": "frozen_exact_artifact",
            "library": "pybaseball",
            "library_version": (
                (manifests[0].get("environment") or {})
                .get("critical_packages", {})
                .get("pybaseball")
            ),
            "retrieval_timestamp": None,
        },
        {
            "source": "statcast_outcome_only",
            "request_identity": f"statcast:outcome-only:{identity.get('outcome_only_date')}",
            "content_sha256": next(iter(outcome_shas)),
            "row_count": outcome_statcast.get("row_count"),
            "schema_columns": outcome_statcast.get("schema_columns"),
            "schema_fingerprint": outcome_statcast.get("schema_fingerprint"),
            "date_coverage": outcome_statcast.get("date_coverage"),
            "cache_mode": "frozen_exact_artifact_grader_only",
            "library": "pybaseball",
            "library_version": (
                (manifests[0].get("environment") or {})
                .get("critical_packages", {})
                .get("pybaseball")
            ),
            "retrieval_timestamp": None,
        },
        {
            "source": "mlb_statsapi_request_ledger",
            "request_identity": f"mlb_statsapi_request_ledger:{args.run_id}",
            "content_sha256": stats_ledger["content_sha256"],
            "row_count": stats_ledger["row_count"],
            "schema_columns": sorted([
                "observed_date", "scientific_phase", "method", "url", "request_body_sha256", "status_code",
                "response_sha256", "response_bytes", "exception_type",
            ]),
            "schema_fingerprint": sha256_bytes(",".join(sorted([
                "scientific_phase", "method", "url", "request_body_sha256", "status_code",
                "response_sha256", "response_bytes", "exception_type",
            ])).encode()),
            "date_coverage": f"{args.start}..{args.end}",
            "cache_mode": "generation_time_content_hash_ledger",
            "library": "requests",
            "library_version": (
                (manifests[0].get("environment") or {})
                .get("critical_packages", {})
                .get("requests")
            ),
            "retrieval_timestamp": None,
            "notes": f"path={stats_ledger['path']} bodies=http_blobs/",
        },
        {
            "source": "mlbcom_dated_lineup_request_ledger",
            "request_identity": f"mlbcom_dated_lineup_request_ledger:{args.run_id}",
            "content_sha256": mlbcom_ledger["content_sha256"],
            "row_count": mlbcom_ledger["row_count"],
            "schema_columns": sorted([
                "scientific_phase", "method", "url", "request_body_sha256", "status_code",
                "response_sha256", "response_bytes", "exception_type",
            ]),
            "schema_fingerprint": sha256_bytes(",".join(sorted([
                "scientific_phase", "method", "url", "request_body_sha256", "status_code",
                "response_sha256", "response_bytes", "exception_type",
            ])).encode()),
            "date_coverage": f"{args.start}..{args.end}",
            "cache_mode": "generation_time_content_hash_ledger",
            "library": "requests",
            "library_version": (
                (manifests[0].get("environment") or {})
                .get("critical_packages", {})
                .get("requests")
            ),
            "retrieval_timestamp": None,
            "notes": f"path={mlbcom_ledger['path']} bodies=http_blobs/",
        },
    ]
    lineage_fingerprint = sha256_bytes(
        "\n".join(
            sorted(
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
                for record in lineage
            )
        ).encode()
    )

    report = {
        "verdict": "CANONICAL_V2_CONSOLIDATED",
        "run_id": args.run_id,
        "requested_date_range": [args.start, args.end],
        "requested_dates": len(expected_dates),
        "status_counts": dict(status_counts),
        "total_rows": assembled.count(b"\n"),
        "unique_candidate_identities": len(global_ids),
        "assembled_rows_sha256": assembled_sha,
        "identity": identity,
        "identity_fingerprint": next(iter(identity_fingerprints)),
        "generation_code_sha": next(iter(code_shas)),
        "scientific_parent_sha": identity.get("scientific_parent_sha"),
        "scientific_environment_signature": next(iter(env_signatures)),
        "scientific_environment": manifests[0].get("environment") or {},
        "execution_environment_fingerprints": sorted({
            (manifest.get("environment") or {}).get("environment_fingerprint")
            for manifest in manifests
        }),
        "statcast_source_sha256": next(iter(source_shas)),
        "statcast_source_path": "source/statcast_2024_through_2026-08-24.parquet",
        "outcome_statcast_source_sha256": next(iter(outcome_shas)),
        "outcome_statcast_source_path": f"source/{outcome_name}",
        "date_metadata_path": "date_metadata",
        "source_lineage": lineage,
        "source_lineage_fingerprint": lineage_fingerprint,
        "http_totals": {
            "request_count": total_requests,
            "response_bytes": total_response_bytes,
            "exceptions": total_exceptions,
            "non_2xx": total_non_2xx,
            "statsapi_ledger": stats_ledger,
            "mlbcom_ledger": mlbcom_ledger,
            "archived_unique_response_bodies": len(response_blob_sources),
            "archived_response_gzip_bytes": archive_bytes,
            "response_body_directory": "http_blobs",
        },
        "shard_summaries": summaries,
    }
    report["report_sha256"] = sha256_bytes(
        json.dumps(report, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    atomic_json(
        os.path.join(args.out_dir, "consolidation_report.json"),
        report,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
