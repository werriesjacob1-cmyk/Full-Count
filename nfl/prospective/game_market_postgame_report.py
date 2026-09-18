"""Postgame reporting for sealed NFL spread/total prospective shadow boards.

This module consumes evidence that already exists:
- full-slate manifest from live_game_market_shadow.py,
- sealed FanDuel market snapshot,
- B0/challenger prediction used to build the board,
- sealed shadow board,
- proven sportsbook<->nflverse game binding,
- ESPN explicit-final outcome,
and delegates settlement semantics to game_market_grader.grade_game_market.

It does not select bets, refit a model, reinterpret a line, or infer finality.
American-odds ROI is computed from the exact frozen selected odds on the board.
NO_PLAY rows stay in the denominator/accounting record but carry zero stake.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from nfl.prospective.game_market_grader import grade_game_market
from nfl.prospective.game_market_shadow_board import verify_game_market_shadow_board
from nfl.prospective.game_market_snapshot import verify_game_market_snapshot

SCHEMA_VERSION = 1
TERMINAL_SETTLEMENTS = {"HIT", "MISS", "PUSH"}
DECISIONS = {"SHADOW_ONLY", "NO_PLAY"}


class GameMarketPostgameError(ValueError):
    """Raised when frozen pregame evidence and final outcome do not chain safely."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketPostgameError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameMarketPostgameError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GameMarketPostgameError(f"{field} must be finite")
    return result


def verify_slate_manifest(manifest: Mapping[str, Any]) -> str:
    """Verify the canonical hash emitted by live_game_market_shadow.py."""
    if not isinstance(manifest, Mapping):
        raise GameMarketPostgameError("manifest must be a mapping")
    expected = _text(manifest.get("manifest_sha256"), "manifest_sha256").lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise GameMarketPostgameError("manifest_sha256 must be lowercase SHA-256")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    observed = _canonical_hash(body)
    if observed != expected:
        raise GameMarketPostgameError("manifest hash mismatch")
    if manifest.get("research_only") is not True or manifest.get("public_eligible") is not False:
        raise GameMarketPostgameError("manifest must remain research-only")
    events = manifest.get("events")
    if not isinstance(events, list):
        raise GameMarketPostgameError("manifest.events must be a list")
    if manifest.get("discovered_event_count") != len(events):
        raise GameMarketPostgameError("manifest discovered_event_count mismatch")
    if manifest.get("accounted_event_count") != len(events):
        raise GameMarketPostgameError("manifest accounted_event_count mismatch")
    ids = [_text(row.get("event_id"), "manifest.event_id") for row in events]
    if len(ids) != len(set(ids)):
        raise GameMarketPostgameError("duplicate manifest event_id")
    return expected


def american_unit_profit(odds: int | float, settlement: str) -> float:
    """Return profit in units for a one-unit stake at frozen American odds."""
    status = str(settlement or "").strip().upper()
    if status not in TERMINAL_SETTLEMENTS:
        raise GameMarketPostgameError(f"unsupported settlement: {status or '<empty>'}")
    if status == "PUSH":
        return 0.0
    if status == "MISS":
        return -1.0
    price = _finite(odds, "selected_odds")
    if price == 0 or not price.is_integer():
        raise GameMarketPostgameError("selected_odds must be a nonzero integer")
    if price > 0:
        return price / 100.0
    return 100.0 / abs(price)


def _snapshot_market_map(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    verify_game_market_snapshot(snapshot)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in snapshot.get("records") or []:
        row = deepcopy(dict(raw))
        key = (_text(row.get("market"), "market").lower(), _text(row.get("market_id"), "market_id"))
        if key in out:
            raise GameMarketPostgameError(f"duplicate snapshot market identity: {key}")
        out[key] = row
    return out


def grade_shadow_event(
    board: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    prediction: Mapping[str, Any],
    binding: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Grade one BOARD_BUILT event without changing any pregame decision."""
    board_hash = verify_game_market_shadow_board(board, snapshot, prediction)
    snapshot_hash = verify_game_market_snapshot(snapshot)

    event_id = _text(board.get("event_id"), "board.event_id")
    if _text(snapshot.get("event_id"), "snapshot.event_id") != event_id:
        raise GameMarketPostgameError("board/snapshot event_id mismatch")
    if _text(binding.get("sportsbook_event_id"), "binding.sportsbook_event_id") != event_id:
        raise GameMarketPostgameError("binding event_id mismatch")
    if _text(outcome.get("event_id"), "outcome.event_id") != event_id:
        raise GameMarketPostgameError("outcome event_id mismatch")
    if _text(binding.get("sportsbook_snapshot_sha256"), "binding.snapshot_sha") != snapshot_hash:
        raise GameMarketPostgameError("binding snapshot hash mismatch")
    binding_sha = _text(binding.get("binding_sha256"), "binding.binding_sha256")
    if _text(outcome.get("binding_sha256"), "outcome.binding_sha256") != binding_sha:
        raise GameMarketPostgameError("outcome binding hash mismatch")
    if _text(outcome.get("final_status"), "outcome.final_status").upper() != "FINAL":
        raise GameMarketPostgameError("outcome must be FINAL")
    if (
        _text(outcome.get("finality_basis"), "outcome.finality_basis")
        != "ESPN_STATUS_FINAL_AND_COMPLETED_TRUE"
    ):
        raise GameMarketPostgameError("outcome finality basis is not explicit ESPN finality")

    market_map = _snapshot_market_map(snapshot)
    rows = []
    for raw in board.get("records") or []:
        decision = deepcopy(dict(raw))
        market = _text(decision.get("market"), "board.market").lower()
        decision_status = _text(decision.get("decision_status"), "decision_status").upper()
        if decision_status not in DECISIONS:
            raise GameMarketPostgameError(f"unsupported decision_status: {decision_status}")

        base = {
            "event_id": event_id,
            "game_id": board.get("game_id"),
            "market": market,
            "decision_status": decision_status,
            "model_name": board.get("model_name"),
            "model_code_sha": board.get("model_code_sha"),
            "board_sha256": board_hash,
            "market_snapshot_sha256": snapshot_hash,
        }
        if decision_status == "NO_PLAY":
            rows.append({
                **base,
                "settlement": None,
                "stake_units": 0.0,
                "profit_units": 0.0,
                "reason": decision.get("reason"),
            })
            continue

        market_id = _text(decision.get("market_id"), "board.market_id")
        frozen_market = market_map.get((market, market_id))
        if frozen_market is None:
            raise GameMarketPostgameError(
                f"selected market missing from frozen snapshot: {(market, market_id)}"
            )
        selected_side = _text(decision.get("selected_side"), "selected_side").upper()
        selected_odds = _finite(decision.get("selected_odds"), "selected_odds")
        if not selected_odds.is_integer() or selected_odds == 0:
            raise GameMarketPostgameError("selected_odds must be a nonzero integer")

        if market == "spread":
            prefix = selected_side.lower()
            frozen_odds = _finite(frozen_market.get(f"{prefix}_odds"), "frozen odds")
            frozen_line = _finite(frozen_market.get(f"{prefix}_line"), "frozen line")
        elif market == "game_total":
            prefix = selected_side.lower()
            frozen_odds = _finite(frozen_market.get(f"{prefix}_odds"), "frozen odds")
            frozen_line = _finite(frozen_market.get("line"), "frozen line")
        else:
            raise GameMarketPostgameError(f"unsupported graded market: {market}")

        if selected_odds != frozen_odds:
            raise GameMarketPostgameError("board selected_odds drift from frozen market")
        board_line = _finite(decision.get("selected_line"), "selected_line")
        if not math.isclose(board_line, frozen_line, rel_tol=0.0, abs_tol=1e-12):
            raise GameMarketPostgameError("board selected_line drift from frozen market")

        grade = grade_game_market(frozen_market, outcome, side=selected_side)
        settlement = _text(grade.get("settlement"), "settlement").upper()
        if settlement not in TERMINAL_SETTLEMENTS:
            raise GameMarketPostgameError(
                f"non-terminal settlement for spread/total shadow: {settlement}"
            )
        rows.append({
            **base,
            "market_id": market_id,
            "selected_side": selected_side,
            "selected_line": board_line,
            "selected_odds": int(selected_odds),
            "settlement": settlement,
            "stake_units": 1.0,
            "profit_units": american_unit_profit(selected_odds, settlement),
            "grade_sha256": grade["grade_sha256"],
            "home_score": grade["home_score"],
            "away_score": grade["away_score"],
        })

    if len(rows) != len(board.get("records") or []):
        raise GameMarketPostgameError("postgame row cardinality mismatch")
    return {
        "event_id": event_id,
        "game_id": board.get("game_id"),
        "board_sha256": board_hash,
        "snapshot_sha256": snapshot_hash,
        "binding_sha256": binding_sha,
        "outcome_sha256": _text(outcome.get("outcome_sha256"), "outcome_sha256"),
        "records": sorted(rows, key=lambda row: row["market"]),
    }


def build_slate_postgame_report(
    manifest: Mapping[str, Any],
    event_packages: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Grade/account the exact frozen full-slate manifest population."""
    manifest_hash = verify_slate_manifest(manifest)
    if not isinstance(event_packages, Sequence) or isinstance(event_packages, (str, bytes)):
        raise GameMarketPostgameError("event_packages must be a sequence")

    manifest_events = {
        _text(row.get("event_id"), "manifest.event_id"): row
        for row in manifest["events"]
    }
    packages: dict[str, Mapping[str, Any]] = {}
    for package in event_packages:
        event_id = _text(package.get("event_id"), "package.event_id")
        if event_id in packages:
            raise GameMarketPostgameError(f"duplicate package event_id: {event_id}")
        packages[event_id] = package
    if set(packages) != set(manifest_events):
        missing = sorted(set(manifest_events) - set(packages))
        extra = sorted(set(packages) - set(manifest_events))
        raise GameMarketPostgameError(
            f"postgame package coverage mismatch missing={missing} extra={extra}"
        )

    event_reports = []
    for event_id in sorted(manifest_events):
        frozen_event = manifest_events[event_id]
        package = packages[event_id]
        status = _text(frozen_event.get("status"), "manifest.event.status").upper()
        if status == "NO_PLAY":
            event_reports.append({
                "event_id": event_id,
                "status": "NO_PLAY",
                "reason": frozen_event.get("reason"),
                "records": [],
            })
            continue
        if status != "BOARD_BUILT":
            raise GameMarketPostgameError(f"unsupported manifest event status: {status}")
        report = grade_shadow_event(
            package.get("board"),
            package.get("snapshot"),
            package.get("prediction"),
            package.get("binding"),
            package.get("outcome"),
        )
        event_reports.append({"status": "GRADED", **report})

    graded_rows = [
        row
        for event in event_reports
        for row in event.get("records", [])
        if row.get("decision_status") == "SHADOW_ONLY"
    ]
    no_play_rows = [
        row
        for event in event_reports
        for row in event.get("records", [])
        if row.get("decision_status") == "NO_PLAY"
    ]
    settlement_counts = {name: 0 for name in sorted(TERMINAL_SETTLEMENTS)}
    by_market: dict[str, dict[str, Any]] = {}
    for row in graded_rows:
        settlement_counts[row["settlement"]] += 1
        bucket = by_market.setdefault(
            row["market"],
            {
                "wagers": 0,
                "hits": 0,
                "misses": 0,
                "pushes": 0,
                "stake_units": 0.0,
                "profit_units": 0.0,
            },
        )
        bucket["wagers"] += 1
        bucket["stake_units"] += row["stake_units"]
        bucket["profit_units"] += row["profit_units"]
        bucket[row["settlement"].lower() + "es" if row["settlement"] == "PUSH" else row["settlement"].lower() + "s"] += 1

    for bucket in by_market.values():
        stake = bucket["stake_units"]
        bucket["roi"] = bucket["profit_units"] / stake if stake else None

    stake_units = sum(row["stake_units"] for row in graded_rows)
    profit_units = sum(row["profit_units"] for row in graded_rows)
    body = {
        "schema_version": SCHEMA_VERSION,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_SHADOW_POSTGAME",
        "research_only": True,
        "public_eligible": False,
        "generated_at": _text(generated_at, "generated_at"),
        "target_local_date": manifest.get("target_local_date"),
        "source_manifest_sha256": manifest_hash,
        "event_count": len(event_reports),
        "graded_wager_count": len(graded_rows),
        "market_no_play_count": len(no_play_rows),
        "settlement_counts": settlement_counts,
        "stake_units": stake_units,
        "profit_units": profit_units,
        "roi": profit_units / stake_units if stake_units else None,
        "by_market": dict(sorted(by_market.items())),
        "events": event_reports,
    }
    return {**body, "report_sha256": _canonical_hash(body)}
