#!/usr/bin/env python3
"""prospective_reporting.py -- reporting tooling for the prospective
candidate-funnel track (Priority 7 of the restart-safety-mission
directive, 2026-08-25). Operates on already-built
candidate_funnel_logger.py records joined with
candidate_funnel_grader.py outcome records.

NO CONCLUSIONS DRAWN HERE: this session's earlier live-logged funnel data
(backtest/candidate_funnel_2026-08-25.jsonl) was itself lost to the same
container restarts that wiped the canonical backfill -- it was gitignored
by design, same as rows_canonical.jsonl. Every function below is tested
against synthetic fixtures only. Do not run this against fewer than many
real logged-and-graded slates before treating any single number it
produces as a real finding -- the standing instruction against drawing
conclusions from tiny samples applies especially hard here, where even
ONE full day is a small sample of a season.

    from prospective_reporting import slate_summary, gate_regret, ...
"""
from __future__ import annotations

from collections import defaultdict


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def slate_summary(records):
    """Per-slate (one date's funnel records) candidate-universe overview.
    `records` are candidate_funnel_logger.py's own record shape --
    top-level identity/prediction/market/evidence/decision/provenance."""
    n_total = len(records)
    by_qc = defaultdict(int)
    n_with_alt_lines = 0
    for r in records:
        qc = (r.get("decision") or {}).get("quality_control_status")
        by_qc[qc] += 1
        if (r.get("decision") or {}).get("n_alt_lines", 0) > 1:
            n_with_alt_lines += 1
    return {
        "n_total_candidates": n_total,
        "by_quality_control_status": dict(by_qc),
        "n_with_multiple_alt_lines": n_with_alt_lines,
    }


def join_outcomes(records, outcomes):
    """outcomes: list of candidate_funnel_grader.py outcome records
    (candidate_id/grade/...). Returns {candidate_id: (record, outcome_or_None)}."""
    outcomes_by_id = {o["candidate_id"]: o for o in outcomes}
    joined = {}
    for r in records:
        cid = (r.get("identity") or {}).get("candidate_id")
        if cid is None:
            continue
        joined[cid] = (r, outcomes_by_id.get(cid))
    return joined


def highest_probability_rejected(records, outcomes, n=10):
    """The N highest-hit_probability candidates that were REJECTED by
    quality_control (not assumed_lineup, not confirmed/kept) -- the
    clearest "what did the board miss out on" view. Includes outcome if
    graded, explicitly None if not."""
    joined = join_outcomes(records, outcomes)
    rejected = []
    for cid, (r, outcome) in joined.items():
        qc = (r.get("decision") or {}).get("quality_control_status")
        if qc != "rejected":
            continue
        prob = (r.get("prediction") or {}).get("hit_probability")
        if prob is None:
            continue
        rejected.append({
            "candidate_id": cid, "hit_probability": prob,
            "qc_reason": (r.get("decision") or {}).get("quality_control_reason"),
            "grade": outcome.get("grade") if outcome else None,
        })
    rejected.sort(key=lambda x: -x["hit_probability"])
    return rejected[:n]


def alternate_line_winner_comparison(records):
    """For every candidate with >1 alt_lines, compares the SELECTED board
    line's probability against the highest-probability alternate that was
    NOT selected -- i.e. was the model's own final line choice actually
    the highest-probability option it had computed? `_pick_line()`'s own
    selection logic is not re-derived here -- this only reads what's
    already in `decision.alt_lines`, checking presence, not recomputing
    or second-guessing the pick."""
    results = []
    for r in records:
        decision = r.get("decision") or {}
        alt_lines = decision.get("alt_lines") or []
        if len(alt_lines) < 2:
            continue
        board_prob = (r.get("prediction") or {}).get("hit_probability")
        if board_prob is None:
            continue
        best_alt = max(alt_lines, key=lambda a: a.get("prob") or 0)
        board_was_best = round(board_prob, 6) >= round((best_alt.get("prob") or 0) - 1e-9, 6)
        results.append({
            "candidate_id": (r.get("identity") or {}).get("candidate_id"),
            "board_prob": board_prob, "best_alt_prob": best_alt.get("prob"),
            "n_alt_lines": len(alt_lines), "board_was_highest_prob_option": board_was_best,
        })
    return results


def gate_failure_counts(records):
    """Tally of decision.blocking_gate across all records (the FIRST gate,
    in recommendation_funnel.GATE_ORDER, that blocked each candidate)."""
    counts = defaultdict(int)
    for r in records:
        bg = (r.get("decision") or {}).get("blocking_gate")
        counts[bg] += 1
    return dict(counts)


def gate_regret(records, outcomes):
    """For each gate, the realized hit rate of candidates blocked SOLELY
    by that gate -- i.e. every OTHER gate in their own `decision.gates`
    dict passed. This is stricter than gate_failure_counts (which credits
    a candidate to whichever gate failed first in GATE_ORDER, even if it
    would also have failed a later one) -- gate_regret only counts a
    candidate toward a gate if that gate is the ONLY reason it was
    blocked, so the reported hit rate is a real answer to "if we ONLY
    relaxed this one gate, what would we have gotten." Requires the full
    `decision.gates` dict (recommendation_funnel.gate_trace()'s own
    output), not just blocking_gate -- both are already captured by
    candidate_funnel_logger.py, per backtest/selection_information_loss_audit_2026-08-25.md."""
    joined = join_outcomes(records, outcomes)
    by_gate = defaultdict(lambda: {"n": 0, "hits": 0, "n_graded": 0})
    for cid, (r, outcome) in joined.items():
        gates = (r.get("decision") or {}).get("gates") or {}
        if not gates:
            continue
        failing = [g for g, passed in gates.items() if passed is False]
        if len(failing) != 1:
            continue  # blocked by 0 or >1 gates -- not attributable to exactly one
        gate = failing[0]
        by_gate[gate]["n"] += 1
        if outcome and outcome.get("grade") in ("hit", "miss"):
            by_gate[gate]["n_graded"] += 1
            by_gate[gate]["hits"] += outcome["grade"] == "hit"
    return {
        gate: {"n_blocked_solely_by_this_gate": v["n"], "n_graded": v["n_graded"],
               "hit_rate": _rate(v["hits"], v["n_graded"])}
        for gate, v in sorted(by_gate.items())
    }
