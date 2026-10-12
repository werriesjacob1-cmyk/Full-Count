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
3. market timing: closing-line value against a LATER pre-kickoff sealed
   snapshot of the same event, when one exists.
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
    """Raised when a freeze/outcome/closing pairing is unsafe."""


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


def _closing_markets(record: Mapping[str, Any], closing: Mapping[str, Any] | None) -> dict:
    if closing is None:
        return {}
    verify_game_market_snapshot(closing)
    if str(closing.get("event_id")) != str(record["event_id"]):
        raise GameMarketResearchGradeError("closing snapshot is for a different event")
    if _dt(closing["sealed_at"]) <= _dt(record["frozen_at"]):
        raise GameMarketResearchGradeError("closing snapshot must be sealed after the freeze")
    kickoff = _dt(record["scheduled_kickoff"])
    out = {}
    for row in closing.get("records") or []:
        if _dt(row["captured_at"]) >= kickoff:
            raise GameMarketResearchGradeError("closing quote observed at/after kickoff")
        out[str(row["market"]).lower()] = row
    return out


def _clv(sel: Mapping[str, Any], closing_market: Mapping[str, Any] | None) -> dict | None:
    if closing_market is None:
        return None
    gi = sel["grading_identity"]
    side, family = gi["side"], gi["market"]
    prefix = side.lower()
    a, b = {"spread": ("home", "away"), "game_total": ("over", "under"),
            "moneyline": ("home", "away")}[family]
    try:
        nv = no_vig_pair(closing_market.get(f"{a}_odds"), closing_market.get(f"{b}_odds"))
        closing_nv = nv[0] if prefix == a else nv[1]
    except GameMarketPricingError:
        closing_nv = None
    out: dict[str, Any] = {"closing_captured_at": closing_market["captured_at"],
                           "closing_odds": closing_market.get(f"{prefix}_odds")}
    if family == "spread":
        later = closing_market.get(f"{prefix}_line")
        out["closing_line"] = later
        out["clv_points"] = None if later is None else float(gi["line"]) - float(later)
    elif family == "game_total":
        later = closing_market.get("line")
        out["closing_line"] = later
        if later is not None:
            delta = float(later) - float(gi["line"])
            out["clv_points"] = delta if side == "OVER" else -delta
    same_line = family == "moneyline" or out.get("closing_line") == gi["line"]
    out["clv_no_vig_probability"] = (
        None if closing_nv is None or not same_line or sel.get("no_vig_probability") is None
        else round(closing_nv - sel["no_vig_probability"], 6))
    return out


def grade_research_freeze(
    record: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    closing_snapshot: Mapping[str, Any] | None = None,
    graded_at: str,
) -> dict[str, Any]:
    record_sha = verify_research_freeze(record)
    before = canonical_sha256(record)
    _verify_outcome(record, outcome)
    markets = {str(r["market"]).lower(): r for r in record["market_snapshot"]["records"]}
    closing = _closing_markets(record, closing_snapshot)
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
        if market.get(f"{gi['side'].lower()}_selection_id") != gi["selection_id"]:
            raise GameMarketResearchGradeError("grading identity selection_id mismatch")
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
            "clv": _clv(sel, closing.get(gi["market"])),
        })

    leans = [g for g in graded if g.get("decision") == RESEARCH_LEAN]
    body = {
        "schema": SCHEMA, "research_only": True, "public_eligible": False,
        "event_id": record["event_id"], "freeze_record_sha256": record_sha,
        "outcome_sha256": outcome["outcome_sha256"], "outcome": dict(outcome),
        "closing_snapshot_sha256": None if closing_snapshot is None else closing_snapshot["snapshot_sha256"],
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
