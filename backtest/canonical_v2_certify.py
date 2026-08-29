#!/usr/bin/env python3
"""Independent certification for a consolidated canonical-v2 package.

This is intentionally separate from the generator/consolidator. It reopens the
final package and recomputes the evidence contract needed by locked experiments.

Success emits the same top-level contract consumed by
hr_contact_state_locked_run.py:
    verdict == "CANONICAL CERTIFIED"

Anything scientifically contradictory is NOT CANONICAL.
Anything merely incomplete/unverifiable is CERTIFICATION BLOCKED.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse


EXPECTED_PYTHON = "3.11.15"
EXPECTED_CRITICAL_PACKAGES = {
    "numpy": "2.4.6",
    "pandas": "3.0.5",
    "pyarrow": "25.0.1",
    "pybaseball": "2.2.7",
    "python-dateutil": "2.9.0.post0",
    "requests": "2.34.2",
    "scikit-learn": "1.9.0",
    "scipy": "1.17.1",
}

REQUIRED_HR_SOURCE_COLUMNS = {
    "bat_speed",
    "swing_length",
    "attack_angle",
    "swing_path_tilt",
    "attack_direction",
    "hit_distance_sc",
}

OUTCOME_ONLY_SOURCE_COLUMNS = {
    "game_date",
    "game_pk",
    "batter",
    "events",
    "launch_speed",
    "hit_distance_sc",
}

PROTECTED_SCIENTIFIC_FILES = (
    "recommendation.py",
    "generate_picks.py",
    "mlb_sources.py",
    "backtest/engine.py",
    "grade_results.py",
)

ALLOWED_CHANGED_EXACT = {
    "mlb_daily.py",
    "backtest/http_provenance.py",
    "backtest/canonical_v2_grading.py",
    "backtest/canonical_v2_team_identity.py",
    "backtest/canonical_v2_shard.py",
    "backtest/canonical_v2_consolidate.py",
    "backtest/canonical_v2_certify.py",
    "test_http_provenance.py",
    "test_historical_lineup_firewall.py",
    "test_canonical_v2_grading.py",
    "test_canonical_v2_team_identity.py",
    "test_canonical_v2_shard.py",
    "test_canonical_v2_consolidate.py",
    "test_canonical_v2_certify.py",
}

ALLOWED_CHANGED_PREFIXES = (
    ".github/workflows/canonical-v2",
    "engineering/CANONICAL_V2_",
)

IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")


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


def requested_dates(start, end):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError("end before start")
    out = []
    current = first
    while current <= last:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def source_lineage_fingerprint(records):
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
    return sha256_bytes("\n".join(keyed).encode())


def git(*args, cwd):
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def code_audit(repo_root, parent_sha, generation_sha):
    failures = []
    blockers = []

    try:
        head = git("rev-parse", "HEAD", cwd=repo_root)
    except Exception as exc:
        return {
            "failures": [],
            "blockers": [f"cannot verify certification checkout Git SHA: {exc}"],
            "changed_files": [],
        }

    if head != generation_sha:
        failures.append(
            f"certification checkout HEAD {head} != generation SHA {generation_sha}"
        )

    changed = []
    try:
        raw = git(
            "diff",
            "--name-only",
            f"{parent_sha}..{generation_sha}",
            cwd=repo_root,
        )
        changed = [line for line in raw.splitlines() if line.strip()]
    except Exception as exc:
        blockers.append(f"cannot diff scientific parent to generation SHA: {exc}")

    for path in changed:
        allowed = (
            path in ALLOWED_CHANGED_EXACT
            or any(path.startswith(prefix) for prefix in ALLOWED_CHANGED_PREFIXES)
        )
        if not allowed:
            failures.append(
                f"generation branch changed non-provenance file outside allowlist: {path}"
            )

    for path in PROTECTED_SCIENTIFIC_FILES:
        try:
            parent_blob = git("rev-parse", f"{parent_sha}:{path}", cwd=repo_root)
            generated_blob = git("rev-parse", f"{generation_sha}:{path}", cwd=repo_root)
            if parent_blob != generated_blob:
                failures.append(
                    f"protected scientific file changed from parent: {path}"
                )
        except Exception as exc:
            blockers.append(f"cannot compare protected file {path}: {exc}")

    return {
        "failures": failures,
        "blockers": blockers,
        "changed_files": changed,
        "protected_files": list(PROTECTED_SCIENTIFIC_FILES),
    }


STATSAPI_SOURCE_SHAPE_POLICY = "canonical-v2-statsapi-v1-20260829"


def _single_query(qs, key):
    values = qs.get(key) or []
    return values[0] if len(values) == 1 else None


def audit_statsapi_request_shapes(rows):
    """Narrow allowlist for scientific StatsAPI traffic.

    Unknown shapes BLOCK certification. Known current-state/wrong-time shapes
    are NOT CANONICAL. The season-wide gameLog exception is deliberate: the
    frozen backtest engine/mlb_sources code (protected by code_audit) filters
    those splits to D-1 before any empirical/rest feature is computed.
    """
    failures = []
    blockers = []
    classes = Counter()
    team_directory_seasons = set()

    for row in rows:
        observed = str(row.get("observed_date") or "")
        try:
            observed_day = date.fromisoformat(observed)
        except ValueError:
            failures.append(
                f"StatsAPI row has invalid observed_date {observed!r}"
            )
            continue
        year = str(observed_day.year)

        parsed = urlparse(str(row.get("url") or ""))
        path = parsed.path
        qs = parse_qs(parsed.query)
        keys = set(qs)

        if (parsed.hostname or "").lower() != "statsapi.mlb.com":
            failures.append(
                f"StatsAPI ledger contains non-StatsAPI host {parsed.hostname!r}"
            )
            continue

        if path == "/api/v1/seasons/all":
            failures.append(
                f"{observed}: current-season helper /api/v1/seasons/all "
                "entered historical replay"
            )
            continue

        if path == "/api/v1/teams":
            if "activeStatus" in qs or "sportIds" in qs:
                failures.append(
                    f"{observed}: current/active team directory entered "
                    f"historical replay: {row.get('url')}"
                )
                continue
            if keys - {"sportId", "season"}:
                blockers.append(
                    f"{observed}: unrecognized historical team-directory "
                    f"query shape: {row.get('url')}"
                )
                continue
            if _single_query(qs, "sportId") != "1":
                blockers.append(
                    f"{observed}: team directory lacks sportId=1"
                )
                continue
            season = _single_query(qs, "season")
            if season != year:
                failures.append(
                    f"{observed}: team directory season={season!r}, "
                    f"expected {year}"
                )
                continue
            classes["historical_team_directory"] += 1
            team_directory_seasons.add(season)
            continue

        if re.fullmatch(r"/api/v1/people/\d+/stats", path):
            allowed = {"stats", "group", "season", "sportId"}
            if keys - allowed:
                blockers.append(
                    f"{observed}: unrecognized player-stats query shape: "
                    f"{row.get('url')}"
                )
                continue
            stats = _single_query(qs, "stats")
            group = _single_query(qs, "group")
            season = _single_query(qs, "season")
            if stats != "gameLog" or group not in {"hitting", "pitching"}:
                blockers.append(
                    f"{observed}: player stats request is not the frozen "
                    f"gameLog shape: {row.get('url')}"
                )
                continue
            if season != year:
                failures.append(
                    f"{observed}: gameLog season={season!r}, expected {year}"
                )
                continue
            sport_id = _single_query(qs, "sportId")
            if sport_id not in (None, "1"):
                failures.append(
                    f"{observed}: gameLog sportId={sport_id!r}"
                )
                continue
            classes[f"season_game_log_{group}"] += 1
            continue

        if path == "/api/v1/people":
            if keys != {"personIds"} or not _single_query(qs, "personIds"):
                blockers.append(
                    f"{observed}: unrecognized player-metadata query shape: "
                    f"{row.get('url')}"
                )
                continue
            classes["player_identity_metadata"] += 1
            continue

        if path == "/api/v1/schedule":
            sport_id = _single_query(qs, "sportId")
            if sport_id != "1":
                blockers.append(
                    f"{observed}: schedule request lacks sportId=1"
                )
                continue

            date_value = _single_query(qs, "date")
            start_value = _single_query(qs, "startDate")
            end_value = _single_query(qs, "endDate")
            if date_value is not None:
                if keys - {"sportId", "date", "hydrate"}:
                    blockers.append(
                        f"{observed}: unrecognized date-schedule query shape: "
                        f"{row.get('url')}"
                    )
                    continue
                if date_value != observed:
                    failures.append(
                        f"{observed}: date-addressed schedule requested "
                        f"{date_value}"
                    )
                    continue
                classes["schedule_on_D"] += 1
                continue

            if start_value is not None or end_value is not None:
                allowed = {
                    "sportId", "startDate", "endDate", "hydrate",
                    "teamId", "gameType",
                }
                if keys - allowed:
                    blockers.append(
                        f"{observed}: unrecognized range-schedule query shape: "
                        f"{row.get('url')}"
                    )
                    continue
                try:
                    start_day = date.fromisoformat(start_value or "")
                    end_day = date.fromisoformat(end_value or "")
                except ValueError:
                    failures.append(
                        f"{observed}: malformed schedule range "
                        f"{start_value!r}..{end_value!r}"
                    )
                    continue
                if start_day > end_day:
                    failures.append(
                        f"{observed}: inverted schedule range"
                    )
                    continue
                if end_day >= observed_day:
                    failures.append(
                        f"{observed}: historical input schedule reaches "
                        f"{end_day.isoformat()} (must end before D)"
                    )
                    continue
                classes["schedule_pre_D_range"] += 1
                continue

            blockers.append(
                f"{observed}: schedule request has neither date nor bounded "
                f"range: {row.get('url')}"
            )
            continue

        if path == "/api/v1/stats":
            allowed = {
                "group", "season", "sportId", "limit", "playerPool",
                "stats", "startDate", "endDate", "gameType",
            }
            if keys - allowed:
                blockers.append(
                    f"{observed}: unrecognized byDateRange stats query shape: "
                    f"{row.get('url')}"
                )
                continue
            if _single_query(qs, "stats") != "byDateRange":
                blockers.append(
                    f"{observed}: /api/v1/stats is not byDateRange"
                )
                continue
            if _single_query(qs, "season") != year:
                failures.append(
                    f"{observed}: byDateRange season differs from simulated year"
                )
                continue
            if _single_query(qs, "sportId") != "1":
                blockers.append(
                    f"{observed}: byDateRange lacks sportId=1"
                )
                continue
            if _single_query(qs, "group") not in {"hitting", "pitching"}:
                blockers.append(
                    f"{observed}: unexpected byDateRange group"
                )
                continue
            try:
                start_day = date.fromisoformat(
                    _single_query(qs, "startDate") or ""
                )
                end_day = date.fromisoformat(
                    _single_query(qs, "endDate") or ""
                )
            except ValueError:
                failures.append(
                    f"{observed}: malformed byDateRange dates"
                )
                continue
            if start_day > end_day or end_day >= observed_day:
                failures.append(
                    f"{observed}: byDateRange reaches D or later: "
                    f"{start_day}..{end_day}"
                )
                continue
            classes["stats_pre_D_range"] += 1
            continue

        if re.fullmatch(r"/api/v1\.1/game/\d+/feed/live", path):
            if keys - {"fields", "timecode"}:
                blockers.append(
                    f"{observed}: unrecognized game-feed query shape: "
                    f"{row.get('url')}"
                )
                continue
            phase = row.get("scientific_phase")
            timecode = _single_query(qs, "timecode")
            if phase == "predictive_input":
                if not timecode:
                    failures.append(
                        f"{observed}: predictive game feed lacks historical timecode"
                    )
                    continue
                if not re.fullmatch(r"\d{8}_\d{6}", timecode):
                    failures.append(
                        f"{observed}: predictive game feed has malformed timecode={timecode!r}"
                    )
                    continue
                classes["historical_predictive_game_feed"] += 1
                continue
            if phase == "outcome_grading":
                if timecode:
                    failures.append(
                        f"{observed}: outcome grading game feed unexpectedly uses predictive timecode"
                    )
                    continue
                classes["outcome_game_feed"] += 1
                continue
            failures.append(
                f"{observed}: game feed has invalid scientific phase {phase!r}"
            )
            continue

        blockers.append(
            f"{observed}: previously unseen StatsAPI request shape: "
            f"{row.get('url')}"
        )

    return {
        "failures": list(dict.fromkeys(failures)),
        "blockers": list(dict.fromkeys(blockers)),
        "classes": dict(sorted(classes.items())),
        "team_directory_seasons": sorted(team_directory_seasons),
    }


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:
                raise ValueError(
                    f"{path}: invalid JSON line {line_no}: {exc}"
                ) from exc
    return rows


def certify(
    package_dir,
    repo_root,
    expected_parent_sha=None,
    expected_source_sha=None,
    expected_outcome_source_sha=None,
):
    failures = []
    blockers = []
    warnings = []

    report_path = os.path.join(package_dir, "consolidation_report.json")
    rows_path = os.path.join(package_dir, "rows.jsonl")
    if not os.path.exists(report_path) or not os.path.exists(rows_path):
        return {
            "verdict": "NOT CANONICAL",
            "failures": ["consolidation_report.json and rows.jsonl are required"],
            "blockers": [],
            "warnings": [],
        }

    report = load_json(report_path)
    embedded_report_sha = report.get("report_sha256")
    logical_report = dict(report)
    logical_report.pop("report_sha256", None)
    recomputed_report_sha = sha256_bytes(
        json.dumps(
            logical_report,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    )
    if embedded_report_sha != recomputed_report_sha:
        failures.append("consolidation report_sha256 does not verify")

    if report.get("verdict") != "CANONICAL_V2_CONSOLIDATED":
        failures.append(
            f"unexpected consolidation verdict {report.get('verdict')!r}"
        )

    identity = report.get("identity") or {}
    run_id = report.get("run_id")
    start, end = report.get("requested_date_range") or (None, None)
    try:
        dates = requested_dates(start, end)
    except Exception as exc:
        failures.append(f"invalid requested date range: {exc}")
        dates = []

    if report.get("requested_dates") != len(dates):
        failures.append(
            f"requested_dates={report.get('requested_dates')} != expected {len(dates)}"
        )

    parent_sha = report.get("scientific_parent_sha")
    generation_sha = report.get("generation_code_sha")
    if expected_parent_sha and parent_sha != expected_parent_sha:
        failures.append(
            f"scientific parent {parent_sha!r} != expected {expected_parent_sha!r}"
        )

    statcast_sha = report.get("statcast_source_sha256")
    if expected_source_sha and statcast_sha != expected_source_sha:
        failures.append(
            f"Statcast source {statcast_sha!r} != expected {expected_source_sha!r}"
        )
    outcome_statcast_sha = report.get("outcome_statcast_source_sha256")
    if expected_outcome_source_sha and outcome_statcast_sha != expected_outcome_source_sha:
        failures.append(
            "outcome-only Statcast source "
            f"{outcome_statcast_sha!r} != expected {expected_outcome_source_sha!r}"
        )

    code = code_audit(
        repo_root,
        parent_sha,
        generation_sha,
    )
    failures.extend(code["failures"])
    blockers.extend(code["blockers"])

    environment = report.get("scientific_environment") or {}
    if environment.get("python_version") != EXPECTED_PYTHON:
        failures.append(
            f"Python {environment.get('python_version')!r} != expected {EXPECTED_PYTHON}"
        )
    critical = environment.get("critical_packages") or {}
    for package, version in EXPECTED_CRITICAL_PACKAGES.items():
        if critical.get(package) != version:
            failures.append(
                f"critical package {package}={critical.get(package)!r} "
                f"!= expected {version!r}"
            )
    if not environment.get("pip_freeze_sha256"):
        blockers.append("scientific environment lacks pip_freeze_sha256")

    if identity.get("weather_mode") != "no_weather":
        failures.append("canonical v2 weather_mode is not no_weather")
    if identity.get("policy_replay") is not False:
        failures.append("canonical v2 unexpectedly replayed selector policy")
    if identity.get("strict_historical_lineups") is not True:
        failures.append("strict historical lineup firewall was not enabled")
    if identity.get("http_strict_host_firewall") is not True:
        failures.append("scientific HTTP host firewall was not enabled")
    if identity.get("http_response_content_bound") is not True:
        failures.append("external responses were not content-bound")
    if identity.get("http_scientific_phase_bound") is not True:
        failures.append(
            "canonical v2 did not bind external requests to scientific phases"
        )
    if identity.get("historical_team_identity") != (
        "schedule_team_ids_plus_season_directory"
    ):
        failures.append(
            "canonical v2 did not declare stable schedule-team-ID + "
            "season-directory historical team identity"
        )
    if identity.get("historical_bullpen_temporal_gate") != (
        "official_date_before_D_completed_status_plus_team_pregame_timecode_v2"
    ):
        failures.append(
            "canonical v2 did not declare the full historical bullpen temporal gate"
        )
    if identity.get("historical_bullpen_boxscore_cutoff") != (
        "earliest_simulated_D_team_first_pitch_minus_1_second_utc"
    ):
        failures.append(
            "canonical v2 did not bind bullpen boxscores to simulated pregame time"
        )
    if identity.get("outcome_source_isolation") != "grader_only_external_parquet_v1":
        failures.append("outcome-only Statcast source is not declared grader-only")
    outcome_only_date = str(identity.get("outcome_only_date") or "")
    if outcome_only_date != end:
        failures.append(
            f"outcome-only Statcast date {outcome_only_date!r} != requested end date {end!r}"
        )
    if identity.get("statsapi_source_shape_policy") != (
        STATSAPI_SOURCE_SHAPE_POLICY
    ):
        failures.append(
            "canonical v2 did not declare the frozen StatsAPI historical "
            "request-shape policy"
        )

    rows_sha = sha256_file(rows_path)
    if rows_sha != report.get("assembled_rows_sha256"):
        failures.append("assembled rows.jsonl SHA differs from consolidation report")

    identities = set()
    observed_code_shas = set()
    market_counts = Counter()
    year_counts = Counter()
    total_rows = 0
    try:
        with open(rows_path, encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception as exc:
                    failures.append(f"rows.jsonl line {line_no} invalid JSON: {exc}")
                    continue
                total_rows += 1
                cid = tuple(row.get(field) for field in IDENTITY_FIELDS)
                if any(value is None or value == "" for value in cid):
                    failures.append(
                        f"rows.jsonl line {line_no} incomplete candidate identity {cid!r}"
                    )
                elif cid in identities:
                    failures.append(f"duplicate candidate identity {cid!r}")
                identities.add(cid)

                day = str(row.get("date") or "")
                if day not in set(dates):
                    failures.append(
                        f"row date {day!r} outside requested range"
                    )
                if len(day) >= 4:
                    year_counts[day[:4]] += 1
                market_counts[str(row.get("prop_type"))] += 1

                code_sha = row.get("code_git_sha")
                if code_sha:
                    observed_code_shas.add(code_sha)
                else:
                    failures.append(f"row {cid!r} missing code_git_sha")

                if row.get("outcome") not in (0, 1):
                    failures.append(f"row {cid!r} has non-binary outcome")
                try:
                    p = float(row.get("predicted_prob"))
                except (TypeError, ValueError):
                    failures.append(f"row {cid!r} invalid predicted_prob")
                else:
                    if not math.isfinite(p) or not 0 <= p <= 1:
                        failures.append(
                            f"row {cid!r} predicted_prob outside [0,1]"
                        )
    except OSError as exc:
        failures.append(f"cannot read rows.jsonl: {exc}")

    if total_rows != report.get("total_rows"):
        failures.append(
            f"row count {total_rows} != consolidation total_rows {report.get('total_rows')}"
        )
    if len(identities) != report.get("unique_candidate_identities"):
        failures.append(
            "unique candidate identity count differs from consolidation report"
        )
    if observed_code_shas != {generation_sha}:
        failures.append(
            f"row code SHA regime is {sorted(observed_code_shas)!r}, "
            f"expected only {generation_sha!r}"
        )

    # Per-date evidence.
    date_meta_dir = os.path.join(
        package_dir,
        report.get("date_metadata_path") or "date_metadata",
    )
    if not os.path.isdir(date_meta_dir):
        blockers.append("consolidated package lacks date_metadata directory")
    else:
        actual_meta_dates = {
            name[:-5]
            for name in os.listdir(date_meta_dir)
            if name.endswith(".json")
        }
        if actual_meta_dates != set(dates):
            failures.append(
                "date_metadata does not cover requested dates exactly"
            )
        ungraded_reasons = Counter()
        lineup_source_failures = []
        for day in dates:
            path = os.path.join(date_meta_dir, f"{day}.json")
            if not os.path.exists(path):
                continue
            meta = load_json(path)
            if meta.get("date") != day:
                failures.append(f"{day}: metadata embeds different date")
            if meta.get("status") not in ("ok", "no_games"):
                failures.append(
                    f"{day}: unresolved date status {meta.get('status')!r}"
                )
            if meta.get("generation_code_sha") != generation_sha:
                failures.append(f"{day}: generation code SHA mismatch")
            if meta.get("source_content_sha256") != statcast_sha:
                failures.append(f"{day}: Statcast source SHA mismatch")
            if meta.get("outcome_source_content_sha256") != outcome_statcast_sha:
                failures.append(f"{day}: outcome-only Statcast source SHA mismatch")

            hp = meta.get("http_provenance") or {}
            if hp.get("strict_host_firewall") is not True:
                failures.append(f"{day}: source firewall not active")
            if int(hp.get("firewall_block_count") or 0):
                failures.append(
                    f"{day}: source firewall blocked "
                    f"{hp.get('firewall_block_count')} request(s)"
                )

            access = meta.get("point_in_time_access") or {}
            if access.get("violations"):
                failures.append(f"{day}: point-in-time input violation recorded")

            for reason, count in (meta.get("ungraded_reasons") or {}).items():
                ungraded_reasons[reason] += int(count)

        # Missing/DNP/limited settlement rows may legitimately be excluded from
        # historical model rows. Source/grader failures may not.
        source_failure_tokens = (
            "couldn't fetch",
            "unavailable",
            "no batted-ball Statcast data",
            "grader error",
            "source",
            "timeout",
            "connection",
            "missing runs data",
        )
        for reason, count in ungraded_reasons.items():
            if any(token.lower() in reason.lower() for token in source_failure_tokens):
                blockers.append(
                    f"source/grader-related ungraded candidates: {count} x {reason}"
                )
        if ungraded_reasons:
            warnings.append(
                f"declared ungraded/excluded candidates: {sum(ungraded_reasons.values())}"
            )

    # Exact Statcast source + feature schema.
    source_rel = report.get("statcast_source_path") or (
        "source/statcast_2024_through_2026-08-24.parquet"
    )
    source_path = os.path.join(package_dir, source_rel)
    source_attestation = None
    if not os.path.exists(source_path):
        blockers.append("exact Statcast parquet missing from consolidated package")
    else:
        actual_sha = sha256_file(source_path)
        if actual_sha != statcast_sha:
            failures.append("consolidated Statcast parquet SHA mismatch")
        try:
            import pandas as pd
            frame = pd.read_parquet(source_path)
            columns = sorted(str(column) for column in frame.columns)
            schema_fingerprint = sha256_bytes(",".join(columns).encode())
            parsed = pd.to_datetime(
                frame["game_date"],
                errors="coerce",
            ).dropna()
            source_attestation = {
                "available": True,
                "path": source_rel,
                "content_sha256": actual_sha,
                "row_count": int(len(frame)),
                "schema_columns": columns,
                "schema_fingerprint": schema_fingerprint,
                "date_coverage": (
                    f"{parsed.min().date()}..{parsed.max().date()}"
                    if len(parsed) else None
                ),
            }
            bound = identity.get("statcast_source") or {}
            for key in (
                "row_count",
                "schema_columns",
                "schema_fingerprint",
                "date_coverage",
            ):
                if bound.get(key) != source_attestation.get(key):
                    failures.append(
                        f"Statcast {key} differs from shard-bound identity"
                    )
            missing_hr = sorted(REQUIRED_HR_SOURCE_COLUMNS - set(columns))
            if missing_hr:
                failures.append(
                    f"Statcast source lacks preregistered HR columns: {missing_hr}"
                )
        except Exception as exc:
            failures.append(f"cannot independently inspect Statcast parquet: {exc}")

    # Separate final-day outcome-only Statcast source. This parquet is never
    # eligible to enter the predictor store: certification requires an exact
    # six-column grading schema and exact D-only coverage, then binds those
    # facts to both the shard identity and source lineage.
    outcome_source_rel = report.get("outcome_statcast_source_path") or (
        f"source/statcast_outcome_{end}.parquet"
    )
    outcome_source_path = os.path.join(package_dir, outcome_source_rel)
    outcome_source_attestation = None
    if not os.path.exists(outcome_source_path):
        blockers.append("exact outcome-only Statcast parquet missing from consolidated package")
    else:
        outcome_actual_sha = sha256_file(outcome_source_path)
        if outcome_actual_sha != outcome_statcast_sha:
            failures.append("consolidated outcome-only Statcast parquet SHA mismatch")
        if os.path.abspath(outcome_source_path) == os.path.abspath(source_path):
            failures.append("outcome-only Statcast path aliases predictor Statcast path")
        if outcome_statcast_sha and outcome_statcast_sha == statcast_sha:
            failures.append("outcome-only Statcast SHA aliases predictor Statcast SHA")
        try:
            import pandas as pd
            outcome_frame = pd.read_parquet(outcome_source_path)
            outcome_columns = sorted(str(column) for column in outcome_frame.columns)
            outcome_column_set = set(outcome_columns)
            if outcome_column_set != OUTCOME_ONLY_SOURCE_COLUMNS:
                failures.append(
                    "outcome-only Statcast schema is not the exact grading-only contract: "
                    f"observed={outcome_columns} expected={sorted(OUTCOME_ONLY_SOURCE_COLUMNS)}"
                )
            if outcome_frame.empty:
                failures.append("outcome-only Statcast parquet is empty")
            parsed_outcome_dates = pd.to_datetime(
                outcome_frame["game_date"], errors="coerce"
            ).dropna() if "game_date" in outcome_frame.columns else pd.Series(dtype="datetime64[ns]")
            outcome_coverage = (
                f"{parsed_outcome_dates.min().date()}..{parsed_outcome_dates.max().date()}"
                if len(parsed_outcome_dates) else None
            )
            if outcome_coverage != f"{end}..{end}":
                failures.append(
                    f"outcome-only Statcast date coverage {outcome_coverage!r} != {end}..{end}"
                )
            outcome_schema_fingerprint = sha256_bytes(",".join(outcome_columns).encode())
            outcome_source_attestation = {
                "available": True,
                "path": outcome_source_rel,
                "content_sha256": outcome_actual_sha,
                "row_count": int(len(outcome_frame)),
                "schema_columns": outcome_columns,
                "schema_fingerprint": outcome_schema_fingerprint,
                "date_coverage": outcome_coverage,
            }
            bound_outcome = identity.get("outcome_statcast_source") or {}
            for key in (
                "content_sha256",
                "row_count",
                "schema_columns",
                "schema_fingerprint",
                "date_coverage",
            ):
                if bound_outcome.get(key) != outcome_source_attestation.get(key):
                    failures.append(
                        f"outcome-only Statcast {key} differs from shard-bound identity"
                    )
        except Exception as exc:
            failures.append(
                f"cannot independently inspect outcome-only Statcast parquet: {exc}"
            )

    # Source lineage + aggregate ledgers.
    lineage = report.get("source_lineage") or []
    if not lineage:
        blockers.append("source lineage is absent")
    elif source_lineage_fingerprint(lineage) != report.get(
        "source_lineage_fingerprint"
    ):
        failures.append("source_lineage_fingerprint does not recompute")

    by_source = {record.get("source"): record for record in lineage}
    required_sources = {
        "statcast_leaguewide",
        "statcast_outcome_only",
        "mlb_statsapi_request_ledger",
        "mlbcom_dated_lineup_request_ledger",
    }
    missing_sources = sorted(required_sources - set(by_source))
    if missing_sources:
        blockers.append(f"missing source-lineage records: {missing_sources}")

    outcome_lineage = by_source.get("statcast_outcome_only")
    if outcome_lineage:
        if outcome_lineage.get("content_sha256") != outcome_statcast_sha:
            failures.append("outcome-only Statcast lineage SHA mismatch")
        if outcome_source_attestation is not None:
            for key in ("row_count", "schema_columns", "schema_fingerprint", "date_coverage"):
                if outcome_lineage.get(key) != outcome_source_attestation.get(key):
                    failures.append(
                        f"outcome-only Statcast lineage {key} mismatch"
                    )
        if outcome_lineage.get("cache_mode") != "frozen_exact_artifact_grader_only":
            failures.append("outcome-only Statcast lineage is not grader-only frozen evidence")

    all_external_rows = []
    recovered_transients = 0
    unrecovered_statsapi = []
    loaded_ledgers = set()
    for source_name, expected_host in (
        ("mlb_statsapi_request_ledger", "statsapi.mlb.com"),
        ("mlbcom_dated_lineup_request_ledger", None),
    ):
        record = by_source.get(source_name)
        if not record:
            continue
        notes = str(record.get("notes") or "")
        ledger_rel = None
        for token in notes.split():
            if token.startswith("path="):
                ledger_rel = token[5:]
                break
        if not ledger_rel:
            blockers.append(f"{source_name} lacks durable path in lineage notes")
            continue
        ledger_path = os.path.join(package_dir, ledger_rel)
        if not os.path.exists(ledger_path):
            blockers.append(f"{source_name} durable ledger artifact missing")
            continue
        if sha256_file(ledger_path) != record.get("content_sha256"):
            failures.append(f"{source_name} content SHA mismatch")
        rows = read_jsonl(ledger_path)
        loaded_ledgers.add(source_name)
        if len(rows) != record.get("row_count"):
            failures.append(f"{source_name} row_count mismatch")

        groups = defaultdict(list)
        for row in rows:
            all_external_rows.append(row)
            phase = row.get("scientific_phase")
            if phase not in {"predictive_input", "outcome_grading"}:
                failures.append(
                    f"{source_name}: request lacks valid scientific phase: {phase!r}"
                )
            observed_day = str(row.get("observed_date") or "")
            if observed_day not in set(dates):
                failures.append(
                    f"{source_name}: observed_date {observed_day!r} outside run"
                )
            host = (urlparse(str(row.get("url") or "")).hostname or "").lower()
            if source_name == "mlb_statsapi_request_ledger":
                if host != expected_host:
                    failures.append(
                        f"StatsAPI ledger contains unexpected host {host!r}"
                    )
            else:
                if phase != "predictive_input":
                    failures.append(
                        f"MLB.com historical lineup request appeared outside predictive_input phase: {phase!r}"
                    )
                if host not in ("www.mlb.com", "mlb.com"):
                    failures.append(
                        f"MLB.com ledger contains unexpected host {host!r}"
                    )
                # Historical HTML fallback must itself be date-addressed.
                if observed_day and observed_day not in str(row.get("url") or ""):
                    failures.append(
                        f"MLB.com fallback URL is not date-bound to {observed_day}: "
                        f"{row.get('url')}"
                    )

            key = (
                observed_day,
                phase,
                row.get("method"),
                row.get("url"),
                row.get("request_body_sha256"),
            )
            groups[key].append(row)

        if source_name == "mlb_statsapi_request_ledger":
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
                if had_failure and success:
                    recovered_transients += 1
                elif had_failure and not success:
                    unrecovered_statsapi.append(key)

    if unrecovered_statsapi:
        blockers.append(
            f"{len(unrecovered_statsapi)} unrecovered StatsAPI request identities"
        )
    if recovered_transients:
        warnings.append(
            f"{recovered_transients} StatsAPI request identities had recovered transient failures"
        )

    # Cross-shard/source-vintage consistency: one logical request identity
    # may be consumed on many simulated dates or in multiple shards, but every
    # successful observation must resolve to the same response bytes. A split
    # brain here means the final dataset mixed external source vintages.
    response_shas_by_request = defaultdict(set)
    for row in all_external_rows:
        status = row.get("status_code")
        response_sha = row.get("response_sha256")
        if (
            isinstance(status, int)
            and 200 <= status < 300
            and response_sha
            and not row.get("exception_type")
        ):
            key = (
                row.get("method"),
                row.get("url"),
                row.get("request_body_sha256"),
            )
            response_shas_by_request[key].add(response_sha)
    divergent_request_identities = {
        key: sorted(shas)
        for key, shas in response_shas_by_request.items()
        if len(shas) > 1
    }
    if divergent_request_identities:
        sample = list(divergent_request_identities.items())[:5]
        failures.append(
            "identical external request identities returned different "
            f"successful content SHAs across the canonical run: {sample}"
        )

    statsapi_rows = [
        row for row in all_external_rows
        if (urlparse(str(row.get("url") or "")).hostname or "").lower()
        == "statsapi.mlb.com"
    ]
    source_shape_audit = audit_statsapi_request_shapes(statsapi_rows)
    failures.extend(source_shape_audit["failures"])
    blockers.extend(source_shape_audit["blockers"])
    expected_team_seasons = sorted({str(date.fromisoformat(day).year) for day in dates})
    if (
        "mlb_statsapi_request_ledger" in loaded_ledgers
        and source_shape_audit.get("team_directory_seasons") != expected_team_seasons
    ):
        failures.append(
            "archived historical team-directory seasons do not exactly cover "
            f"the dataset years: observed="
            f"{source_shape_audit.get('team_directory_seasons')} "
            f"expected={expected_team_seasons}"
        )

    # Every successful external response must be reconstructable from the
    # content-addressed final body archive.
    blob_dir = os.path.join(
        package_dir,
        (report.get("http_totals") or {}).get("response_body_directory")
        or "http_blobs",
    )
    response_shas = {
        row.get("response_sha256")
        for row in all_external_rows
        if row.get("response_sha256")
    }
    decoded_json = {}
    valid_body_shas = set()
    non_json_body_shas = set()
    if not os.path.isdir(blob_dir):
        blockers.append("external response body archive is absent")
    else:
        for response_sha in sorted(response_shas):
            path = os.path.join(blob_dir, f"{response_sha}.gz")
            if not os.path.exists(path):
                blockers.append(
                    f"archived external response body missing: {response_sha}"
                )
                continue
            try:
                with gzip.open(path, "rb") as handle:
                    body = handle.read()
            except Exception as exc:
                failures.append(
                    f"archived response {response_sha} unreadable: {exc}"
                )
                continue
            if sha256_bytes(body) != response_sha:
                failures.append(
                    f"archived external response body SHA mismatch: {response_sha}"
                )
                continue
            valid_body_shas.add(response_sha)
            try:
                decoded_json[response_sha] = json.loads(body)
            except Exception:
                non_json_body_shas.add(response_sha)
                # MLB.com HTML is expected. StatsAPI bodies that require
                # semantic inspection are rejected below only when their
                # archived bytes actually exist but are non-JSON.
                pass

        # Independently verify each season directory's archived CONTENT, not
        # merely its URL: exactly 30 unique MLB team IDs with names/abbrs.
        for row in statsapi_rows:
            parsed = urlparse(str(row.get("url") or ""))
            if parsed.path != "/api/v1/teams":
                continue
            qs = parse_qs(parsed.query)
            season = _single_query(qs, "season")
            if not season or "activeStatus" in qs:
                continue
            response_sha = row.get("response_sha256")
            if response_sha not in valid_body_shas:
                # Missing/unreadable evidence is already a blocker/failure at
                # the archive layer; do not convert absence into a semantic
                # contradiction merely because it cannot be decoded.
                continue
            payload = decoded_json.get(response_sha)
            if not isinstance(payload, dict):
                failures.append(
                    f"season {season} historical team directory body is not "
                    "valid StatsAPI JSON"
                )
                continue
            teams = payload.get("teams") or []
            valid = [
                team for team in teams
                if team.get("id") is not None
                and team.get("name")
                and team.get("abbreviation")
            ]
            ids = {int(team["id"]) for team in valid}
            if len(valid) != 30 or len(ids) != 30:
                failures.append(
                    f"season {season} historical team directory archive has "
                    f"{len(valid)} valid rows / {len(ids)} unique IDs, expected 30"
                )

        # Bind immutable game-feed use to scientific phase. Predictive
        # bullpen feeds are only legitimate when an archived PRE-D schedule
        # response proves that exact gamePk was already completed before D.
        # Same-day feeds are legitimate only in outcome_grading.
        schedule_evidence = defaultdict(list)
        team_pregame_timecodes = {}
        for row in statsapi_rows:
            parsed = urlparse(str(row.get("url") or ""))
            if parsed.path != "/api/v1/schedule":
                continue
            response_sha = row.get("response_sha256")
            if response_sha not in valid_body_shas:
                continue
            payload = decoded_json.get(response_sha)
            if not isinstance(payload, dict):
                failures.append(
                    f"{row.get('observed_date')}: archived schedule body is not valid StatsAPI JSON"
                )
                continue
            qs = parse_qs(parsed.query)
            observed = str(row.get("observed_date") or "")
            date_value = _single_query(qs, "date")
            range_end = _single_query(qs, "endDate")
            pre_d_range = False
            if range_end:
                try:
                    pre_d_range = date.fromisoformat(range_end) < date.fromisoformat(observed)
                except ValueError:
                    pre_d_range = False
            for date_block in payload.get("dates") or []:
                block_day = str(date_block.get("date") or "")
                for game in date_block.get("games") or []:
                    game_pk = game.get("gamePk")
                    if game_pk is None:
                        continue
                    teams = game.get("teams") or {}
                    team_ids = []
                    for side in ("away", "home"):
                        raw_team_id = ((teams.get(side) or {}).get("team") or {}).get("id")
                        if raw_team_id is not None:
                            try:
                                team_ids.append(int(raw_team_id))
                            except (TypeError, ValueError):
                                failures.append(
                                    f"{observed}: schedule game {game_pk} has invalid team id {raw_team_id!r}"
                                )

                    # The date-addressed D slate independently proves each
                    # team's earliest actionable first pitch. Canonical v2
                    # freezes prior-game boxscores exactly one second before
                    # that moment, so suspended/resumed innings later than the
                    # prop's pregame cutoff cannot enter prediction.
                    if (
                        row.get("scientific_phase") == "predictive_input"
                        and date_value == observed
                    ):
                        raw_game_start = str(game.get("gameDate") or "")
                        try:
                            parsed_start = datetime.fromisoformat(
                                raw_game_start.replace("Z", "+00:00")
                            )
                            if parsed_start.tzinfo is None:
                                parsed_start = parsed_start.replace(tzinfo=timezone.utc)
                            expected_timecode = (
                                parsed_start.astimezone(timezone.utc) - timedelta(seconds=1)
                            ).strftime("%Y%m%d_%H%M%S")
                        except Exception:
                            failures.append(
                                f"{observed}: schedule game {game_pk} has invalid gameDate {raw_game_start!r}"
                            )
                            expected_timecode = None
                        if expected_timecode:
                            for team_id in team_ids:
                                key = (observed, team_id)
                                prior = team_pregame_timecodes.get(key)
                                if prior is None or expected_timecode < prior:
                                    team_pregame_timecodes[key] = expected_timecode

                    status = game.get("status") or {}
                    schedule_evidence[(observed, int(game_pk))].append({
                        "phase": row.get("scientific_phase"),
                        "pre_d_range": pre_d_range,
                        "date_block": block_day,
                        "official_date": str(game.get("officialDate") or ""),
                        "coded_state": str(
                            status.get("codedGameState")
                            or status.get("statusCode")
                            or ""
                        ),
                        "detailed_state": str(status.get("detailedState") or ""),
                        "team_ids": sorted(set(team_ids)),
                    })

        for row in statsapi_rows:
            parsed = urlparse(str(row.get("url") or ""))
            match = re.fullmatch(r"/api/v1\.1/game/(\d+)/feed/live", parsed.path)
            if not match:
                continue
            game_pk = int(match.group(1))
            observed = str(row.get("observed_date") or "")
            phase = row.get("scientific_phase")
            evidence = schedule_evidence.get((observed, game_pk), [])
            if not evidence:
                blockers.append(
                    f"{observed}: game feed {game_pk} cannot be linked to any archived schedule response"
                )
                continue

            response_sha = row.get("response_sha256")
            feed_payload = (
                decoded_json.get(response_sha)
                if response_sha in valid_body_shas
                else None
            )
            feed_official = None
            if isinstance(feed_payload, dict):
                feed_official = str(
                    (
                        (feed_payload.get("gameData") or {})
                        .get("datetime", {})
                        .get("officialDate")
                    )
                    or ""
                )
            elif response_sha in valid_body_shas:
                failures.append(
                    f"{observed}: game feed {game_pk} archive is not valid StatsAPI JSON"
                )

            if phase == "predictive_input":
                feed_qs = parse_qs(parsed.query)
                timecode = _single_query(feed_qs, "timecode")
                safe_schedule_evidence = [
                    item for item in evidence
                    if item["phase"] == "predictive_input"
                    and item["pre_d_range"]
                    and item["official_date"]
                    and item["official_date"] < observed
                    and item["coded_state"] in {"F", "O"}
                ]
                if not safe_schedule_evidence:
                    failures.append(
                        f"{observed}: predictive game feed {game_pk} lacks proof of completed pre-D schedule state"
                    )
                allowed_timecodes = {
                    team_pregame_timecodes[(observed, team_id)]
                    for item in safe_schedule_evidence
                    for team_id in item.get("team_ids") or []
                    if (observed, team_id) in team_pregame_timecodes
                }
                if not allowed_timecodes:
                    failures.append(
                        f"{observed}: predictive game feed {game_pk} cannot be tied to a simulated-D team first pitch"
                    )
                elif timecode not in allowed_timecodes:
                    failures.append(
                        f"{observed}: predictive game feed {game_pk} timecode={timecode!r} "
                        f"is not an allowed team pregame cutoff {sorted(allowed_timecodes)!r}"
                    )
                if feed_official and feed_official >= observed:
                    failures.append(
                        f"{observed}: predictive game feed {game_pk} has officialDate={feed_official}"
                    )
            elif phase == "outcome_grading":
                if feed_official and feed_official > observed:
                    failures.append(
                        f"{observed}: outcome game feed {game_pk} belongs to future officialDate={feed_official}"
                    )
            else:
                failures.append(
                    f"{observed}: game feed {game_pk} has invalid scientific phase {phase!r}"
                )

    status_counts = report.get("status_counts") or {}
    if sum(int(value) for value in status_counts.values()) != len(dates):
        failures.append("status_counts do not account for every requested date")
    if set(status_counts) - {"ok", "no_games"}:
        failures.append(
            f"non-terminal date statuses remain: {sorted(set(status_counts) - {'ok','no_games'})}"
        )

    failures = list(dict.fromkeys(failures))
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))

    if failures:
        verdict = "NOT CANONICAL"
    elif blockers:
        verdict = "CERTIFICATION BLOCKED"
    else:
        verdict = "CANONICAL CERTIFIED"

    derived_manifest = {
        "manifest_schema_version": 2,
        "artifact_sha256": rows_sha,
        "artifact_row_count": total_rows,
        "artifact_date_range": [start, end],
        "code_git_sha_at_lock": generation_sha,
    }
    strength = {
        "manifest_schema_version": 2,
        "has_artifact_checksum": True,
        "has_row_count": True,
        "has_date_range": True,
        "has_code_sha_at_lock": True,
        "promotion_grade": True,
        "can_detect_content_replacement": True,
    }

    certification = {
        "verdict": verdict,
        "canonical_generation": "v2_provenance_complete",
        "run_id": run_id,
        "requested_date_range": [start, end],
        "summary": status_counts,
        "total_rows": total_rows,
        "unique_candidate_identities": len(identities),
        "market_counts": dict(sorted(market_counts.items())),
        "year_row_counts": dict(sorted(year_counts.items())),
        "virtual_assembled_byte_sha256": rows_sha,
        "observed_code_shas": sorted(observed_code_shas),
        "scientific_parent_sha": parent_sha,
        "generation_code_sha": generation_sha,
        "code_audit": code,
        "environment": environment,
        "source_lineage": lineage,
        "source_lineage_fingerprint": report.get("source_lineage_fingerprint"),
        "source_schema_attestation": source_attestation,
        "outcome_source_attestation": outcome_source_attestation,
        "statsapi_source_shape_audit": source_shape_audit,
        "cross_shard_request_consistency": {
            "successful_request_identities": len(response_shas_by_request),
            "divergent_request_identities": len(divergent_request_identities),
        },
        "external_response_archive": {
            "unique_response_sha_count": len(response_shas),
            "directory": os.path.relpath(blob_dir, package_dir)
            if os.path.isdir(blob_dir) else None,
            "recovered_statsapi_transient_identities": recovered_transients,
            "unrecovered_statsapi_request_identities": len(unrecovered_statsapi),
        },
        "dataset_identity": {
            "derived_manifest": derived_manifest,
            "strength": strength,
        },
        "failures": failures,
        "blockers": blockers,
        "warnings": warnings,
    }
    certification["certification_report_sha256"] = sha256_bytes(
        json.dumps(
            certification,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    )
    return certification


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package_dir")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--expected-parent-sha")
    ap.add_argument("--expected-source-sha")
    ap.add_argument("--expected-outcome-source-sha")
    ap.add_argument("--output")
    args = ap.parse_args()

    result = certify(
        os.path.abspath(args.package_dir),
        os.path.abspath(args.repo_root),
        expected_parent_sha=args.expected_parent_sha,
        expected_source_sha=args.expected_source_sha,
        expected_outcome_source_sha=args.expected_outcome_source_sha,
    )
    raw = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    print(raw, end="")
    if args.output:
        if os.path.exists(args.output):
            raise FileExistsError(
                f"refusing to overwrite certification report {args.output!r}"
            )
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    return 0 if result["verdict"] == "CANONICAL CERTIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
