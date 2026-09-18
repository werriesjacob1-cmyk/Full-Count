"""Prospective NFL spread/total shadow board bound to a sealed market snapshot.

This module is deliberately research-only. It does not promote a model, publish
plays, or estimate probabilities. It binds an already-built model prediction to
an already-sealed FanDuel game-market snapshot and emits an explicit SHADOW_ONLY
or NO_PLAY decision for spread and game_total.

The board hash binds:
- exact market snapshot hash,
- exact model identity/provenance,
- exact selected side/price/line,
- capture/seal clocks,
- explicit rejection reasons.

Moneyline is intentionally out of scope for this first live bridge.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from nfl.prospective.game_market_snapshot import verify_game_market_snapshot

SCHEMA_VERSION = 1
TARGET_MARKETS = ("spread", "game_total")
ALLOWED_DECISIONS = {"SHADOW_ONLY", "NO_PLAY"}


class GameMarketShadowBoardError(ValueError):
    """Raised when prospective game-market evidence is unsafe or ambiguous."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameMarketShadowBoardError(f"{field} must be a non-empty string")
    return value.strip()


def _dt(value: Any, field: str) -> datetime:
    raw = _text(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GameMarketShadowBoardError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GameMarketShadowBoardError(f"{field} must be timezone-aware")
    return parsed


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameMarketShadowBoardError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GameMarketShadowBoardError(f"{field} must be finite")
    return result


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _market_by_family(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = snapshot.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise GameMarketShadowBoardError("snapshot.records must be a sequence")
    out: dict[str, dict[str, Any]] = {}
    for raw in records:
        row = deepcopy(dict(raw))
        market = _text(row.get("market"), "market").lower()
        if market in out:
            raise GameMarketShadowBoardError(f"duplicate market family: {market}")
        out[market] = row
    return out


def _prediction_contract(prediction: Mapping[str, Any]) -> dict[str, Any]:
    row = deepcopy(dict(prediction))
    if _text(row.get("baseline_name"), "baseline_name") != "GAME_MARKET_B0_PRIOR_SCORING_BLEND":
        raise GameMarketShadowBoardError("live bridge currently accepts B0 control only")
    if row.get("uses_market_line_as_feature") is not False:
        raise GameMarketShadowBoardError("prediction must not use market line as feature")
    if row.get("uses_current_game_outcome_as_feature") is not False:
        raise GameMarketShadowBoardError("prediction must not use current-game outcome")
    if _text(row.get("target_final_status"), "target_final_status").upper() != "PREGAME":
        raise GameMarketShadowBoardError("prediction target must be PREGAME")
    for field in ("game_id", "home_team", "away_team", "eligibility"):
        _text(row.get(field), field)
    return row


def _spread_decision(market: Mapping[str, Any], prediction: Mapping[str, Any]) -> dict[str, Any]:
    if prediction.get("eligibility") != "ELIGIBLE":
        return {
            "decision_status": "NO_PLAY",
            "reason": str(prediction.get("eligibility") or "MODEL_INELIGIBLE"),
        }
    model_margin = _finite(prediction.get("predicted_home_margin"), "predicted_home_margin")
    home_line = _finite(market.get("home_line"), "home_line")
    away_line = _finite(market.get("away_line"), "away_line")
    if not math.isclose(home_line + away_line, 0.0, abs_tol=1e-9):
        raise GameMarketShadowBoardError("spread lines must be opposites")
    edge = model_margin + home_line
    if math.isclose(edge, 0.0, abs_tol=1e-12):
        return {"decision_status": "NO_PLAY", "reason": "MODEL_EQUALS_SPREAD", "model_edge_points": 0.0}
    side = "HOME" if edge > 0 else "AWAY"
    prefix = side.lower()
    return {
        "decision_status": "SHADOW_ONLY",
        "selected_side": side,
        "selected_line": home_line if side == "HOME" else away_line,
        "selected_odds": int(_finite(market.get(f"{prefix}_odds"), f"{prefix}_odds")),
        "selection_id": _text(market.get(f"{prefix}_selection_id"), f"{prefix}_selection_id"),
        "model_edge_points": abs(edge),
    }


def _total_decision(market: Mapping[str, Any], prediction: Mapping[str, Any]) -> dict[str, Any]:
    if prediction.get("eligibility") != "ELIGIBLE":
        return {
            "decision_status": "NO_PLAY",
            "reason": str(prediction.get("eligibility") or "MODEL_INELIGIBLE"),
        }
    model_total = _finite(prediction.get("predicted_total"), "predicted_total")
    line = _finite(market.get("line"), "line")
    edge = model_total - line
    if math.isclose(edge, 0.0, abs_tol=1e-12):
        return {"decision_status": "NO_PLAY", "reason": "MODEL_EQUALS_TOTAL", "model_edge_points": 0.0}
    side = "OVER" if edge > 0 else "UNDER"
    prefix = side.lower()
    return {
        "decision_status": "SHADOW_ONLY",
        "selected_side": side,
        "selected_line": line,
        "selected_odds": int(_finite(market.get(f"{prefix}_odds"), f"{prefix}_odds")),
        "selection_id": _text(market.get(f"{prefix}_selection_id"), f"{prefix}_selection_id"),
        "model_edge_points": abs(edge),
    }


def build_game_market_shadow_board(
    market_snapshot: Mapping[str, Any],
    prediction: Mapping[str, Any],
    *,
    model_code_sha: str,
    source_vintage: str,
    sealed_at: str,
) -> dict[str, Any]:
    """Bind one B0 prediction to one sealed event market snapshot."""
    market_snapshot_sha = verify_game_market_snapshot(market_snapshot)
    pred = _prediction_contract(prediction)
    sealed_dt = _dt(sealed_at, "sealed_at")
    market_sealed_dt = _dt(market_snapshot.get("sealed_at"), "market_snapshot.sealed_at")
    if sealed_dt < market_sealed_dt:
        raise GameMarketShadowBoardError("board cannot be sealed before market snapshot")

    markets = _market_by_family(market_snapshot)
    event_id = _text(market_snapshot.get("event_id"), "event_id")
    rows: list[dict[str, Any]] = []
    for family in TARGET_MARKETS:
        market = markets.get(family)
        if market is None:
            rows.append({
                "event_id": event_id,
                "game_id": pred["game_id"],
                "market": family,
                "decision_status": "NO_PLAY",
                "reason": "MARKET_NOT_NORMALIZED",
            })
            continue

        market_home = _text(market.get("home_team"), "market.home_team")
        market_away = _text(market.get("away_team"), "market.away_team")
        if market_home != _text(pred.get("home_team"), "prediction.home_team_full"):
            raise GameMarketShadowBoardError("home team identity mismatch")
        if market_away != _text(pred.get("away_team"), "prediction.away_team_full"):
            raise GameMarketShadowBoardError("away team identity mismatch")

        decision = (
            _spread_decision(market, pred)
            if family == "spread"
            else _total_decision(market, pred)
        )
        row = {
            "event_id": event_id,
            "game_id": pred["game_id"],
            "market": family,
            "market_id": market["market_id"],
            "captured_at": market["captured_at"],
            "market_time": market["market_time"],
            "sportsbook": market["sportsbook"],
            "source_payload_sha256": market["source_payload_sha256"],
            "model_name": pred["baseline_name"],
            "model_eligibility": pred["eligibility"],
            "predicted_home_margin": pred.get("predicted_home_margin"),
            "predicted_total": pred.get("predicted_total"),
            **decision,
        }
        if row["decision_status"] not in ALLOWED_DECISIONS:
            raise GameMarketShadowBoardError("invalid decision_status")
        rows.append(row)

    body = {
        "schema_version": SCHEMA_VERSION,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_SHADOW",
        "research_only": True,
        "public_eligible": False,
        "event_id": event_id,
        "game_id": pred["game_id"],
        "market_snapshot_sha256": market_snapshot_sha,
        "model_name": pred["baseline_name"],
        "model_code_sha": _text(model_code_sha, "model_code_sha"),
        "source_vintage": _text(source_vintage, "source_vintage"),
        "sealed_at": _text(sealed_at, "sealed_at"),
        "records": sorted(rows, key=lambda row: row["market"]),
    }
    return {**body, "board_sha256": _canonical_hash(body)}


def verify_game_market_shadow_board(
    board: Mapping[str, Any],
    market_snapshot: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> str:
    """Rebuild one board and require canonical equality."""
    expected = _text(board.get("board_sha256"), "board_sha256")
    rebuilt = build_game_market_shadow_board(
        market_snapshot,
        prediction,
        model_code_sha=board.get("model_code_sha"),
        source_vintage=board.get("source_vintage"),
        sealed_at=board.get("sealed_at"),
    )
    if rebuilt["board_sha256"] != expected or dict(board) != rebuilt:
        raise GameMarketShadowBoardError("shadow board hash/content mismatch")
    return expected
