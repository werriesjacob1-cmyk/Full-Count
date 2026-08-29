#!/usr/bin/env python3
"""Deterministic canonical-v2 shard runner.

Scientific/model code is inherited from the pinned canonical lineage; this
runner adds only execution integrity:
- exact Statcast source SHA binding;
- strict historical lineup fallback firewall;
- allow-listed HTTP source firewall;
- generation-time content-bound response ledger;
- runtime no-lookahead enforcement from AccessLog;
- one checksummed row/meta artifact per requested date.

Shards are deterministic contiguous slices of ONE global requested date range.
They may execute in parallel because each shard has a disjoint date set and a
private output directory. Consolidation must later prove every global date is
present exactly once under the same run identity.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import mlb_daily as m
import recommendation
from backtest import canonical_durability as cd
from backtest.engine import StatcastStore, date_range, simulate_date
from backtest.http_provenance import (
    DEFAULT_ALLOWED_HOSTS,
    ResponseLedger,
    install_requests_hook,
    set_active_ledger,
)

V2_SCHEMA_VERSION = 1


class CanonicalV2IntegrityError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_gzip(path, raw):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gz:
            gz.write(raw)
        raw_file.flush()
        os.fsync(raw_file.fileno())
    os.replace(tmp, path)


def git_sha():
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise CanonicalV2IntegrityError("cannot resolve git HEAD")
    value = proc.stdout.strip()
    if len(value) != 40:
        raise CanonicalV2IntegrityError(f"invalid git SHA {value!r}")
    return value


def contiguous_shard(items, shard_index, shard_count):
    if shard_count < 1:
        raise ValueError("shard_count must be >=1")
    if not 0 <= shard_index < shard_count:
        raise ValueError(
            f"shard_index must be in [0,{shard_count}), got {shard_index}"
        )
    n = len(items)
    q, r = divmod(n, shard_count)
    start = shard_index * q + min(shard_index, r)
    size = q + (1 if shard_index < r else 0)
    return items[start:start + size]


def source_identity(path, expected_sha):
    actual = sha256_file(path)
    if actual != expected_sha:
        raise CanonicalV2IntegrityError(
            f"Statcast source SHA mismatch: expected={expected_sha} actual={actual}"
        )
    try:
        import pandas as pd
        frame = pd.read_parquet(path)
    except Exception as exc:
        raise CanonicalV2IntegrityError(
            f"exact Statcast source is unreadable: {exc}"
        ) from exc
    if frame.empty:
        raise CanonicalV2IntegrityError("exact Statcast source is empty")
    if "game_date" not in frame.columns:
        raise CanonicalV2IntegrityError("exact Statcast source lacks game_date")
    parsed = pd.to_datetime(frame["game_date"], errors="coerce").dropna()
    columns = sorted(str(c) for c in frame.columns)
    return {
        "path": os.path.abspath(path),
        "content_sha256": actual,
        "row_count": int(len(frame)),
        "schema_columns": columns,
        "schema_fingerprint": sha256_bytes(",".join(columns).encode("utf-8")),
        "date_coverage": (
            f"{parsed.min().date()}..{parsed.max().date()}"
            if len(parsed) else None
        ),
    }


def model_versions():
    return {
        "model_version": recommendation.MODEL_VERSION,
        "selection_policy_version": recommendation.SELECTION_POLICY_VERSION,
        "calibration_version": recommendation.CALIBRATION_VERSION,
        "feature_version": recommendation.FEATURE_VERSION,
    }


def run_identity(args, code_sha, source):
    identity = {
        "canonical_v2_schema_version": V2_SCHEMA_VERSION,
        "run_id": args.run_id,
        "repository_identity": "werriesjacob1-cmyk/Full-Count",
        "evidence_regime": "canonical_historical_model_data_v2_provenance_complete",
        "requested_start_date": args.start,
        "requested_end_date": args.end,
        "scientific_parent_sha": args.scientific_parent_sha,
        "generation_code_sha": code_sha,
        "model_artifact_versions": model_versions(),
        "weather_mode": "no_weather",
        "bullpen_mode": "enabled",
        "policy_replay": False,
        "strict_historical_lineups": True,
        "candidate_identity_fields": [
            "date", "game_pk", "player_id", "prop_type", "line"
        ],
        "statcast_source": {
            key: source[key]
            for key in (
                "content_sha256",
                "row_count",
                "schema_fingerprint",
                "date_coverage",
            )
        },
        "http_allowed_hosts": sorted(DEFAULT_ALLOWED_HOSTS),
        "http_strict_host_firewall": True,
        "http_response_content_bound": True,
    }
    identity["identity_fingerprint"] = sha256_bytes(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return identity


def row_blob(rows):
    return b"".join(
        (
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def access_log_report(res):
    log = getattr(res, "log", None)
    if log is None:
        return {
            "reads": [],
            "violations": [],
            "logical_fingerprint": sha256_bytes(b""),
        }
    reads = list(getattr(log, "reads", []) or [])
    violations = [
        {"read": read, "reason": reason}
        for read, reason in (log.violations() if hasattr(log, "violations") else [])
    ]
    logical = sha256_bytes(
        "\n".join(
            sorted(
                json.dumps(read, sort_keys=True, separators=(",", ":"))
                for read in reads
            )
        ).encode("utf-8")
    )
    return {
        "reads": reads,
        "violations": violations,
        "logical_fingerprint": logical,
    }


def write_date(out_dir, day, res, http_summary, access_report, code_sha, source):
    rows_dir = os.path.join(out_dir, "rows")
    raw = row_blob(res.rows if res.status == "ok" else [])
    raw_sha = sha256_bytes(raw)
    data_path = os.path.join(rows_dir, f"{day}.jsonl.gz")
    meta_path = os.path.join(rows_dir, f"{day}.meta.json")
    atomic_gzip(data_path, raw)

    status = {
        "ok": "ok",
        "no_games": "no_games",
        "failed": "error",
    }.get(res.status, "error")

    meta = {
        "date": day,
        "status": status,
        "row_count": len(res.rows) if res.status == "ok" else 0,
        "decompressed_rows_sha256": raw_sha,
        "generation_code_sha": code_sha,
        "source_content_sha256": source["content_sha256"],
        "source_schema_fingerprint": source["schema_fingerprint"],
        "generated_at": now_iso(),
        "n_games": res.n_games,
        "n_candidates": res.n_candidates,
        "n_ungraded": res.n_ungraded,
        "ungraded_reasons": dict(res.ungraded_reasons),
        "reason": res.reason,
        "http_provenance": http_summary,
        "point_in_time_access": access_report,
    }
    atomic_json(meta_path, meta)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--shard-index", type=int, required=True)
    ap.add_argument("--shard-count", type=int, required=True)
    ap.add_argument("--source-parquet", required=True)
    ap.add_argument("--expected-source-sha256", required=True)
    ap.add_argument("--scientific-parent-sha", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--archive-http-bodies", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    all_dates = date_range(args.start, args.end)
    shard_dates = contiguous_shard(
        all_dates,
        args.shard_index,
        args.shard_count,
    )
    if not shard_dates:
        raise CanonicalV2IntegrityError("this shard received zero dates")

    code_sha = git_sha()
    source = source_identity(
        args.source_parquet,
        args.expected_source_sha256,
    )
    identity = run_identity(args, code_sha, source)

    os.makedirs(args.out_dir, exist_ok=True)
    environment = cd.environment_identity()
    manifest = {
        "identity": identity,
        "environment": environment,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "shard_first_date": shard_dates[0],
        "shard_last_date": shard_dates[-1],
        "shard_dates": shard_dates,
        "created_at": now_iso(),
    }
    atomic_json(os.path.join(args.out_dir, "shard_manifest.json"), manifest)

    # StatcastStore chooses its path from cache_dir/year/through. The exact
    # source artifact must already be materialized under this filename by the
    # workflow. No pull is allowed; the network firewall would block Savant
    # anyway once a date begins.
    cache_dir = os.path.dirname(os.path.abspath(args.source_parquet))
    expected_name = "statcast_2024_through_2026-08-24.parquet"
    if os.path.basename(args.source_parquet) != expected_name:
        raise CanonicalV2IntegrityError(
            f"source parquet must be named {expected_name!r}, got "
            f"{os.path.basename(args.source_parquet)!r}"
        )

    store = StatcastStore(
        2024,
        "2026-08-24",
        cache_dir=cache_dir,
        verbose=args.verbose,
    )
    loaded = store.load()
    if len(loaded) != source["row_count"]:
        raise CanonicalV2IntegrityError(
            "StatcastStore row count differs from independently bound source"
        )

    # Default False in production. Canonical v2 explicitly enables strict
    # historical behavior here and nowhere else.
    m.HISTORICAL_REPLAY_STRICT_LINEUPS = True

    http_root = os.path.join(args.out_dir, "http")
    ledger = ResponseLedger(
        http_root,
        archive_bodies=args.archive_http_bodies,
        strict_host_firewall=True,
    )
    install_requests_hook()
    set_active_ledger(ledger)

    summaries = {}
    errors = {}
    started = time.time()
    try:
        for index, day in enumerate(shard_dates, 1):
            if args.verbose:
                print(
                    f"[shard {args.shard_index}/{args.shard_count} "
                    f"{index}/{len(shard_dates)}] {day}",
                    flush=True,
                )
            ledger.start_date(day)
            t0 = time.time()
            try:
                res = simulate_date(
                    day,
                    store,
                    use_weather=False,
                    use_bullpen=True,
                    keep_unpriced=False,
                    verbose=args.verbose,
                    apply_policy=False,
                )
                http_summary = ledger.finish_date(day)
            except Exception as exc:
                ledger.abort_date(day, f"{type(exc).__name__}: {exc}")
                errors[day] = f"{type(exc).__name__}: {exc}"
                break

            access = access_log_report(res)
            if access["violations"]:
                errors[day] = (
                    "point-in-time violation: "
                    + "; ".join(v["reason"] for v in access["violations"][:5])
                )
                # Preserve evidence but mark date error.
                res.status = "failed"
                res.reason = errors[day]

            meta = write_date(
                args.out_dir,
                day,
                res,
                http_summary,
                access,
                code_sha,
                source,
            )
            meta["elapsed_seconds"] = round(time.time() - t0, 3)
            atomic_json(
                os.path.join(args.out_dir, "rows", f"{day}.meta.json"),
                meta,
            )
            summaries[day] = {
                "status": meta["status"],
                "rows": meta["row_count"],
                "rows_sha256": meta["decompressed_rows_sha256"],
                "http_logical_fingerprint": http_summary["logical_fingerprint"],
                "http_request_count": http_summary["request_count"],
                "access_logical_fingerprint": access["logical_fingerprint"],
            }
            if meta["status"] == "error":
                errors[day] = meta.get("reason") or "date returned error"
                break
            if index < len(shard_dates):
                time.sleep(args.sleep)
    finally:
        set_active_ledger(None)
        m.HISTORICAL_REPLAY_STRICT_LINEUPS = False

    completed_dates = sorted(summaries)
    shard_summary = {
        "identity_fingerprint": identity["identity_fingerprint"],
        "generation_code_sha": code_sha,
        "scientific_parent_sha": args.scientific_parent_sha,
        "source_content_sha256": source["content_sha256"],
        "source_schema_fingerprint": source["schema_fingerprint"],
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "requested_dates": shard_dates,
        "completed_dates": completed_dates,
        "date_summaries": summaries,
        "errors": errors,
        "elapsed_seconds": round(time.time() - started, 3),
        "finished_at": now_iso(),
    }
    shard_summary["logical_fingerprint"] = sha256_bytes(
        json.dumps(
            {
                "identity_fingerprint": shard_summary["identity_fingerprint"],
                "source_content_sha256": shard_summary["source_content_sha256"],
                "date_summaries": shard_summary["date_summaries"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    atomic_json(
        os.path.join(args.out_dir, "shard_summary.json"),
        shard_summary,
    )

    print(json.dumps(shard_summary, indent=2, sort_keys=True))
    if errors or completed_dates != shard_dates:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
