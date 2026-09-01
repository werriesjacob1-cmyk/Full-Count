"""Same-production-event capture tap for the prospective Hits PA-v1 shadow.

Locked protocol section 4.

WHAT THIS IS. One observational read of the Hits research universe, taken
inside the SAME dashboard build process that produces the customer board,
after the board expression is fully materialized and before public
serialization strips the scientific fields. No second network pass, no second
scoring pass -- a re-scored universe is a different universe.

WHAT THIS IS NOT. It is not part of the recommendation path. It reads rows,
never writes them. Nothing here can change a probability, a score, a
recommendation status, a price, or which props reach the site.

NON-BLOCKING BY CONSTRUCTION. capture() cannot raise. Every failure -- a
missing artifact, a hash mismatch, an unwritable ledger, a bug in this file --
is caught, recorded in the returned report, and logged loudly. The customer
dashboard proceeds unchanged. Research observability failing loudly is
correct; research breaking a live board is not.

The one thing capture() will NOT do quietly is score against the wrong model:
if the loaded PA-v1 artifact's scientific hash is not the authoritative frozen
value, capture aborts and reports. A shadow scored by an unknown model is
worse than no shadow.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ARTIFACT = os.path.join(REPO_ROOT, "backtest", "pa_v1_fitted_artifact.json")

# Where the ledger worktree is materialized during a CI run. Deliberately
# outside the product worktree so a research write can never dirty the tree
# the build is being made from.
DEFAULT_LEDGER_WORKTREE = os.path.join(
    os.environ.get("RUNNER_TEMP", "/tmp"), "fullcount-research-ledger")


def _log(message):
    print(f"[prospective-shadow] {message}", flush=True)


def load_artifact(path=DEFAULT_ARTIFACT, *, expected_sha=None):
    """Load the frozen PA-v1 artifact and PROVE it is the authoritative one.

    Verifies both that the artifact's own recorded scientific hash recomputes
    (it has not been edited) and that it equals the pinned authoritative value
    (it is not some other fit). Either failure raises.
    """
    from backtest.pa_v1_fit import recompute_scientific_sha
    from backtest.prospective_receipt import PA_V1_SCIENTIFIC_SHA256

    expected = expected_sha or PA_V1_SCIENTIFIC_SHA256
    with open(path, "r", encoding="utf-8") as fh:
        artifact = json.load(fh)
    claimed = artifact.get("scientific_content_sha256")
    actual = recompute_scientific_sha(artifact)
    if claimed != actual:
        raise ValueError(
            f"PA-v1 artifact at {path} does not verify: claims {claimed}, "
            f"recomputes {actual}. It has been edited.")
    if claimed != expected:
        raise ValueError(
            f"PA-v1 artifact at {path} is not the authoritative freeze: "
            f"{claimed} != {expected}. Refusing to score a shadow against an "
            f"unpinned model.")
    return artifact


def score_pool(pool, artifact):
    """PA-v1 probability per eligible row, plus an explicit fallback state.

    The fallback state is recorded rather than collapsed into a null because
    "PA-v1 had a full joint cell", "PA-v1 fell back to the batting-order
    marginal" and "PA-v1 could not score this at all" are three different
    epistemic situations, and the third is the one that must never silently
    look like the first.
    """
    from backtest.pa_v1_fit import derive_batting_order, joint_key, score

    scores, states = {}, {}
    for row, verdict in pool:
        pid = verdict.get("canonical_prop_id")
        if pid is None:
            continue
        signals = row.get("signals") or {}
        value = score(signals, artifact)
        scores[pid] = value
        if value is None:
            states[pid] = "unscorable_no_batting_order"
        elif joint_key(signals) is not None:
            states[pid] = "joint_cell"
        elif derive_batting_order(signals.get("lineup_slot")) is not None:
            states[pid] = "order_marginal_fallback"
        else:
            states[pid] = "unknown"
    return scores, states


def build_snapshot(*, slate_date, board_generated_at, odds_fetched_at,
                   eligible, rejected, pa_scores, pa_states, artifact_sha,
                   protocol_sha, funnel):
    """The frozen shadow snapshot: the exact universe this epoch saw.

    Carries BOTH the eligible cohort and the rejection funnel. A snapshot that
    recorded only survivors could not later distinguish a correctly gated
    cohort from one that silently shrank.
    """
    return {
        "snapshot_schema_version": 1,
        "slate_date": slate_date,
        "board_generated_at": board_generated_at,
        "odds_fetched_at": odds_fetched_at,
        "pa_v1_artifact_scientific_sha256": artifact_sha,
        "protocol_sha256": protocol_sha,
        "raw_hits_universe_count": len(eligible) + len(rejected),
        "eligible_count": len(eligible),
        "rejected_count": len(rejected),
        "rejection_funnel": funnel,
        "eligible": [
            {
                "canonical_prop_id": v.get("canonical_prop_id"),
                "settlement_identity_key": v.get("identity_key"),
                "expression": v.get("expression"),
                "game_pk": r.get("game_pk"),
                "game_start": v.get("game_start"),
                "player_id": r.get("player_id"),
                "player_name": r.get("name"),
                "team": r.get("team"),
                "champion_probability": r.get("hit_probability"),
                "champion_score": r.get("score"),
                "recommendation_status": r.get("status"),
                "reliability_grade": r.get("reliability"),
                "sample_n": r.get("sample_n"),
                "odds_american": r.get("market_odds"),
                "pa_v1_probability": pa_scores.get(v.get("canonical_prop_id")),
                "pa_v1_fallback_state": pa_states.get(v.get("canonical_prop_id")),
                "signals": r.get("signals") or {},
            }
            for r, v in eligible
        ],
        "rejected": [
            {
                "canonical_prop_id": v.get("canonical_prop_id"),
                "game_pk": r.get("game_pk"),
                "player_id": r.get("player_id"),
                "failed_gates": list(v.get("failed_gates") or ()),
            }
            for r, v in rejected
        ],
    }


def capture(hits_rows, *, slate_date, board_generated_at, odds_fetched_at,
            schedule, board_metadata=None, artifact_path=DEFAULT_ARTIFACT,
            ledger_worktree=DEFAULT_LEDGER_WORKTREE, persist=True,
            source_integrity_holds=frozenset(), now=None):
    """Observe and persist the shadow universe. NEVER RAISES.

    Returns a report dict describing exactly what happened, so a caller can log
    it. The caller is expected to ignore the return value entirely as far as
    production behaviour is concerned.
    """
    report = {"ok": False, "stage": "start", "error": None,
              "captured_at": datetime.now(timezone.utc).isoformat()}
    try:
        from backtest import prospective_eligibility as pe
        from backtest import prospective_epoch as pep
        from backtest import prospective_ledger as pl

        now = now or datetime.now(timezone.utc)

        report["stage"] = "load_artifact"
        artifact = load_artifact(artifact_path)
        artifact_sha = artifact["scientific_content_sha256"]

        # EFFECTIVE-FROM IS A HARD PREREGISTRATION BOUNDARY. The artifact was
        # frozen with a deliberate effective_from that is prior to the first
        # countable receipt. A slate before that instant is not shadow
        # evidence, and backdating after seeing a result is exactly the
        # failure the field exists to prevent.
        report["stage"] = "effective_from"
        effective_from = artifact.get("effective_from")
        eff = pep._parse(effective_from)
        board_dt = pep._parse(board_generated_at)
        if eff is not None and board_dt is not None and board_dt < eff:
            report.update(ok=True, skipped=True,
                          reason=f"board_generated_at {board_generated_at} precedes "
                                 f"PA-v1 effective_from {effective_from}")
            _log(report["reason"])
            return report

        report["stage"] = "gate"
        eligible, rejected = pe.partition(
            hits_rows, now=now, schedule=schedule,
            odds_fetched_at=odds_fetched_at,
            board_generated_at=board_generated_at,
            source_integrity_holds=source_integrity_holds)
        funnel = pe.funnel_counts(rejected)
        report.update(raw_count=len(hits_rows or []),
                      eligible_count=len(eligible),
                      rejected_count=len(rejected),
                      rejection_funnel=funnel)

        report["stage"] = "score"
        pa_scores, pa_states = score_pool(eligible, artifact)
        report["pa_scored"] = sum(1 for v in pa_scores.values() if v is not None)
        report["pa_unscorable"] = sum(1 for v in pa_scores.values() if v is None)

        report["stage"] = "snapshot"
        snapshot = build_snapshot(
            slate_date=slate_date, board_generated_at=board_generated_at,
            odds_fetched_at=odds_fetched_at, eligible=eligible,
            rejected=rejected, pa_scores=pa_scores, pa_states=pa_states,
            artifact_sha=artifact_sha, protocol_sha=pe.PROTOCOL_SHA256,
            funnel=funnel)
        snap_sha = pep.snapshot_sha256(snapshot)
        snapshot["snapshot_content_sha256"] = snap_sha

        report["stage"] = "epoch_candidate"
        candidate = pep.build_epoch_candidate(
            slate_date=slate_date, board_generated_at=board_generated_at,
            odds_fetched_at=odds_fetched_at, snapshot_sha=snap_sha)
        report["epoch_candidate_id"] = candidate["epoch_candidate_id"]
        report["snapshot_content_sha256"] = snap_sha

        if not persist:
            report.update(ok=True, persisted=False)
            _log(f"captured {len(eligible)}/{len(hits_rows or [])} eligible "
                 f"(dry run, not persisted); snapshot {snap_sha[:12]}")
            return report

        report["stage"] = "ledger"
        pl.ensure_ledger_worktree(REPO_ROOT, ledger_worktree)
        path = os.path.join(ledger_worktree, pl.LEDGER_RELPATH)
        events = [
            pl.make_event(pl.EVENT_EPOCH_BOUND,
                          candidate["epoch_candidate_id"],
                          {"candidate": candidate, "snapshot": snapshot}),
        ]
        result = pl.append_events(path, events)
        report["ledger"] = result

        report["stage"] = "push"
        report["push"] = pl.commit_and_push(
            ledger_worktree,
            f"shadow snapshot {slate_date} {candidate['epoch_candidate_id']}")
        if not report["push"].get("pushed") and report["push"].get("committed"):
            _log(f"WARNING: ledger committed locally but push failed: "
                 f"{report['push'].get('error')}")

        report.update(ok=True, persisted=True, stage="done")
        _log(f"captured {len(eligible)}/{len(hits_rows or [])} eligible Hits rows; "
             f"snapshot {snap_sha[:12]}; ledger {result}")
        return report

    except BaseException as exc:      # noqa: BLE001 -- see module docstring
        # DELIBERATELY BaseException. A research tap that lets anything at all
        # escape into the customer build has failed at its single most
        # important requirement. Loud in research observability, invisible to
        # production output.
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        _log(f"CAPTURE FAILED at stage={report['stage']}: {report['error']}")
        _log(report["traceback"])
        return report
