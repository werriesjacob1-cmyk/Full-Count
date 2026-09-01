"""Decisive-epoch binding for the prospective Hits PA-v1 shadow.

Locked protocol sections 7 and 8.

ONE DECISIVE EPOCH PER SLATE DATE. The dashboard rebuilds eight times a day
and refreshes prices far more often than that. If every snapshot counted, the
same wager would enter the scoreboard repeatedly and the effective sample size
would be a fiction. So exactly one full-build epoch per MLB slate date is
decisive, chosen AFTER the date is over, MECHANICALLY and OUTCOME-BLIND:

    the latest successfully converged Dashboard Refresh-originated Pages
    deployment whose artifact was prepared early enough to admit new Top Picks
    under the existing publication cutoff contract, and whose shadow snapshot
    is hash-bound to that exact full build.

"Outcome-blind" is the whole point of fixing the rule in code: the selection
reads deployment identity and timestamps only. It cannot see a single result.

WHY "Dashboard Refresh-ORIGINATED" MATTERS. dashboard-deploy.yml triggers on
workflow_run from BOTH `Dashboard Refresh` and `Dashboard Live Update`. Only
the former re-materializes the full in-memory scoring event that PA-v1 needs
its features from; a live-update deployment carries a price overlay over an
older model basis. Live-update deployments are retained as secondary
observations and are never primary.

THE HASH BINDING. The build writes `board_generated_at`, which travels into
the artifact as `data.json`'s `generated_at`. The deploy workflow's existing
convergence check polls the PUBLIC url until both `publication_manifest.json`
`source_commit` AND `data.json` `generated_at` match the artifact. So
`generated_at` is an exact join key between a shadow snapshot and a deployment
that provably went public -- not a timestamp correlation.

NO ELIGIBLE DEPLOYMENT MEANS NO PRIMARY EPOCH. A date with no converged
refresh-originated deployment is missing operational evidence. It is not a
loss, and it is emphatically not an invitation to fall back to a
live-update snapshot or an earlier build that happens to look better.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.prospective_eligibility import (  # noqa: E402
    PUBLICATION_LEAD_SECONDS,
    admits_new_top_pick,
    evaluate_row,
)
from backtest.prospective_receipt import canonical_json  # noqa: E402

REFRESH_WORKFLOW_NAME = "Dashboard Refresh"
LIVE_WORKFLOW_NAME = "Dashboard Live Update"
DEPLOY_WORKFLOW_NAME = "Dashboard Pages Deploy"

ORIGIN_FULL_REFRESH = "full_refresh"
ORIGIN_LIVE_UPDATE = "live_update"
ORIGIN_UNKNOWN = "unknown"


class NoPrimaryEpoch(Exception):
    """This slate date has no decisive epoch. Missing evidence, not a loss."""


def _parse(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc))


def build_identity(env=None):
    """Capture-time identity of the full build currently running.

    Read from the GitHub Actions environment. Every field is honestly None
    outside Actions, so a local dry run is visibly a local dry run rather than
    silently claiming CI provenance.
    """
    env = env if env is not None else os.environ
    return {
        "workflow_name": env.get("GITHUB_WORKFLOW"),
        "workflow_run_id": env.get("GITHUB_RUN_ID"),
        "workflow_run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "source_commit": env.get("GITHUB_SHA"),
        "event_name": env.get("GITHUB_EVENT_NAME"),
        "repository": env.get("GITHUB_REPOSITORY"),
        "runner_environment": "github_actions" if env.get("GITHUB_ACTIONS") else "local",
    }


def origin_of(workflow_name):
    """Classify a deployment's triggering workflow."""
    if workflow_name == REFRESH_WORKFLOW_NAME:
        return ORIGIN_FULL_REFRESH
    if workflow_name == LIVE_WORKFLOW_NAME:
        return ORIGIN_LIVE_UPDATE
    return ORIGIN_UNKNOWN


def epoch_candidate_id(slate_date, board_generated_at, snapshot_sha256):
    """Stable id for one captured full-build epoch candidate.

    Includes the snapshot hash, so two builds that somehow shared a
    generated_at could never collide into one epoch id.
    """
    token = f"{slate_date}\x1f{board_generated_at}\x1f{snapshot_sha256}"
    return f"{slate_date}:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:24]}"


def snapshot_sha256(payload):
    """Content hash of a captured shadow snapshot."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_epoch_candidate(*, slate_date, board_generated_at, odds_fetched_at,
                          snapshot_sha, identity=None, lineups_observed_at=None):
    """The PREGAME half of the two-stage exposure proof.

    Recorded at capture, before any deployment exists. It asserts nothing about
    public exposure -- that is stage two, and it is a separate later event.
    """
    ident = identity or build_identity()
    return {
        "epoch_candidate_id": epoch_candidate_id(slate_date, board_generated_at,
                                                 snapshot_sha),
        "slate_date": slate_date,
        "board_generated_at": board_generated_at,
        "odds_fetched_at": odds_fetched_at,
        "lineups_observed_at": lineups_observed_at,
        "shadow_snapshot_sha256": snapshot_sha,
        "origin": origin_of(ident.get("workflow_name")),
        "build_workflow_name": ident.get("workflow_name"),
        "build_workflow_run_id": ident.get("workflow_run_id"),
        "build_workflow_run_attempt": ident.get("workflow_run_attempt"),
        "build_source_commit": ident.get("source_commit"),
        "build_event_name": ident.get("event_name"),
        "repository": ident.get("repository"),
        "runner_environment": ident.get("runner_environment"),
        "publicly_converged": False,   # stage two has not happened yet
    }


def bind_deployment(candidate, deployment):
    """The SECOND stage: mark a captured epoch as operationally countable.

    A deployment binds to a candidate only when it PROVES the same build went
    public. Every condition below is required; each one is a way the binding
    could otherwise be faked by coincidence.
    """
    reasons = []
    if origin_of(deployment.get("triggering_workflow_name")) != ORIGIN_FULL_REFRESH:
        reasons.append("deployment not originated by Dashboard Refresh")
    if not deployment.get("converged"):
        reasons.append("deployment never converged publicly")
    if deployment.get("public_generated_at") != candidate.get("board_generated_at"):
        reasons.append(
            f"public data.json generated_at "
            f"{deployment.get('public_generated_at')!r} != captured "
            f"board_generated_at {candidate.get('board_generated_at')!r}")
    if (candidate.get("build_source_commit")
            and deployment.get("source_commit")
            and deployment["source_commit"] != candidate["build_source_commit"]):
        reasons.append("deployment source_commit != build source_commit")
    prepared_at = _parse(deployment.get("prepared_at"))
    if prepared_at is None:
        reasons.append("deployment has no parsable prepared_at")
    if reasons:
        return {"bound": False, "reasons": reasons}
    bound = dict(candidate)
    bound.update({
        "decisive_epoch_id": candidate["epoch_candidate_id"],
        "publicly_converged": True,
        "deployment_artifact_id": deployment.get("artifact_id"),
        "deployment_source_commit": deployment.get("source_commit"),
        "deployment_prepared_at": deployment.get("prepared_at"),
        "deployment_converged_at": deployment.get("converged_at"),
        "deployment_run_id": deployment.get("run_id"),
        "deployment_page_url": deployment.get("page_url"),
        "publication_cutoff_at": deployment.get("publication_cutoff_at"),
        "champion_candidate_ids": sorted(deployment.get("candidate_ids") or []),
    })
    return {"bound": True, "epoch": bound}


def select_decisive_epoch(candidates, deployments, slate_date):
    """Choose the ONE decisive epoch for a slate date. Outcome-blind.

    Selection reads only: origin workflow, public convergence, the generated_at
    hash binding, and prepared_at. It never reads a probability, a rank, or a
    result -- there is nothing in the inputs that could tell it who won.

    Raises NoPrimaryEpoch when nothing qualifies. That is the correct outcome
    for a date with missing operational evidence, and it must not be worked
    around by relaxing to a live-update deployment.
    """
    day_candidates = [c for c in candidates if c.get("slate_date") == slate_date]
    bound = []
    rejected = []
    for cand in day_candidates:
        if cand.get("origin") != ORIGIN_FULL_REFRESH:
            rejected.append((cand.get("epoch_candidate_id"),
                             ["capture was not a full Dashboard Refresh build"]))
            continue
        for dep in deployments:
            result = bind_deployment(cand, dep)
            if result["bound"]:
                bound.append(result["epoch"])
                break
        else:
            rejected.append((cand.get("epoch_candidate_id"),
                             ["no converged refresh-originated deployment matched"]))
    if not bound:
        raise NoPrimaryEpoch(
            f"{slate_date} has NO PRIMARY EPOCH: no converged Dashboard "
            f"Refresh-originated deployment with a hash-bound shadow snapshot. "
            f"This is missing operational evidence, not a loss. "
            f"Rejections: {rejected}")
    # "Latest" by the artifact's own preparation instant, which is the clock
    # the publication cutoff is measured against -- not by convergence time,
    # which varies with CDN propagation and would make the choice depend on
    # infrastructure noise.
    bound.sort(key=lambda e: (_parse(e.get("deployment_prepared_at"))
                              or datetime.min.replace(tzinfo=timezone.utc)))
    return bound[-1]


def regate_pool(pool, epoch, *, schedule, lead_seconds=PUBLICATION_LEAD_SECONDS):
    """Re-apply the publication cutoff at the BOUND DEPLOYMENT's real clock.

    THIS CLOSES A REAL ASYMMETRY. The capture-time pool was gated against the
    build instant inside Dashboard Refresh. The champion set, by contrast, is
    whatever the Pages artifact actually admitted, gated against the LATER
    artifact-preparation instant. Preparation is strictly later, so the
    capture-time pool can contain rows whose games were already inside the
    15-minute window by the time the artifact was built -- wagers the site was
    structurally unable to publish.

    Leaving those in the pool would let PA-v1 select from a strictly larger
    opportunity set than the champion ever had. That is not a small edge: it is
    precisely the kind of invisible advantage that manufactures a win at equal
    headline volume. So the decisive cohort is re-gated here, against
    deployment_prepared_at, before any selection happens.

    Returns (kept, dropped_with_reason).
    """
    prepared_at = _parse(epoch.get("deployment_prepared_at"))
    if prepared_at is None:
        raise NoPrimaryEpoch(
            "cannot re-gate the pool: bound epoch has no deployment_prepared_at")
    kept, dropped = [], []
    for row, verdict in pool:
        start = _parse((verdict or {}).get("game_start")
                       or ((schedule or {}).get(row.get("game_pk")) or {}).get("start"))
        if admits_new_top_pick(start, prepared_at, lead_seconds=lead_seconds):
            kept.append((row, verdict))
        else:
            dropped.append((row, "inside the publication cutoff at "
                                 "deployment_prepared_at"))
    return kept, dropped
