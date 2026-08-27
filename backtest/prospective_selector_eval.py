#!/usr/bin/env python3
"""Locked prospective selector evaluation plans.

A prospective dataset only protects research integrity if the observation and
challenger are fixed BEFORE outcomes are known. This module makes that rule
executable:

1. lock selects explicit durable snapshot IDs and one challenger ranking.
2. Locking fails if any durable outcomes already exist for a selected slate.
3. The plan binds each snapshot's candidate-universe fingerprint.
4. evaluate later reloads those exact snapshots and settled outcomes.
5. Champion/challenger comparison is exact-volume and aggregation resamples
   whole slate/date clusters.

This module never chooses the best-looking historical snapshot, never picks a
challenger after seeing results, and never emits a promotion verdict.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os

try:
    from backtest import prospective_durability as pdur
    from backtest import prospective_reporting as pr
except ImportError:
    import prospective_durability as pdur
    import prospective_reporting as pr


PLAN_SCHEMA_VERSION = 1
ALLOWED_CHALLENGER_RANKINGS = (
    "edge_vs_fair",
    "hit_probability",
    "score",
)


class ProspectivePlanError(RuntimeError):
    pass


def _canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _fingerprint(plan_without_fingerprint):
    return hashlib.sha256(_canonical_bytes(plan_without_fingerprint)).hexdigest()


def _normalize_observations(observations):
    normalized = []
    for obs in observations:
        date = obs.get("slate_date")
        sid = obs.get("snapshot_id")
        if not date or not sid:
            raise ProspectivePlanError(
                "every observation needs slate_date and snapshot_id")
        normalized.append({
            "slate_date": str(date),
            "snapshot_id": str(sid),
        })
    dates = [o["slate_date"] for o in normalized]
    if len(dates) != len(set(dates)):
        raise ProspectivePlanError(
            "one locked observation per slate/date is required; duplicate dates "
            "would double-count the same realized games")
    snapshot_ids = [o["snapshot_id"] for o in normalized]
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise ProspectivePlanError("duplicate snapshot_id in evaluation plan")
    return sorted(normalized, key=lambda o: o["slate_date"])


def lock_plan(durable_root, *, observations, challenger_ranking,
              locked_at=None):
    """Build a plan only while selected slates still have no durable outcomes."""
    if challenger_ranking not in ALLOWED_CHALLENGER_RANKINGS:
        raise ProspectivePlanError(
            f"unsupported challenger ranking {challenger_ranking!r}")
    observations = _normalize_observations(observations)

    bound = []
    for obs in observations:
        date = obs["slate_date"]
        sid = obs["snapshot_id"]
        if pdur.load_materialized_outcomes(durable_root, date=date):
            raise ProspectivePlanError(
                f"cannot lock {date}/{sid}: outcomes already exist; selecting an "
                "observation now would be post-outcome research")
        manifest, _records = pdur.load_materialized_snapshot(
            durable_root, date=date, snapshot_id=sid)
        bound.append({
            **obs,
            "observed_at": manifest.get("observed_at"),
            "n_candidates": manifest.get("n_candidates"),
            "candidate_universe_fingerprint": (
                manifest.get("candidate_universe_fingerprint")
            ),
        })

    core = {
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "locked_at": (
            locked_at
            or datetime.now(timezone.utc).isoformat()
        ),
        "challenger_ranking": challenger_ranking,
        "observations": bound,
        "research_contract": {
            "equal_volume": True,
            "one_observation_per_slate_date": True,
            "outcomes_absent_at_lock": True,
            "promotion_verdict": False,
        },
    }
    return {**core, "plan_fingerprint": _fingerprint(core)}


def validate_plan(durable_root, plan):
    if plan.get("plan_schema_version") != PLAN_SCHEMA_VERSION:
        raise ProspectivePlanError(
            f"unsupported plan schema {plan.get('plan_schema_version')!r}")
    ranking = plan.get("challenger_ranking")
    if ranking not in ALLOWED_CHALLENGER_RANKINGS:
        raise ProspectivePlanError(
            f"invalid locked challenger ranking {ranking!r}")

    supplied_fp = plan.get("plan_fingerprint")
    core = {k: v for k, v in plan.items() if k != "plan_fingerprint"}
    expected_fp = _fingerprint(core)
    if supplied_fp != expected_fp:
        raise ProspectivePlanError(
            "evaluation plan fingerprint mismatch; locked protocol was edited")

    observations = _normalize_observations(plan.get("observations") or [])
    by_key = {
        (o["slate_date"], o["snapshot_id"]): o
        for o in plan.get("observations") or []
    }
    for obs in observations:
        date, sid = obs["slate_date"], obs["snapshot_id"]
        locked = by_key[(date, sid)]
        manifest, _records = pdur.load_materialized_snapshot(
            durable_root, date=date, snapshot_id=sid)
        checks = {
            "observed_at": manifest.get("observed_at"),
            "n_candidates": manifest.get("n_candidates"),
            "candidate_universe_fingerprint": (
                manifest.get("candidate_universe_fingerprint")
            ),
        }
        for field, actual in checks.items():
            if locked.get(field) != actual:
                raise ProspectivePlanError(
                    f"locked snapshot evidence drift for {date}/{sid}: "
                    f"{field}={locked.get(field)!r} now {actual!r}")
    return True


def evaluate_plan(durable_root, plan, *, bootstrap_samples=4000, seed=0):
    """Evaluate the locked protocol against outcomes; fail on incomplete slates."""
    validate_plan(durable_root, plan)
    ranking = plan["challenger_ranking"]
    reports = []

    for obs in sorted(
            plan["observations"], key=lambda o: o["slate_date"]):
        date = obs["slate_date"]
        manifest, records = pdur.load_materialized_snapshot(
            durable_root, date=date, snapshot_id=obs["snapshot_id"])
        outcomes = pdur.load_materialized_outcomes(
            durable_root, date=date)
        report = pr.equal_volume_selector_comparison(
            records,
            outcomes,
            challenger_ranking=ranking,
            observed_at=manifest.get("observed_at"),
            slate_date=date,
        )
        reports.append(report)

    aggregate = pr.aggregate_equal_volume_comparisons(
        reports, bootstrap_samples=bootstrap_samples, seed=seed)
    return {
        "plan_fingerprint": plan["plan_fingerprint"],
        "challenger_ranking": ranking,
        "per_slate": reports,
        "aggregate": aggregate,
        "promotion_verdict": None,
        "promotion_note": (
            "Measurement only. Promotion requires independent review of dataset "
            "identity, operational volume, stability, leakage, and uncertainty."
        ),
    }


def write_plan(plan, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)


def read_plan(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _parse_observation_arg(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "observation must be YYYY-MM-DD=SNAPSHOT_ID")
    date, sid = value.split("=", 1)
    if not date or not sid:
        raise argparse.ArgumentTypeError(
            "observation must be YYYY-MM-DD=SNAPSHOT_ID")
    return {"slate_date": date, "snapshot_id": sid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    lock_parser = sub.add_parser("lock")
    lock_parser.add_argument("--durable-root", required=True)
    lock_parser.add_argument(
        "--challenger-ranking",
        choices=ALLOWED_CHALLENGER_RANKINGS,
        required=True)
    lock_parser.add_argument(
        "--observation", action="append", type=_parse_observation_arg,
        required=True,
        help="repeatable YYYY-MM-DD=SNAPSHOT_ID")
    lock_parser.add_argument("--output", required=True)

    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--durable-root", required=True)
    evaluate_parser.add_argument("--plan", required=True)
    evaluate_parser.add_argument("--bootstrap-samples", type=int, default=4000)
    evaluate_parser.add_argument("--seed", type=int, default=0)

    args = parser.parse_args()
    if args.command == "lock":
        plan = lock_plan(
            args.durable_root,
            observations=args.observation,
            challenger_ranking=args.challenger_ranking,
        )
        write_plan(plan, args.output)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    plan = read_plan(args.plan)
    result = evaluate_plan(
        args.durable_root, plan,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
