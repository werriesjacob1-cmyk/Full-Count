#!/usr/bin/env python3
"""Pure point-in-time HR contact-state feature extraction.

No network access lives in this module. The caller supplies the already-bound
canonical Statcast source frame. Every feature for candidate date D is computed
from the candidate batter's rows with game_date < D only.

The tracked-swing universe follows the locked HR prereg: bat_speed non-null,
last 100 tracked swings, minimum 30 non-null observations per required feature.
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd


RETAINED_SOURCE_COLUMNS = (
    "game_date",
    "batter",
    "bat_speed",
    "swing_length",
    "attack_angle",
    "swing_path_tilt",
    "attack_direction",
    "hit_distance_sc",
)

GEOMETRY_COLUMNS = (
    "attack_angle",
    "swing_length",
    "swing_path_tilt",
    "attack_direction",
)


class ContactStateIntegrityError(ValueError):
    """Source/schema/timing defect that must stop the HR experiment."""


def _date_string(value):
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception as exc:
        raise ContactStateIntegrityError(f"invalid candidate date: {value!r}") from exc


def _finite_values(series):
    values = []
    for value in pd.to_numeric(series, errors="coerce").tolist():
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _mean(values):
    return sum(values) / len(values) if values else None


def _linear_percentile(values, q):
    """Deterministic NumPy-style linear percentile without version defaults."""
    if not values:
        return None
    if not 0 <= q <= 1:
        raise ValueError("q must be in [0,1]")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    weight = pos - lo
    return xs[lo] * (1 - weight) + xs[hi] * weight


def validate_source_frame(frame):
    if frame is None or not isinstance(frame, pd.DataFrame):
        raise ContactStateIntegrityError("source must be a pandas DataFrame")
    missing = [column for column in RETAINED_SOURCE_COLUMNS if column not in frame.columns]
    if missing:
        raise ContactStateIntegrityError(
            "canonical Statcast source missing preregistered retained columns: "
            + ", ".join(missing)
        )
    if frame.empty:
        raise ContactStateIntegrityError("canonical Statcast source is empty")


def extract_contact_state(
    frame,
    batter_id,
    candidate_date,
    max_tracked_swings=100,
    min_feature_observations=30,
):
    """Return locked trailing contact state for one batter/date.

    Ordering is by game_date, game_pk (when present), at_bat_number (when
    present), then immutable artifact row order. The final tie-break matters
    only if the 100-swing cutoff falls inside one at-bat; using artifact order
    makes that choice deterministic without inventing a pitch sequence field
    the pinned source did not retain.
    """
    validate_source_frame(frame)

    if not isinstance(max_tracked_swings, int) or max_tracked_swings <= 0:
        raise ContactStateIntegrityError("max_tracked_swings must be a positive integer")
    if not isinstance(min_feature_observations, int) or min_feature_observations <= 0:
        raise ContactStateIntegrityError(
            "min_feature_observations must be a positive integer"
        )

    try:
        batter_id = int(batter_id)
    except (TypeError, ValueError) as exc:
        raise ContactStateIntegrityError(f"invalid batter id: {batter_id!r}") from exc

    asof = _date_string(candidate_date)

    working = frame.copy()
    working["_artifact_order"] = range(len(working))
    parsed_dates = pd.to_datetime(working["game_date"], errors="coerce")
    if parsed_dates.isna().any():
        bad = int(parsed_dates.isna().sum())
        raise ContactStateIntegrityError(
            f"canonical Statcast source has {bad} unparseable game_date rows"
        )
    working["_game_date_norm"] = parsed_dates.dt.strftime("%Y-%m-%d")

    batter_values = pd.to_numeric(working["batter"], errors="coerce")
    working = working[
        (batter_values == batter_id)
        & (working["_game_date_norm"] < asof)
        & working["bat_speed"].notna()
    ].copy()

    sort_columns = ["_game_date_norm"]
    if "game_pk" in working.columns:
        sort_columns.append("game_pk")
    if "at_bat_number" in working.columns:
        sort_columns.append("at_bat_number")
    sort_columns.append("_artifact_order")
    working = working.sort_values(sort_columns, kind="mergesort")
    window = working.tail(max_tracked_swings).copy()

    feature_columns = ("bat_speed",) + GEOMETRY_COLUMNS + ("hit_distance_sc",)
    counts = {}
    raw_values = {}
    for column in feature_columns:
        values = _finite_values(window[column])
        raw_values[column] = values
        counts[column] = len(values)

    def available_mean(column):
        values = raw_values[column]
        return _mean(values) if len(values) >= min_feature_observations else None

    bat_values = raw_values["bat_speed"]
    bat_supported = len(bat_values) >= min_feature_observations
    geometry_supported = all(
        counts[column] >= min_feature_observations
        for column in GEOMETRY_COLUMNS
    )
    d_supported = bat_supported and geometry_supported
    hit_distance_supported = counts["hit_distance_sc"] >= min_feature_observations

    features = {
        "bat_speed_mean": _mean(bat_values) if bat_supported else None,
        "bat_speed_p90": (
            _linear_percentile(bat_values, 0.90)
            if bat_supported
            else None
        ),
        "attack_angle_mean": available_mean("attack_angle"),
        "swing_length_mean": available_mean("swing_length"),
        "swing_path_tilt_mean": available_mean("swing_path_tilt"),
        "attack_direction_mean": available_mean("attack_direction"),
        "hit_distance_sc_mean": available_mean("hit_distance_sc"),
    }

    return {
        "batter_id": batter_id,
        "candidate_date": asof,
        "tracked_window_n": len(window),
        "window_first_game_date": (
            window["_game_date_norm"].iloc[0] if len(window) else None
        ),
        "window_last_game_date": (
            window["_game_date_norm"].iloc[-1] if len(window) else None
        ),
        "feature_counts": counts,
        "features": features,
        "support": {
            "B": bat_supported,
            "C": geometry_supported,
            "D": d_supported,
            "E": d_supported and hit_distance_supported,
        },
    }
