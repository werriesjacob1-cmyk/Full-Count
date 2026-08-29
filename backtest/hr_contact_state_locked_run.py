#!/usr/bin/env python3
"""Guarded execution wrapper for the locked HR contact-state experiment.

This file makes the eventual experiment operational without authorizing it.

Every command requires:
1. a CANONICAL CERTIFIED report;
2. exact canonical/source file hashes matching that report;
3. a separate explicit-user-authorization JSON record whose scope and
   canonical artifact identity match the run.

Commands:
  venue-map  Materialize immutable game_pk -> venue.id/name metadata.
  stage1     Fit <=2025 B/C/D and write one immutable outcome-free freeze.
  stage2     Join 2026 truth to the frozen selections and write one immutable
             evaluation report.

No command merges, deploys, or promotes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter

from backtest.experiment_primitives import deterministic_sha256
from backtest.hr_contact_state_stage1 import (
    build_hr_prediction_freezes,
    fit_hr_arms,
    write_immutable_stage1_bundle,
)
from backtest.hr_contact_state_stage2 import (
    evaluate_hr_stage2,
    write_immutable_evaluation_report,
)
from backtest.hr_contact_state_arm_e import (
    build_hr_e_prediction_bundle,
    evaluate_hr_e_stage2,
    write_immutable_e_bundle,
)

AUTH_SCOPE = "hr_contact_state_2026_holdout"
TRAIN_END = "2025-12-31"
HOLDOUT_START = "2026-01-01"

MASKED_HOLDOUT_FIELDS = (
    "date",
    "game_pk",
    "player_id",
    "player_name",
    "team",
    "prop_type",
    "line",
    "predicted_prob",
    "score",
)


class LockedRunGateError(RuntimeError):
    """Execution gate failed before experiment state was mutated."""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_once(path, payload):
    if os.path.exists(path):
        raise FileExistsError(f"refusing to overwrite immutable artifact {path!r}")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": path,
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _git_head():
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise LockedRunGateError(
            "cannot prove runner code SHA: git rev-parse HEAD failed"
        )
    value = proc.stdout.strip()
    if len(value) != 40:
        raise LockedRunGateError(f"unexpected runner git SHA {value!r}")
    return value


def validate_certification_report(report):
    if report.get("verdict") != "CANONICAL CERTIFIED":
        raise LockedRunGateError(
            f"canonical report verdict is {report.get('verdict')!r}, not CANONICAL CERTIFIED"
        )
    if report.get("failures"):
        raise LockedRunGateError("canonical certification report still carries failures")
    if report.get("blockers"):
        raise LockedRunGateError("canonical certification report still carries blockers")

    dataset_identity = report.get("dataset_identity") or {}
    strength = dataset_identity.get("strength") or {}
    derived = dataset_identity.get("derived_manifest") or {}
    if not strength.get("promotion_grade"):
        raise LockedRunGateError("canonical dataset identity is below promotion grade")

    canonical_sha = derived.get("artifact_sha256")
    if not canonical_sha:
        raise LockedRunGateError("canonical certification lacks artifact_sha256")
    if report.get("virtual_assembled_byte_sha256") != canonical_sha:
        raise LockedRunGateError(
            "canonical report assembled SHA disagrees with dataset identity SHA"
        )

    source = report.get("source_schema_attestation") or {}
    if not source.get("available"):
        raise LockedRunGateError("canonical source schema attestation is unavailable")
    source_sha = source.get("content_sha256")
    if not source_sha:
        raise LockedRunGateError("canonical source attestation lacks content SHA")

    observed = report.get("observed_code_shas") or []
    if len(observed) != 1:
        raise LockedRunGateError(
            f"HR runner requires a single-SHA certified artifact, observed={observed!r}"
        )

    return {
        "canonical_sha256": canonical_sha,
        "source_sha256": source_sha,
        "run_id": report.get("run_id"),
        "code_git_sha": observed[0],
        "canonical_artifact_identity": derived,
        "source_artifact_identity": source,
    }


def validate_authorization_record(record, *, stage, canonical_sha256):
    """Validate a record created only after explicit user authorization.

    This function does not create or infer authorization. It merely makes an
    existing approval auditable and artifact-bound.
    """
    if record.get("authorized") is not True:
        raise LockedRunGateError("authorization record is not explicitly authorized")
    if record.get("authorization_type") != "explicit_user_authorization":
        raise LockedRunGateError(
            "authorization_type must be 'explicit_user_authorization'"
        )
    if record.get("scope") != AUTH_SCOPE:
        raise LockedRunGateError(
            f"authorization scope {record.get('scope')!r} != {AUTH_SCOPE!r}"
        )

    allowed = record.get("allowed_stages") or []
    if stage not in allowed:
        raise LockedRunGateError(
            f"authorization does not permit stage {stage!r}; allowed={allowed!r}"
        )
    if record.get("canonical_artifact_sha256") != canonical_sha256:
        raise LockedRunGateError(
            "authorization is bound to a different canonical artifact SHA"
        )
    reference = record.get("authorization_reference")
    if not isinstance(reference, str) or not reference.strip():
        raise LockedRunGateError(
            "authorization_reference is required for auditability"
        )
    return True


def validate_execution_gate(
    certification_path,
    authorization_path,
    canonical_rows_path,
    source_parquet_path,
    *,
    stage,
):
    cert = _load_json(certification_path)
    cert_identity = validate_certification_report(cert)

    canonical_file_sha = _sha256_file(canonical_rows_path)
    if canonical_file_sha != cert_identity["canonical_sha256"]:
        raise LockedRunGateError(
            "canonical rows file bytes do not match certified artifact SHA"
        )

    source_file_sha = _sha256_file(source_parquet_path)
    if source_file_sha != cert_identity["source_sha256"]:
        raise LockedRunGateError(
            "source parquet bytes do not match certified source SHA"
        )

    auth = _load_json(authorization_path)
    validate_authorization_record(
        auth,
        stage=stage,
        canonical_sha256=cert_identity["canonical_sha256"],
    )

    return {
        "certification": cert,
        "certification_identity": cert_identity,
        "certification_file_sha256": _sha256_file(certification_path),
        "authorization": auth,
        "authorization_file_sha256": _sha256_file(authorization_path),
        "canonical_rows_sha256": canonical_file_sha,
        "source_parquet_sha256": source_file_sha,
    }


def _eligible_hr_row(row):
    if row.get("prop_type") != "home_run":
        return False, "other_market"
    missing = [
        field for field in ("outcome", "predicted_prob", "score")
        if row.get(field) is None
    ]
    if missing:
        return False, "missing_" + "_".join(missing)
    return True, None


def load_stage1_populations(canonical_rows_path):
    """Read canonical rows once, discarding 2026 truth immediately.

    Training rows retain <=2025 outcomes. Holdout rows are copied into a
    strict allow-list that excludes outcome/actual/fair_test/postgame fields.
    """
    training = []
    masked_holdout = []
    exclusions = Counter()
    home_run_rows_seen = 0

    with open(canonical_rows_path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LockedRunGateError(
                    f"canonical JSONL row {line_number} is invalid: {exc}"
                ) from exc

            if row.get("prop_type") == "home_run":
                home_run_rows_seen += 1

            eligible, reason = _eligible_hr_row(row)
            if not eligible:
                if row.get("prop_type") == "home_run":
                    exclusions[reason] += 1
                continue

            day = str(row.get("date") or "")[:10]
            if len(day) != 10:
                raise LockedRunGateError(
                    f"eligible HR row {line_number} has invalid date {row.get('date')!r}"
                )

            if day <= TRAIN_END:
                training.append(row)
            elif day >= HOLDOUT_START:
                # IMPORTANT: outcome truth is not retained in Stage-1 memory.
                masked_holdout.append({
                    field: row.get(field)
                    for field in MASKED_HOLDOUT_FIELDS
                })

    if not training:
        raise LockedRunGateError("no eligible <=2025 HR training rows")
    if not masked_holdout:
        raise LockedRunGateError("no eligible 2026 HR holdout rows")

    return {
        "training": training,
        "masked_holdout": masked_holdout,
        "counts": {
            "home_run_rows_seen": home_run_rows_seen,
            "training_eligible_n": len(training),
            "holdout_eligible_n": len(masked_holdout),
            "excluded_home_run_rows": dict(exclusions),
        },
    }


def load_training_only(canonical_rows_path):
    """Load only preregistered <=2025 HR training rows for conditional E."""
    training = []
    exclusions = Counter()
    with open(canonical_rows_path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LockedRunGateError(
                    f"canonical JSONL row {line_number} is invalid: {exc}"
                ) from exc
            eligible, reason = _eligible_hr_row(row)
            if not eligible:
                if row.get("prop_type") == "home_run":
                    exclusions[reason] += 1
                continue
            day = str(row.get("date") or "")[:10]
            if day <= TRAIN_END:
                training.append(row)

    if not training:
        raise LockedRunGateError("no eligible <=2025 HR training rows")
    return {
        "training": training,
        "counts": {
            "training_eligible_n": len(training),
            "excluded_home_run_rows": dict(exclusions),
        },
    }


def load_stage2_holdout_truth(canonical_rows_path):
    holdout = []
    exclusions = Counter()
    with open(canonical_rows_path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LockedRunGateError(
                    f"canonical JSONL row {line_number} is invalid: {exc}"
                ) from exc

            eligible, reason = _eligible_hr_row(row)
            if not eligible:
                if row.get("prop_type") == "home_run":
                    exclusions[reason] += 1
                continue
            day = str(row.get("date") or "")[:10]
            if day >= HOLDOUT_START:
                holdout.append(row)

    if not holdout:
        raise LockedRunGateError("no eligible 2026 HR truth rows")
    return {
        "holdout": holdout,
        "counts": {
            "holdout_eligible_n": len(holdout),
            "excluded_home_run_rows": dict(exclusions),
        },
    }


def build_venue_map_from_schedule_payloads(payloads, required_game_pks):
    """Pure extraction of only game identity + scheduled venue metadata."""
    required = {int(value) for value in required_game_pks}
    venue_map = {}

    for payload in payloads:
        for date_entry in payload.get("dates") or []:
            for game in date_entry.get("games") or []:
                game_pk = game.get("gamePk")
                if game_pk is None:
                    continue
                game_pk = int(game_pk)
                if game_pk not in required:
                    continue
                venue = game.get("venue") or {}
                venue_id = venue.get("id")
                venue_name = venue.get("name")
                if venue_id is None:
                    raise LockedRunGateError(
                        f"MLB schedule game {game_pk} lacks venue.id"
                    )
                record = {
                    "venue_id": int(venue_id),
                    "venue_name": venue_name,
                }
                prior = venue_map.get(game_pk)
                if prior is not None and prior != record:
                    raise LockedRunGateError(
                        f"conflicting venue metadata for game {game_pk}: "
                        f"{prior!r} vs {record!r}"
                    )
                venue_map[game_pk] = record

    missing = sorted(required - set(venue_map))
    if missing:
        raise LockedRunGateError(
            f"venue metadata unresolved for {len(missing)} game(s): {missing[:10]!r}"
        )
    return venue_map


def fetch_venue_map(masked_holdout_rows):
    """Fetch only schedule venue identity for the already-frozen game set."""
    import requests

    games_by_date = {}
    for row in masked_holdout_rows:
        games_by_date.setdefault(row["date"], set()).add(int(row["game_pk"]))

    payloads = []
    for day in sorted(games_by_date):
        response = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": day,
                "hydrate": "venue",
            },
            headers={"User-Agent": "FULL-COUNT-canonical-research/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        payloads.append(response.json())

    required = {
        int(row["game_pk"])
        for row in masked_holdout_rows
    }
    return build_venue_map_from_schedule_payloads(payloads, required)


def _execution_manifest(gate, *, runner_code_sha, stage, extra=None):
    manifest = {
        "stage": stage,
        "runner_code_sha": runner_code_sha,
        "certification_file_sha256": gate["certification_file_sha256"],
        "authorization_file_sha256": gate["authorization_file_sha256"],
        "authorization_reference": gate["authorization"]["authorization_reference"],
        "canonical_rows_sha256": gate["canonical_rows_sha256"],
        "source_parquet_sha256": gate["source_parquet_sha256"],
        "canonical_run_id": gate["certification_identity"]["run_id"],
    }
    if extra:
        manifest.update(extra)
    return manifest


def run_venue_map(args):
    gate = validate_execution_gate(
        args.certification,
        args.authorization,
        args.canonical_rows,
        args.source_parquet,
        stage="venue-map",
    )
    populations = load_stage1_populations(args.canonical_rows)
    venue_map = fetch_venue_map(populations["masked_holdout"])

    payload = {
        "scope": AUTH_SCOPE,
        "canonical_rows_sha256": gate["canonical_rows_sha256"],
        "authorization_file_sha256": gate["authorization_file_sha256"],
        "venue_map": {str(key): value for key, value in sorted(venue_map.items())},
    }
    payload["logical_sha256"] = deterministic_sha256(payload)
    result = _write_json_once(args.output, payload)
    print(json.dumps(result, indent=2))


def _load_venue_map_artifact(path, *, canonical_sha256):
    artifact = _load_json(path)
    logical = dict(artifact)
    embedded = logical.pop("logical_sha256", None)
    if embedded != deterministic_sha256(logical):
        raise LockedRunGateError("venue-map artifact logical SHA does not verify")
    if artifact.get("canonical_rows_sha256") != canonical_sha256:
        raise LockedRunGateError("venue-map artifact belongs to a different canonical dataset")
    raw_map = artifact.get("venue_map") or {}
    return {
        int(game_pk): value
        for game_pk, value in raw_map.items()
    }, artifact


def run_stage1(args):
    gate = validate_execution_gate(
        args.certification,
        args.authorization,
        args.canonical_rows,
        args.source_parquet,
        stage="stage1",
    )
    populations = load_stage1_populations(args.canonical_rows)
    venue_map, venue_artifact = _load_venue_map_artifact(
        args.venue_map,
        canonical_sha256=gate["canonical_rows_sha256"],
    )

    import pandas as pd
    source_frame = pd.read_parquet(args.source_parquet)
    runner_sha = _git_head()

    fitted = fit_hr_arms(
        populations["training"],
        source_frame,
    )
    bundle = build_hr_prediction_freezes(
        populations["masked_holdout"],
        source_frame,
        fitted,
        venue_map,
        runner_code_sha=runner_sha,
        canonical_artifact_identity=gate["certification_identity"]["canonical_artifact_identity"],
        source_artifact_identity=gate["certification_identity"]["source_artifact_identity"],
    )

    # Add execution provenance, then re-bind the WHOLE Stage-1 bundle.
    bundle.pop("bundle_sha256", None)
    bundle["execution_manifest"] = _execution_manifest(
        gate,
        runner_code_sha=runner_sha,
        stage="stage1",
        extra={
            "population_counts": populations["counts"],
            "venue_map_artifact_byte_sha256": _sha256_file(args.venue_map),
            "venue_map_artifact_logical_sha256": venue_artifact["logical_sha256"],
        },
    )
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    result = write_immutable_stage1_bundle(args.output, bundle)
    print(json.dumps(result, indent=2))


def run_stage2(args):
    gate = validate_execution_gate(
        args.certification,
        args.authorization,
        args.canonical_rows,
        args.source_parquet,
        stage="stage2",
    )
    bundle = _load_json(args.stage1_bundle)
    truth = load_stage2_holdout_truth(args.canonical_rows)
    runner_sha = _git_head()

    report = evaluate_hr_stage2(truth["holdout"], bundle)

    # Bind the revealed report to the exact gate/artifact files used.
    report.pop("evaluation_report_sha256", None)
    report["execution_manifest"] = _execution_manifest(
        gate,
        runner_code_sha=runner_sha,
        stage="stage2",
        extra={
            "stage1_bundle_byte_sha256": _sha256_file(args.stage1_bundle),
            "stage1_bundle_sha256": bundle.get("bundle_sha256"),
            "population_counts": truth["counts"],
        },
    )
    report["evaluation_report_sha256"] = deterministic_sha256(report)
    result = write_immutable_evaluation_report(args.output, report)
    print(json.dumps(result, indent=2))


def run_stage1_e(args):
    gate = validate_execution_gate(
        args.certification,
        args.authorization,
        args.canonical_rows,
        args.source_parquet,
        stage="stage1-e",
    )
    initial_stage1 = _load_json(args.stage1_bundle)
    initial_stage2 = _load_json(args.stage2_report)
    training = load_training_only(args.canonical_rows)

    import pandas as pd
    source_frame = pd.read_parquet(args.source_parquet)
    runner_sha = _git_head()

    bundle = build_hr_e_prediction_bundle(
        training["training"],
        source_frame,
        initial_stage1,
        initial_stage2,
        runner_code_sha=runner_sha,
    )
    bundle.pop("bundle_sha256", None)
    bundle["execution_manifest"] = _execution_manifest(
        gate,
        runner_code_sha=runner_sha,
        stage="stage1-e",
        extra={
            "parent_stage1_bundle_byte_sha256": _sha256_file(args.stage1_bundle),
            "parent_stage1_bundle_sha256": initial_stage1.get("bundle_sha256"),
            "trigger_stage2_report_byte_sha256": _sha256_file(args.stage2_report),
            "trigger_stage2_report_sha256": initial_stage2.get("evaluation_report_sha256"),
            "training_counts": training["counts"],
        },
    )
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    result = write_immutable_e_bundle(args.output, bundle)
    print(json.dumps(result, indent=2))


def run_stage2_e(args):
    gate = validate_execution_gate(
        args.certification,
        args.authorization,
        args.canonical_rows,
        args.source_parquet,
        stage="stage2-e",
    )
    initial_stage1 = _load_json(args.stage1_bundle)
    initial_stage2 = _load_json(args.stage2_report)
    e_bundle = _load_json(args.e_bundle)
    truth = load_stage2_holdout_truth(args.canonical_rows)
    runner_sha = _git_head()

    report = evaluate_hr_e_stage2(
        truth["holdout"],
        initial_stage1,
        initial_stage2,
        e_bundle,
    )
    report.pop("evaluation_report_sha256", None)
    report["execution_manifest"] = _execution_manifest(
        gate,
        runner_code_sha=runner_sha,
        stage="stage2-e",
        extra={
            "parent_stage1_bundle_byte_sha256": _sha256_file(args.stage1_bundle),
            "parent_stage2_report_byte_sha256": _sha256_file(args.stage2_report),
            "e_bundle_byte_sha256": _sha256_file(args.e_bundle),
            "population_counts": truth["counts"],
        },
    )
    report["evaluation_report_sha256"] = deterministic_sha256(report)
    result = write_immutable_evaluation_report(args.output, report)
    print(json.dumps(result, indent=2))


def _common(parser):
    parser.add_argument("--certification", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--canonical-rows", required=True)
    parser.add_argument("--source-parquet", required=True)
    parser.add_argument("--output", required=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    venue = sub.add_parser("venue-map")
    _common(venue)
    venue.set_defaults(func=run_venue_map)

    stage1 = sub.add_parser("stage1")
    _common(stage1)
    stage1.add_argument("--venue-map", required=True)
    stage1.set_defaults(func=run_stage1)

    stage2 = sub.add_parser("stage2")
    _common(stage2)
    stage2.add_argument("--stage1-bundle", required=True)
    stage2.set_defaults(func=run_stage2)

    stage1e = sub.add_parser("stage1-e")
    _common(stage1e)
    stage1e.add_argument("--stage1-bundle", required=True)
    stage1e.add_argument("--stage2-report", required=True)
    stage1e.set_defaults(func=run_stage1_e)

    stage2e = sub.add_parser("stage2-e")
    _common(stage2e)
    stage2e.add_argument("--stage1-bundle", required=True)
    stage2e.add_argument("--stage2-report", required=True)
    stage2e.add_argument("--e-bundle", required=True)
    stage2e.set_defaults(func=run_stage2_e)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
