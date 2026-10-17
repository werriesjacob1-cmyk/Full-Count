#!/usr/bin/env python3
"""Fail visibly when the PUBLIC GitHub Pages site stops tracking main.

2026-10-12 incident. Dashboard Pages Deploy run 37486111588 entered the
``waiting`` state at the github-pages environment gate at 2026-10-06T15:16Z
(no reviewers, wait_timer 0) and never left it. Because dashboard-deploy.yml
uses ``concurrency: dashboard-pages-deployment`` with
``cancel-in-progress: false``, that one run held the group, and every later
deploy (well over 1,000 runs) waited behind it and was cancelled by its
successor. The public site froze at the 2026-10-06T15:13Z artifact. No
deployment meant no publication confirmation, so the publication registry,
public Top Pick grading and History all stopped advancing.

Nothing noticed, because every existing check read the REPOSITORY's
docs/*.json, which kept refreshing normally. This check compares the
deployed artifact against the repository and inspects non-terminal deploy
runs. It never cancels, approves or deploys anything: recovering from a
stuck deploy is an operator action.

Findings:
  PUBLIC_UNREACHABLE  public publication_manifest.json cannot be read
  STALE_PUBLICATION   repo docs/data.json is newer than the deployed
                      artifact by more than PUBLICATION_SLA_MINUTES
  STUCK_DEPLOY        a Dashboard Pages Deploy run has been non-terminal
                      (waiting/queued/pending/in_progress/requested) for
                      more than STUCK_DEPLOY_MINUTES AND no deploy has
                      succeeded since it was created (i.e. it is actually
                      blocking). During recovery, run 37642347599 stayed a
                      'queued' phantom that GitHub refuses to cancel or
                      force-cancel (HTTP 409) while later deploys succeeded
                      past it; flagging it forever would recreate the
                      cry-wolf watchdog that hid this incident.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

PUBLIC_BASE_URL = "https://werriesjacob1-cmyk.github.io/Full-Count"
DEPLOY_WORKFLOW = "dashboard-deploy.yml"
# Live updates commit every ~5 minutes and a healthy deploy takes <10. One
# hour without the public artifact catching up is far beyond normal lag
# (including Pages' 10-minute CDN cache) and well inside a betting window.
PUBLICATION_SLA_MINUTES = 60
# deploy-pages' job timeout is 10 minutes; a run non-terminal for 30 is stuck.
STUCK_DEPLOY_MINUTES = 30
NON_TERMINAL = ("waiting", "queued", "pending", "in_progress", "requested")


def _parse(ts):
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def evaluate(*, repo_generated_at, public_manifest, deploy_runs, now,
             last_success_created_at=None,
             sla_minutes=PUBLICATION_SLA_MINUTES, stuck_minutes=STUCK_DEPLOY_MINUTES):
    """Pure decision. Returns a list of (code, message) findings; empty = healthy."""
    findings = []
    repo_dt = _parse(repo_generated_at)
    if repo_dt is None:
        findings.append(("REPO_UNREADABLE", "repository docs/data.json has no valid generated_at"))
    if public_manifest is None:
        findings.append(("PUBLIC_UNREACHABLE", "public publication_manifest.json could not be read"))
    else:
        public_dt = _parse(public_manifest.get("prepared_at"))
        if public_dt is None:
            findings.append(("PUBLIC_UNREACHABLE", "public manifest has no valid prepared_at"))
        elif repo_dt is not None:
            lag = (repo_dt - public_dt).total_seconds() / 60.0
            if lag > sla_minutes:
                findings.append((
                    "STALE_PUBLICATION",
                    f"public site artifact prepared {public_dt.isoformat()} "
                    f"(source_commit {public_manifest.get('source_commit')}) is {lag:.0f} minutes "
                    f"behind repo docs/data.json generated {repo_dt.isoformat()} "
                    f"(SLA {sla_minutes}m)"))
    for run in deploy_runs or []:
        status = run.get("status")
        created = _parse(run.get("created_at"))
        if status not in NON_TERMINAL or created is None:
            continue
        last_ok = _parse(last_success_created_at)
        if last_ok is not None and last_ok > created:
            continue  # a later deploy succeeded past it: not blocking
        age = (now - created).total_seconds() / 60.0
        if age > stuck_minutes:
            findings.append((
                "STUCK_DEPLOY",
                f"Dashboard Pages Deploy run {run.get('id')} has been '{status}' since "
                f"{created.isoformat()} ({age:.0f} minutes); it holds the "
                f"dashboard-pages-deployment concurrency group, so later deploys queue "
                f"behind it and are cancelled. Operator recovery: cancel run {run.get('id')} "
                f"(force-cancel if it will not stop), then dispatch Dashboard Pages Deploy."))
    return findings


def _get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_public_manifest(base_url=PUBLIC_BASE_URL):
    try:
        return _get_json(f"{base_url.rstrip('/')}/publication_manifest.json?t={time.time_ns()}")
    except Exception as exc:  # noqa: BLE001 - unreachable is itself a finding
        print(f"public manifest fetch failed: {exc}")
        return None


def fetch_non_terminal_deploy_runs(repository, token):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    runs = []
    for status in NON_TERMINAL:
        url = (f"https://api.github.com/repos/{repository}/actions/workflows/"
               f"{DEPLOY_WORKFLOW}/runs?status={status}&per_page=20")
        try:
            runs += _get_json(url, headers).get("workflow_runs", [])
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            print(f"deploy-run query ({status}) failed: {exc}")
    return runs


def fetch_last_success_created_at(repository, token):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = (f"https://api.github.com/repos/{repository}/actions/workflows/"
           f"{DEPLOY_WORKFLOW}/runs?status=success&per_page=1")
    try:
        runs = _get_json(url, headers).get("workflow_runs", [])
    except Exception as exc:  # noqa: BLE001 - unknown means "do not excuse"
        print(f"last-success query failed: {exc}")
        return None
    return runs[0].get("created_at") if runs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("data_json", nargs="?", default="docs/data.json")
    ap.add_argument("--base-url", default=PUBLIC_BASE_URL)
    args = ap.parse_args(argv)
    try:
        with open(args.data_json, encoding="utf-8") as fh:
            repo_generated_at = json.load(fh).get("generated_at")
    except (OSError, json.JSONDecodeError):
        repo_generated_at = None
    repository = os.environ.get("GITHUB_REPOSITORY", "werriesjacob1-cmyk/Full-Count")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    findings = evaluate(
        repo_generated_at=repo_generated_at,
        public_manifest=fetch_public_manifest(args.base_url),
        deploy_runs=fetch_non_terminal_deploy_runs(repository, token),
        last_success_created_at=fetch_last_success_created_at(repository, token),
        now=datetime.now(timezone.utc),
    )
    if not findings:
        print("PUBLIC DEPLOYMENT HEALTHY: public artifact tracks main; no stuck deploy run.")
        return 0
    for code, message in findings:
        print(f"::error::{code}: {message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
