#!/usr/bin/env python3
"""HR contact-state experiment Stage-1 integration scaffold.

This module is intentionally split at the holdout truth boundary:

* fit_hr_arms() may consume <=2025 outcomes as training targets.
* build_hr_prediction_freezes() rejects ANY postgame/outcome field and creates
  immutable B/C/D prediction/selection artifacts.
* No evaluation or GO/KILL decision lives here.

Arm E is implemented by the lower-level feature/estimator modules but is
refused here by default because the frozen prereg allows E only after D earns
continuation.
"""
from __future__ import annotations

import math

import numpy as np

from backtest.experiment_primitives import (
    ExperimentIntegrityError,
    build_prediction_freeze,
    candidate_identity,
    require_unique_population,
    select_top_k_per_date,
    deterministic_sha256,
)
from backtest.hr_contact_state_features import extract_contact_state
from backtest.hr_offset_estimator import (
    apply_standardizer,
    fit_offset_logistic,
    predict_with_champion_fallback,
)

MARKET = "home_run"
TRAIN_END = "2025-12-31"
HOLDOUT_START = "2026-01-01"
K_PRIMARY = 5
DEFAULT_ARMS = ("B", "C", "D")

ARM_FEATURES = {
    "B": ("bat_speed_mean", "bat_speed_p90"),
    "C": (
        "attack_angle_mean",
        "swing_length_mean",
        "swing_path_tilt_mean",
        "attack_direction_mean",
    ),
    "D": (
        "bat_speed_mean",
        "bat_speed_p90",
        "attack_angle_mean",
        "swing_length_mean",
        "swing_path_tilt_mean",
        "attack_direction_mean",
    ),
    "E": (
        "bat_speed_mean",
        "bat_speed_p90",
        "attack_angle_mean",
        "swing_length_mean",
        "swing_path_tilt_mean",
        "attack_direction_mean",
        "hit_distance_sc_mean",
    ),
}

FORBIDDEN_HOLDOUT_FIELDS = {
    "outcome",
    "actual",
    "actual_pa",
    "actual_ip",
    "fair_test",
    "settlement_state",
    "result_actual",
    "result_reason",
    "grade",
}


class HRStageIntegrityError(ExperimentIntegrityError):
    """Research-integrity failure before holdout outcome reveal."""


def _date(row):
    value = str(row.get("date") or "")[:10]
    if len(value) != 10:
        raise HRStageIntegrityError(f"invalid row date: {row.get('date')!r}")
    return value


def _finite_score(value, label):
    if value is None or isinstance(value, bool):
        raise HRStageIntegrityError(f"{label} missing/non-numeric")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HRStageIntegrityError(f"{label} non-numeric: {value!r}") from exc
    if not math.isfinite(value):
        raise HRStageIntegrityError(f"{label} non-finite: {value!r}")
    return value


def _probability(value, label):
    if value is None or isinstance(value, bool):
        raise HRStageIntegrityError(f"{label} missing/non-numeric")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HRStageIntegrityError(f"{label} non-numeric: {value!r}") from exc
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise HRStageIntegrityError(f"{label} outside [0,1]: {value!r}")
    return value


def _state_cache_key(row):
    return (int(row["player_id"]), _date(row))


def _extract_state_cached(source_frame, row, cache):
    key = _state_cache_key(row)
    if key not in cache:
        cache[key] = extract_contact_state(
            source_frame,
            batter_id=row["player_id"],
            candidate_date=row["date"],
        )
    return cache[key]


def _arm_vector(state, arm):
    if arm not in ARM_FEATURES:
        raise HRStageIntegrityError(f"unknown arm {arm!r}")
    names = ARM_FEATURES[arm]
    features = state["features"]
    values = [features.get(name) for name in names]
    supported = bool(state["support"].get(arm))
    if supported and any(value is None for value in values):
        raise HRStageIntegrityError(
            f"arm {arm} marked supported but feature vector contains None"
        )
    return values, supported


def _jsonable_fitted(fitted):
    return {
        "beta": [float(value) for value in np.asarray(fitted["beta"]).tolist()],
        "standardizer": {
            "mean": [
                float(value)
                for value in np.asarray(fitted["standardizer"]["mean"]).tolist()
            ],
            "std": [
                float(value)
                for value in np.asarray(fitted["standardizer"]["std"]).tolist()
            ],
        },
        "optimizer": dict(fitted["optimizer"]),
    }


def venue_map_attestation(venue_map):
    """Deterministic identity for the predeclared game_pk -> venue map."""
    records = []
    for game_pk, value in venue_map.items():
        try:
            game_int = int(game_pk)
        except (TypeError, ValueError) as exc:
            raise HRStageIntegrityError(
                f"invalid game_pk in venue map: {game_pk!r}"
            ) from exc
        if not isinstance(value, dict):
            raise HRStageIntegrityError(
                f"venue map value for {game_pk!r} must be a dict"
            )
        venue_id = value.get("venue_id")
        try:
            venue_id = int(venue_id)
        except (TypeError, ValueError) as exc:
            raise HRStageIntegrityError(
                f"invalid venue_id for game {game_pk!r}: {venue_id!r}"
            ) from exc
        records.append({
            "game_pk": game_int,
            "venue_id": venue_id,
            "venue_name": value.get("venue_name"),
        })
    records.sort(key=lambda row: row["game_pk"])
    return {
        "row_count": len(records),
        "sha256": deterministic_sha256(records),
        "records": records,
    }


def _validated_training_rows(training_rows):
    rows = list(training_rows)
    if not rows:
        raise HRStageIntegrityError("no HR training rows")
    require_unique_population(rows)

    for row in rows:
        if row.get("prop_type") != MARKET:
            raise HRStageIntegrityError(
                f"training population contains non-{MARKET} row: "
                f"{candidate_identity(row)!r}"
            )
        if _date(row) > TRAIN_END:
            raise HRStageIntegrityError(
                f"training row crosses frozen cutoff: {_date(row)}"
            )
        if row.get("outcome") not in (0, 1):
            raise HRStageIntegrityError(
                f"training row lacks binary outcome: {candidate_identity(row)!r}"
            )
        _probability(
            row.get("predicted_prob"),
            f"predicted_prob for {candidate_identity(row)!r}",
        )
        _finite_score(
            row.get("score"),
            f"score for {candidate_identity(row)!r}",
        )
    return rows


def fit_hr_arms(training_rows, source_frame, arms=DEFAULT_ARMS):
    """Fit preregistered arms using supported <=2025 rows only."""
    rows = _validated_training_rows(training_rows)
    if "E" in arms:
        raise HRStageIntegrityError(
            "Arm E may not be fit in the initial B/C/D stage; D must earn continuation first"
        )
    if tuple(arms) != DEFAULT_ARMS:
        raise HRStageIntegrityError(
            f"initial arm set is locked to {DEFAULT_ARMS!r}, got {tuple(arms)!r}"
        )

    cache = {}
    states = {
        candidate_identity(row): _extract_state_cached(source_frame, row, cache)
        for row in rows
    }
    fitted = {}

    for arm in arms:
        feature_names = ARM_FEATURES[arm]
        supported_rows = []
        feature_matrix = []
        champion = []
        outcomes = []

        for row in rows:
            state = states[candidate_identity(row)]
            values, supported = _arm_vector(state, arm)
            if not supported:
                continue
            supported_rows.append(candidate_identity(row))
            feature_matrix.append(values)
            champion.append(float(row["predicted_prob"]))
            outcomes.append(float(row["outcome"]))

        if not supported_rows:
            raise HRStageIntegrityError(
                f"arm {arm} has zero supported training rows"
            )

        fit = fit_offset_logistic(
            champion,
            feature_matrix,
            outcomes,
        )
        fitted[arm] = {
            "feature_names": feature_names,
            "supported_training_ids": supported_rows,
            "supported_training_n": len(supported_rows),
            "training_population_n": len(rows),
            "fit": fit,
        }

    return fitted


def _validate_holdout_masked(rows):
    rows = list(rows)
    if not rows:
        raise HRStageIntegrityError("no HR holdout prediction rows")
    require_unique_population(rows)

    for row in rows:
        forbidden = sorted(FORBIDDEN_HOLDOUT_FIELDS.intersection(row))
        if forbidden:
            raise HRStageIntegrityError(
                f"holdout prediction row exposes postgame field(s) {forbidden}: "
                f"{candidate_identity(row)!r}"
            )
        if row.get("prop_type") != MARKET:
            raise HRStageIntegrityError(
                f"holdout population contains non-{MARKET} row: "
                f"{candidate_identity(row)!r}"
            )
        if _date(row) < HOLDOUT_START:
            raise HRStageIntegrityError(
                f"holdout row precedes frozen holdout start: {_date(row)}"
            )
        if not row.get("team"):
            raise HRStageIntegrityError(
                f"holdout row missing team required for frozen robustness audit: "
                f"{candidate_identity(row)!r}"
            )
        _probability(
            row.get("predicted_prob"),
            f"predicted_prob for {candidate_identity(row)!r}",
        )
        _finite_score(
            row.get("score"),
            f"score for {candidate_identity(row)!r}",
        )
    return rows


def write_immutable_stage1_bundle(path, bundle):
    """Persist one Stage-1 prediction bundle exactly once.

    The embedded bundle_sha256 covers every field except itself. A caller
    cannot silently rewrite coverage, venue provenance, fitted diagnostics,
    frozen populations, or selection identities after Stage 1.
    """
    import json
    import os

    if os.path.exists(path):
        raise FileExistsError(
            f"refusing to overwrite immutable HR Stage-1 bundle at {path!r}"
        )

    stored = dict(bundle)
    embedded = stored.pop("bundle_sha256", None)
    if embedded != deterministic_sha256(stored):
        raise HRStageIntegrityError(
            "Stage-1 bundle_sha256 does not match bundle content"
        )

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = (
        json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

    return {
        "path": path,
        "byte_sha256": __import__("hashlib").sha256(payload).hexdigest(),
        "bundle_sha256": embedded,
        "bytes": len(payload),
    }


def build_hr_prediction_freezes(
    masked_holdout_rows,
    source_frame,
    fitted_arms,
    venue_map,
    *,
    runner_code_sha,
    canonical_artifact_identity,
    source_artifact_identity,
):
    """Create B/C/D outcome-free prediction/selection freeze artifacts."""
    rows = _validate_holdout_masked(masked_holdout_rows)

    if tuple(sorted(fitted_arms)) != tuple(sorted(DEFAULT_ARMS)):
        raise HRStageIntegrityError(
            f"initial fitted arm set must be exactly {DEFAULT_ARMS!r}"
        )
    if not runner_code_sha:
        raise HRStageIntegrityError("runner_code_sha is required")
    if not canonical_artifact_identity:
        raise HRStageIntegrityError("canonical_artifact_identity is required")
    if not source_artifact_identity:
        raise HRStageIntegrityError("source_artifact_identity is required")

    venue_attestation = venue_map_attestation(venue_map)
    venue_lookup = {
        record["game_pk"]: record
        for record in venue_attestation["records"]
    }
    missing_games = sorted({
        int(row["game_pk"])
        for row in rows
        if int(row["game_pk"]) not in venue_lookup
    })
    if missing_games:
        raise HRStageIntegrityError(
            f"venue map unresolved for holdout game_pk(s): {missing_games[:10]!r}"
        )

    cache = {}
    states = {
        candidate_identity(row): _extract_state_cached(source_frame, row, cache)
        for row in rows
    }

    coverage = {
        "population_n": len(rows),
        "per_arm_supported_n": {},
        "per_feature_available_n": {},
    }
    all_feature_names = sorted({
        feature
        for arm in DEFAULT_ARMS
        for feature in ARM_FEATURES[arm]
    })
    for feature in all_feature_names:
        coverage["per_feature_available_n"][feature] = sum(
            1
            for state in states.values()
            if state["features"].get(feature) is not None
        )

    freezes = {}
    champion_selection_reference = None

    for arm in DEFAULT_ARMS:
        spec = fitted_arms[arm]
        if tuple(spec["feature_names"]) != ARM_FEATURES[arm]:
            raise HRStageIntegrityError(
                f"arm {arm} fitted feature order differs from prereg"
            )
        fit = spec["fit"]
        feature_names = ARM_FEATURES[arm]

        current_probs = []
        raw_matrix = []
        supported_mask = []
        raw_vectors = []

        for row in rows:
            state = states[candidate_identity(row)]
            values, supported = _arm_vector(state, arm)
            current_probs.append(float(row["predicted_prob"]))
            raw_vectors.append(values)
            raw_matrix.append([
                float(value) if value is not None else np.nan
                for value in values
            ])
            supported_mask.append(supported)

        mask = np.asarray(supported_mask, dtype=bool)
        challenger_probs = predict_with_champion_fallback(
            current_probs,
            raw_matrix,
            mask,
            fit,
        )
        coverage["per_arm_supported_n"][arm] = int(mask.sum())

        standardized_by_index = {}
        if mask.any():
            supported_x = np.asarray(raw_matrix, dtype=float)[mask]
            standardized = apply_standardizer(
                supported_x,
                fit["standardizer"],
            )
            supported_indexes = np.where(mask)[0].tolist()
            for index, values in zip(supported_indexes, standardized):
                standardized_by_index[index] = [
                    float(value) for value in values.tolist()
                ]

        prediction_rows = []
        for index, row in enumerate(rows):
            cid = candidate_identity(row)
            venue = venue_lookup[int(row["game_pk"])]
            raw_values = raw_vectors[index]
            standardized_values = standardized_by_index.get(index)
            prediction_rows.append({
                "date": row["date"],
                "game_pk": row["game_pk"],
                "player_id": row["player_id"],
                "prop_type": row["prop_type"],
                "line": row["line"],
                "team": row["team"],
                "venue_id": venue["venue_id"],
                "eligibility_score": float(row["score"]),
                "current_prob": float(row["predicted_prob"]),
                "challenger_prob": float(challenger_probs[index]),
                "supported": bool(mask[index]),
                "prediction_path": (
                    "contact_state_model" if mask[index] else "champion_fallback"
                ),
                "raw_features": {
                    name: (
                        float(value) if value is not None else None
                    )
                    for name, value in zip(feature_names, raw_values)
                },
                "standardized_features": (
                    {
                        name: float(value)
                        for name, value in zip(feature_names, standardized_values)
                    }
                    if standardized_values is not None
                    else None
                ),
            })

        selection = select_top_k_per_date(
            prediction_rows,
            K_PRIMARY,
            champion_score_key="current_prob",
            challenger_score_key="challenger_prob",
        )

        if champion_selection_reference is None:
            champion_selection_reference = selection["champion_ids"]
        elif selection["champion_ids"] != champion_selection_reference:
            raise HRStageIntegrityError(
                "champion selection changed across B/C/D despite identical population"
            )

        metadata = {
            "experiment": "hr_contact_state",
            "arm": arm,
            "feature_names": list(feature_names),
            "k_primary": K_PRIMARY,
            "runner_code_sha": runner_code_sha,
            "canonical_artifact_identity": canonical_artifact_identity,
            "source_artifact_identity": source_artifact_identity,
            "venue_map_attestation": {
                "row_count": venue_attestation["row_count"],
                "sha256": venue_attestation["sha256"],
            },
            "coverage": {
                "population_n": coverage["population_n"],
                "supported_n": coverage["per_arm_supported_n"][arm],
                "per_feature_available_n": {
                    name: coverage["per_feature_available_n"][name]
                    for name in feature_names
                },
            },
            "training": {
                "population_n": spec["training_population_n"],
                "supported_n": spec["supported_training_n"],
                "supported_training_ids_sha256": deterministic_sha256(
                    spec["supported_training_ids"]
                ),
                "fitted": _jsonable_fitted(fit),
            },
        }

        freezes[arm] = build_prediction_freeze(
            prediction_rows,
            selection,
            metadata=metadata,
        )

    freeze_hashes = {
        arm: freezes[arm]["sha256"]
        for arm in DEFAULT_ARMS
    }
    bundle = {
        "arms": freezes,
        "coverage": coverage,
        "venue_map_attestation": venue_attestation,
        "freeze_set_sha256": deterministic_sha256(freeze_hashes),
    }
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    return bundle
