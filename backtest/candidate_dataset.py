#!/usr/bin/env python3
"""candidate_dataset.py -- reusable builder for the point-in-time
CANDIDATE-LEVEL DECISION DATASET this session's research phase needs (see
backtest/candidate_dataset_feasibility_2026-08-25.md for the full gap
analysis this design is grounded in).

A CandidateRecord is a plain dict with 7 top-level sections (identity,
prediction, market, evidence, decision, outcome, provenance), matching this
codebase's existing convention of documented plain dicts over ORMs/
dataclasses (backtest/SCHEMA.md is the same style). Every field is either a
real value or an explicit None WITH a reason recorded elsewhere in this
module's docs -- never silently absent, matching the project's standing
"absent is not zero and not neutral" discipline (see generate_picks.py's
own scale()/_sig() docstrings for the same rule applied to signals).

This module performs NO new data fetches and NO new time-travel risk: it
only reshapes/overlays already-point-in-time-safe inputs --
backtest/engine.py rows (already covered by verify_no_lookahead()),
publication_registry.py snapshots (already immutable-at-publication by
construction), and recommendation_funnel.gate_trace()'s own read-only,
non-mutating introspection (reused verbatim, never reimplemented -- that
module's own docstring is explicit that classify_recommendation() is the
only place allowed to decide Top Pick/Lean/Value/Neutral).

Deliberately NOT a full historical build yet -- backtest/rows_canonical.jsonl
does not exist as of this module's creation (main backfill still running).
This is the reusable interface + tests so building the real dataset is a
mechanical step once canonical history lands, not a design exercise done
under time pressure then.
"""
from __future__ import annotations

MARKET_UNAVAILABLE_BACKTEST = (
    "backtest rows carry no historical market data by design -- see "
    "backtest/SCHEMA.md's own \"THE RULE THAT MATTERS MOST\" section: "
    "market signals are explicitly out of scope for backtesting."
)
DECISION_UNAVAILABLE_NO_TRACE = (
    "no recommendation_funnel.gate_trace() result was supplied for this "
    "candidate -- selection/rejection reasoning was not computed."
)
SHRINKAGE_INPUTS_UNAVAILABLE = (
    "shrinkage inputs (n0, prior) are not persisted on backtest rows or "
    "registry snapshots today -- only the post-shrinkage probability is "
    "recorded. See candidate_dataset_feasibility_2026-08-25.md."
)


def _empty_record():
    return {
        "identity": {
            "date": None, "game_pk": None, "player_id": None,
            "player_name": None, "team": None, "matchup": None,
            "stat": None, "line": None, "needs": None,
        },
        "prediction": {
            "predicted_prob": None, "calibrated_prob": None,
            "calibrated_by": None, "prob_ci": None, "reliability": None,
            "reliability_note": None, "sample_n": None,
            "shrinkage_inputs": None,
            "shrinkage_inputs_unavailable_reason": SHRINKAGE_INPUTS_UNAVAILABLE,
            "stable_lift": None, "lift": None, "base_rate": None,
        },
        "market": {
            "market_odds": None, "market_implied": None, "market_edge": None,
            "market_hold": None, "price_clears": None,
            "market_unavailable_reason": MARKET_UNAVAILABLE_BACKTEST,
        },
        "evidence": {
            "signals": None, "cat_matchup": None, "cat_recent_form": None,
            "cat_environment": None, "cat_baseline_skill": None,
            "cat_context": None, "lineup_assumed": None,
        },
        "decision": {
            "recommendation_status": None, "status_reasons": None,
            "gates": None, "blocking_gate": None,
            "decision_unavailable_reason": DECISION_UNAVAILABLE_NO_TRACE,
        },
        "outcome": {
            "outcome": None, "actual": None, "actual_pa": None,
            "actual_ip": None, "fair_test": None, "settlement_state": None,
            "result_actual": None, "result_reason": None,
        },
        "provenance": {
            "code_git_sha": None, "backtest_generated_at": None,
            "publication_source_commit": None, "publication_run_id": None,
            "publication_deployment_id": None, "published_top_pick_at": None,
        },
    }


def from_backtest_row(row):
    """Map one backtest/SCHEMA.md row into IDENTITY/PREDICTION/EVIDENCE/
    OUTCOME/PROVENANCE directly -- 1:1 field mapping, no invention. MARKET
    and DECISION stay explicitly unavailable (see their own reason fields)
    unless overlaid separately -- a raw backtest row structurally cannot
    supply either (see MARKET_UNAVAILABLE_BACKTEST)."""
    record = _empty_record()
    identity = record["identity"]
    identity["date"] = row.get("date")
    identity["game_pk"] = row.get("game_pk")
    identity["player_id"] = row.get("player_id")
    identity["player_name"] = row.get("player_name")
    identity["team"] = row.get("team")
    identity["stat"] = row.get("prop_type")
    identity["line"] = row.get("line")
    identity["needs"] = row.get("needs")

    prediction = record["prediction"]
    prediction["predicted_prob"] = row.get("predicted_prob")
    prediction["calibrated_prob"] = row.get("calibrated_prob")
    prediction["calibrated_by"] = row.get("calibrated_by")
    prediction["reliability"] = row.get("reliability")

    evidence = record["evidence"]
    evidence["signals"] = row.get("signals")
    evidence["cat_matchup"] = row.get("cat_matchup")
    evidence["cat_recent_form"] = row.get("cat_recent_form")
    evidence["cat_environment"] = row.get("cat_environment")
    evidence["cat_baseline_skill"] = row.get("cat_baseline_skill")
    evidence["cat_context"] = row.get("cat_context")

    decision = record["decision"]
    if row.get("recommendation_status") is not None:
        decision["recommendation_status"] = row["recommendation_status"]
        decision["status_reasons"] = row.get("status_reasons")

    outcome = record["outcome"]
    outcome["outcome"] = row.get("outcome")
    outcome["actual"] = row.get("actual")
    outcome["actual_pa"] = row.get("actual_pa")
    outcome["actual_ip"] = row.get("actual_ip")
    outcome["fair_test"] = row.get("fair_test")

    provenance = record["provenance"]
    provenance["code_git_sha"] = row.get("code_git_sha")
    provenance["backtest_generated_at"] = row.get("backtest_generated_at")

    return record


def overlay_registry_snapshot(record, snapshot):
    """Fill MARKET + the "was selected" half of DECISION from a real
    publication_registry.py immutable snapshot -- ONLY ever called for a
    candidate proven to correspond to that snapshot's own (date, game_pk,
    player_id, stat) identity (the caller's responsibility, not
    re-verified here, matching this module's read-only/no-new-joins
    contract). Never invents data for a candidate absent from the registry
    -- if snapshot is None, this is a no-op and the market/decision
    unavailable reasons stay in place untouched."""
    if snapshot is None:
        return record

    market = record["market"]
    market["market_odds"] = snapshot.get("market_odds")
    market["market_implied"] = snapshot.get("market_implied")
    market["market_edge"] = snapshot.get("market_edge")
    market["market_hold"] = snapshot.get("market_hold")
    market["price_clears"] = snapshot.get("price_clears")
    market["market_unavailable_reason"] = None

    prediction = record["prediction"]
    if snapshot.get("hit_probability") is not None:
        prediction["predicted_prob"] = prediction["predicted_prob"] or snapshot["hit_probability"]
    prediction["prob_ci"] = snapshot.get("prob_ci")
    prediction["reliability"] = snapshot.get("reliability") or prediction["reliability"]
    prediction["reliability_note"] = snapshot.get("reliability_note")
    prediction["sample_n"] = snapshot.get("sample_n")
    prediction["stable_lift"] = snapshot.get("stable_lift")
    prediction["lift"] = snapshot.get("lift")
    prediction["base_rate"] = snapshot.get("base_rate")

    evidence = record["evidence"]
    evidence["lineup_assumed"] = snapshot.get("lineup_assumed")

    decision = record["decision"]
    decision["recommendation_status"] = snapshot.get("recommendation_status")
    decision["status_reasons"] = snapshot.get("status_reasons")
    decision["decision_unavailable_reason"] = None

    provenance = record["provenance"]
    provenance["publication_source_commit"] = snapshot.get("publication_source_commit")
    provenance["publication_run_id"] = snapshot.get("publication_run_id")
    provenance["publication_deployment_id"] = snapshot.get("publication_deployment_id")
    provenance["published_top_pick_at"] = snapshot.get("published_top_pick_at")

    return record


def overlay_gate_trace(record, gate_trace_result):
    """Fill DECISION's rejection-reason fields from
    recommendation_funnel.classify_with_trace()'s own output shape
    ({"status": ..., "gates": {...}, "blocking_gate": ...}) -- reused
    verbatim, this module never recomputes or reimplements gate logic. A
    None gate_trace_result is a no-op, matching overlay_registry_snapshot's
    same "no data means no change" contract."""
    if gate_trace_result is None:
        return record
    decision = record["decision"]
    decision["gates"] = gate_trace_result.get("gates")
    decision["blocking_gate"] = gate_trace_result.get("blocking_gate")
    if decision["recommendation_status"] is None:
        decision["recommendation_status"] = gate_trace_result.get("status")
    decision["decision_unavailable_reason"] = None
    return record


def overlay_settlement(record, settlement):
    """Normalizes a dashboard/live_state.py-style settlement fact
    (settlement_state in {hit,miss,void,provisional_hit,...}) alongside
    backtest's own outcome in {0,1} -- the two vocabularies are NOT assumed
    to agree; both are kept, never collapsed into one field, so a caller can
    detect and investigate any real disagreement rather than have it
    silently hidden by a premature normalization."""
    if settlement is None:
        return record
    outcome = record["outcome"]
    outcome["settlement_state"] = settlement.get("settlement_state")
    outcome["result_actual"] = settlement.get("result_actual")
    outcome["result_reason"] = settlement.get("result_reason")
    return record
