#!/usr/bin/env python3
"""Decisive PA/opportunity replication scaffold.

This module reuses the existing joint empirical PA challenger and replaces only
the evaluation plumbing that made the 2026-08-25 closure non-decisive:
aggregate holdout N and dropped unsupported rows.

It intentionally exposes prediction and evaluation as separate stages. The
prediction stage rejects any row carrying an outcome key; the evaluation stage
never re-ranks or re-fits anything.
"""
from __future__ import annotations

import math

from backtest.experiment_primitives import (
    ExperimentIntegrityError,
    build_prediction_freeze,
    candidate_identity,
    paired_game_cluster_bootstrap,
    require_unique_population,
    select_floor_matched_per_date,
    selection_anatomy,
)
from backtest.opportunity_decomposition import HITTER_MARKETS
from backtest.pa_opportunity_model import (
    MIN_LINE_PROB,
    dedupe_player_games,
    fit_hit_rate_given_pa,
    fit_pa_distribution,
)
from backtest.residual_challenger_model import (
    MIN_CELL_N,
    challenger_probability_joint,
    fit_joint_pa_distribution,
    joint_key,
)

MARKET = "hits"
TRAIN_END = "2025-12-31"


class PAExperimentIntegrityError(ExperimentIntegrityError):
    """PA-specific research-integrity failure."""


def _date_value(row):
    value = str(row.get("date") or "")
    if len(value) < 10:
        raise PAExperimentIntegrityError(f"missing/invalid date: {value!r}")
    return value[:10]


def _finite_probability(value, label):
    if value is None or isinstance(value, bool):
        raise PAExperimentIntegrityError(f"{label} missing/non-numeric")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise PAExperimentIntegrityError(f"{label} non-numeric: {value!r}") from exc
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise PAExperimentIntegrityError(f"{label} outside [0,1]: {value!r}")
    return value


def fit_training_state(training_rows):
    """Fit exactly the existing joint PA mechanism on <=2025 rows.

    PA distributions use one row per hitter player-game across hitter markets.
    P(hit | PA) uses hits rows only, matching residual_challenger_model.py.
    """
    rows = list(training_rows)
    if not rows:
        raise PAExperimentIntegrityError("no training rows")

    for row in rows:
        if _date_value(row) > TRAIN_END:
            raise PAExperimentIntegrityError(
                f"training row crosses frozen cutoff: {_date_value(row)}"
            )
        if row.get("prop_type") not in HITTER_MARKETS:
            raise PAExperimentIntegrityError(
                f"training row outside hitter-market universe: {row.get('prop_type')!r}"
            )
        if row.get("outcome") not in (0, 1):
            raise PAExperimentIntegrityError("training rows require binary outcomes")

    player_games = dedupe_player_games(rows)
    joint_dist = fit_joint_pa_distribution(player_games, min_cell_n=MIN_CELL_N)
    order_dist = fit_pa_distribution(player_games)
    hit_rate_given_pa = fit_hit_rate_given_pa(rows, MARKET)

    if not order_dist:
        raise PAExperimentIntegrityError("order-only PA distribution is empty")
    if not hit_rate_given_pa:
        raise PAExperimentIntegrityError("P(hit|PA) table is empty")

    return {
        "joint_dist": joint_dist,
        "order_dist": order_dist,
        "hit_rate_given_pa": hit_rate_given_pa,
        "metadata": {
            "market": MARKET,
            "train_end": TRAIN_END,
            "min_cell_n": MIN_CELL_N,
            "min_line_prob": MIN_LINE_PROB,
            "n_training_rows": len(rows),
            "n_training_player_games": len(player_games),
            "n_joint_cells": len(joint_dist),
        },
    }


def build_prediction_stage(masked_holdout_rows, training_state, metadata=None):
    """Build outcome-free holdout predictions + per-date matched selections.

    Input rows must already be restricted to the frozen hits population and
    must NOT carry an outcome key, even with value None.
    """
    source_rows = list(masked_holdout_rows)
    if not source_rows:
        raise PAExperimentIntegrityError("no holdout prediction rows")
    require_unique_population(source_rows)

    predictions = []
    path_counts = {"joint_cell": 0, "order_fallback": 0, "champion_fallback": 0}

    for row in source_rows:
        if "outcome" in row:
            raise PAExperimentIntegrityError(
                f"prediction stage received outcome for {candidate_identity(row)!r}"
            )
        if row.get("prop_type") != MARKET:
            raise PAExperimentIntegrityError(
                f"prediction population contains non-{MARKET} row: "
                f"{candidate_identity(row)!r}"
            )
        if _date_value(row) <= TRAIN_END:
            raise PAExperimentIntegrityError(
                f"holdout row is not after training cutoff: {_date_value(row)}"
            )

        current_prob = _finite_probability(
            row.get("predicted_prob"),
            f"predicted_prob for {candidate_identity(row)!r}",
        )

        key = joint_key(row)
        uses_joint = key is not None and key in training_state["joint_dist"]
        challenger = challenger_probability_joint(
            row,
            training_state["joint_dist"],
            training_state["order_dist"],
            training_state["hit_rate_given_pa"],
        )

        if challenger is None:
            challenger = current_prob
            path = "champion_fallback"
        elif uses_joint:
            path = "joint_cell"
        else:
            path = "order_fallback"

        challenger = _finite_probability(
            challenger,
            f"challenger_prob for {candidate_identity(row)!r}",
        )
        path_counts[path] += 1

        predictions.append({
            "date": row["date"],
            "game_pk": row["game_pk"],
            "player_id": row["player_id"],
            "prop_type": row["prop_type"],
            "line": row["line"],
            "current_prob": current_prob,
            "challenger_prob": challenger,
            "prediction_path": path,
        })

    selection = select_floor_matched_per_date(
        predictions,
        MIN_LINE_PROB,
        champion_score_key="current_prob",
        challenger_score_key="challenger_prob",
    )

    freeze_metadata = dict(training_state["metadata"])
    freeze_metadata.update(metadata or {})
    freeze_metadata["prediction_path_counts"] = path_counts
    freeze_metadata["selection_contract"] = "per_date_floor_matched"

    freeze = build_prediction_freeze(
        predictions,
        selection,
        metadata=freeze_metadata,
    )
    return freeze


def evaluate_frozen_predictions(evaluation_rows, prediction_freeze):
    """Join truth to a frozen population without re-fitting or re-ranking."""
    rows = list(evaluation_rows)
    by_id = require_unique_population(rows)

    frozen_payload = prediction_freeze["payload"]
    frozen_population = frozen_payload["population"]
    frozen_ids = {candidate_identity(row) for row in frozen_population}

    if set(by_id) != frozen_ids:
        missing = sorted(frozen_ids - set(by_id), key=repr)
        extra = sorted(set(by_id) - frozen_ids, key=repr)
        raise PAExperimentIntegrityError(
            f"evaluation population differs from prediction freeze: "
            f"missing={missing[:3]!r} extra={extra[:3]!r}"
        )

    for row in rows:
        if row.get("outcome") not in (0, 1):
            raise PAExperimentIntegrityError(
                f"evaluation requires binary outcome for {candidate_identity(row)!r}"
            )

    selection = frozen_payload["selection"]
    anatomy = selection_anatomy(rows, selection)
    bootstrap = paired_game_cluster_bootstrap(
        rows,
        selection,
        n_replicates=5000,
        seed=20260829,
        min_changed_valid_fraction=0.95,
    )

    return {
        "prediction_freeze_sha256": prediction_freeze["sha256"],
        "selection_anatomy": anatomy,
        "cluster_bootstrap": bootstrap,
    }


def decisive_verdict(evaluation_report):
    """Locked GO/KILL logic from the decisive PA prereg draft."""
    anatomy = evaluation_report["selection_anatomy"]
    bootstrap = evaluation_report["cluster_bootstrap"]

    reasons = []
    if anatomy["realized_winner_delta"] <= 0:
        reasons.append("challenger did not produce more realized winners")
    overall_ci = bootstrap["overall_delta_ci95"]
    if overall_ci is None or overall_ci[0] <= 0:
        reasons.append("overall paired game-cluster CI is not strictly above zero")
    if anatomy["added_minus_removed_hit_rate"] is None:
        reasons.append("added-minus-removed point estimate unavailable")
    elif anatomy["added_minus_removed_hit_rate"] <= 0:
        reasons.append("added-minus-removed point estimate is not positive")

    if bootstrap["changed_estimable"]:
        changed_ci = bootstrap["added_minus_removed_ci95"]
        if changed_ci is None or changed_ci[0] <= 0:
            reasons.append("changed-set paired CI is not strictly above zero")
    else:
        reasons.append("changed-set uncertainty is not robustly estimable")

    return {
        "verdict": "SURVIVES" if not reasons else "KILL_CLOSE",
        "reasons": reasons or ["all preregistered continuation gates passed"],
    }
