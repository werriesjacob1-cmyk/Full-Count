"""Real nflverse charting -> identity-bound descriptive research features.

Not film analysis or a predictive model. Derived charting data: CC-BY-SA-4.0,
FTN Data via nflverse (participation 2023+ and FTN charting 2022+).
Capture timestamps are availability lower bounds, never historical backfills.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re

LICENSE = "https://creativecommons.org/licenses/by-sa/4.0/"
FIELDS = {
    "participation": ("route", "defense_man_zone_type", "defense_coverage_type", "was_pressure", "offense_personnel", "defense_personnel"),
    "ftn_charting": ("is_motion", "is_play_action", "is_rpo", "read_thrown", "is_catchable_ball", "is_contested_ball", "n_blitzers", "n_pass_rushers"),
}


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _rows_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _value(value):
    if value is None or str(value).strip().lower() in {"", "na", "nan", "null", "none", "unknown"}:
        return None
    return str(value).strip()


def capture(raw: bytes, *, kind: str, season: int, captured_at: str):
    """Hash exact bytes and parse a caller-acquired documented public release."""
    if kind not in FIELDS or isinstance(season, bool) or not isinstance(season, int) or season < 2023:
        raise ValueError("unsupported source/season")
    _time(captured_at)
    stem = "pbp_participation" if kind == "participation" else "ftn_charting"
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    required = {"nflverse_game_id", "play_id" if kind == "participation" else "nflverse_play_id"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("source schema missing identity")
    return {"kind": kind, "season": season, "captured_at": captured_at,
            "sha256": hashlib.sha256(raw).hexdigest(), "rows_sha256": _rows_digest(rows), "rows": rows,
            "url": f"https://github.com/nflverse/nflverse-data/releases/download/{stem}/{stem}_{season}.csv",
            "license": LICENSE, "attribution": "FTN Data via nflverse",
            "observation_kind": "THIRD_PARTY_CHARTING_NOT_FILM_VIEWED"}


def bind_pass_targets(source, pbp_rows, *, pbp_sha256: str, pbp_captured_at: str, cutoff: str):
    """Bind only prior, legal targeted forward passes; retain all source unknowns.

    PBP capture itself must be before cutoff. The caller supplies its raw digest
    at the trusted acquisition boundary; rows must derive from those same bytes.
    Same UTC date games are conservatively excluded without inventing end times.
    No assumptions about availability based on game's historical date/date_pulled.
    """
    stop = _time(cutoff)
    if not isinstance(pbp_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", pbp_sha256):
        raise ValueError("PBP SHA256 required")
    if source.get("rows_sha256") != _rows_digest(source["rows"]):
        raise ValueError("source rows modified after capture")
    if max(_time(source["captured_at"]), _time(pbp_captured_at)) > stop:
        raise ValueError("source unavailable at cutoff")
    if source["kind"] not in FIELDS:
        raise ValueError("unsupported source")
    index = {}
    for row in pbp_rows:
        key = (row["game_id"], str(row["play_id"]))
        if key in index:
            raise ValueError("duplicate pbp key")
        index[key] = row
    seen, result, excluded = set(), [], Counter()
    play_field = "play_id" if source["kind"] == "participation" else "nflverse_play_id"
    for chart in source["rows"]:
        key = (chart["nflverse_game_id"], str(chart[play_field]))
        if key in seen:
            raise ValueError("duplicate chart key")
        seen.add(key)
        if not re.fullmatch(rf'{source["season"]}_\d{{2}}_[A-Z]{{2,3}}_[A-Z]{{2,3}}', key[0]):
            raise ValueError("source season/game mismatch")
        pbp = index.get(key)
        if pbp is None:
            excluded["UNMATCHED_PBP"] += 1
            continue
        if not pbp.get("game_date") or pbp["game_date"] >= stop.date().isoformat():
            excluded["GAME_NOT_STRICTLY_PRIOR"] += 1
            continue
        if pbp.get("pass_attempt") not in ("1", "1.0") or pbp.get("play_type") != "pass" or pbp.get("no_play") in ("1", "1.0"):
            excluded["NOT_LEGAL_PASS_ATTEMPT"] += 1
            continue
        receiver = pbp.get("receiver_player_id", "")
        if not re.fullmatch(r"00-\d{7}", receiver) or not pbp.get("posteam") or not pbp.get("defteam"):
            excluded["UNKNOWN_TARGET_IDENTITY"] += 1
            continue
        if source["kind"] == "participation" and chart.get("possession_team") != pbp["posteam"]:
            excluded["TEAM_MISMATCH"] += 1
            continue
        result.append({"game_id": key[0], "play_id": key[1], "receiver_gsis_id": receiver,
                       "offense": pbp["posteam"], "defense": pbp["defteam"],
                       "complete_pass": pbp.get("complete_pass"),
                       "charting": {field: _value(chart.get(field)) for field in FIELDS[source["kind"]]},
                       "source_sha256": source["sha256"], "pbp_sha256": pbp_sha256,
                       "available_at": max(_time(source["captured_at"]), _time(pbp_captured_at)).isoformat()})
    return {"rows": result, "excluded": dict(excluded), "source_rows": len(seen),
            "source_sha256": source["sha256"], "pbp_sha256": pbp_sha256,
            "cutoff": cutoff, "status": "DESCRIPTIVE_RESEARCH_ONLY"}


def summarize(bound):
    """Actual downstream consumer: field availability and conditional catch counts.

    Target-conditioned samples cannot estimate all-route target probabilities.
    These are descriptive sufficient statistics, never an automatic adjustment.
    """
    coverage, cells = defaultdict(Counter), defaultdict(Counter)
    for row in bound["rows"]:
        for field, value in row["charting"].items():
            coverage[field]["unknown" if value is None else "known"] += 1
        label = row["charting"].get("defense_man_zone_type")
        if label not in {"MAN_COVERAGE", "ZONE_COVERAGE"}:
            continue
        cell = cells[(row["receiver_gsis_id"], row["defense"], label)]
        cell["targets"] += 1
        complete = row["complete_pass"]
        if complete in ("0", "0.0", "1", "1.0"):
            cell["known_outcomes"] += 1
            cell["catches"] += int(float(complete))
    return {"bound_targets": len(bound["rows"]), "field_coverage": dict(coverage),
            "conditional_cells": [{"receiver_gsis_id": k[0], "defense": k[1], "coverage": k[2], **v} for k, v in sorted(cells.items())],
            "limitations": ["target-conditioned; not all-route opportunity", "third-party charting; not independently reviewed film", "no predictive value established", "no historical PIT replay before capture"]}

