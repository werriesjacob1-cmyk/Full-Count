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
from bisect import bisect_left
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


class ContactStateIndex:
    """One-time indexed view of the immutable Statcast source.

    The original scalar extractor copied and reparsed the full multi-million-row
    source for every batter/date. This index preserves identical semantics while
    preprocessing the source exactly once, then slicing only one batter's
    already-sorted tracked swings per candidate.
    """

    def __init__(self, frame):
        validate_source_frame(frame)

        columns = list(RETAINED_SOURCE_COLUMNS)
        for optional in ("game_pk", "at_bat_number"):
            if optional in frame.columns and optional not in columns:
                columns.append(optional)

        working = frame.loc[:, columns].copy()
        working["_artifact_order"] = range(len(frame))

        parsed_dates = pd.to_datetime(working["game_date"], errors="coerce")
        if parsed_dates.isna().any():
            bad = int(parsed_dates.isna().sum())
            raise ContactStateIntegrityError(
                f"canonical Statcast source has {bad} unparseable game_date rows"
            )
        working["_game_date_norm"] = parsed_dates.dt.strftime("%Y-%m-%d")

        # Preserve the preregistered tracked-swing definition exactly:
        # bat_speed non-null. Non-numeric/non-finite values can still occupy
        # one of the last 100 tracked rows but do not count toward feature
        # support, matching the original scalar implementation.
        working = working[working["bat_speed"].notna()].copy()
        working["_batter_numeric"] = pd.to_numeric(
            working["batter"],
            errors="coerce",
        )

        self._by_batter = {}
        self.source_row_count = int(len(frame))
        self.tracked_row_count = int(len(working))

        for batter_value, group in working.groupby(
            "_batter_numeric",
            sort=False,
            dropna=True,
        ):
            try:
                batter_id = int(batter_value)
            except (TypeError, ValueError):
                continue

            sort_columns = ["_game_date_norm"]
            if "game_pk" in group.columns:
                sort_columns.append("game_pk")
            if "at_bat_number" in group.columns:
                sort_columns.append("at_bat_number")
            sort_columns.append("_artifact_order")

            ordered = group.sort_values(
                sort_columns,
                kind="mergesort",
            ).reset_index(drop=True)
            self._by_batter[batter_id] = {
                "frame": ordered,
                "dates": ordered["_game_date_norm"].tolist(),
            }

    def extract(
        self,
        batter_id,
        candidate_date,
        max_tracked_swings=100,
        min_feature_observations=30,
    ):
        if not isinstance(max_tracked_swings, int) or max_tracked_swings <= 0:
            raise ContactStateIntegrityError(
                "max_tracked_swings must be a positive integer"
            )
        if not isinstance(min_feature_observations, int) or min_feature_observations <= 0:
            raise ContactStateIntegrityError(
                "min_feature_observations must be a positive integer"
            )

        try:
            batter_id = int(batter_id)
        except (TypeError, ValueError) as exc:
            raise ContactStateIntegrityError(
                f"invalid batter id: {batter_id!r}"
            ) from exc

        asof = _date_string(candidate_date)
        indexed = self._by_batter.get(batter_id)
        if indexed is None:
            window = pd.DataFrame(columns=RETAINED_SOURCE_COLUMNS + ("_game_date_norm",))
        else:
            ordered = indexed["frame"]
            # Strict game_date < D. bisect_left returns the first row whose
            # normalized date is >= D, so same-day/future rows are excluded
            # before the last-100 slice is taken.
            stop = bisect_left(indexed["dates"], asof)
            start = max(0, stop - max_tracked_swings)
            window = ordered.iloc[start:stop]

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
        hit_distance_supported = (
            counts["hit_distance_sc"] >= min_feature_observations
        )

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


def ensure_contact_state_index(source):
    if isinstance(source, ContactStateIndex):
        return source
    return ContactStateIndex(source)


def extract_contact_state(
    frame,
    batter_id,
    candidate_date,
    max_tracked_swings=100,
    min_feature_observations=30,
):
    """Backward-compatible scalar API.

    Experiment runners should build/reuse ContactStateIndex once. This wrapper
    exists for focused callers/tests and preserves the old function surface.
    """
    return ContactStateIndex(frame).extract(
        batter_id,
        candidate_date,
        max_tracked_swings=max_tracked_swings,
        min_feature_observations=min_feature_observations,
    )
