#!/usr/bin/env python3
"""Immutable Stage-2 evaluation for the frozen HR contact-state experiment.

This module deliberately has no model-fitting, feature-extraction, ranking, or
selection imports. Stage 2 can only:

1. verify the Stage-1 prediction-freeze hashes and frozen selection anatomy;
2. join canonical binary outcomes by exact candidate identity;
3. evaluate those frozen selections under the preregistered uncertainty,
   robustness, and descriptive-stability rules.

If Stage-1 bytes or selection identity sets changed, evaluation aborts.
"""
from __future__ import annotations

import json
import math
import os

from backtest.experiment_primitives import (
    ExperimentIntegrityError,
    candidate_identity,
    deterministic_sha256,
    paired_game_cluster_bootstrap,
    require_unique_population,
    selection_anatomy,
)

ARMS = ("B", "C", "D")
BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 20260828
MIN_CHANGED_VALID_REPLICATES = 4750
MIN_CHANGED_VALID_FRACTION = MIN_CHANGED_VALID_REPLICATES / BOOTSTRAP_REPLICATES
MIN_BASE_TRAILING_SUPPORT = 500

PROBABILITY_BANDS = (
    (0.00, 0.05, False, "[0.00, 0.05)"),
    (0.05, 0.10, False, "[0.05, 0.10)"),
    (0.10, 0.15, False, "[0.10, 0.15)"),
    (0.15, 0.20, False, "[0.15, 0.20)"),
    (0.20, 0.30, False, "[0.20, 0.30)"),
    (0.30, 1.00, True, "[0.30, 1.00]"),
)

ROBUSTNESS_AXES = ("player", "team", "park", "month")


class HRStage2IntegrityError(ExperimentIntegrityError):
    """Stage-2 outcome/reproducibility contract violation."""


def _finite_probability(value, label):
    if value is None or isinstance(value, bool):
        raise HRStage2IntegrityError(f"{label} missing/non-numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HRStage2IntegrityError(f"{label} non-numeric: {value!r}") from exc
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise HRStage2IntegrityError(f"{label} outside [0,1]: {value!r}")
    return number


def _recompute_selection_identity_sets(selection):
    champion_ids = list(selection.get("champion_ids") or [])
    challenger_ids = list(selection.get("challenger_ids") or [])
    champion_set = set(tuple(cid) for cid in champion_ids)
    challenger_set = set(tuple(cid) for cid in challenger_ids)

    overlap = [tuple(cid) for cid in champion_ids if tuple(cid) in challenger_set]
    removed = [tuple(cid) for cid in champion_ids if tuple(cid) not in challenger_set]
    added = [tuple(cid) for cid in challenger_ids if tuple(cid) not in champion_set]
    return {
        "champion_ids": [tuple(cid) for cid in champion_ids],
        "challenger_ids": [tuple(cid) for cid in challenger_ids],
        "overlap_ids": overlap,
        "removed_ids": removed,
        "added_ids": added,
    }


def _verify_selection(selection):
    required = (
        "dates",
        "population_n",
        "champion_ids",
        "challenger_ids",
        "overlap_ids",
        "removed_ids",
        "added_ids",
    )
    missing = [key for key in required if key not in selection]
    if missing:
        raise HRStage2IntegrityError(
            f"frozen selection missing key(s): {missing}"
        )

    recomputed = _recompute_selection_identity_sets(selection)
    for key in (
        "champion_ids",
        "challenger_ids",
        "overlap_ids",
        "removed_ids",
        "added_ids",
    ):
        stored = [tuple(cid) for cid in selection[key]]
        if stored != recomputed[key]:
            raise HRStage2IntegrityError(
                f"frozen selection {key} changed or is internally inconsistent"
            )

    if len(recomputed["champion_ids"]) != len(recomputed["challenger_ids"]):
        raise HRStage2IntegrityError("frozen aggregate selection volume differs")

    champion_by_date = {}
    challenger_by_date = {}
    for cid in recomputed["champion_ids"]:
        champion_by_date[cid[0]] = champion_by_date.get(cid[0], 0) + 1
    for cid in recomputed["challenger_ids"]:
        challenger_by_date[cid[0]] = challenger_by_date.get(cid[0], 0) + 1

    for day, info in selection["dates"].items():
        selected_n = int(info.get("selected_n", -1))
        population_n = int(info.get("population_n", -1))
        if selected_n != min(5, population_n):
            raise HRStage2IntegrityError(
                f"{day}: frozen selected_n={selected_n} violates locked top-five capacity"
            )
        if champion_by_date.get(day, 0) != selected_n:
            raise HRStage2IntegrityError(
                f"{day}: champion frozen per-date volume differs from selected_n"
            )
        if challenger_by_date.get(day, 0) != selected_n:
            raise HRStage2IntegrityError(
                f"{day}: challenger frozen per-date volume differs from selected_n"
            )
        if [tuple(cid) for cid in info.get("champion_ids", [])] != [
            cid for cid in recomputed["champion_ids"] if cid[0] == day
        ]:
            raise HRStage2IntegrityError(
                f"{day}: per-date champion identities differ from aggregate freeze"
            )
        if [tuple(cid) for cid in info.get("challenger_ids", [])] != [
            cid for cid in recomputed["challenger_ids"] if cid[0] == day
        ]:
            raise HRStage2IntegrityError(
                f"{day}: per-date challenger identities differ from aggregate freeze"
            )

    return recomputed


def verify_stage1_bundle(stage1_bundle):
    """Verify all Stage-1 hashes and cross-arm population invariants first."""
    if not isinstance(stage1_bundle, dict):
        raise HRStage2IntegrityError("Stage-1 bundle must be a dict")

    stored_bundle = dict(stage1_bundle)
    embedded_bundle_sha = stored_bundle.pop("bundle_sha256", None)
    if embedded_bundle_sha is None:
        raise HRStage2IntegrityError("Stage-1 bundle_sha256 is absent")
    recomputed_bundle_sha = deterministic_sha256(stored_bundle)
    if embedded_bundle_sha != recomputed_bundle_sha:
        raise HRStage2IntegrityError(
            "Stage-1 bundle_sha256 no longer matches bundle content"
        )

    arms = stage1_bundle.get("arms") or {}
    if tuple(sorted(arms)) != tuple(sorted(ARMS)):
        raise HRStage2IntegrityError(
            f"Stage-1 arm set must be exactly {ARMS!r}"
        )

    frozen_hashes = {}
    common_population_ids = None
    common_champion_ids = None
    common_runner_code_sha = None
    common_canonical_identity = None
    common_source_identity = None
    common_venue_attestation = None
    verified = {}

    for arm in ARMS:
        freeze = arms[arm]
        if not isinstance(freeze, dict) or "payload" not in freeze or "sha256" not in freeze:
            raise HRStage2IntegrityError(f"arm {arm} freeze malformed")
        recomputed_sha = deterministic_sha256(freeze["payload"])
        if recomputed_sha != freeze["sha256"]:
            raise HRStage2IntegrityError(
                f"arm {arm} prediction-freeze SHA no longer matches payload"
            )
        frozen_hashes[arm] = freeze["sha256"]

        payload = freeze["payload"]
        metadata = payload.get("metadata") or {}
        if metadata.get("arm") != arm:
            raise HRStage2IntegrityError(
                f"arm {arm} freeze metadata identifies {metadata.get('arm')!r}"
            )
        if int(metadata.get("k_primary", -1)) != 5:
            raise HRStage2IntegrityError(
                f"arm {arm} freeze does not carry locked K_PRIMARY=5"
            )

        runner_code_sha = metadata.get("runner_code_sha")
        canonical_identity = metadata.get("canonical_artifact_identity")
        source_identity = metadata.get("source_artifact_identity")
        venue_attestation = metadata.get("venue_map_attestation")
        if not runner_code_sha or not canonical_identity or not source_identity or not venue_attestation:
            raise HRStage2IntegrityError(
                f"arm {arm} freeze is missing locked provenance metadata"
            )

        if common_runner_code_sha is None:
            common_runner_code_sha = runner_code_sha
            common_canonical_identity = canonical_identity
            common_source_identity = source_identity
            common_venue_attestation = venue_attestation
        else:
            if runner_code_sha != common_runner_code_sha:
                raise HRStage2IntegrityError("runner_code_sha differs across B/C/D")
            if canonical_identity != common_canonical_identity:
                raise HRStage2IntegrityError("canonical artifact identity differs across B/C/D")
            if source_identity != common_source_identity:
                raise HRStage2IntegrityError("source artifact identity differs across B/C/D")
            if venue_attestation != common_venue_attestation:
                raise HRStage2IntegrityError("venue-map attestation differs across B/C/D")

        population = payload.get("population") or []
        by_id = require_unique_population(population)
        population_ids = set(by_id)
        if int((payload.get("selection") or {}).get("population_n", -1)) != len(population_ids):
            raise HRStage2IntegrityError(
                f"arm {arm} selection population_n differs from frozen population"
            )
        if common_population_ids is None:
            common_population_ids = population_ids
        elif population_ids != common_population_ids:
            raise HRStage2IntegrityError(
                "B/C/D frozen populations are not identical"
            )

        selection_sets = _verify_selection(payload["selection"])
        if common_champion_ids is None:
            common_champion_ids = selection_sets["champion_ids"]
        elif selection_sets["champion_ids"] != common_champion_ids:
            raise HRStage2IntegrityError(
                "champion frozen selection changed across B/C/D"
            )

        verified[arm] = {
            "freeze": freeze,
            "population_by_id": by_id,
            "selection_sets": selection_sets,
        }

    recomputed_set_sha = deterministic_sha256(frozen_hashes)
    if stage1_bundle.get("freeze_set_sha256") != recomputed_set_sha:
        raise HRStage2IntegrityError(
            "Stage-1 freeze_set_sha256 no longer matches B/C/D freeze hashes"
        )

    outer_venue = stage1_bundle.get("venue_map_attestation") or {}
    outer_venue_compact = {
        "row_count": outer_venue.get("row_count"),
        "sha256": outer_venue.get("sha256"),
    }
    if outer_venue_compact != common_venue_attestation:
        raise HRStage2IntegrityError(
            "outer Stage-1 venue-map attestation differs from arm freezes"
        )

    # Base contact-state support gate is Arm B by locked prereg.
    b_population = verified["B"]["population_by_id"]
    b_support_count = sum(
        1 for row in b_population.values() if bool(row.get("supported"))
    )
    b_metadata_support = (
        verified["B"]["freeze"]["payload"]["metadata"]
        .get("coverage", {})
        .get("supported_n")
    )
    if b_metadata_support != b_support_count:
        raise HRStage2IntegrityError(
            "Arm-B support count differs between frozen population and metadata"
        )
    bundle_b_support = (
        (stage1_bundle.get("coverage") or {})
        .get("per_arm_supported_n", {})
        .get("B")
    )
    if bundle_b_support != b_support_count:
        raise HRStage2IntegrityError(
            "Arm-B support count differs between frozen bundle and arm payload"
        )

    return {
        "arms": verified,
        "freeze_hashes": frozen_hashes,
        "freeze_set_sha256": recomputed_set_sha,
        "bundle_sha256": embedded_bundle_sha,
        "runner_code_sha": common_runner_code_sha,
        "canonical_artifact_identity": common_canonical_identity,
        "source_artifact_identity": common_source_identity,
        "venue_map_attestation": outer_venue,
        "population_ids": common_population_ids,
        "b_support_count": b_support_count,
    }


def _validate_evaluation_rows(evaluation_rows, verified_stage1):
    rows = list(evaluation_rows)
    by_id = require_unique_population(rows)
    if set(by_id) != verified_stage1["population_ids"]:
        missing = sorted(
            verified_stage1["population_ids"] - set(by_id),
            key=repr,
        )
        extra = sorted(set(by_id) - verified_stage1["population_ids"], key=repr)
        raise HRStage2IntegrityError(
            "outcome population differs from Stage-1 frozen population: "
            f"missing={missing[:3]!r} extra={extra[:3]!r}"
        )

    frozen_b = verified_stage1["arms"]["B"]["population_by_id"]

    for cid, row in by_id.items():
        if row.get("prop_type") != "home_run":
            raise HRStage2IntegrityError(
                f"evaluation contains non-home_run candidate {cid!r}"
            )
        if row.get("outcome") not in (0, 1):
            raise HRStage2IntegrityError(
                f"evaluation outcome must be binary for {cid!r}"
            )

        frozen = frozen_b[cid]
        current = _finite_probability(
            row.get("predicted_prob"),
            f"predicted_prob for {cid!r}",
        )
        if current != float(frozen["current_prob"]):
            raise HRStage2IntegrityError(
                f"champion probability changed after Stage-1 freeze for {cid!r}"
            )
        if row.get("team") != frozen.get("team"):
            raise HRStage2IntegrityError(
                f"team changed after Stage-1 freeze for {cid!r}"
            )

    return by_id


def _hit_rate(ids, eval_by_id):
    if not ids:
        return None
    return sum(int(eval_by_id[cid]["outcome"]) for cid in ids) / len(ids)


def _group_value(axis, cid, frozen_by_id):
    row = frozen_by_id[cid]
    if axis == "player":
        return row["player_id"]
    if axis == "team":
        return row["team"]
    if axis == "park":
        return row["venue_id"]
    if axis == "month":
        return str(row["date"])[:7]
    raise HRStage2IntegrityError(f"unknown robustness axis {axis!r}")


def robustness_removal_audit(selection, eval_by_id, frozen_by_id):
    """Exact preregistered largest-contributor removal audit."""
    added = [tuple(cid) for cid in selection["added_ids"]]
    removed = [tuple(cid) for cid in selection["removed_ids"]]

    if not added or not removed:
        return {
            "full_delta": None,
            "axes": {
                axis: {
                    "status": "dependency_unresolvable",
                    "reason": "observed added or removed set is empty",
                    "stop_triggered": True,
                }
                for axis in ROBUSTNESS_AXES
            },
        }

    full_delta = _hit_rate(added, eval_by_id) - _hit_rate(removed, eval_by_id)
    axes = {}

    for axis in ROBUSTNESS_AXES:
        groups = sorted(
            {
                _group_value(axis, cid, frozen_by_id)
                for cid in added + removed
            },
            key=lambda value: repr(value),
        )

        removals = []
        unresolved = []
        for group in groups:
            added_without = [
                cid for cid in added
                if _group_value(axis, cid, frozen_by_id) != group
            ]
            removed_without = [
                cid for cid in removed
                if _group_value(axis, cid, frozen_by_id) != group
            ]

            if not added_without or not removed_without:
                unresolved.append(group)
                continue

            delta_without = (
                _hit_rate(added_without, eval_by_id)
                - _hit_rate(removed_without, eval_by_id)
            )
            impact = full_delta - delta_without
            removals.append({
                "group": group,
                "added_n_without": len(added_without),
                "removed_n_without": len(removed_without),
                "delta_without": delta_without,
                "impact": impact,
            })

        if unresolved:
            axes[axis] = {
                "status": "dependency_unresolvable",
                "reason": "single-group removal empties added or removed side",
                "unresolvable_groups": unresolved,
                "removals": removals,
                "stop_triggered": True,
            }
            continue

        positive = [entry for entry in removals if entry["impact"] > 0]
        positive.sort(key=lambda entry: (-entry["impact"], repr(entry["group"])))
        largest = positive[0] if positive else None
        sign_flip = bool(
            full_delta > 0
            and largest is not None
            and largest["delta_without"] <= 0
        )

        axes[axis] = {
            "status": "resolved",
            "largest_positive_contributor": largest,
            "has_positive_contributor": largest is not None,
            "sign_flip_to_nonpositive": sign_flip,
            "stop_triggered": sign_flip,
            "removals": removals,
        }

    return {
        "full_delta": full_delta,
        "axes": axes,
    }


def _band_label(probability):
    p = _finite_probability(probability, "frozen champion probability")
    for lo, hi, inclusive_hi, label in PROBABILITY_BANDS:
        if p >= lo and (p <= hi if inclusive_hi else p < hi):
            return label
    raise HRStage2IntegrityError(
        f"champion probability {p!r} did not fit locked bands"
    )


def _side_summary(ids, eval_by_id):
    ids = list(ids)
    hits = sum(int(eval_by_id[cid]["outcome"]) for cid in ids)
    return {
        "n": len(ids),
        "hits": hits,
        "misses": len(ids) - hits,
        "hit_rate": hits / len(ids) if ids else None,
    }


def stability_tables(selection, eval_by_id, frozen_by_id):
    """Descriptive-only month and champion-probability-band summaries."""
    champion_ids = [tuple(cid) for cid in selection["champion_ids"]]
    challenger_ids = [tuple(cid) for cid in selection["challenger_ids"]]

    months = sorted({
        str(frozen_by_id[cid]["date"])[:7]
        for cid in champion_ids + challenger_ids
    })
    month_rows = []
    for month in months:
        champion = [
            cid for cid in champion_ids
            if str(frozen_by_id[cid]["date"])[:7] == month
        ]
        challenger = [
            cid for cid in challenger_ids
            if str(frozen_by_id[cid]["date"])[:7] == month
        ]
        champ = _side_summary(champion, eval_by_id)
        chal = _side_summary(challenger, eval_by_id)
        month_rows.append({
            "month": month,
            "champion": champ,
            "challenger": chal,
            "hit_rate_delta": (
                chal["hit_rate"] - champ["hit_rate"]
                if chal["hit_rate"] is not None and champ["hit_rate"] is not None
                else None
            ),
            "descriptive_only": True,
        })

    band_rows = []
    for _, _, _, label in PROBABILITY_BANDS:
        champion = [
            cid for cid in champion_ids
            if _band_label(frozen_by_id[cid]["current_prob"]) == label
        ]
        challenger = [
            cid for cid in challenger_ids
            if _band_label(frozen_by_id[cid]["current_prob"]) == label
        ]
        champ = _side_summary(champion, eval_by_id)
        chal = _side_summary(challenger, eval_by_id)
        band_rows.append({
            "band": label,
            "champion": champ,
            "challenger": chal,
            "hit_rate_delta": (
                chal["hit_rate"] - champ["hit_rate"]
                if chal["hit_rate"] is not None and champ["hit_rate"] is not None
                else None
            ),
            "descriptive_only": True,
        })

    return {
        "month": month_rows,
        "champion_probability_band": band_rows,
    }


def _arm_survival(anatomy, bootstrap, robustness, b_support_count):
    failures = []

    if b_support_count < MIN_BASE_TRAILING_SUPPORT:
        failures.append(
            f"Arm-B holdout support {b_support_count} < {MIN_BASE_TRAILING_SUPPORT}"
        )

    if anatomy["added_n"] == 0 or anatomy["removed_n"] == 0:
        failures.append("observed added or removed set is empty")

    if not bootstrap.get("changed_estimable"):
        failures.append(
            f"changed-set bootstrap valid replicates "
            f"{bootstrap.get('changed_valid_replicates')} < "
            f"{MIN_CHANGED_VALID_REPLICATES}"
        )

    changed_ci = bootstrap.get("added_minus_removed_ci95")
    if changed_ci is None:
        failures.append("added-minus-removed CI unavailable")
    elif changed_ci[0] <= 0:
        failures.append(
            f"added-minus-removed 95% CI is not strictly positive: {changed_ci!r}"
        )

    for axis in ROBUSTNESS_AXES:
        axis_report = robustness["axes"][axis]
        if axis_report.get("status") != "resolved":
            failures.append(f"{axis} robustness axis is dependency-unresolvable")
        elif axis_report.get("sign_flip_to_nonpositive"):
            failures.append(
                f"{axis} largest-contributor removal flips effect non-positive"
            )

    return {
        "earns_continuation": not failures,
        "failures": failures,
    }


def evaluate_hr_stage2(evaluation_rows, stage1_bundle):
    """Reveal outcomes once against already-frozen B/C/D selections."""
    # Critical ordering: verify immutable Stage-1 bytes before reading outcomes.
    verified = verify_stage1_bundle(stage1_bundle)
    eval_by_id = _validate_evaluation_rows(evaluation_rows, verified)

    arm_reports = {}
    survivors = []

    for arm in ARMS:
        arm_verified = verified["arms"][arm]
        freeze = arm_verified["freeze"]
        payload = freeze["payload"]
        selection = payload["selection"]
        frozen_by_id = arm_verified["population_by_id"]

        anatomy = selection_anatomy(list(eval_by_id.values()), selection)

        # selection_anatomy independently recomputes identity sets after truth
        # joins. They must still equal the pre-outcome frozen anatomy exactly.
        for key in ("overlap_ids", "removed_ids", "added_ids"):
            if [tuple(cid) for cid in anatomy[key]] != [
                tuple(cid) for cid in selection[key]
            ]:
                raise HRStage2IntegrityError(
                    f"arm {arm} {key} changed after outcome join"
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
            verified["b_support_count"],
        )
        if survival["earns_continuation"]:
            survivors.append(arm)

        arm_reports[arm] = {
            "prediction_freeze_sha256": freeze["sha256"],
            "selection_anatomy": anatomy,
            "cluster_bootstrap": bootstrap,
            "robustness": robustness,
            "stability": stability,
            "survival": survival,
        }

    report = {
        "experiment": "hr_contact_state",
        "stage": 2,
        "stage1_bundle_sha256": verified["bundle_sha256"],
        "stage1_freeze_set_sha256": verified["freeze_set_sha256"],
        "stage1_arm_freeze_sha256": verified["freeze_hashes"],
        "runner_code_sha": verified["runner_code_sha"],
        "canonical_artifact_identity": verified["canonical_artifact_identity"],
        "source_artifact_identity": verified["source_artifact_identity"],
        "venue_map_attestation": {
            "row_count": verified["venue_map_attestation"].get("row_count"),
            "sha256": verified["venue_map_attestation"].get("sha256"),
        },
        "holdout_population_n": len(eval_by_id),
        "arm_b_supported_holdout_n": verified["b_support_count"],
        "coverage_gate_minimum": MIN_BASE_TRAILING_SUPPORT,
        "bootstrap": {
            "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED,
            "minimum_valid_changed_replicates": MIN_CHANGED_VALID_REPLICATES,
        },
        "arms": arm_reports,
        "initial_survivors": survivors,
        "arm_e_permitted": "D" in survivors,
        "thread_closes_without_e": not survivors,
        "historical_evidence_only": True,
        "production_promotion_authorized": False,
    }
    report["evaluation_report_sha256"] = deterministic_sha256(report)
    return report


def write_immutable_evaluation_report(path, report):
    """Create one report exactly once; never overwrite a revealed holdout."""
    if os.path.exists(path):
        raise FileExistsError(
            f"refusing to overwrite immutable HR evaluation report at {path!r}"
        )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    logical = dict(report)
    embedded = logical.pop("evaluation_report_sha256", None)
    if embedded != deterministic_sha256(logical):
        raise HRStage2IntegrityError(
            "evaluation_report_sha256 does not match report content"
        )

    payload = (
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

    return {
        "path": path,
        "byte_sha256": __import__("hashlib").sha256(payload).hexdigest(),
        "logical_sha256": deterministic_sha256(report),
        "bytes": len(payload),
    }
