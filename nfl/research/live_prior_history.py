#!/usr/bin/env python3
"""Build strictly-prior player history for a live NFL target week.

This helper is intentionally separate from the frozen B0 calibration corpus.
Current-season rows may extend a player's live projection history, but they
must never alter the pinned historical residual population used to benchmark B0.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

_SEASON_TYPE_RANK = {"REG": 0, "POST": 1}


def _to_int(value: Any, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _chronology_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
    season = _to_int(row.get("season"), "season")
    week = _to_int(row.get("week"), "week")
    season_type = str(row.get("season_type") or "").strip().upper()
    if season_type not in _SEASON_TYPE_RANK:
        raise ValueError(f"unsupported season_type: {season_type!r}")
    return season, _SEASON_TYPE_RANK[season_type], week


def collect_prior_appearances(
    source_rows: Iterable[Mapping[str, Any]],
    *,
    target_season: int,
    target_week: int,
    target_season_type: str = "REG",
) -> dict[str, list[dict[str, Any]]]:
    """Group only rows strictly before the target season/week.

    For a regular-season target, all earlier seasons (including their
    postseasons) are prior, while current-season regular-season rows are prior
    only when their week is smaller than the target week. Current-season POST
    rows cannot precede a REG target and are excluded.
    """
    target_season = _to_int(target_season, "target_season")
    target_week = _to_int(target_week, "target_week")
    target_type = str(target_season_type or "").strip().upper()
    if target_type not in _SEASON_TYPE_RANK:
        raise ValueError(f"unsupported target season_type: {target_type!r}")
    if target_week <= 0:
        raise ValueError("target_week must be positive")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()

    for raw in source_rows:
        row = dict(raw)
        player_id = str(row.get("player_id") or "").strip()
        if not player_id:
            continue

        season = _to_int(row.get("season"), "season")
        week = _to_int(row.get("week"), "week")
        season_type = str(row.get("season_type") or "").strip().upper()
        if season_type not in _SEASON_TYPE_RANK:
            raise ValueError(f"unsupported season_type: {season_type!r}")

        identity = (player_id, season, week, season_type)
        if identity in seen:
            raise ValueError(f"duplicate player/week row: {identity}")
        seen.add(identity)

        if season > target_season:
            continue
        if season == target_season:
            if target_type == "REG":
                if season_type != "REG" or week >= target_week:
                    continue
            else:
                if season_type == "POST" and week >= target_week:
                    continue
                # Every REG row in the same season precedes the postseason.

        grouped[player_id].append(row)

    for rows in grouped.values():
        rows.sort(key=_chronology_key)
    return dict(grouped)
