#!/usr/bin/env python3
"""analyze_fullboard_calibration.py -- MLB full-board calibration and
winner's-curse investigation (Mission 9, Workstream E).

Reads ONLY real, already-committed artifacts under ``output/``:

    output/board_freeze_<date>.json          (board_freeze.py, PR #132/#138/#139)
    output/board_freeze_graded_<date>.json   (board_freeze_grader.py / grade_board_freeze.py,
                                               PR #138/#163)

Both mechanisms already existed and are already running in production
(snapshot capture began 2026-09-20). This script builds NO new snapshot or
freeze infrastructure; it is a pure, reproducible, read-only analysis over
the real committed JSON. It requires no network access.

Usage:
    python3 analyze_fullboard_calibration.py
        [--output-dir OUTPUT_DIR]   default: repo-root/output
        [--report PATH]             default: ./report.json (this directory)
        [--n-buckets N]             default: 5 (see README for why not deciles)

Every number in the written report is derived directly from the real files
read at run time -- nothing in this script hardcodes a result.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration_lib as cl  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Grade values grade_results.grade_pick() / board_freeze_grader.py can emit.
# "ungraded" means the underlying game was not final at grading run time --
# it is a real, disclosed population gap, never treated as a miss or hit.
GRADE_HIT = "hit"
GRADE_MISS = "miss"
GRADE_UNGRADED = "ungraded"

DEFAULT_N_BUCKETS = 5  # see README: real n does not support true deciles


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def discover_paired_dates(output_dir: str) -> List[str]:
    """Real dates that have BOTH a frozen board and a graded board file.
    Ungraded-only dates (e.g. today's board, generated but no game has been
    played yet) are discovered and reported separately, never silently
    treated as part of the analyzed population."""
    board_paths = glob.glob(os.path.join(output_dir, "board_freeze_*.json"))
    dates = set()
    for p in board_paths:
        base = os.path.basename(p)
        if not base.startswith("board_freeze_") or base.startswith("board_freeze_graded_"):
            continue
        date_part = base[len("board_freeze_"): -len(".json")]
        if DATE_RE.match(date_part):
            dates.add(date_part)
    paired = []
    for d in sorted(dates):
        board_path = os.path.join(output_dir, f"board_freeze_{d}.json")
        graded_path = os.path.join(output_dir, f"board_freeze_graded_{d}.json")
        if os.path.exists(board_path) and os.path.exists(graded_path):
            paired.append(d)
    return paired


def discover_board_only_dates(output_dir: str, paired: List[str]) -> List[str]:
    all_board_dates = set()
    for p in glob.glob(os.path.join(output_dir, "board_freeze_*.json")):
        base = os.path.basename(p)
        if base.startswith("board_freeze_graded_"):
            continue
        date_part = base[len("board_freeze_"): -len(".json")]
        if DATE_RE.match(date_part):
            all_board_dates.add(date_part)
    return sorted(all_board_dates - set(paired))


def _sha256_of_records(board_json: Dict[str, Any]) -> str:
    """Independent recomputation is out of scope (board_freeze.py owns the
    exact sealing algorithm and is explicitly not touched by this
    workstream); this only reports the sealed hash already present so the
    report is self-documenting about board integrity."""
    return board_json.get("board_sha256", "")


def load_board_metadata(output_dir: str, date: str) -> Dict[str, Any]:
    with open(os.path.join(output_dir, f"board_freeze_{date}.json")) as f:
        board = json.load(f)
    return {
        "date": date,
        "schema_version": board.get("schema_version"),
        "board_freeze_version": board.get("board_freeze_version"),
        "sport": board.get("sport"),
        "evidence_class": board.get("evidence_class"),
        "board_generated_at": board.get("board_generated_at"),
        "sealed_at": board.get("sealed_at"),
        "n_games": len(board.get("game_start_times", {}) or {}),
        "record_count": board.get("record_count"),
        "research_only": board.get("research_only"),
        "public_eligible": board.get("public_eligible"),
        "board_sha256": _sha256_of_records(board),
    }


def load_graded_records(output_dir: str, date: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    path = os.path.join(output_dir, f"board_freeze_graded_{date}.json")
    with open(path) as f:
        graded = json.load(f)
    meta = {
        "date": date,
        "schema_version": graded.get("schema_version"),
        "evidence_class": graded.get("evidence_class"),
        "source_board_sha256": graded.get("source_board_sha256"),
        "graded_at": graded.get("graded_at"),
        "record_count": graded.get("record_count"),
    }
    return meta, list(graded.get("records", []))


def verify_seal_linkage(board_meta: Dict[str, Any], graded_meta: Dict[str, Any]) -> bool:
    """The graded file is supposed to be graded from exactly the sealed
    board with the same sha256. This is a read-only, informational check --
    it does not re-verify board_freeze.py's own seal, which is out of scope
    for this workstream."""
    return bool(board_meta["board_sha256"]) and board_meta["board_sha256"] == graded_meta["source_board_sha256"]


def grade_breakdown(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        g = r.get("grade")
        counts[str(g)] = counts.get(str(g), 0) + 1
    return counts


def fair_test_breakdown(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        ft = r.get("fair_test")
        counts[str(ft)] = counts.get(str(ft), 0) + 1
    return counts


def qc_status_breakdown(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        qc = r.get("eligibility", {}).get("qc_status")
        counts[str(qc)] = counts.get(str(qc), 0) + 1
    return counts


def to_analysis_row(record: Dict[str, Any], date: str) -> Optional[Dict[str, Any]]:
    """Map one real graded board_freeze record to the minimal shape
    calibration_lib functions need: predicted, hit, cluster, plus the real
    fields needed for grouping. Returns None (never a fabricated row) when
    the record lacks a usable predicted probability."""
    prob = record.get("prediction", {}).get("hit_probability")
    if prob is None:
        return None
    grade = record.get("grade")
    if grade not in (GRADE_HIT, GRADE_MISS):
        return None
    return {
        "predicted": float(prob),
        "hit": 1 if grade == GRADE_HIT else 0,
        "cluster": record.get("game_pk"),
        "date": date,
        "candidate_id": record.get("candidate_id"),
        "stat": record.get("stat"),
        "qc_status": record.get("eligibility", {}).get("qc_status"),
        "recommendation_status": record.get("selector", {}).get("recommendation_status"),
        "selected_top_pick": record.get("selector", {}).get("selected_top_pick"),
        "fair_test": record.get("fair_test"),
    }


def build_population(output_dir: str, dates: List[str]) -> Dict[str, Any]:
    """Builds the real FAIR_GRADED population (grade in {hit, miss} AND
    fair_test is True) used for the core calibration/winner's-curse
    analysis, alongside honest counts of everything excluded and why."""
    all_rows_graded_any: List[Dict[str, Any]] = []  # grade in {hit, miss}, fair_test any
    all_rows_fair_graded: List[Dict[str, Any]] = []  # grade in {hit, miss} AND fair_test True
    excluded_no_probability = 0
    per_date_summary = []

    for date in dates:
        _, records = load_graded_records(output_dir, date)
        gcounts = grade_breakdown(records)
        fcounts = fair_test_breakdown(records)
        qcounts = qc_status_breakdown(records)

        date_fair_graded = 0
        date_graded_any = 0
        for r in records:
            if r.get("grade") not in (GRADE_HIT, GRADE_MISS):
                continue
            row = to_analysis_row(r, date)
            if row is None:
                if r.get("prediction", {}).get("hit_probability") is None:
                    excluded_no_probability += 1
                continue
            all_rows_graded_any.append(row)
            date_graded_any += 1
            if row["fair_test"] is True:
                all_rows_fair_graded.append(row)
                date_fair_graded += 1

        per_date_summary.append(
            {
                "date": date,
                "record_count": len(records),
                "grade_breakdown": gcounts,
                "fair_test_breakdown": fcounts,
                "qc_status_breakdown": qcounts,
                "n_graded_any_fair_test": date_graded_any,
                "n_fair_graded_with_probability": date_fair_graded,
            }
        )

    return {
        "per_date_summary": per_date_summary,
        "fair_graded_rows": all_rows_fair_graded,
        "graded_any_rows": all_rows_graded_any,
        "excluded_no_probability": excluded_no_probability,
    }


def build_calibration_curve(rows: List[Dict[str, Any]], n_buckets: int) -> Dict[str, Any]:
    buckets = cl.make_quantile_buckets(rows, n_buckets)
    bucket_reports = []
    for i, b in enumerate(buckets):
        summary = cl.summarize_bucket(b)
        ci = cl.cluster_bootstrap_ci(b, cl.mean_hit_rate)
        summary["bucket_index"] = i
        summary["realized_hit_rate_cluster_bootstrap_ci"] = {
            "ci_low": ci["ci_low"],
            "ci_high": ci["ci_high"],
            "n_clusters": ci["n_clusters"],
            "note": ci["note"],
        }
        bucket_reports.append(summary)

    overall_ci = cl.cluster_bootstrap_ci(rows, cl.calibration_gap_statistic) if rows else None
    return {
        "requested_n_buckets": n_buckets,
        "actual_n_buckets": len(buckets),
        "total_n": len(rows),
        "buckets": bucket_reports,
        "overall_gap_realized_minus_predicted": {
            "point_estimate": overall_ci["point_estimate"] if overall_ci else None,
            "ci_low": overall_ci["ci_low"] if overall_ci else None,
            "ci_high": overall_ci["ci_high"] if overall_ci else None,
            "n_clusters": overall_ci["n_clusters"] if overall_ci else 0,
            "note": overall_ci["note"] if overall_ci else "no rows",
        },
    }


def build_winners_curse_test(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    top_pick = [r for r in rows if r.get("recommendation_status") == "top_pick"]
    non_top_pick = [r for r in rows if r.get("recommendation_status") != "top_pick"]

    def _group_report(group: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not group:
            return {
                "n": 0,
                "n_games": 0,
                "mean_predicted": None,
                "realized_hit_rate": None,
                "gap_realized_minus_predicted": None,
                "gap_cluster_bootstrap_ci": {"ci_low": None, "ci_high": None, "n_clusters": 0, "note": "n=0"},
            }
        gap_ci = cl.cluster_bootstrap_ci(group, cl.calibration_gap_statistic)
        return {
            "n": len(group),
            "n_games": len({r["cluster"] for r in group}),
            "mean_predicted": cl.mean_predicted(group),
            "realized_hit_rate": cl.mean_hit_rate(group),
            "gap_realized_minus_predicted": gap_ci["point_estimate"],
            "gap_cluster_bootstrap_ci": {
                "ci_low": gap_ci["ci_low"],
                "ci_high": gap_ci["ci_high"],
                "n_clusters": gap_ci["n_clusters"],
                "note": gap_ci["note"],
            },
        }

    # The library helper's group split matches literal field values already
    # on each row; recommendation_status has many non-top_pick values, so
    # build an explicit binary key rather than trying to match a single
    # "other" sentinel against the library's generic two-value split.
    tagged = [dict(r, _is_top_pick=(r.get("recommendation_status") == "top_pick")) for r in rows]
    diff = cl.cluster_bootstrap_group_gap_diff_ci(tagged, "_is_top_pick", True, False)

    return {
        "definition": (
            "selected == selector.recommendation_status == 'top_pick' (the real production Top Pick "
            "classification owned by recommendation.py); non_selected == every other real graded, "
            "fair-test candidate in the same frozen boards, regardless of qc_status"
        ),
        "top_pick": _group_report(top_pick),
        "non_top_pick": _group_report(non_top_pick),
        "gap_difference_top_pick_minus_non_top_pick": diff,
    }


def build_qc_status_breakdown_analysis(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_status.setdefault(str(r.get("qc_status")), []).append(r)

    out = {}
    for status, group in sorted(by_status.items()):
        if not group:
            continue
        gap_ci = cl.cluster_bootstrap_ci(group, cl.calibration_gap_statistic)
        out[status] = {
            "n": len(group),
            "n_games": len({r["cluster"] for r in group}),
            "mean_predicted": cl.mean_predicted(group),
            "realized_hit_rate": cl.mean_hit_rate(group),
            "gap_realized_minus_predicted": gap_ci["point_estimate"],
            "gap_cluster_bootstrap_ci": {
                "ci_low": gap_ci["ci_low"],
                "ci_high": gap_ci["ci_high"],
                "n_clusters": gap_ci["n_clusters"],
                "note": gap_ci["note"],
            },
        }
    return out


def build_qc_rejected_population_check(output_dir: str, dates: List[str]) -> Dict[str, Any]:
    """Real, dynamically-computed check of how many qc_rejected candidates
    across the paired dates have reached a real graded, fair-test outcome.
    Computed at run time (never hardcoded) because this population changes
    as later grading runs settle more games -- see README for the concrete
    example of this happening mid-investigation."""
    total_qc_rejected = 0
    graded_any = 0
    fair_graded_with_prob = 0
    per_date = {}
    for date in dates:
        _, records = load_graded_records(output_dir, date)
        recs = [r for r in records if r.get("eligibility", {}).get("qc_status") == "qc_rejected"]
        d_graded_any = sum(1 for r in recs if r.get("grade") in (GRADE_HIT, GRADE_MISS))
        d_fair = sum(
            1
            for r in recs
            if r.get("grade") in (GRADE_HIT, GRADE_MISS)
            and r.get("fair_test") is True
            and r.get("prediction", {}).get("hit_probability") is not None
        )
        total_qc_rejected += len(recs)
        graded_any += d_graded_any
        fair_graded_with_prob += d_fair
        if recs:
            per_date[date] = {
                "n_qc_rejected": len(recs),
                "n_graded_any": d_graded_any,
                "n_fair_graded_with_probability": d_fair,
            }
    return {
        "total_qc_rejected_across_paired_dates": total_qc_rejected,
        "total_graded_any": graded_any,
        "total_fair_graded_with_probability": fair_graded_with_prob,
        "per_date": per_date,
    }


def build_market_breakdown(rows: List[Dict[str, Any]], min_n: int = 15) -> Dict[str, Any]:
    by_stat: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_stat.setdefault(str(r.get("stat")), []).append(r)

    out = {}
    for stat, group in sorted(by_stat.items()):
        entry = {
            "n": len(group),
            "n_games": len({r["cluster"] for r in group}),
            "mean_predicted": cl.mean_predicted(group) if group else None,
            "realized_hit_rate": cl.mean_hit_rate(group) if group else None,
        }
        if len(group) >= min_n:
            gap_ci = cl.cluster_bootstrap_ci(group, cl.calibration_gap_statistic)
            entry["gap_realized_minus_predicted"] = gap_ci["point_estimate"]
            entry["gap_cluster_bootstrap_ci"] = {
                "ci_low": gap_ci["ci_low"],
                "ci_high": gap_ci["ci_high"],
                "n_clusters": gap_ci["n_clusters"],
                "note": gap_ci["note"],
            }
            entry["sample_size_note"] = f"n={len(group)} meets the min_n={min_n} threshold for this breakdown"
        else:
            entry["gap_realized_minus_predicted"] = None
            entry["gap_cluster_bootstrap_ci"] = None
            entry["sample_size_note"] = (
                f"n={len(group)} is BELOW min_n={min_n} -- point counts reported, no gap/CI computed; "
                "too small to support even a bucketed calibration claim for this market alone"
            )
        out[stat] = entry
    return out


def _build_honest_limitations(
    *,
    paired_dates: List[str],
    board_only_dates: List[str],
    board_metas: List[Dict[str, Any]],
    graded_metas: List[Dict[str, Any]],
    fair_rows: List[Dict[str, Any]],
    winners_curse: Dict[str, Any],
    qc_rejected_check: Dict[str, Any],
) -> List[str]:
    """Every limitation statement here is computed from the real data this
    run just loaded, not hardcoded -- the board_freeze_graded_2026-09-22.json
    file itself changed mid-development of this script (a live regrading run
    settled more games between two runs of this exact script), so any
    hardcoded date/count claim would have gone stale within the same
    session. See README for that concrete example."""
    total_games_scheduled = sum(m["n_games"] for m in board_metas)
    games_with_any_fair_evidence = len({r["cluster"] for r in fair_rows})
    limitations = [
        f"Real evidence spans only {len(paired_dates)} paired day(s) with both a sealed board and a "
        f"graded file ({', '.join(paired_dates)}); the snapshot program began 2026-09-20 and cannot "
        "have more real history than that as of this run.",
    ]
    for bm, gm in zip(board_metas, graded_metas):
        limitations.append(
            f"{bm['date']}: board sealed at {bm['sealed_at']} with {bm['n_games']} real scheduled "
            f"game(s) and {bm['record_count']} candidates; grading last ran at {gm['graded_at']}. "
            "Any game not yet final at that grading timestamp contributes zero real hit/miss evidence "
            "here regardless of how many candidates it produced -- re-running this script after a "
            "later grading pass can change these numbers, as already happened once during this "
            "investigation."
        )
    limitations.append(
        f"Real fair-test-graded evidence in this report comes from {games_with_any_fair_evidence} "
        f"distinct real games out of {total_games_scheduled} real games scheduled across the paired "
        "dates; day-of-week, park, and weather effects are not separable from date/game effects at "
        "this n."
    )
    if qc_rejected_check["total_fair_graded_with_probability"] == 0:
        limitations.append(
            "No real qc_rejected candidate currently has a real fair-test-graded outcome with a usable "
            "probability (see known_population_blockers)."
        )
    else:
        limitations.append(
            f"Only {qc_rejected_check['total_fair_graded_with_probability']} real qc_rejected candidates "
            "currently have a real fair-test-graded outcome with a usable probability -- too few for a "
            "standalone qc_rejected calibration claim (see known_population_blockers)."
        )
    limitations.append(
        f"The winner's-curse top_pick sample (n={winners_curse['top_pick']['n']} across "
        f"{winners_curse['top_pick']['n_games']} real games) is below this report's own predeclared "
        "minimum (n>=30 across >=10 games) for any confident claim in either direction; this report "
        "deliberately does not assert one."
    )
    if board_only_dates:
        limitations.append(
            f"Real board(s) exist for {', '.join(board_only_dates)} with no graded file yet (games not "
            "final / grading not yet run) -- this script does not and must not treat those as analyzed "
            "evidence; they are listed only under dates_with_board_only_no_graded_file_yet."
        )
    return limitations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=os.path.join(_repo_root(), "output"))
    parser.add_argument(
        "--report",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.json"),
    )
    parser.add_argument("--n-buckets", type=int, default=DEFAULT_N_BUCKETS)
    args = parser.parse_args()

    paired_dates = discover_paired_dates(args.output_dir)
    board_only_dates = discover_board_only_dates(args.output_dir, paired_dates)

    if not paired_dates:
        print("No date has BOTH a board_freeze_<date>.json and a board_freeze_graded_<date>.json "
              "file -- nothing to analyze.", file=sys.stderr)
        return 1

    board_metas = [load_board_metadata(args.output_dir, d) for d in paired_dates]
    graded_metas = []
    seal_linkage = {}
    for d in paired_dates:
        gm, _ = load_graded_records(args.output_dir, d)
        graded_metas.append(gm)
    for bm, gm in zip(board_metas, graded_metas):
        seal_linkage[bm["date"]] = verify_seal_linkage(bm, gm)

    population = build_population(args.output_dir, paired_dates)
    fair_rows = population["fair_graded_rows"]

    calibration_curve = build_calibration_curve(fair_rows, args.n_buckets)
    winners_curse = build_winners_curse_test(fair_rows)
    qc_breakdown = build_qc_status_breakdown_analysis(fair_rows)
    market_breakdown = build_market_breakdown(fair_rows)
    qc_rejected_check = build_qc_rejected_population_check(args.output_dir, paired_dates)

    report = {
        "generated_by": "engineering/mlb_fullboard_calibration_20260923/analyze_fullboard_calibration.py",
        "workstream_id": "MLB-FULLBOARD-CALIBRATION-20260923",
        "evidence_class": "RESEARCH_ONLY_POST_HOC_ANALYSIS",
        "inputs": {
            "output_dir": os.path.abspath(args.output_dir),
            "dates_with_board_and_graded_file": paired_dates,
            "dates_with_board_only_no_graded_file_yet": board_only_dates,
            "board_metadata": board_metas,
            "graded_metadata": graded_metas,
            "board_to_graded_sha256_linkage_verified": seal_linkage,
        },
        "population_definition": {
            "note": (
                "The core analysis population is every real candidate across the paired dates whose "
                "grade_board_freeze.py 'grade' is 'hit' or 'miss' (game was final at grading run time), "
                "whose 'fair_test' is True (the same fairness gate results/grades_*.py already uses for "
                "its own fair_test_hit_rate), and whose prediction.hit_probability is not null. "
                "'ungraded' candidates (game not yet final when the grader ran) and fair_test=False/None "
                "candidates (no genuine opportunity, e.g. early substitution/pulled starter) are excluded "
                "from the calibration claim on purpose, not silently folded in as misses."
            ),
            "per_date_summary": population["per_date_summary"],
            "excluded_missing_probability": population["excluded_no_probability"],
            "total_fair_graded_with_probability_n": len(fair_rows),
        },
        "calibration_curve": calibration_curve,
        "winners_curse_test": winners_curse,
        "qc_status_breakdown": qc_breakdown,
        "market_breakdown": market_breakdown,
        "known_population_blockers": [
            {
                "population": "eligibility.qc_status == 'qc_rejected'",
                "real_counts_at_run_time": qc_rejected_check,
                "blocker": (
                    f"Across the {len(paired_dates)} paired dates, {qc_rejected_check['total_qc_rejected_across_paired_dates']} "
                    f"real qc_rejected candidates exist, of which {qc_rejected_check['total_graded_any']} have a real "
                    f"hit/miss grade so far, but only {qc_rejected_check['total_fair_graded_with_probability']} also pass "
                    "fair_test and carry a usable predicted probability -- the same bar every other group in this "
                    "report must clear. This is a genuine, currently-unavoidable population gap (most qc_rejected "
                    "candidates were still 'ungraded' when this run's grading files were captured, and the few real "
                    "graded ones so far were pulled/substituted before a fair opportunity), not a missing field or a "
                    "bug in this script. A QC-rejected-vs-kept calibration comparison is therefore not yet answerable "
                    "from the real data on disk; re-run this script once more real qc_rejected candidates clear both "
                    "gates."
                ),
            }
        ],
        "next_falsifiable_hypothesis": {
            "hypothesis": (
                "The MLB general-formula probability pipeline (attach_hit_probabilities' empirical/"
                "modeled blend feeding recommendation.py's Top Pick gate) is subject to a real, "
                "measurable winner's-curse effect: candidates selected as recommendation_status == "
                "'top_pick' realize a hit rate below their own mean predicted probability by a larger "
                "margin than the full frozen candidate pool does, because argmax-style selection over "
                "many correlated candidate probabilities preferentially surfaces candidates whose "
                "probability estimate is noisiest on the high side."
            ),
            "population": (
                "All real board_freeze_graded_<date>.json records with grade in {hit, miss}, "
                "fair_test == True, and a non-null prediction.hit_probability, drawn only from dates "
                "with both a sealed board_freeze and a graded file (the same population this report "
                "used), accumulated going forward from 2026-09-20."
            ),
            "comparison": (
                "cluster_bootstrap_group_gap_diff_ci(rows, '_is_top_pick', True, False) from "
                "calibration_lib.py, i.e. (realized_hit_rate - mean_predicted) for "
                "recommendation_status == 'top_pick' minus that same gap for every other real graded, "
                "fair-test candidate, with games as the resampling cluster."
            ),
            "falsification_rule": (
                "The hypothesis is REJECTED if the cluster-bootstrap 95 percent CI for the gap "
                "difference (top_pick minus non-top_pick) includes 0, or if it is centered at/above 0 "
                "(i.e. Top Picks are not worse-calibrated than the rest of the board). It is SUPPORTED "
                "only if that CI excludes 0 on the negative side (Top Picks show a real, larger "
                "over-confidence gap) AND the top_pick sample has reached a predeclared minimum of at "
                "least 30 real fair-test-graded Top Pick candidates spanning at least 10 distinct real "
                f"games -- this report's current n={winners_curse['top_pick']['n']} Top Pick candidates "
                f"across {winners_curse['top_pick']['n_games']} real games is explicitly too small to "
                "run this test with any power; this hypothesis is recorded for a FUTURE workstream to "
                "execute once more real graded days accumulate, not resolved here."
            ),
            "what_would_falsify_the_overall_full_board_calibration_claim": (
                "If, once n is large enough to bucket into true deciles (roughly 300-500+ fair-test-"
                "graded candidates per bucket), any bucket's realized hit rate cluster-bootstrap CI "
                "excludes that bucket's own mean predicted probability, the current probability "
                "pipeline is measurably miscalibrated in that probability range and recalibration "
                "(not merely a different selector policy) would be the falsified target."
            ),
        },
        "honest_limitations": _build_honest_limitations(
            paired_dates=paired_dates,
            board_only_dates=board_only_dates,
            board_metas=board_metas,
            graded_metas=graded_metas,
            fair_rows=fair_rows,
            winners_curse=winners_curse,
            qc_rejected_check=qc_rejected_check,
        ),
    }

    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, sort_keys=False, default=str)

    print(f"Wrote report to {args.report}")
    print(f"Paired dates analyzed: {paired_dates}")
    print(f"Board-only (not yet graded) dates: {board_only_dates}")
    print(f"Total fair-graded-with-probability n: {len(fair_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
