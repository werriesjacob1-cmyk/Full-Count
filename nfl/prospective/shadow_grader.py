#!/usr/bin/env python3
"""Grade sealed NFL prospective shadow observations against weekly outcomes.

This module is intentionally offline and source-agnostic. The caller supplies
the sealed pregame snapshot plus outcome rows and is responsible for preserving
the outcome source bytes/provenance.

Population integrity:
- only records that were SHADOW_ONLY before kickoff can enter the eligible
  selection denominator;
- QUARANTINED and NEUTRAL observations may be settled for diagnosis but never
  retroactively become eligible;
- outcome identity requires exact GSIS + team + opponent + season/type.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {label}: {value!r}")
    return out


def _opponent(record: Mapping[str, Any]) -> str | None:
    team = _text(record.get("team")).upper()
    away = _text(record.get("event_away_team")).upper()
    home = _text(record.get("event_home_team")).upper()
    if not team or not away or not home or away == home:
        return None
    if team == away:
        return home
    if team == home:
        return away
    return None


def _match_rows(
    record: Mapping[str, Any],
    outcome_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
    season_type: str,
) -> list[Mapping[str, Any]]:
    gsis = _text(record.get("gsis_id"))
    team = _text(record.get("team")).upper()
    opponent = _opponent(record)
    if not gsis or not team or not opponent:
        return []

    matches = []
    for row in outcome_rows:
        if not isinstance(row, Mapping):
            continue
        try:
            row_season = int(float(row.get("season")))
        except (TypeError, ValueError):
            continue
        if row_season != int(season):
            continue
        if _text(row.get("season_type")) != str(season_type):
            continue
        if _text(row.get("player_id")) != gsis:
            continue
        if _text(row.get("team")).upper() != team:
            continue
        if _text(row.get("opponent_team")).upper() != opponent:
            continue
        matches.append(row)
    return matches


def _eligible(record: Mapping[str, Any]) -> bool:
    return (
        _text(record.get("decision_status")).upper() == "SHADOW_ONLY"
        and _text(record.get("research_direction")).upper()
        in {"OVER", "UNDER"}
    )


def grade_snapshot(
    snapshot: Mapping[str, Any],
    outcome_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
    season_type: str = "REG",
) -> dict[str, Any]:
    """Grade one sealed prospective shadow snapshot without changing eligibility."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be a mapping")
    if _text(snapshot.get("evidence_class")) != "PROSPECTIVE_SHADOW":
        raise ValueError("snapshot evidence_class must be PROSPECTIVE_SHADOW")

    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ValueError("snapshot records must be a list")

    graded = []
    for source in records:
        if not isinstance(source, Mapping):
            raise ValueError("snapshot record must be a mapping")
        row = dict(source)
        eligible = _eligible(source)
        row["eligible_shadow"] = eligible

        matches = _match_rows(
            source,
            outcome_rows,
            season=season,
            season_type=season_type,
        )
        if not matches:
            row.update({
                "outcome_status": "UNRESOLVED_OUTCOME",
                "actual_passing_yards": None,
                "outcome_side": None,
                "selection_result": "UNSETTLED",
            })
            graded.append(row)
            continue

        if len(matches) != 1:
            row.update({
                "outcome_status": "AMBIGUOUS_OUTCOME",
                "actual_passing_yards": None,
                "outcome_side": None,
                "selection_result": "UNSETTLED",
            })
            graded.append(row)
            continue

        actual = _finite(matches[0].get("passing_yards"), "passing_yards")
        line = _finite(source.get("line"), "line")
        if actual > line:
            outcome_side = "OVER"
        elif actual < line:
            outcome_side = "UNDER"
        else:
            outcome_side = "PUSH"

        if not eligible:
            result = "NOT_ELIGIBLE"
        elif outcome_side == "PUSH":
            result = "PUSH"
        else:
            direction = _text(source.get("research_direction")).upper()
            result = "HIT" if direction == outcome_side else "MISS"

        row.update({
            "outcome_status": "SETTLED",
            "actual_passing_yards": actual,
            "outcome_side": outcome_side,
            "selection_result": result,
            "outcome_week": (
                int(float(matches[0]["week"]))
                if matches[0].get("week") not in (None, "")
                else None
            ),
        })
        graded.append(row)

    hits = sum(r["selection_result"] == "HIT" for r in graded)
    misses = sum(r["selection_result"] == "MISS" for r in graded)
    pushes = sum(r["selection_result"] == "PUSH" for r in graded)
    eligible_settled = hits + misses + pushes
    decided = hits + misses

    return {
        "grading_contract_version": 1,
        "evidence_class": "PROSPECTIVE_SHADOW_GRADED",
        "source_snapshot_sha256": snapshot.get("snapshot_sha256"),
        "season": int(season),
        "season_type": str(season_type),
        "records": graded,
        "summary": {
            "records_total": len(graded),
            "outcomes_settled": sum(
                r["outcome_status"] == "SETTLED" for r in graded
            ),
            "outcomes_unresolved": sum(
                r["outcome_status"] == "UNRESOLVED_OUTCOME" for r in graded
            ),
            "outcomes_ambiguous": sum(
                r["outcome_status"] == "AMBIGUOUS_OUTCOME" for r in graded
            ),
            "eligible_settled": eligible_settled,
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "hit_rate": (hits / decided) if decided else None,
        },
    }
