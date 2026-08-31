#!/usr/bin/env python3
"""Independent certification of a canonical-v2 date-quarantined research view.

The raw Run #6 parent is never relabeled. This auditor first runs the existing
canonical-v2 certifier on the immutable parent, then permits ONLY two known
classes of parent incompleteness to be resolved by a deterministic whole-date
quarantine:

- source/grader-related ungraded candidates, and
- unrecovered StatsAPI request identities.

It independently re-derives the quarantine from archived pre-outcome evidence,
requires the materializer to have excluded exactly that set (no hand-picked
extra dates), and proves rows.jsonl is a byte-preserving parent subset.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backtest import canonical_v2_certify as base_cert

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
ALLOWED_PARENT_BLOCKER_PREFIXES = (
    "source/grader-related ungraded candidates:",
)
UNRECOVERED_BLOCKER_RE = re.compile(
    r"^(\d+) unrecovered StatsAPI request identities$"
)


class ResearchCertificationError(RuntimeError):
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


def _dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ResearchCertificationError("parent range inverted")
    out = []
    current = first
    while current <= last:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _source_failure(reason):
    low = str(reason or "").lower()
    return any(token in low for token in SOURCE_FAILURE_TOKENS)


def _ledger_path(package_dir, report):
    for record in report.get("source_lineage") or []:
        if record.get("source") != "mlb_statsapi_request_ledger":
            continue
        for token in str(record.get("notes") or "").split():
            if token.startswith("path="):
                path = os.path.join(package_dir, token[5:])
                if os.path.exists(path):
                    return path
    path = os.path.join(package_dir, "mlb_statsapi_request_ledger.jsonl")
    if os.path.exists(path):
        return path
    raise ResearchCertificationError("StatsAPI ledger missing")


def _blob_json(package_dir, report, response_sha):
    if not response_sha:
        raise ResearchCertificationError("successful D-schedule lacks response SHA")
    blob_rel = (
        ((report.get("http_totals") or {}).get("response_body_directory"))
        or "http_blobs"
    )
    path = os.path.join(package_dir, blob_rel, f"{response_sha}.gz")
    if not os.path.exists(path):
        raise ResearchCertificationError(
            f"archived response body {response_sha} missing"
        )
    with gzip.open(path, "rb") as handle:
        raw = handle.read()
    if sha256_bytes(raw) != response_sha:
        raise ResearchCertificationError(
            f"archived response body {response_sha} SHA mismatch"
        )
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ResearchCertificationError(
            f"archived D-schedule {response_sha} invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchCertificationError(
            f"archived D-schedule {response_sha} is not an object"
        )
    return payload


def expected_rules():
    return {
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


def independently_derive_quarantine(package_dir):
    """Separate implementation of the transform's date decision."""
    package_dir = os.path.abspath(package_dir)
    report = load_json(os.path.join(package_dir, "consolidation_report.json"))
    start, end = report.get("requested_date_range") or (None, None)
    requested = _dates(start, end)
    requested_set = set(requested)

    source_failure = defaultdict(list)
    meta_dir = os.path.join(
        package_dir, report.get("date_metadata_path") or "date_metadata"
    )
    for day in requested:
        path = os.path.join(meta_dir, f"{day}.json")
        if not os.path.exists(path):
            raise ResearchCertificationError(f"{day}: date metadata missing")
        meta = load_json(path)
        for reason, count in (meta.get("ungraded_reasons") or {}).items():
            try:
                n = int(count)
            except Exception:
                n = 0
            if n > 0 and _source_failure(reason):
                source_failure[day].append((str(reason), n))

    groups = defaultdict(list)
    d_schedule_successes = []
    ledger = _ledger_path(package_dir, report)
    with open(ledger, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise ResearchCertificationError(
                    f"StatsAPI ledger invalid line {line_no}: {exc}"
                ) from exc
            observed = str(row.get("observed_date") or "")
            if observed not in requested_set:
                raise ResearchCertificationError(
                    f"StatsAPI observed date {observed!r} outside parent range"
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
            if not (
                row.get("scientific_phase") == "predictive_input"
                and isinstance(status, int)
                and 200 <= status < 300
                and not row.get("exception_type")
            ):
                continue
            parsed = urlparse(str(row.get("url") or ""))
            if parsed.path != "/api/v1/schedule":
                continue
            if _single(parse_qs(parsed.query), "date") != observed:
                continue
            d_schedule_successes.append(row)

    unrecovered = defaultdict(list)
    for key, attempts in groups.items():
        success = any(
            isinstance(r.get("status_code"), int)
            and 200 <= r["status_code"] < 300
            and not r.get("exception_type")
            for r in attempts
        )
        failure = any(
            r.get("exception_type")
            or (
                isinstance(r.get("status_code"), int)
                and not 200 <= r["status_code"] < 300
            )
            for r in attempts
        )
        if failure and not success:
            unrecovered[key[0]].append(key)

    resumed = defaultdict(list)
    seen = set()
    schedule_days = set()
    for row in d_schedule_successes:
        observed = str(row["observed_date"])
        schedule_days.add(observed)
        payload = _blob_json(package_dir, report, row.get("response_sha256"))
        for block in payload.get("dates") or []:
            for game in block.get("games") or []:
                if game.get("gamePk") is None or not game.get("resumedFromDate"):
                    continue
                origin = str(game["resumedFromDate"])
                if origin >= observed:
                    continue
                key = (observed, int(game["gamePk"]), origin)
                if key in seen:
                    continue
                seen.add(key)
                resumed[observed].append({
                    "game_pk": int(game["gamePk"]),
                    "resumed_from_date": origin,
                })

    missing = sorted(requested_set - schedule_days)
    if missing:
        raise ResearchCertificationError(
            f"missing successful predictive D-schedule for {len(missing)} dates"
        )

    excluded = sorted(set(source_failure) | set(unrecovered) | set(resumed))
    return {
        "excluded_dates": excluded,
        "source_failure_dates": sorted(source_failure),
        "resumed_dates": sorted(resumed),
        "unrecovered_dates": sorted(unrecovered),
        "unrecovered_identity_count": sum(len(v) for v in unrecovered.values()),
        "resumed_game_count": sum(len(v) for v in resumed.values()),
        "source_failure_reason_count": sum(len(v) for v in source_failure.values()),
    }


def _manifest_sha_valid(manifest):
    embedded = manifest.get("manifest_sha256")
    logical = dict(manifest)
    logical.pop("manifest_sha256", None)
    computed = sha256_bytes(
        json.dumps(
            logical, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    )
    return embedded == computed


def certify_research_view(
    parent_package,
    research_dir,
    repo_root=".",
    expected_parent_sha=None,
    expected_source_sha=None,
    expected_outcome_source_sha=None,
):
    parent_package = os.path.abspath(parent_package)
    research_dir = os.path.abspath(research_dir)
    failures = []
    blockers = []
    warnings = []

    base = base_cert.certify(
        parent_package,
        os.path.abspath(repo_root),
        expected_parent_sha=expected_parent_sha,
        expected_source_sha=expected_source_sha,
        expected_outcome_source_sha=expected_outcome_source_sha,
    )
    if base.get("failures"):
        failures.extend(
            f"parent canonical-v2 failure: {item}"
            for item in base["failures"]
        )

    allowed_source_blockers = []
    allowed_unrecovered_counts = []
    for blocker in base.get("blockers") or []:
        if blocker.startswith(ALLOWED_PARENT_BLOCKER_PREFIXES):
            allowed_source_blockers.append(blocker)
            continue
        m = UNRECOVERED_BLOCKER_RE.fullmatch(blocker)
        if m:
            allowed_unrecovered_counts.append(int(m.group(1)))
            continue
        blockers.append(f"unresolved parent blocker: {blocker}")

    try:
        independent = independently_derive_quarantine(parent_package)
    except Exception as exc:
        independent = None
        failures.append(f"cannot independently derive quarantine: {exc}")

    if allowed_source_blockers and independent is not None:
        if not independent["source_failure_dates"]:
            failures.append(
                "parent reports source/grader blocker but independent audit found no source-failure date"
            )
    if allowed_unrecovered_counts and independent is not None:
        if sum(allowed_unrecovered_counts) != independent["unrecovered_identity_count"]:
            failures.append(
                "parent unrecovered StatsAPI blocker count does not match independent audit"
            )

    manifest_path = os.path.join(research_dir, "research_view_manifest.json")
    rows_path = os.path.join(research_dir, "rows.jsonl")
    if not os.path.exists(manifest_path) or not os.path.exists(rows_path):
        failures.append("research_view_manifest.json and rows.jsonl are required")
        manifest = {}
    else:
        manifest = load_json(manifest_path)
        if not _manifest_sha_valid(manifest):
            failures.append("research-view manifest SHA does not verify")

    parent_report_path = os.path.join(parent_package, "consolidation_report.json")
    parent_rows_path = os.path.join(parent_package, "rows.jsonl")
    parent_report = load_json(parent_report_path)

    if manifest:
        if manifest.get("artifact_kind") != (
            "canonical_v2_date_quarantined_research_view"
        ):
            failures.append("unexpected research-view artifact kind")
        parent_manifest = manifest.get("parent") or {}
        bindings = {
            "run_id": parent_report.get("run_id"),
            "generation_code_sha": parent_report.get("generation_code_sha"),
            "scientific_parent_sha": parent_report.get("scientific_parent_sha"),
            "requested_date_range": parent_report.get("requested_date_range"),
            "requested_dates": parent_report.get("requested_dates"),
            "parent_total_rows": parent_report.get("total_rows"),
            "parent_rows_sha256": sha256_file(parent_rows_path),
            "parent_report_sha256": parent_report.get("report_sha256"),
            "parent_report_file_sha256": sha256_file(parent_report_path),
            "parent_source_lineage_fingerprint": parent_report.get(
                "source_lineage_fingerprint"
            ),
        }
        for key, expected in bindings.items():
            if parent_manifest.get(key) != expected:
                failures.append(f"research-view parent binding mismatch: {key}")

        transform = manifest.get("transform") or {}
        rules = expected_rules()
        if transform.get("rules") != rules:
            failures.append("research-view rules differ from locked whole-date rules")
        rules_fp = sha256_bytes(
            json.dumps(rules, sort_keys=True, separators=(",", ":")).encode()
        )
        if transform.get("rules_fingerprint") != rules_fp:
            failures.append("research-view rules fingerprint mismatch")
        transform_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "canonical_v2_research_view.py",
        )
        if not os.path.exists(transform_path):
            failures.append("materializer source unavailable to auditor")
        elif transform.get("transform_file_sha256") != sha256_file(transform_path):
            failures.append("materializer code SHA differs from manifest")

        if independent is not None:
            observed_excluded = (
                (manifest.get("quarantine") or {}).get("excluded_dates") or []
            )
            if observed_excluded != independent["excluded_dates"]:
                failures.append(
                    "research-view excluded dates differ from independently derived set"
                )

    # Recompute the exact expected byte stream from the immutable parent. This
    # proves retained rows are neither edited nor reordered and no extra date
    # was silently removed.
    if independent is not None and os.path.exists(rows_path):
        excluded = set(independent["excluded_dates"])
        expected_hash = hashlib.sha256()
        expected_count = 0
        parent_count = 0
        excluded_count = 0
        with open(parent_rows_path, "rb") as handle:
            for line_no, raw in enumerate(handle, 1):
                if not raw.strip():
                    failures.append(
                        f"parent rows contain blank line {line_no}"
                    )
                    continue
                try:
                    row = json.loads(raw)
                except Exception as exc:
                    failures.append(
                        f"parent rows invalid JSON line {line_no}: {exc}"
                    )
                    continue
                parent_count += 1
                if str(row.get("date") or "") in excluded:
                    excluded_count += 1
                    continue
                expected_hash.update(raw)
                expected_count += 1

        actual_hash = sha256_file(rows_path)
        if actual_hash != expected_hash.hexdigest():
            failures.append(
                "research rows are not the exact byte-preserving parent subset"
            )
        actual_count = 0
        with open(rows_path, "rb") as handle:
            for line_no, raw in enumerate(handle, 1):
                if not raw.strip():
                    failures.append(
                        f"research rows contain blank line {line_no}"
                    )
                    continue
                try:
                    row = json.loads(raw)
                except Exception as exc:
                    failures.append(
                        f"research rows invalid JSON line {line_no}: {exc}"
                    )
                    continue
                actual_count += 1
                if str(row.get("date") or "") in excluded:
                    failures.append(
                        f"quarantined date {row.get('date')} remains in research rows"
                    )

        if actual_count != expected_count:
            failures.append(
                f"research row count {actual_count} != expected {expected_count}"
            )
        if manifest:
            accounting = manifest.get("row_accounting") or {}
            if accounting.get("parent_rows") != parent_count:
                failures.append("manifest parent row count mismatch")
            if accounting.get("research_rows") != expected_count:
                failures.append("manifest research row count mismatch")
            if accounting.get("excluded_rows") != excluded_count:
                failures.append("manifest excluded row count mismatch")
            if manifest.get("research_rows_sha256") != expected_hash.hexdigest():
                failures.append("manifest research rows SHA mismatch")

    failures = list(dict.fromkeys(failures))
    blockers = list(dict.fromkeys(blockers))
    warnings.extend(base.get("warnings") or [])
    warnings = list(dict.fromkeys(warnings))

    if failures:
        verdict = "NOT CANONICAL"
    elif blockers:
        verdict = "CERTIFICATION BLOCKED"
    else:
        verdict = "CANONICAL CERTIFIED"

    result = {
        "verdict": verdict,
        "artifact_kind": "canonical_v2_date_quarantined_research_view",
        "parent_raw_verdict": base.get("verdict"),
        "parent_raw_blockers": base.get("blockers") or [],
        "parent_generation_sha": parent_report.get("generation_code_sha"),
        "parent_rows_sha256": sha256_file(parent_rows_path),
        "research_rows_sha256": (
            sha256_file(rows_path) if os.path.exists(rows_path) else None
        ),
        "research_row_count": (
            (manifest.get("row_accounting") or {}).get("research_rows")
            if manifest else None
        ),
        "independent_quarantine": independent,
        "dataset_identity": {
            "manifest_sha256": manifest.get("manifest_sha256") if manifest else None,
            "ruleset_version": RULESET_VERSION,
            "parent_source_lineage_fingerprint": parent_report.get(
                "source_lineage_fingerprint"
            ),
        },
        "failures": failures,
        "blockers": blockers,
        "warnings": warnings,
    }
    result["certification_report_sha256"] = sha256_bytes(
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    )
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("parent_package")
    ap.add_argument("research_dir")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--expected-parent-sha")
    ap.add_argument("--expected-source-sha")
    ap.add_argument("--expected-outcome-source-sha")
    ap.add_argument("--output")
    args = ap.parse_args()

    result = certify_research_view(
        args.parent_package,
        args.research_dir,
        repo_root=args.repo_root,
        expected_parent_sha=args.expected_parent_sha,
        expected_source_sha=args.expected_source_sha,
        expected_outcome_source_sha=args.expected_outcome_source_sha,
    )
    raw = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    print(raw, end="")
    if args.output:
        if os.path.exists(args.output):
            raise FileExistsError(f"refusing to overwrite {args.output}")
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    return 0 if result["verdict"] == "CANONICAL CERTIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
