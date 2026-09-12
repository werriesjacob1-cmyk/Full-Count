#!/usr/bin/env python3
"""Fail-closed pregame availability gate from official NFL inactive reports.

A player's absence from an inactive list is only meaningful when an official
bound report covers BOTH teams in that player's event. If game coverage is
incomplete, or the player's own source row cannot be bound cleanly, availability
remains UNKNOWN.

This module does not infer starter status or healthy status. The strongest
positive state it emits is NOT_LISTED_INACTIVE.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _report_team_map(report: Mapping[str, Any]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for block in report.get("teams") or []:
        if not isinstance(block, Mapping):
            continue
        team = str(block.get("team") or "").strip().upper()
        if not team:
            continue
        players = [
            dict(p)
            for p in (block.get("players") or [])
            if isinstance(p, Mapping)
        ]
        out[team] = players
    return out


def evaluate_candidate(
    candidate: Mapping[str, Any],
    bound_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate one bound market candidate against official inactive reports."""
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be a mapping")

    if (
        str(candidate.get("binding_status") or "") != "BOUND"
        or not candidate.get("gsis_id")
        or not candidate.get("team")
    ):
        return {
            "availability_status": "UNKNOWN_CANDIDATE_IDENTITY",
            "availability_gate_pass": False,
            "covered_report_count": 0,
        }

    team = str(candidate["team"]).strip().upper()
    away = str(candidate.get("event_away_team") or "").strip().upper()
    home = str(candidate.get("event_home_team") or "").strip().upper()
    if not away or not home or away == home or team not in {away, home}:
        return {
            "availability_status": "UNKNOWN_CANDIDATE_IDENTITY",
            "availability_gate_pass": False,
            "covered_report_count": 0,
        }

    gsis_id = str(candidate["gsis_id"]).strip()
    name_key = _name_key(candidate.get("player_name"))

    covered = []
    listed_inactive = False
    unresolved_same_name = False

    for report in bound_reports:
        if not isinstance(report, Mapping):
            continue
        teams = _report_team_map(report)
        if away not in teams or home not in teams:
            continue

        covered.append(report)
        for player in teams.get(team, []):
            status = str(player.get("binding_status") or "")
            player_gsis = str(player.get("gsis_id") or "").strip()
            if status == "BOUND" and player_gsis == gsis_id:
                listed_inactive = True
            if (
                status != "BOUND"
                and name_key
                and _name_key(player.get("player_name")) == name_key
            ):
                unresolved_same_name = True

    if listed_inactive:
        return {
            "availability_status": "LISTED_INACTIVE",
            "availability_gate_pass": False,
            "covered_report_count": len(covered),
        }

    if unresolved_same_name:
        return {
            "availability_status": "UNKNOWN_PLAYER_BINDING",
            "availability_gate_pass": False,
            "covered_report_count": len(covered),
        }

    if not covered:
        return {
            "availability_status": "UNKNOWN_GAME_COVERAGE",
            "availability_gate_pass": False,
            "covered_report_count": 0,
        }

    return {
        "availability_status": "NOT_LISTED_INACTIVE",
        "availability_gate_pass": True,
        "covered_report_count": len(covered),
    }
