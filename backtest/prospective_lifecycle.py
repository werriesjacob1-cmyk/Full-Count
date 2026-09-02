"""The prospective Hits PA-v1 lifecycle state machine.

Mission 1 built correct components and never connected them. An independent
audit found that `build_epoch_selection` had NO CALLER ANYWHERE, tests
included; that `regate_pool` -- the documented fix for the two-clock asymmetry
-- was never invoked; that no module had a `__main__`, a `main()` or an
argparse block; and that no workflow referenced the shadow at all. A
unit-tested helper nobody calls is not a safeguard.

This module is the missing state machine, and it is executable.

    python3 -m backtest.prospective_lifecycle bind-exposure --deployment D.json
    python3 -m backtest.prospective_lifecycle designate     --slate-date DATE
    python3 -m backtest.prospective_lifecycle settle        --slate-date DATE
    python3 -m backtest.prospective_lifecycle report

═══════════════════════════════════════════════════════════════════════════
WHY SELECTIONS ARE SEALED AT CONVERGENCE, NOT AFTER THE DATE
═══════════════════════════════════════════════════════════════════════════

The tempting design is to wait until the games are over, pick the decisive
epoch, and only then build the comparison. That is strictly weaker, because
the pool, the verdicts and the receipts would all be reconstructed after
outcomes are visible, through functions whose evaluation clock the caller
chooses.

So EVERY successfully bound epoch seals its own complete comparison at public
convergence time -- champion set, PA-v1 selection at exact matched volume, and
immutable receipts -- BEFORE any game has been decided. Designating which of
those already-sealed epochs is the date's primary is then a separate, later,
purely mechanical read over deployment metadata. It cannot change a single
selection; it can only point at one of several sealed sets.

That ordering is what makes the eventual claim survive an adversarial reading:
the selections provably existed, hashed and remotely durable, before the
outcomes did.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import prospective_epoch as pep  # noqa: E402
from backtest import prospective_ledger as pl  # noqa: E402
from backtest import prospective_receipt as pr  # noqa: E402
from backtest import prospective_selection as ps  # noqa: E402
from backtest import prospective_settlement as pset  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LEDGER_WORKTREE = os.path.join(
    os.environ.get("RUNNER_TEMP", "/tmp"), "fullcount-research-ledger")


def _log(msg):
    print(f"[prospective-lifecycle] {msg}", flush=True)


def _read_ledger(worktree, slate_date):
    return pl.read_events(os.path.join(worktree, pl.ledger_relpath(slate_date)))


def _by_type(events, event_type):
    return [e for e in events if e.get("event_type") == event_type]


# ── stage 1: bind public exposure and seal the comparison ───────────────

def bind_exposure(deployment, *, worktree, payload, schedule=None,
                  branch=pl.LEDGER_BRANCH):
    """Bind a converged deployment to its snapshot and seal the comparison.

    Returns a report. Every outcome -- including the negative ones -- is
    recorded as an immutable ledger event, because "this epoch produced
    nothing, and here is exactly why" is a §12 reporting requirement and must
    never be inferred from absence.
    """
    slate_date = deployment.get("slate_date")
    if not slate_date:
        return {"ok": False, "error": "deployment has no slate_date"}

    events = _read_ledger(worktree, slate_date)
    snapshots = _by_type(events, pl.EVENT_SNAPSHOT_CAPTURED)
    if not snapshots:
        reason = ("no durably persisted pregame snapshot for this date; an "
                  "epoch with no remote pregame evidence can never be counted")
        # Recorded, not merely returned. This was the one path that produced
        # no negative evidence, so the per-deployment reason was lost.
        try:
            pl.append_and_push(
                worktree, slate_date,
                [pl.make_event(
                    pl.EVENT_EPOCH_FAILED_CLOSED,
                    f"{slate_date}:deploy-{deployment.get('run_id')}"
                    f":{deployment.get('artifact_id')}",
                    {"slate_date": slate_date, "stage": "bind",
                     "deployment_run_id": deployment.get("run_id"),
                     "reason": reason}, writer="prospective_lifecycle")],
                branch=branch, message=f"no pregame evidence {slate_date}")
        except pl.LedgerConflict:
            pass    # already recorded for this exact deployment
        return {"ok": False, "bound": False, "error": reason}

    # Find the snapshot this deployment actually published. The join key is the
    # board's generated_at, which the deploy workflow proves against the PUBLIC
    # url -- not a timestamp correlation.
    bound_epoch, chosen = None, None
    reasons = []
    for ev in snapshots:
        candidate = (ev.get("body") or {}).get("candidate") or {}
        result = pep.bind_deployment(candidate, deployment)
        if result.get("bound"):
            bound_epoch, chosen = result["epoch"], ev
            break
        reasons.append({"candidate": candidate.get("epoch_candidate_id"),
                        "reasons": result.get("reasons")})

    if bound_epoch is None:
        out = [pl.make_event(
            pl.EVENT_EPOCH_FAILED_CLOSED,
            # Keyed by the DEPLOYMENT's full identity. A run id alone is not
            # unique enough -- one run can prepare more than one artifact --
            # and a weak key would make two genuinely different failures
            # collide as an illegal edit.
            f"{slate_date}:deploy-{deployment.get('run_id')}"
            f":{deployment.get('artifact_id')}",
            {"slate_date": slate_date, "stage": "bind",
             "deployment_run_id": deployment.get("run_id"),
             "reason": "no captured snapshot matched this deployment",
             "detail": reasons}, writer="prospective_lifecycle")]
        pl.append_and_push(worktree, slate_date, out, branch=branch,
                           message=f"bind failed {slate_date}")
        return {"ok": True, "bound": False, "reasons": reasons}

    snapshot = (chosen.get("body") or {}).get("snapshot") or {}

    # Reconstruct (row, verdict) pairs from SEALED evidence only. Never from a
    # live board: the raw candidate is long gone, and re-reading a newer board
    # to fill a field is the late-information leak this whole design forbids.
    pool, universe, pa_scores = [], [], {}
    for entry in snapshot.get("eligible") or []:
        basis = entry.get("receipt_basis")
        if not basis:
            return {"ok": False, "error":
                    "sealed snapshot has no receipt_basis; this snapshot "
                    "predates the completeness fix and cannot produce a "
                    "receipt without re-reading a later board"}
        row, verdict = pr.basis_to_inputs(basis)
        pool.append((row, verdict))
        universe.append((row, verdict))
        pa_scores[verdict.get("canonical_prop_id")] = entry.get("pa_v1_probability")
    for entry in snapshot.get("rejected") or []:
        universe.append(({"game_pk": entry.get("game_pk"),
                          "player_id": entry.get("player_id"),
                          "name": entry.get("player_name")},
                         {"canonical_prop_id": entry.get("canonical_prop_id"),
                          "gates": entry.get("gates"),
                          "failed_gates": entry.get("failed_gates"),
                          "game_start": entry.get("game_start"),
                          "expression": entry.get("expression")}))

    out_events = [pl.make_event(
        pl.EVENT_PUBLIC_EXPOSURE_BOUND, bound_epoch["decisive_epoch_id"],
        {"epoch": bound_epoch, "snapshot_content_sha256":
            snapshot.get("snapshot_content_sha256")},
        writer="prospective_lifecycle")]

    try:
        selection = ps.build_epoch_selection(
            epoch=bound_epoch, payload=payload, universe=universe, pool=pool,
            pa_scores=pa_scores, schedule=schedule or {})
    except ps.EpochFailedClosed as exc:
        out_events.append(pl.make_event(
            pl.EVENT_EPOCH_FAILED_CLOSED, bound_epoch["decisive_epoch_id"],
            {"slate_date": slate_date, "stage": "selection",
             "epoch_id": bound_epoch["decisive_epoch_id"],
             "reason": str(exc)}, writer="prospective_lifecycle"))
        res = pl.append_and_push(worktree, slate_date, out_events, branch=branch,
                                 message=f"epoch failed closed {slate_date}")
        return {"ok": True, "bound": True, "sealed": False,
                "failed_closed": str(exc), "ledger": res}

    if selection is None:
        out_events.append(pl.make_event(
            pl.EVENT_EPOCH_FAILED_CLOSED, bound_epoch["decisive_epoch_id"],
            {"slate_date": slate_date, "stage": "selection",
             "epoch_id": bound_epoch["decisive_epoch_id"],
             "reason": "N(date) == 0: no exposed Hits Top Pick in this "
                       "artifact's re-gated universe. No comparison is "
                       "created and nothing is manufactured."},
            writer="prospective_lifecycle"))
        res = pl.append_and_push(worktree, slate_date, out_events, branch=branch,
                                 message=f"no comparison {slate_date}")
        return {"ok": True, "bound": True, "sealed": False, "n": 0, "ledger": res}

    # Seal the selection, then one immutable receipt per selected wager --
    # BEFORE any outcome exists.
    champion_ids = {pid for pid, _ in selection["champion_selected"]}
    pa_ids = {r["canonical_prop_id"] for r in selection["pa_v1_selected"]}
    pa_rank = {r["canonical_prop_id"]: r["pa_v1_rank"]
               for r in selection["pa_v1_selected"]}

    out_events.append(pl.make_event(
        pl.EVENT_EPOCH_SELECTION_SEALED, bound_epoch["decisive_epoch_id"],
        {k: v for k, v in selection.items() if k not in
         ("champion_selected", "pa_v1_selected", "pa_v1_ranked")}
        | {"champion_ids": sorted(champion_ids),
           "pa_v1_ids": sorted(pa_ids)},
        writer="prospective_lifecycle"))

    meta = dict(snapshot.get("board_metadata") or {})
    receipts = 0
    for pid in sorted(champion_ids | pa_ids):
        entry = next((e for e in snapshot.get("eligible") or []
                      if e.get("canonical_prop_id") == pid), None)
        if entry is None:
            continue
        row, verdict = pr.basis_to_inputs(entry["receipt_basis"])
        receipt = pr.build_receipt(
            row, verdict,
            epoch=bound_epoch,
            snapshot_id=chosen.get("idempotent_key"),
            snapshot_sha256=snapshot.get("snapshot_content_sha256"),
            slate_date=slate_date,
            pa_probability=entry.get("pa_v1_probability"),
            pa_fallback_state=entry.get("pa_v1_fallback_state"),
            champion_member=pid in champion_ids,
            champion_rank=selection["champion_ranks"].get(pid),
            pa_member=pid in pa_ids,
            pa_rank=pa_rank.get(pid),
            board_metadata=meta,
            source_integrity_state=(snapshot.get("source_integrity") or {}).get("state"),
            repo_git_sha=meta.get("git_sha"),
            pa_compat_version=snapshot.get("pa_v1_compat_version"),
            pa_compat=entry.get("pa_v1_compat"))
        out_events.append(pl.make_event(
            pl.EVENT_PREGAME_RECEIPT, receipt["receipt_id"], receipt,
            writer="prospective_lifecycle"))
        receipts += 1

    res = pl.append_and_push(
        worktree, slate_date, out_events, branch=branch,
        message=f"sealed epoch {bound_epoch['decisive_epoch_id']} "
                f"N={selection['n']}")
    return {"ok": True, "bound": True, "sealed": True, "n": selection["n"],
            "receipts": receipts, "epoch_id": bound_epoch["decisive_epoch_id"],
            "ledger": res}


# ── stage 2: designate the one decisive epoch, outcome-blind ────────────

def designate(slate_date, *, worktree, branch=pl.LEDGER_BRANCH):
    """Pick the date's primary epoch from already-sealed bound epochs.

    Reads ONLY deployment metadata. It cannot see a probability, a rank or a
    result, and it cannot change any selection -- every candidate epoch was
    sealed before outcomes existed. It only points at one of them.
    """
    events = _read_ledger(worktree, slate_date)
    sealed = {e["idempotent_key"] for e in _by_type(events, pl.EVENT_EPOCH_SELECTION_SEALED)}
    bound = [(e["body"] or {}).get("epoch") or {}
             for e in _by_type(events, pl.EVENT_PUBLIC_EXPOSURE_BOUND)]
    eligible = [ep for ep in bound if ep.get("decisive_epoch_id") in sealed]

    if not eligible:
        ev = pl.make_event(
            pl.EVENT_NO_PRIMARY_EPOCH, slate_date,
            {"slate_date": slate_date,
             "reason": "no bound epoch with a sealed selection",
             "bound_epochs": len(bound), "sealed_selections": len(sealed)},
            writer="prospective_lifecycle")
        res = pl.append_and_push(worktree, slate_date, [ev], branch=branch,
                                 message=f"no primary epoch {slate_date}")
        return {"ok": True, "primary": None,
                "reason": "no bound epoch with a sealed selection",
                "ledger": res}

    # LATEST by the artifact's own preparation instant -- the clock the
    # publication cutoff is measured against. Not convergence time, which
    # varies with CDN propagation and would make the choice depend on
    # infrastructure noise.
    #
    # PARSED, never sorted as a string. A red team found this sorting
    # lexicographically, which orders "+00:00" before "Z" at the SAME instant
    # ('+' is 0x2B, 'Z' is 0x5A) and mis-orders any mixed-offset stamp. Ties
    # break on the epoch id so the choice is deterministic per experiment
    # rather than a function of ledger append order, which depends on the
    # concurrent-push race.
    eligible.sort(key=lambda ep: (
        pep._parse(ep.get("deployment_prepared_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        str(ep.get("decisive_epoch_id") or "")))
    primary = eligible[-1]
    ev = pl.make_event(
        pl.EVENT_DECISIVE_EPOCH_DESIGNATED, slate_date,
        {"slate_date": slate_date,
         "decisive_epoch_id": primary.get("decisive_epoch_id"),
         "deployment_prepared_at": primary.get("deployment_prepared_at"),
         "deployment_artifact_id": primary.get("deployment_artifact_id"),
         "candidates_considered": sorted(
             ep.get("decisive_epoch_id") for ep in eligible),
         "rule": "latest converged Dashboard Refresh-originated deployment "
                 "with a hash-bound snapshot and a sealed selection"},
        writer="prospective_lifecycle")
    res = pl.append_and_push(worktree, slate_date, [ev], branch=branch,
                             message=f"designate {slate_date}")
    return {"ok": True, "primary": primary.get("decisive_epoch_id"),
            "considered": len(eligible), "ledger": res}


# ── stage 3: settle the decisive epoch's receipts ───────────────────────

def settle_date(slate_date, *, worktree, contexts, branch=pl.LEDGER_BRANCH,
                grader=None):
    """Settle exactly the receipts of the designated primary epoch.

    Idempotent: a retry after a partial run appends only the missing,
    byte-identical events. A changed settlement under the same key raises
    rather than silently overwriting prior evidence.
    """
    events = _read_ledger(worktree, slate_date)
    designated = _by_type(events, pl.EVENT_DECISIVE_EPOCH_DESIGNATED)
    if not designated:
        return {"ok": False, "error": "no decisive epoch designated for this date"}
    epoch_id = (designated[-1]["body"] or {}).get("decisive_epoch_id")

    receipts = [e["body"] for e in _by_type(events, pl.EVENT_PREGAME_RECEIPT)
                if (e["body"] or {}).get("decisive_epoch_id") == epoch_id]
    if not receipts:
        return {"ok": True, "settled": 0,
                "note": f"no sealed receipts for {epoch_id}"}

    # NOTE: already-settled receipts are deliberately re-graded rather than
    # short-circuited. The ledger's own append_events() drops a byte-identical
    # repeat and RAISES LedgerConflict on a changed settlement under the same
    # key; skipping them here would silently disarm that check.
    out, skipped, pending = [], 0, []
    for receipt in receipts:
        ctx = (contexts or {}).get(receipt.get("game_pk")) or {}
        ev = pset.settle(receipt, ctx, date=slate_date, grader=grader)
        pset.verify_pairing(receipt, ev)
        # ONLY TERMINAL SETTLEMENTS ARE APPENDED. `ungraded` in this codebase
        # is "not knowable yet" -- "game not final yet (status: In Progress)"
        # is its single commonest cause -- not a verdict. The ledger is
        # append-only and immutable, so writing a non-final settlement would
        # seal a non-answer PERMANENTLY: the later run holding the real box
        # score raises LedgerConflict instead of recording the true grade, and
        # the wager is lost from the decided denominator forever.
        #
        # A skipped receipt is NOT lost evidence. prospective_scoreboard reads
        # an unsettled receipt as outcome "ungraded", so it still counts in
        # selected_n and in ungraded_rate -- it simply stays eligible to be
        # settled truthfully on a later pass, which is exactly the behaviour
        # grade_results.dates_needing_grading() already relies on.
        if ev.get("outcome") not in pset.TERMINAL_OUTCOMES:
            skipped += 1
            pending.append({"receipt_id": receipt["receipt_id"],
                            "canonical_prop_id": receipt.get("canonical_prop_id"),
                            "reason": ev.get("settlement_reason")})
            continue
        out.append(pl.make_event(pl.EVENT_SETTLEMENT, receipt["receipt_id"], ev,
                                 writer="prospective_lifecycle"))

    res = (pl.append_and_push(worktree, slate_date, out, branch=branch,
                              message=f"settle {slate_date} {epoch_id}")
           if out else {"appended": 0, "note": "nothing terminal to settle"})
    return {"ok": True, "settled": len(out), "skipped": skipped,
            "pending": pending, "epoch_id": epoch_id, "ledger": res}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=["bind-exposure", "designate", "settle",
                                      "report"])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER_WORKTREE)
    ap.add_argument("--branch", default=pl.LEDGER_BRANCH)
    ap.add_argument("--slate-date")
    ap.add_argument("--deployment", help="path to the deployment observation JSON")
    ap.add_argument("--payload", help="path to the deployed data.json")
    ap.add_argument("--contexts",
                    help="path to a JSON object mapping game_pk -> the "
                         "settlement context grade_results.grade_public_pick "
                         "reads (box score / status). Without it every "
                         "receipt grades 'ungraded' and nothing is settled.")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    if a.stage == "bind-exposure":
        if not a.deployment or not a.payload:
            raise SystemExit("--deployment and --payload are required")
        deployment = json.load(open(a.deployment))
        payload = json.load(open(a.payload))
        report = bind_exposure(deployment, worktree=a.ledger, payload=payload,
                               branch=a.branch)
    elif a.stage == "designate":
        report = designate(a.slate_date, worktree=a.ledger, branch=a.branch)
    elif a.stage == "settle":
        contexts = {}
        if a.contexts:
            # Keys arrive as JSON object keys (strings); receipts carry game_pk
            # as an int. Index BOTH so a caller cannot silently settle nothing.
            raw = json.load(open(a.contexts))
            for k, v in (raw or {}).items():
                contexts[k] = v
                try:
                    contexts[int(k)] = v
                except (TypeError, ValueError):
                    pass
        report = settle_date(a.slate_date, worktree=a.ledger, contexts=contexts,
                             branch=a.branch)
    else:
        from backtest import prospective_scoreboard as psb
        report = psb.build_report(a.ledger)

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(text)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
