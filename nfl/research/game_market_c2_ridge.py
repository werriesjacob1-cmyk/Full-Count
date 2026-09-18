"""Minimal, dependency-free ridge regression used by the NFL game-market C2 challenger.

This module intentionally reimplements nothing more than closed-form ridge
regression on standardized features with a fixed, predeclared L2 penalty. It
has no dependency on numpy/scipy/scikit-learn so it can run inside the NFL
CI job, which installs only `nfl/requirements-nfl.txt` plus `requests`.

Ridge form: minimize sum((y - mean(y) - Z @ beta)^2) + lambda * sum(beta^2),
where Z is the design matrix of features standardized (z-score) using
*training-only* mean/std, and the intercept is fit separately as mean(y) so
it is never penalized. This is a standard, textbook formulation chosen for
auditability over an opaque model class.
"""
from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence


class RidgeError(ValueError):
    """Raised when a ridge fit or prediction request is malformed."""


def _feature_stats(
    rows: Sequence[Mapping[str, float]], feature_names: Sequence[str]
) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for name in feature_names:
        values = [float(row[name]) for row in rows]
        mean = statistics.fmean(values)
        if len(values) > 1:
            std = statistics.pstdev(values)
        else:
            std = 0.0
        stats[name] = {"mean": mean, "std": std if std > 1e-12 else 1.0}
    return stats


def _standardize_row(
    row: Mapping[str, float], feature_names: Sequence[str], stats: Mapping[str, Mapping[str, float]]
) -> list[float]:
    return [
        (float(row[name]) - stats[name]["mean"]) / stats[name]["std"]
        for name in feature_names
    ]


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve `matrix @ x = vector` via Gauss-Jordan elimination with partial pivoting."""
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < 1e-12:
            raise RidgeError("singular design matrix; increase ridge penalty or drop a feature")
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]
        pivot = augmented[col][col]
        augmented[col] = [value / pivot for value in augmented[col]]
        for row_index in range(n):
            if row_index == col:
                continue
            factor = augmented[row_index][col]
            if factor == 0.0:
                continue
            augmented[row_index] = [
                a - factor * b for a, b in zip(augmented[row_index], augmented[col])
            ]
    return [augmented[i][n] for i in range(n)]


def fit_ridge(
    rows: Sequence[Mapping[str, float]],
    *,
    feature_names: Sequence[str],
    target_key: str,
    ridge_lambda: float,
) -> dict:
    """Fit a standardized-feature ridge regression on `rows` only.

    `rows` must already be the exact intended fit population (e.g. a fixed
    development partition) -- this function performs no partitioning itself.
    """
    if not feature_names:
        raise RidgeError("feature_names must be non-empty")
    if isinstance(ridge_lambda, bool) or not isinstance(ridge_lambda, (int, float)) or ridge_lambda < 0:
        raise RidgeError("ridge_lambda must be a non-negative number")
    if len(rows) <= len(feature_names):
        raise RidgeError("fit requires more rows than features")

    stats = _feature_stats(rows, feature_names)
    targets = [float(row[target_key]) for row in rows]
    target_mean = statistics.fmean(targets)
    centered_targets = [value - target_mean for value in targets]

    design = [_standardize_row(row, feature_names, stats) for row in rows]
    n_features = len(feature_names)

    # Normal equations: (Z^T Z + lambda * I) beta = Z^T y_centered
    gram: list[list[float]] = [[0.0] * n_features for _ in range(n_features)]
    moment: list[float] = [0.0] * n_features
    for z_row, y_value in zip(design, centered_targets):
        for i in range(n_features):
            moment[i] += z_row[i] * y_value
            for j in range(n_features):
                gram[i][j] += z_row[i] * z_row[j]
    for i in range(n_features):
        gram[i][i] += ridge_lambda

    coefficients = _solve_linear_system(gram, moment)

    fitted_residuals = []
    for z_row, y_value in zip(design, centered_targets):
        prediction = sum(c * z for c, z in zip(coefficients, z_row))
        fitted_residuals.append(y_value - prediction)
    training_mae = statistics.fmean(abs(value) for value in fitted_residuals)

    return {
        "feature_names": list(feature_names),
        "target_key": target_key,
        "ridge_lambda": ridge_lambda,
        "feature_stats": stats,
        "target_mean": target_mean,
        "coefficients": dict(zip(feature_names, coefficients)),
        "fit_rows": len(rows),
        "training_mae": training_mae,
    }


def predict_ridge(row: Mapping[str, float], model: Mapping) -> float:
    feature_names = model["feature_names"]
    stats = model["feature_stats"]
    coefficients = model["coefficients"]
    total = float(model["target_mean"])
    for name in feature_names:
        z = (float(row[name]) - stats[name]["mean"]) / stats[name]["std"]
        total += coefficients[name] * z
    if not math.isfinite(total):
        raise RidgeError("ridge prediction is not finite")
    return total
