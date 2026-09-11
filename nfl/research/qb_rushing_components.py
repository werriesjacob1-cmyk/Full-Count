#!/usr/bin/env python3
"""QB rushing process decomposition from nflverse play-by-play.

NFL box-score rushing is not one process for quarterbacks. A QB rushing line can
contain:
- designed rushes called by the offense,
- scrambles created after a pass play breaks down,
- kneels whose purpose is clock management rather than yardage generation.

Pooling them is especially dangerous for a player-opportunity model because the
drivers differ: designed rushes are play-calling/role, scrambles are pressure +
coverage + QB tendency, and kneels are game-state artifacts.

This module is deliberately small and pure. It classifies already-fetched
play-by-play rows and aggregates the resulting observations. It does not fetch
data, estimate a model, use prices, select picks, or publish anything.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


COMPONENTS = ("designed_rush", "scramble", "kneel")


def _flag(row: Mapping[str, Any], name: str) -> bool:
    value = row.get(name)
    if value in (None, "", False, 0, 0.0, "0", "0.0", "False", "false"):
        return False
    if value in (True, 1, 1.0, "1", "1.0", "True", "true"):
        return True
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid binary flag {name}: {value!r}") from exc
    if numeric == 0.0:
        return False
    if numeric == 1.0:
        return True
    raise ValueError(f"binary flag {name} outside 0/1: {value!r}")


def _yards(row: Mapping[str, Any]) -> float:
    value = row.get("yards_gained")
    if value in (None, ""):
        raise ValueError("missing yards_gained for QB rush")
    try:
        yards = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid yards_gained: {value!r}") from exc
    if not math.isfinite(yards):
        raise ValueError(f"non-finite yards_gained: {value!r}")
    return yards


def classify_qb_rush(
    row: Mapping[str, Any],
    *,
    is_qb: bool,
) -> dict[str, Any] | None:
    """Classify one official QB rushing attempt into one football process.

    Two-point attempts are excluded because they do not belong in official
    player rushing-attempt/rushing-yard props.

    nflverse marks kneels and scrambles explicitly. A QB rush which is neither
    is classified as a designed rush. A row claiming both kneel and scramble is
    structurally contradictory and fails closed.
    """
    if not is_qb:
        return None
    if not _flag(row, "rush_attempt"):
        return None
    if _flag(row, "two_point_attempt"):
        return None

    kneel = _flag(row, "qb_kneel")
    scramble = _flag(row, "qb_scramble")
    if kneel and scramble:
        raise ValueError("QB rush cannot be both kneel and scramble")

    if kneel:
        component = "kneel"
    elif scramble:
        component = "scramble"
    else:
        component = "designed_rush"

    return {
        "component": component,
        "yards": _yards(row),
    }


def aggregate_components(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, float | int]:
    """Aggregate classified observations without mixing their mechanisms."""
    rows = list(observations)
    totals: dict[str, float | int] = {
        "carries": 0,
        "rushing_yards": 0.0,
        "designed_rush_attempts": 0,
        "designed_rush_yards": 0.0,
        "scramble_attempts": 0,
        "scramble_yards": 0.0,
        "kneel_attempts": 0,
        "kneel_yards": 0.0,
    }

    for index, observation in enumerate(rows):
        component = str(observation.get("component") or "")
        if component not in COMPONENTS:
            raise ValueError(
                f"unknown QB rushing component at index {index}: {component!r}"
            )
        try:
            yards = float(observation["yards"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid QB rushing yards at index {index}"
            ) from exc
        if not math.isfinite(yards):
            raise ValueError(
                f"non-finite QB rushing yards at index {index}"
            )

        totals["carries"] = int(totals["carries"]) + 1
        totals["rushing_yards"] = float(totals["rushing_yards"]) + yards

        attempts_key = f"{component}_attempts"
        yards_key = f"{component}_yards"
        totals[attempts_key] = int(totals[attempts_key]) + 1
        totals[yards_key] = float(totals[yards_key]) + yards

    return totals
