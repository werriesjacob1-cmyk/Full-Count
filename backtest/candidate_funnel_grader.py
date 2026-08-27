#!/usr/bin/env python3
"""candidate_funnel_grader.py -- the "later outcome join" for
backtest/candidate_funnel_{date}.jsonl (Priority 2, item 3 of the
prospective-candidate-log lifecycle, 2026-08-25).

WHY A SEPARATE FILE: the standing instruction is explicit that "pregame and
postgame data must remain logically separate" -- a candidate's point-in-time
prediction must never be mutated once written (candidate_funnel_logger.py's
own non-mutation guarantee), and an outcome must never be back-filled into
the same row a downstream reader might treat as "what was known pregame."
So grading here NEVER touches candidate_funnel_{date}.jsonl -- it only reads
it, and writes a brand new backtest/candidate_funnel_outcomes_{date}.jsonl
keyed by the same candidate_id.

WHY REJECTED CANDIDATES MUST BE GRADABLE TOO: the whole point of logging the
full funnel (not just the board's kept picks) is to later ask "was the
selection logic right to reject/hold these?" -- that question is unanswerable
if only kept candidates get an outcome. This module grades every record in
the funnel file, kept/rejected/assumed_lineup alike, using the same real
grade_results.grade_pick() the production pipeline uses -- no separate or
looser grading logic for non-selected candidates.

REUSES, NEVER REIMPLEMENTS: grade_results.fetch_game_contexts() (the
ThreadPoolExecutor-parallelized version fixed in 47a75920) and
grade_results.grade_pick() are called exactly as production calls them. This
module's only job is reducing the append-only funnel changelog to one latest
record per candidate, reconstructing a grade_pick()-compatible pick dict from
a funnel record's identity section, and writing results to their own file.

    /tmp/mlbvenv/bin/python3 backtest/candidate_funnel_grader.py 2026-08-25
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grade_results as gr

DEFAULT_OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def funnel_path_for_date(date, out_dir=DEFAULT_OUT_DIR):
    return os.path.join(out_dir, f"candidate_funnel_{date}.jsonl")


def outcomes_path_for_date(date, out_dir=DEFAULT_OUT_DIR):
    return os.path.join(out_dir, f"candidate_funnel_outcomes_{date}.jsonl")


def load_latest_records(path):
    """Reduce an append-only funnel changelog to the latest record per
    candidate_id -- later snapshots supersede earlier ones for the same
    identity, matching candidate_funnel_logger.py's own append semantics."""
    latest = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = (record.get("identity") or {}).get("candidate_id")
            if not cid:
                continue
            latest[cid] = record
    return latest


def pick_from_funnel_record(record):
    """Pure reconstruction of a grade_results.grade_pick()-compatible pick
    dict from one funnel record's identity section. Read-only -- never
    mutates the record passed in."""
    identity = record.get("identity") or {}
    return {
        "type": identity.get("type"),
        "game_pk": identity.get("game_pk"),
        "player_id": identity.get("player_id"),
        "combo_player_ids": identity.get("combo_player_ids"),
        "projection": {
            "stat": identity.get("stat"),
            "needs": identity.get("needs"),
            "value": identity.get("threshold"),
        },
        "bet_side": identity.get("side"),
        "team": identity.get("team"),
        "matchup": identity.get("matchup"),
        "prop": "",
    }


def grade_records(date, records, refresh=True):
    """Grade an explicit candidate-id -> record mapping with production logic."""
    if not records:
        return [], 0

    game_pks = {
        r.get("identity", {}).get("game_pk") for r in records.values()
        if r.get("identity", {}).get("game_pk") is not None
    }
    game_statuses = gr.fetch_game_contexts(game_pks, refresh=refresh)

    outcomes = []
    for cid, record in sorted(records.items()):
        pick = pick_from_funnel_record(record)
        graded = gr.grade_pick(
            pick, game_statuses, date=date, allow_in_progress=False)
        outcomes.append({
            "candidate_id": cid,
            "date": date,
            "grade": graded.get("grade"),
            "actual": graded.get("actual"),
            "actual_stat": graded.get("actual_stat"),
            "reason": graded.get("reason"),
            "quality_control_status": (
                (record.get("decision") or {}).get("quality_control_status")
            ),
            "graded_at": datetime.utcnow().isoformat() + "Z",
        })
    return outcomes, len(records)


def grade_date(date, out_dir=DEFAULT_OUT_DIR, refresh=True):
    """Grade the latest state per candidate from the ephemeral JSONL spool."""
    in_path = funnel_path_for_date(date, out_dir)
    latest = load_latest_records(in_path)
    return grade_records(date, latest, refresh=refresh)


def load_materialized_date_records(destination_root, date):
    """Union every candidate identity ever observed in durable snapshots.

    Candidate content can legitimately change through the day, but fields that
    determine settlement (game/player/stat/needs/threshold/side) may not change
    under one candidate_id. If they do, the identity contract is broken and
    grading fails closed instead of silently choosing one version.
    """
    try:
        from backtest import prospective_durability as pdur
    except ImportError:
        import prospective_durability as pdur

    snapshots = pdur.discover_materialized_snapshots(
        destination_root, date=date)
    by_id = {}
    settlement_identity = {}

    def settle_key(record):
        identity = record.get("identity") or {}
        return {
            key: identity.get(key)
            for key in (
                "candidate_id", "game_pk", "type", "player_id",
                "combo_player_ids", "stat", "side", "threshold", "needs",
            )
        }

    for item in snapshots:
        _, records = pdur.load_materialized_snapshot(
            destination_root, date=date, snapshot_id=item["snapshot_id"])
        for record in records:
            cid = (record.get("identity") or {}).get("candidate_id")
            if not cid:
                raise RuntimeError(
                    f"durable snapshot {item['snapshot_id']} has candidate without id")
            key = settle_key(record)
            if cid in settlement_identity and settlement_identity[cid] != key:
                raise RuntimeError(
                    f"candidate identity {cid} changed settlement fields across "
                    "prospective observations")
            settlement_identity[cid] = key
            by_id[cid] = record
    return by_id, len(snapshots)


def grade_materialized_date(destination_root, date, refresh=True):
    """Grade every candidate identity ever observed in durable date snapshots."""
    records, _n_snapshots = load_materialized_date_records(
        destination_root, date)
    return grade_records(date, records, refresh=refresh)


def write_outcomes(outcomes, path):
    """Append-only write to the SEPARATE outcomes file -- never touches the
    pregame candidate_funnel_{date}.jsonl file this data was derived from."""
    with open(path, "a", encoding="utf-8") as fh:
        for record in outcomes:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return len(outcomes)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date")
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="ephemeral candidate-funnel spool directory")
    parser.add_argument(
        "--durable-root",
        help="grade the union of immutable materialized snapshots instead of the local spool")
    parser.add_argument(
        "--outcome-path",
        help="explicit JSONL output path (default: candidate_funnel_outcomes_DATE.jsonl)")
    args = parser.parse_args()

    if args.durable_root:
        outcomes, n_read = grade_materialized_date(
            args.durable_root, args.date)
    else:
        outcomes, n_read = grade_date(
            args.date, out_dir=args.out_dir)

    if n_read == 0:
        print(
            f"No candidate funnel records found for {args.date} -- nothing to grade.",
            file=sys.stderr)
        return 1
    out_path = (
        args.outcome_path
        or outcomes_path_for_date(args.date, args.out_dir)
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    n_written = write_outcomes(outcomes, out_path)
    graded = sum(1 for o in outcomes if o["grade"] in ("hit", "miss"))
    print(
        f"{args.date}: read {n_read} candidates, wrote {n_written} outcome records "
        f"({graded} graded, {n_written - graded} ungraded) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
