"""Grade an immutable FC-NFL-004 research freeze against an authoritative final.

Settlement reuses ``game_market_grader.grade_game_market`` on the market record
embedded in the frozen snapshot, so HIT/MISS/PUSH semantics have one owner.
The outcome must come from ``espn_final_outcome`` bound to the SAME identity
binding hash the freeze carries. The freeze is verified before and after and is
never modified; the grade is a separate, create-only document.

Three things are measured separately and must not be conflated:
1. prediction quality: per-config realised log loss of the (win, push, lose)
   probabilities, and point error of expected margin/total;
2. price performance: units at the captured price (research leans and the
   full counterfactual population, reported separately);
3. market timing: line/price movement against a LATER pre-kickoff sealed
   snapshot of the same event, when one exists. That reference is only the
   latest later research vintage; it is NOT a verified closing price unless a
   separate near-kickoff observation proves it (``reference_basis`` says so).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from nfl.prospective.game_market_grader import grade_game_market
from nfl.prospective.game_market_pricing import (
    RESEARCH_LEAN,
    american_to_decimal,
    no_vig_pair,
    GameMarketPricingError,
)
from nfl.prospective.game_market_research_freeze import (
    canonical_sha256,
    verify_research_freeze,
)
from nfl.prospective.game_market_snapshot import verify_game_market_snapshot

SCHEMA = "fc-nfl-004-game-market-research-grade-v1"
EPS = 1e-12


class GameMarketResearchGradeError(ValueError):
    """Raised when a freeze/outcome/later-vintage pairing is unsafe."""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _verify_outcome(record: Mapping[str, Any], outcome: Mapping[str, Any]) -> None:
    body = {k: v for k, v in outcome.items() if k != "outcome_sha256"}
    if canonical_sha256(body) != outcome.get("outcome_sha256"):
        raise GameMarketResearchGradeError("outcome hash mismatch")
    binding = record.get("identity_binding")
    if not binding:
        raise GameMarketResearchGradeError("ungradable: freeze has no identity binding")
    if outcome.get("binding_sha256") != binding.get("binding_sha256"):
        raise GameMarketResearchGradeError("outcome bound to a different identity")
    if str(outcome.get("event_id")) != str(record["event_id"]):
        raise GameMarketResearchGradeError("outcome event_id mismatch")
    if outcome.get("final_status") != "FINAL":
        raise GameMarketResearchGradeError("outcome not FINAL")


REFERENCE_BASIS = "LATEST_LATER_PRE_KICKOFF_RESEARCH_VINTAGE_NOT_A_VERIFIED_CLOSE"


def _later_markets(record: Mapping[str, Any], later: Mapping[str, Any] | None) -> dict:
    if later is None:
        return {}
    verify_game_market_snapshot(later)
    if str(later.get("event_id")) != str(record["event_id"]):
        raise GameMarketResearchGradeError("later snapshot is for a different event")
    if _dt(later["sealed_at"]) <= _dt(record["frozen_at"]):
        raise GameMarketResearchGradeError("later snapshot must be sealed after the freeze")
    kickoff = _dt(record["scheduled_kickoff"])
    out = {}
    for row in later.get("records") or []:
        if _dt(row["captured_at"]) >= kickoff:
            raise GameMarketResearchGradeError("later quote observed at/after kickoff")
        out[str(row["market"]).lower()] = row
    return out


def _movement(sel: Mapping[str, Any], reference_market: Mapping[str, Any] | None,
              kickoff: datetime) -> dict | None:
    if reference_market is None:
        return None
    gi = sel["grading_identity"]
    side, family = gi["side"], gi["market"]
    prefix = side.lower()
    a, b = {"spread": ("home", "away"), "game_total": ("over", "under"),
            "moneyline": ("home", "away")}[family]
    try:
        nv = no_vig_pair(reference_market.get(f"{a}_odds"), reference_market.get(f"{b}_odds"))
        reference_nv = nv[0] if prefix == a else nv[1]
    except GameMarketPricingError:
        reference_nv = None
    out: dict[str, Any] = {
        "reference_basis": REFERENCE_BASIS,
        "reference_captured_at": reference_market["captured_at"],
        "reference_minutes_before_kickoff": round(
            (kickoff - _dt(reference_market["captured_at"])).total_seconds() / 60.0, 3),
        "reference_odds": reference_market.get(f"{prefix}_odds"),
    }
    if family == "spread":
        later = reference_market.get(f"{prefix}_line")
        out["reference_line"] = later
        out["line_movement_points"] = None if later is None else float(gi["line"]) - float(later)
    elif family == "game_total":
        later = reference_market.get("line")
        out["reference_line"] = later
        if later is not None:
            delta = float(later) - float(gi["line"])
            out["line_movement_points"] = delta if side == "OVER" else -delta
    same_line = family == "moneyline" or out.get("reference_line") == gi["line"]
    out["no_vig_probability_change"] = (
        None if reference_nv is None or not same_line or sel.get("no_vig_probability") is None
        else round(reference_nv - sel["no_vig_probability"], 6))
    return out


def _check_grading_identity(record: Mapping[str, Any], market: Mapping[str, Any],
                            gi: Mapping[str, Any]) -> None:
    """The frozen selection must equal the embedded sealed offer, field by field."""
    prefix = gi["side"].lower()
    family = gi["market"]
    expected_line = (market.get(f"{prefix}_line") if family == "spread"
                     else market.get("line") if family == "game_total" else None)
    checks = {
        "event_id": (str(gi["event_id"]), str(record["event_id"])),
        "market_id": (gi["market_id"], str(market["market_id"])),
        "selection_id": (gi["selection_id"], market.get(f"{prefix}_selection_id")),
        "line": (gi["line"], expected_line),
        "american_odds": (gi["american_odds"], market.get(f"{prefix}_odds")),
        "captured_at": (gi["captured_at"], market.get("captured_at")),
    }
    binding = record.get("identity_binding") or {}
    checks["nflverse_game_id"] = (gi.get("nflverse_game_id"), binding.get("nflverse_game_id"))
    checks["espn_event_id"] = (gi.get("espn_event_id"), binding.get("nflverse_espn"))
    for field, (frozen, sealed) in checks.items():
        if frozen != sealed:
            raise GameMarketResearchGradeError(f"grading identity {field} mismatch")


def grade_research_freeze(
    record: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    later_snapshot: Mapping[str, Any] | None = None,
    graded_at: str,
) -> dict[str, Any]:
    record_sha = verify_research_freeze(record)
    before = canonical_sha256(record)
    _verify_outcome(record, outcome)
    markets = {str(r["market"]).lower(): r for r in record["market_snapshot"]["records"]}
    later = _later_markets(record, later_snapshot)
    kickoff = _dt(record["scheduled_kickoff"])
    home, away = int(outcome["home_score"]), int(outcome["away_score"])

    point = {}
    for config, summ in record["config_summaries"].items():
        if not summ.get("available"):
            continue
        point[config] = {
            "abs_margin_error": None if summ.get("expected_home_margin") is None
            else abs(summ["expected_home_margin"] - (home - away)),
            "abs_total_error": None if summ.get("expected_total") is None
            else abs(summ["expected_total"] - (home + away)),
        }

    graded = []
    for sel in record["selections"]:
        if "grading_identity" not in sel:
            graded.append({"market": sel["market"], "decision": sel["decision"],
                           "settlement": "NOT_GRADABLE", "reasons": sel["reasons"]})
            continue
        gi = sel["grading_identity"]
        market = markets[gi["market"]]
        if str(market["market_id"]) != gi["market_id"]:
            raise GameMarketResearchGradeError("grading identity market_id mismatch")
        _check_grading_identity(record, market, gi)
        settled = grade_game_market(market, outcome, side=gi["side"])
        s = settled["settlement"]
        idx = {"HIT": 0, "PUSH": 1, "UNRESOLVED_TIE": 1, "MISS": 2}[s]
        logloss = {}
        for config, ev in (sel.get("models") or {}).items():
            if ev is None:
                continue
            p = (ev["p_win"], ev["p_push"], ev["p_lose"])[idx]
            logloss[config] = round(-math.log(max(p, EPS)), 6)
        units = None
        if gi["american_odds"] is not None:
            units = (american_to_decimal(gi["american_odds"]) - 1.0 if s == "HIT"
                     else -1.0 if s == "MISS" else 0.0)
        graded.append({
            "selection_key": sel["selection_key"], "market": gi["market"], "side": gi["side"],
            "line": gi["line"], "american_odds": gi["american_odds"], "decision": sel["decision"],
            "settlement": s, "settlement_grade_sha256": settled["grade_sha256"],
            "units_at_captured_price": None if units is None else round(units, 6),
            "realised_logloss": logloss,
            "later_vintage_comparison": _movement(sel, later.get(gi["market"]), kickoff),
        })

    leans = [g for g in graded if g.get("decision") == RESEARCH_LEAN]
    body = {
        "schema": SCHEMA, "research_only": True, "public_eligible": False,
        "event_id": record["event_id"], "freeze_record_sha256": record_sha,
        "outcome_sha256": outcome["outcome_sha256"], "outcome": dict(outcome),
        "later_snapshot_sha256": None if later_snapshot is None else later_snapshot["snapshot_sha256"],
        "later_reference_basis": None if later_snapshot is None else REFERENCE_BASIS,
        "graded_at": graded_at,
        "final": {"home_score": home, "away_score": away, "margin": home - away, "total": home + away},
        "prediction_quality": {"point_error": point},
        "price_performance": {
            "research_lean_count": len(leans),
            "research_lean_units": round(sum(g["units_at_captured_price"] or 0 for g in leans), 6),
        },
        "selections": graded,
    }
    if canonical_sha256(record) != before:
        raise GameMarketResearchGradeError("freeze mutated during grading")
    return {**body, "grade_sha256": canonical_sha256(body)}


def write_grade(path: Path, grade: Mapping[str, Any]) -> Path:
    body = {k: v for k, v in grade.items() if k != "grade_sha256"}
    if canonical_sha256(body) != grade.get("grade_sha256"):
        raise GameMarketResearchGradeError("grade hash mismatch")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps(grade, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except FileExistsError as exc:
        raise GameMarketResearchGradeError(f"grade already exists: {path}") from exc
    return path
