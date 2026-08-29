#!/usr/bin/env python3
"""Locked champion-anchored logistic-offset estimator for HR challengers.

Pure numerical layer. No file I/O, no holdout loading, no network access.

Model:
    logit(p_challenger_i) = logit(p_champion_i) + x_i^T beta

There is deliberately NO fitted intercept. beta=0 must reproduce champion
exactly. Objective and optimizer semantics match PREREG_HR_EXECUTION_V2.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize

PROB_EPS = 1e-6
MAXITER = 1000
FTOL = 1e-12
GTOL = 1e-8
MAXLS = 50
MAX_FINAL_ABS_GRAD = 1e-5


class HREstimatorIntegrityError(ValueError):
    """Fail-closed training/prediction contract violation."""


def _as_vector(values, label):
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        raise HREstimatorIntegrityError(
            f"{label} must be a non-empty 1D vector"
        )
    if not np.isfinite(arr).all():
        raise HREstimatorIntegrityError(f"{label} contains non-finite values")
    return arr


def _as_matrix(values, label):
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise HREstimatorIntegrityError(
            f"{label} must be a non-empty 2D matrix"
        )
    if not np.isfinite(arr).all():
        raise HREstimatorIntegrityError(f"{label} contains non-finite values")
    return arr


def clip_probabilities(probabilities):
    probs = _as_vector(probabilities, "champion probabilities")
    if ((probs < 0) | (probs > 1)).any():
        raise HREstimatorIntegrityError(
            "champion probabilities must lie in [0,1]"
        )
    return np.clip(probs, PROB_EPS, 1.0 - PROB_EPS)


def logit(probabilities):
    probs = clip_probabilities(probabilities)
    return np.log(probs / (1.0 - probs))


def sigmoid(values):
    values = np.asarray(values, dtype=float)
    # Stable branch form avoids overflow at large absolute logits.
    out = np.empty_like(values, dtype=float)
    pos = values >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-values[pos]))
    exp_values = np.exp(values[~pos])
    out[~pos] = exp_values / (1.0 + exp_values)
    return out


def fit_standardizer(features):
    """Training-only population mean/SD (ddof=0), locked by prereg."""
    x = _as_matrix(features, "features")
    means = np.mean(x, axis=0)
    stds = np.std(x, axis=0, ddof=0)
    if not np.isfinite(means).all() or not np.isfinite(stds).all():
        raise HREstimatorIntegrityError(
            "training standardizer produced non-finite parameters"
        )
    if (stds <= 0).any():
        bad = np.where(stds <= 0)[0].tolist()
        raise HREstimatorIntegrityError(
            f"zero-variance training feature column(s): {bad}"
        )
    return {
        "mean": means,
        "std": stds,
    }


def apply_standardizer(features, standardizer):
    x = _as_matrix(features, "features")
    means = np.asarray(standardizer.get("mean"), dtype=float)
    stds = np.asarray(standardizer.get("std"), dtype=float)
    if means.ndim != 1 or stds.ndim != 1 or means.shape != stds.shape:
        raise HREstimatorIntegrityError(
            "standardizer mean/std must be same-length 1D vectors"
        )
    if x.shape[1] != means.size:
        raise HREstimatorIntegrityError(
            f"feature width {x.shape[1]} != standardizer width {means.size}"
        )
    if not np.isfinite(means).all() or not np.isfinite(stds).all():
        raise HREstimatorIntegrityError(
            "standardizer contains non-finite values"
        )
    if (stds <= 0).any():
        raise HREstimatorIntegrityError(
            "standardizer contains non-positive standard deviation"
        )
    out = (x - means) / stds
    if not np.isfinite(out).all():
        raise HREstimatorIntegrityError(
            "standardized feature matrix contains non-finite values"
        )
    return out


def objective_and_gradient(beta, champion_logits, standardized_features, outcomes):
    """Summed BCE + 0.5*||beta||^2 and exact analytic gradient."""
    beta = _as_vector(beta, "beta")
    offsets = _as_vector(champion_logits, "champion logits")
    x = _as_matrix(standardized_features, "standardized features")
    y = _as_vector(outcomes, "outcomes")

    if x.shape[0] != offsets.size or x.shape[0] != y.size:
        raise HREstimatorIntegrityError(
            "offset/features/outcomes row counts differ"
        )
    if x.shape[1] != beta.size:
        raise HREstimatorIntegrityError(
            "beta width differs from feature width"
        )
    if not np.isin(y, (0.0, 1.0)).all():
        raise HREstimatorIntegrityError(
            "outcomes must be binary 0/1"
        )

    eta = offsets + x @ beta
    # BCE for logits: log(1+exp(eta)) - y*eta, stably.
    loss = float(
        np.sum(np.logaddexp(0.0, eta) - y * eta)
        + 0.5 * np.dot(beta, beta)
    )
    p = sigmoid(eta)
    gradient = x.T @ (p - y) + beta

    if not math.isfinite(loss) or not np.isfinite(gradient).all():
        raise HREstimatorIntegrityError(
            "objective/gradient became non-finite"
        )
    return loss, gradient


def fit_offset_logistic(champion_probabilities, features, outcomes):
    """Fit exactly the preregistered no-intercept L-BFGS-B estimator."""
    champion = clip_probabilities(champion_probabilities)
    x_raw = _as_matrix(features, "features")
    y = _as_vector(outcomes, "outcomes")

    if champion.size != x_raw.shape[0] or champion.size != y.size:
        raise HREstimatorIntegrityError(
            "champion/features/outcomes row counts differ"
        )
    if not np.isin(y, (0.0, 1.0)).all():
        raise HREstimatorIntegrityError("outcomes must be binary 0/1")

    standardizer = fit_standardizer(x_raw)
    x = apply_standardizer(x_raw, standardizer)
    offsets = logit(champion)
    x0 = np.zeros(x.shape[1], dtype=float)

    def fun(beta):
        loss, gradient = objective_and_gradient(beta, offsets, x, y)
        return loss, gradient

    result = minimize(
        fun,
        x0,
        method="L-BFGS-B",
        jac=True,
        options={
            "maxiter": MAXITER,
            "ftol": FTOL,
            "gtol": GTOL,
            "maxls": MAXLS,
        },
    )

    beta = np.asarray(result.x, dtype=float)
    final_loss, final_gradient = objective_and_gradient(
        beta,
        offsets,
        x,
        y,
    )
    max_abs_grad = float(np.max(np.abs(final_gradient)))

    if not bool(result.success):
        raise HREstimatorIntegrityError(
            f"L-BFGS-B did not converge: status={result.status} "
            f"message={result.message!s}"
        )
    if not np.isfinite(beta).all():
        raise HREstimatorIntegrityError(
            "fitted beta contains non-finite values"
        )
    if not math.isfinite(final_loss):
        raise HREstimatorIntegrityError(
            "final objective is non-finite"
        )
    if max_abs_grad > MAX_FINAL_ABS_GRAD:
        raise HREstimatorIntegrityError(
            f"final max absolute analytic gradient {max_abs_grad:.6g} "
            f"> locked {MAX_FINAL_ABS_GRAD}"
        )

    return {
        "beta": beta,
        "standardizer": standardizer,
        "optimizer": {
            "method": "L-BFGS-B",
            "success": True,
            "status": int(result.status),
            "message": str(result.message),
            "nit": int(result.nit),
            "nfev": int(result.nfev),
            "njev": int(getattr(result, "njev", result.nfev)),
            "final_objective": final_loss,
            "max_abs_gradient": max_abs_grad,
            "maxiter": MAXITER,
            "ftol": FTOL,
            "gtol": GTOL,
            "maxls": MAXLS,
            "l2_half_coefficient": 0.5,
            "intercept": None,
        },
    }


def predict_supported(champion_probabilities, features, fitted):
    """Predict supported rows with the frozen training transform/coefficients."""
    champion = clip_probabilities(champion_probabilities)
    x = apply_standardizer(features, fitted["standardizer"])
    beta = _as_vector(fitted["beta"], "beta")
    if x.shape[0] != champion.size:
        raise HREstimatorIntegrityError(
            "champion/features row counts differ at prediction"
        )
    if x.shape[1] != beta.size:
        raise HREstimatorIntegrityError(
            "prediction feature width differs from fitted beta width"
        )
    eta = logit(champion) + x @ beta
    p = sigmoid(eta)
    if not np.isfinite(p).all() or ((p <= 0) | (p >= 1)).any():
        raise HREstimatorIntegrityError(
            "challenger probability is non-finite/outside (0,1)"
        )
    return p


def predict_with_champion_fallback(
    champion_probabilities,
    feature_rows,
    supported_mask,
    fitted,
):
    """Full-population prediction: unsupported rows are EXACT champion."""
    champion = clip_probabilities(champion_probabilities)
    mask = np.asarray(supported_mask, dtype=bool)
    if mask.ndim != 1 or mask.size != champion.size:
        raise HREstimatorIntegrityError(
            "supported_mask must be a 1D vector matching population size"
        )

    x_all = _as_matrix(feature_rows, "feature rows")
    if x_all.shape[0] != champion.size:
        raise HREstimatorIntegrityError(
            "feature rows do not match population size"
        )

    result = champion.copy()
    if mask.any():
        result[mask] = predict_supported(
            champion[mask],
            x_all[mask],
            fitted,
        )
    # Exact copy, not approximate equality, for unsupported rows.
    if not np.array_equal(result[~mask], champion[~mask]):
        raise HREstimatorIntegrityError(
            "unsupported-row champion fallback was not exact"
        )
    return result
