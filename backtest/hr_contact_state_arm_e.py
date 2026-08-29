#!/usr/bin/env python3
"""Conditional Arm-E path for the preregistered HR contact-state experiment.

Arm E is a sequential arm. It is not available until the immutable B/C/D
Stage-2 report proves D earned continuation. Even then, this module receives
only the boolean gate plus already-frozen Stage-1 inputs for 2026 prediction.

No B/C/D outcome, subgroup, effect-size, or bootstrap value is used to fit or
rank E.
"""
from __future__ import annotations

import json
import os

import numpy as np

from backtest.experiment_primitives import (
    build_prediction_freeze,
    candidate_identity,
    deterministic_sha256,
    paired_game_cluster_bootstrap,
    require_unique_population,
    select_top_k_per_date,
    selection_anatomy,
)
from backtest.hr_contact_state_stage1 import (
    ARM_FEATURES,
    HRStageIntegrityError,
    K_PRIMARY,
    _arm_vector,
    _extract_state_cached,
    _jsonable_fitted,
    _validated_training_rows,
)
from backtest.hr_contact_state_features import ensure_contact_state_index
from backtest.hr_offset_estimator import (
    apply_standardizer,
    fit_offset_logistic,
    predict_with_champion_fallback,
)
from backtest.hr_contact_state_stage2 import (
    ARMS,
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    MIN_CHANGED_VALID_FRACTION,
    HRStage2IntegrityError,
    _arm_survival,
    _validate_evaluation_rows,
    _verify_selection,
    robustness_removal_audit,
    stability_tables,
    verify_stage1_bundle,
)

ARM = "E"


class HRESequentialIntegrityError(RuntimeError):
    """Conditional Arm-E gate or immutable-parent violation."""


def verify_stage2_trigger(stage2_report, verified_stage1):
    if not isinstance(stage2_report, dict):
        raise HRESequentialIntegrityError("B/C/D Stage-2 report must be a dict")

    logical = dict(stage2_report)
    embedded = logical.pop("evaluation_report_sha256", None)
    if embedded is None or embedded != deterministic_sha256(logical):
        raise HRESequentialIntegrityError(
            "B/C/D Stage-2 evaluation_report_sha256 does not verify"
        )

    if stage2_report.get("stage") != 2:
        raise HRESequentialIntegrityError("trigger report is not B/C/D Stage 2")
    if stage2_report.get("stage1_bundle_sha256") != verified_stage1["bundle_sha256"]:
        raise HRESequentialIntegrityError(
            "B/C/D Stage-2 report belongs to a different Stage-1 bundle"
        )
    if stage2_report.get("stage1_freeze_set_sha256") != verified_stage1["freeze_set_sha256"]:
        raise HRESequentialIntegrityError(
            "B/C/D Stage-2 report freeze-set identity mismatch"
        )

    d = ((stage2_report.get("arms") or {}).get("D") or {})
    survival = d.get("survival") or {}
    if survival.get("earns_continuation") is not True:
        raise HRESequentialIntegrityError(
            "Arm E is forbidden because D did not earn continuation"
        )
    if stage2_report.get("arm_e_permitted") is not True:
        raise HRESequentialIntegrityError(
            "Arm E trigger report does not explicitly permit E"
        )

    return embedded


def frozen_holdout_from_stage1(verified_stage1):
    """Reconstruct E's 2026 input only from the already-frozen B population."""
    b_rows = verified_stage1["arms"]["B"]["population_by_id"]
    rows = []
    for cid in sorted(b_rows, key=repr):
        row = b_rows[cid]
        rows.append({
            "date": row["date"],
            "game_pk": row["game_pk"],
            "player_id": row["player_id"],
            "prop_type": row["prop_type"],
            "line": row["line"],
            "team": row["team"],
            "predicted_prob": row["current_prob"],
            "score": row["eligibility_score"],
        })
    require_unique_population(rows)
    if set(candidate_identity(row) for row in rows) != verified_stage1["population_ids"]:
        raise HRESequentialIntegrityError(
            "reconstructed E population differs from initial frozen population"
        )
    return rows


def fit_hr_arm_e(training_rows, source_frame):
    """Fit locked E only on supported <=2025 training rows."""
    rows = _validated_training_rows(training_rows)
    source_index = ensure_contact_state_index(source_frame)
    cache = {}
    supported_ids = []
    x = []
    champion = []
    outcomes = []

    for row in rows:
        state = _extract_state_cached(source_index, row, cache)
        values, supported = _arm_vector(state, ARM)
        if not supported:
            continue
        supported_ids.append(candidate_identity(row))
        x.append(values)
        champion.append(float(row["predicted_prob"]))
        outcomes.append(float(row["outcome"]))

    if not supported_ids:
        raise HRESequentialIntegrityError(
            "Arm E has zero supported <=2025 training rows"
        )

    fit = fit_offset_logistic(champion, x, outcomes)
    return {
        "feature_names": ARM_FEATURES[ARM],
        "supported_training_ids": supported_ids,
        "supported_training_n": len(supported_ids),
        "training_population_n": len(rows),
        "fit": fit,
    }


def build_hr_e_prediction_bundle(
    training_rows,
    source_frame,
    initial_stage1_bundle,
    stage2_report,
    *,
    runner_code_sha,
):
    """Build one conditional E prediction freeze after verified D survival."""
    verified = verify_stage1_bundle(initial_stage1_bundle)
    trigger_sha = verify_stage2_trigger(stage2_report, verified)

    if runner_code_sha != verified["runner_code_sha"]:
        raise HRESequentialIntegrityError(
            "runner code SHA changed after B/C/D outcome reveal; Arm E aborted"
        )

    holdout = frozen_holdout_from_stage1(verified)
    source_index = ensure_contact_state_index(source_frame)
    spec = fit_hr_arm_e(training_rows, source_index)

    cache = {}
    raw_matrix = []
    raw_vectors = []
    supported_mask = []
    current_probs = []

    for row in holdout:
        state = _extract_state_cached(source_index, row, cache)
        values, supported = _arm_vector(state, ARM)
        raw_vectors.append(values)
        raw_matrix.append([
            float(value) if value is not None else np.nan
            for value in values
        ])
        supported_mask.append(bool(supported))
        current_probs.append(float(row["predicted_prob"]))

    mask = np.asarray(supported_mask, dtype=bool)
    challenger = predict_with_champion_fallback(
        current_probs,
        raw_matrix,
        mask,
        spec["fit"],
    )

    standardized_by_index = {}
    if mask.any():
        supported_x = np.asarray(raw_matrix, dtype=float)[mask]
        standardized = apply_standardizer(
            supported_x,
            spec["fit"]["standardizer"],
        )
        for index, values in zip(np.where(mask)[0].tolist(), standardized):
            standardized_by_index[index] = [
                float(value) for value in values.tolist()
            ]

    b_population = verified["arms"]["B"]["population_by_id"]
    venue_records = {
        int(record["game_pk"]): record
        for record in verified["venue_map_attestation"].get("records", [])
    }

    prediction_rows = []
    for index, row in enumerate(holdout):
        cid = candidate_identity(row)
        b_row = b_population[cid]
        venue = venue_records.get(int(row["game_pk"]))
        if venue is None:
            raise HRESequentialIntegrityError(
                f"frozen venue record missing for game {row['game_pk']!r}"
            )
        if int(venue["venue_id"]) != int(b_row["venue_id"]):
            raise HRESequentialIntegrityError(
                f"frozen venue changed for candidate {cid!r}"
            )
        standardized = standardized_by_index.get(index)
        prediction_rows.append({
            "date": row["date"],
            "game_pk": row["game_pk"],
            "player_id": row["player_id"],
            "prop_type": row["prop_type"],
            "line": row["line"],
            "team": row["team"],
            "venue_id": int(b_row["venue_id"]),
            "eligibility_score": float(row["score"]),
            "current_prob": float(row["predicted_prob"]),
            "challenger_prob": float(challenger[index]),
            "supported": bool(mask[index]),
            "prediction_path": (
                "contact_state_model" if mask[index] else "champion_fallback"
            ),
            "raw_features": {
                name: (
                    float(value) if value is not None else None
                )
                for name, value in zip(ARM_FEATURES[ARM], raw_vectors[index])
            },
            "standardized_features": (
                {
                    name: float(value)
                    for name, value in zip(ARM_FEATURES[ARM], standardized)
                }
                if standardized is not None
                else None
            ),
        })

    selection = select_top_k_per_date(
        prediction_rows,
        K_PRIMARY,
        champion_score_key="current_prob",
        challenger_score_key="challenger_prob",
    )

    b_champion_ids = verified["arms"]["B"]["selection_sets"]["champion_ids"]
    if [tuple(cid) for cid in selection["champion_ids"]] != b_champion_ids:
        raise HRESequentialIntegrityError(
            "Arm E champion selection differs from initial frozen champion"
        )

    metadata = {
        "experiment": "hr_contact_state",
        "arm": ARM,
        "conditional_on": "D survival",
        "parent_stage1_bundle_sha256": verified["bundle_sha256"],
        "trigger_stage2_report_sha256": trigger_sha,
        "runner_code_sha": runner_code_sha,
        "canonical_artifact_identity": verified["canonical_artifact_identity"],
        "source_artifact_identity": verified["source_artifact_identity"],
        "venue_map_attestation": {
            "row_count": verified["venue_map_attestation"].get("row_count"),
            "sha256": verified["venue_map_attestation"].get("sha256"),
        },
        "feature_names": list(ARM_FEATURES[ARM]),
        "k_primary": K_PRIMARY,
        "coverage": {
            "population_n": len(prediction_rows),
            "supported_n": int(mask.sum()),
        },
        "training": {
            "population_n": spec["training_population_n"],
            "supported_n": spec["supported_training_n"],
            "supported_training_ids_sha256": deterministic_sha256(
                spec["supported_training_ids"]
            ),
            "fitted": _jsonable_fitted(spec["fit"]),
        },
    }

    freeze = build_prediction_freeze(
        prediction_rows,
        selection,
        metadata=metadata,
    )
    bundle = {
        "arm": ARM,
        "freeze": freeze,
        "parent_stage1_bundle_sha256": verified["bundle_sha256"],
        "trigger_stage2_report_sha256": trigger_sha,
    }
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    return bundle


def verify_e_bundle(e_bundle, verified_stage1, stage2_report):
    if not isinstance(e_bundle, dict):
        raise HRESequentialIntegrityError("Arm-E bundle must be a dict")
    logical = dict(e_bundle)
    embedded_bundle = logical.pop("bundle_sha256", None)
    if embedded_bundle is None or embedded_bundle != deterministic_sha256(logical):
        raise HRESequentialIntegrityError("Arm-E bundle_sha256 does not verify")

    trigger_sha = verify_stage2_trigger(stage2_report, verified_stage1)
    if e_bundle.get("arm") != ARM:
        raise HRESequentialIntegrityError("conditional bundle is not Arm E")
    if e_bundle.get("parent_stage1_bundle_sha256") != verified_stage1["bundle_sha256"]:
        raise HRESequentialIntegrityError("Arm-E bundle parent Stage-1 mismatch")
    if e_bundle.get("trigger_stage2_report_sha256") != trigger_sha:
        raise HRESequentialIntegrityError("Arm-E trigger Stage-2 report mismatch")

    freeze = e_bundle.get("freeze") or {}
    payload = freeze.get("payload")
    embedded_freeze = freeze.get("sha256")
    if payload is None or embedded_freeze != deterministic_sha256(payload):
        raise HRESequentialIntegrityError("Arm-E prediction-freeze SHA does not verify")

    metadata = payload.get("metadata") or {}
    if metadata.get("arm") != ARM:
        raise HRESequentialIntegrityError("Arm-E freeze metadata arm mismatch")
    if metadata.get("runner_code_sha") != verified_stage1["runner_code_sha"]:
        raise HRESequentialIntegrityError("Arm-E runner SHA differs from initial Stage 1")
    if metadata.get("canonical_artifact_identity") != verified_stage1["canonical_artifact_identity"]:
        raise HRESequentialIntegrityError("Arm-E canonical identity mismatch")
    if metadata.get("source_artifact_identity") != verified_stage1["source_artifact_identity"]:
        raise HRESequentialIntegrityError("Arm-E source identity mismatch")

    population = payload.get("population") or []
    by_id = require_unique_population(population)
    if set(by_id) != verified_stage1["population_ids"]:
        raise HRESequentialIntegrityError(
            "Arm-E frozen population differs from initial B/C/D population"
        )

    selection_sets = _verify_selection(payload.get("selection") or {})
    if selection_sets["champion_ids"] != verified_stage1["arms"]["B"]["selection_sets"]["champion_ids"]:
        raise HRESequentialIntegrityError(
            "Arm-E champion selection differs from initial B/C/D champion"
        )

    b_population = verified_stage1["arms"]["B"]["population_by_id"]
    for cid, row in by_id.items():
        base = b_population[cid]
        for key in ("team", "venue_id", "eligibility_score", "current_prob"):
            if row.get(key) != base.get(key):
                raise HRESequentialIntegrityError(
                    f"Arm-E frozen {key} differs from initial Stage 1 for {cid!r}"
                )

    return {
        "bundle_sha256": embedded_bundle,
        "freeze": freeze,
        "population_by_id": by_id,
        "selection_sets": selection_sets,
        "trigger_stage2_report_sha256": trigger_sha,
    }


def evaluate_hr_e_stage2(
    evaluation_rows,
    initial_stage1_bundle,
    stage2_report,
    e_bundle,
):
    verified_initial = verify_stage1_bundle(initial_stage1_bundle)
    verified_e = verify_e_bundle(
        e_bundle,
        verified_initial,
        stage2_report,
    )
    eval_by_id = _validate_evaluation_rows(
        evaluation_rows,
        verified_initial,
    )

    freeze = verified_e["freeze"]
    payload = freeze["payload"]
    selection = payload["selection"]
    frozen_by_id = verified_e["population_by_id"]

    anatomy = selection_anatomy(list(eval_by_id.values()), selection)
    for key in ("overlap_ids", "removed_ids", "added_ids"):
        if [tuple(cid) for cid in anatomy[key]] != [
            tuple(cid) for cid in selection[key]
        ]:
            raise HRESequentialIntegrityError(
                f"Arm-E {key} changed after outcome join"
            )

    bootstrap = paired_game_cluster_bootstrap(
        list(eval_by_id.values()),
        selection,
        n_replicates=BOOTSTRAP_REPLICATES,
        seed=BOOTSTRAP_SEED,
        min_changed_valid_fraction=MIN_CHANGED_VALID_FRACTION,
    )
    robustness = robustness_removal_audit(
        selection,
        eval_by_id,
        frozen_by_id,
    )
    stability = stability_tables(
        selection,
        eval_by_id,
        frozen_by_id,
    )
    survival = _arm_survival(
        anatomy,
        bootstrap,
        robustness,
        verified_initial["b_support_count"],
    )

    report = {
        "experiment": "hr_contact_state",
        "stage": "2-E",
        "arm": ARM,
        "parent_stage1_bundle_sha256": verified_initial["bundle_sha256"],
        "parent_stage2_report_sha256": verified_e["trigger_stage2_report_sha256"],
        "e_bundle_sha256": verified_e["bundle_sha256"],
        "e_prediction_freeze_sha256": freeze["sha256"],
        "holdout_population_n": len(eval_by_id),
        "selection_anatomy": anatomy,
        "cluster_bootstrap": bootstrap,
        "robustness": robustness,
        "stability": stability,
        "survival": survival,
        "prior_bcd_survivors": list(stage2_report.get("initial_survivors") or []),
        "e_failure_does_not_erase_prior_survivors": True,
        "historical_evidence_only": True,
        "production_promotion_authorized": False,
    }
    report["evaluation_report_sha256"] = deterministic_sha256(report)
    return report


def write_immutable_e_bundle(path, bundle):
    if os.path.exists(path):
        raise FileExistsError(
            f"refusing to overwrite immutable Arm-E bundle at {path!r}"
        )
    logical = dict(bundle)
    embedded = logical.pop("bundle_sha256", None)
    if embedded != deterministic_sha256(logical):
        raise HRESequentialIntegrityError("Arm-E bundle_sha256 does not verify")

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    raw = (json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": path,
        "byte_sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "bundle_sha256": embedded,
        "bytes": len(raw),
    }
