#!/usr/bin/env python3
"""Materialize a date-quarantined research view from immutable canonical-v2.

This NEVER edits the parent package. It removes whole simulated dates only when
frozen PRE-OUTCOME evidence proves the date is not a trustworthy pregame
research opportunity:

1. the predictive D-schedule explicitly says a game is resuming a game that
   already commenced on an earlier date;
2. per-date metadata records a source/grader-related ungraded candidate; or
3. a StatsAPI predictive/outcome request identity had a failure with no
   successful retry in the immutable request ledger.

The transform consults no row outcome when deciding what to quarantine. Kept
rows are copied byte-for-byte, in original order, so the derived artifact is a
deterministic subset of the immutable parent rather than a regenerated dataset.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = 1
RULESET_VERSION = "canonical-v2-whole-date-source-integrity-v1"
SOURCE_FAILURE_TOKENS = (
    "couldn't fetch",
    "unavailable",
    "no batted-ball statcast data",
    "grader error",
    "source",
    "timeout",
    "connection",
    "missing runs data",
)


class ResearchViewError(RuntimeError):
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


def _single(qs, key):
    values = qs.get(key) or []
    return values[0] if len(values) == 1 else None


def _requested_dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ResearchViewError("parent requested date range is inverted")
    out = []
    current = first
    while current <= last:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _ledger_path(package_dir, report):
    lineage = report.get("source_lineage") or []
    record = next(
        (r for r in lineage if r.get("source") == "mlb_statsapi_request_ledger"),
        None,
    )
    if record:
        for token in str(record.get("notes") or "").split():
            if token.startswith("path="):
                path = os.path.join(package_dir, token[5:])
                if os.path.exists(path):
                    return path
    fallback = os.path.join(package_dir, "mlb_statsapi_request_ledger.jsonl")
    if os.path.exists(fallback):
        return fallback
    raise ResearchViewError("parent package lacks durable StatsAPI request ledger")


def _load_blob(package_dir, report, response_sha):
    if not response_sha:
        return None
    blob_rel = (
        ((report.get("http_totals") or {}).get("response_body_directory"))
        or "http_blobs"
    )
    path = os.path.join(package_dir, blob_rel, f"{response_sha}.gz")
    if not os.path.exists(path):
        raise ResearchViewError(
            f"archived response body missing for SHA {response_sha}"
        )
    with gzip.open(path, "rb") as handle:
        raw = handle.read()
    if sha256_bytes(raw) != response_sha:
        raise ResearchViewError(
            f"archived response body SHA mismatch for {response_sha}"
        )
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ResearchViewError(
            f"archived StatsAPI body {response_sha} is not JSON: {exc}"
        ) from exc


def _source_failure_reason(reason):
    low = str(reason or "").lower()
    return any(token in low for token in SOURCE_FAILURE_TOKENS)


def discover_quarantine(package_dir):
    """Derive the whole-date quarantine from immutable parent evidence only."""
    package_dir = os.path.abspath(package_dir)
    report_path = os.path.join(package_dir, "consolidation_report.json")
    rows_path = os.path.join(package_dir, "rows.jsonl")
    if not os.path.exists(report_path) or not os.path.exists(rows_path):
        raise ResearchViewError("parent consolidation_report.json/rows.jsonl missing")

    report = load_json(report_path)
    start, end = report.get("requested_date_range") or (None, None)
    requested = _requested_dates(start, end)
    requested_set = set(requested)

    evidence = defaultdict(lambda: {
        "prior_date_resumed_games": [],
        "source_grader_ungraded_reasons": [],
        "unrecovered_statsapi_request_identities": [],
    })

    # Rule 2: date metadata. This is pre-transform generation metadata, not
    # outcome inspection of the rows we later keep/drop.
    meta_dir = os.path.join(
        package_dir, report.get("date_metadata_path") or "date_metadata"
    )
    if not os.path.isdir(meta_dir):
        raise ResearchViewError("parent package lacks date_metadata")
    for day in requested:
        path = os.path.join(meta_dir, f"{day}.json")
        if not os.path.exists(path):
            raise ResearchViewError(f"{day}: missing parent date metadata")
        meta = load_json(path)
        for reason, count in (meta.get("ungraded_reasons") or {}).items():
            try:
                n = int(count)
            except Exception:
                n = 0
            if n > 0 and _source_failure_reason(reason):
                evidence[day]["source_grader_ungraded_reasons"].append({
                    "reason": str(reason),
                    "count": n,
                })

    ledger_path = _ledger_path(package_dir, report)
    groups = defaultdict(list)
    d_schedule_rows = []
    with open(ledger_path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise ResearchViewError(
                    f"StatsAPI ledger invalid JSON line {line_no}: {exc}"
                ) from exc
            observed = str(row.get("observed_date") or "")
            if observed not in requested_set:
                raise ResearchViewError(
                    f"StatsAPI observed_date {observed!r} outside parent range"
                )
            key = (
                observed,
                row.get("scientific_phase"),
                row.get("method"),
                row.get("url"),
                row.get("request_body_sha256"),
            )
            groups[key].append(row)

            status = row.get("status_code")
            success = (
                isinstance(status, int)
                and 200 <= status < 300
                and not row.get("exception_type")
            )
            if not success or row.get("scientific_phase") != "predictive_input":
                continue
            parsed = urlparse(str(row.get("url") or ""))
            if parsed.path != "/api/v1/schedule":
                continue
            qs = parse_qs(parsed.query)
            if _single(qs, "date") != observed:
                continue
            d_schedule_rows.append(row)

    # Rule 3: failed logical identity with no successful retry.
    for key, attempts in groups.items():
        success = any(
            isinstance(row.get("status_code"), int)
            and 200 <= row["status_code"] < 300
            and not row.get("exception_type")
            for row in attempts
        )
        had_failure = any(
            row.get("exception_type")
            or (
                isinstance(row.get("status_code"), int)
                and not 200 <= row["status_code"] < 300
            )
            for row in attempts
        )
        if had_failure and not success:
            day = key[0]
            evidence[day]["unrecovered_statsapi_request_identities"].append({
                "scientific_phase": key[1],
                "method": key[2],
                "url": key[3],
                "request_body_sha256": key[4],
                "attempt_count": len(attempts),
                "status_codes": [r.get("status_code") for r in attempts],
                "exception_types": [r.get("exception_type") for r in attempts],
            })

    # Rule 1: globally inspect every successful frozen predictive D-schedule.
    resumed_seen = set()
    d_schedule_days = set()
    for row in d_schedule_rows:
        observed = str(row["observed_date"])
        d_schedule_days.add(observed)
        payload = _load_blob(package_dir, report, row.get("response_sha256"))
        if not isinstance(payload, dict):
            raise ResearchViewError(f"{observed}: D-schedule archive is not an object")
        for block in payload.get("dates") or []:
            for game in block.get("games") or []:
                game_pk = game.get("gamePk")
                origin = game.get("resumedFromDate")
                if game_pk is None or not origin:
                    continue
                origin = str(origin)
                if origin >= observed:
                    continue
                identity = (observed, int(game_pk), origin)
                if identity in resumed_seen:
                    continue
                resumed_seen.add(identity)
                teams = game.get("teams") or {}
                def team(side):
                    t = (((teams.get(side) or {}).get("team")) or {})
                    return {"id": t.get("id"), "name": t.get("name")}
                evidence[observed]["prior_date_resumed_games"].append({
                    "game_pk": int(game_pk),
                    "resumed_from_date": origin,
                    "resumed_from": game.get("resumedFrom"),
                    "official_date": game.get("officialDate"),
                    "game_date": game.get("gameDate"),
                    "game_type": game.get("gameType"),
                    "away": team("away"),
                    "home": team("home"),
                })

    missing_schedule_days = sorted(requested_set - d_schedule_days)
    if missing_schedule_days:
        raise ResearchViewError(
            "cannot prove research population: no successful predictive "
            f"D-schedule evidence for {len(missing_schedule_days)} date(s), "
            f"sample={missing_schedule_days[:10]}"
        )

    excluded = sorted(
        day for day in requested
        if any(evidence[day][key] for key in evidence[day])
    )
    normalized_evidence = {
        day: evidence[day]
        for day in excluded
    }
    return {
        "ruleset_version": RULESET_VERSION,
        "requested_dates": requested,
        "excluded_dates": excluded,
        "evidence_by_date": normalized_evidence,
        "counts": {
            "requested_dates": len(requested),
            "excluded_dates": len(excluded),
            "included_dates": len(requested) - len(excluded),
            "prior_date_resumed_games": sum(
                len(v["prior_date_resumed_games"]) for v in normalized_evidence.values()
            ),
            "source_grader_failure_dates": sum(
                bool(v["source_grader_ungraded_reasons"])
                for v in normalized_evidence.values()
            ),
            "unrecovered_statsapi_identity_dates": sum(
                bool(v["unrecovered_statsapi_request_identities"])
                for v in normalized_evidence.values()
            ),
            "unrecovered_statsapi_identities": sum(
                len(v["unrecovered_statsapi_request_identities"])
                for v in normalized_evidence.values()
            ),
        },
    }


def materialize(package_dir, out_dir):
    package_dir = os.path.abspath(package_dir)
    out_dir = os.path.abspath(out_dir)
    if os.path.exists(out_dir):
        raise ResearchViewError(f"refusing to overwrite existing output {out_dir}")
    os.makedirs(out_dir)

    report_path = os.path.join(package_dir, "consolidation_report.json")
    rows_path = os.path.join(package_dir, "rows.jsonl")
    report = load_json(report_path)
    parent_rows_sha = sha256_file(rows_path)
    if parent_rows_sha != report.get("assembled_rows_sha256"):
        raise ResearchViewError(
            "parent rows SHA does not match consolidation_report assembled_rows_sha256"
        )

    quarantine = discover_quarantine(package_dir)
    excluded = set(quarantine["excluded_dates"])
    research_rows_path = os.path.join(out_dir, "rows.jsonl")

    total = kept = dropped = 0
    kept_dates = set()
    dropped_by_date = defaultdict(int)
    h = hashlib.sha256()
    with open(rows_path, "rb") as src, open(research_rows_path, "xb") as dst:
        for line_no, raw in enumerate(src, 1):
            if not raw.strip():
                raise ResearchViewError(f"parent rows.jsonl contains blank line {line_no}")
            try:
                row = json.loads(raw)
            except Exception as exc:
                raise ResearchViewError(
                    f"parent rows.jsonl invalid JSON line {line_no}: {exc}"
                ) from exc
            day = str(row.get("date") or "")
            total += 1
            if day in excluded:
                dropped += 1
                dropped_by_date[day] += 1
                continue
            dst.write(raw)
            h.update(raw)
            kept += 1
            kept_dates.add(day)

    if total != int(report.get("total_rows") or -1):
        raise ResearchViewError(
            f"parent row count {total} != consolidation report {report.get('total_rows')}"
        )
    if kept <= 0:
        raise ResearchViewError("research view would contain zero rows")

    rules = {
        "ruleset_version": RULESET_VERSION,
        "granularity": "whole_simulated_date",
        "decision_sources": [
            "archived_predictive_D_schedule",
            "per_date_generation_metadata",
            "immutable_statsapi_request_ledger",
        ],
        "exclude_if": [
            "predictive_D_schedule_has_resumedFromDate_before_D",
            "source_or_grader_related_ungraded_candidate_count_gt_0",
            "statsapi_logical_request_identity_failed_without_successful_retry",
        ],
        "source_failure_tokens": list(SOURCE_FAILURE_TOKENS),
        "row_outcome_fields_consulted_for_exclusion": False,
        "kept_row_bytes_modified": False,
    }
    rules_fp = sha256_bytes(
        json.dumps(rules, sort_keys=True, separators=(",", ":")).encode()
    )

    manifest = {
        "research_view_schema_version": SCHEMA_VERSION,
        "artifact_kind": "canonical_v2_date_quarantined_research_view",
        "parent": {
            "run_id": report.get("run_id"),
            "generation_code_sha": report.get("generation_code_sha"),
            "scientific_parent_sha": report.get("scientific_parent_sha"),
            "requested_date_range": report.get("requested_date_range"),
            "requested_dates": report.get("requested_dates"),
            "parent_total_rows": report.get("total_rows"),
            "parent_rows_sha256": parent_rows_sha,
            "parent_report_sha256": report.get("report_sha256"),
            "parent_report_file_sha256": sha256_file(report_path),
            "parent_source_lineage_fingerprint": report.get("source_lineage_fingerprint"),
        },
        "transform": {
            "transform_file": "backtest/canonical_v2_research_view.py",
            "transform_file_sha256": sha256_file(__file__),
            "rules": rules,
            "rules_fingerprint": rules_fp,
        },
        "quarantine": quarantine,
        "row_accounting": {
            "parent_rows": total,
            "research_rows": kept,
            "excluded_rows": dropped,
            "excluded_rows_by_date": dict(sorted(dropped_by_date.items())),
            "research_row_dates_with_rows": len(kept_dates),
        },
        "research_rows_path": "rows.jsonl",
        "research_rows_sha256": h.hexdigest(),
    }
    logical = dict(manifest)
    manifest["manifest_sha256"] = sha256_bytes(
        json.dumps(logical, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    manifest_path = os.path.join(out_dir, "research_view_manifest.json")
    with open(manifest_path, "x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("parent_package")
    ap.add_argument("out_dir")
    args = ap.parse_args()
    manifest = materialize(args.parent_package, args.out_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
