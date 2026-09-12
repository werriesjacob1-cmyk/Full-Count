"""Validate the publisher's source workflow and bind its artifact to the run."""
from __future__ import annotations

WORKFLOW_PATH = ".github/workflows/nfl-live-passing-yards-shadow-board.yml"


def validate_source_run(run: dict, workflow: dict, *, repository: str, run_id: str) -> str:
    if not str(run_id).isdigit() or str(run.get("id")) != str(run_id):
        raise ValueError("source run ID mismatch")
    expected = {
        "name": "NFL Live Passing-Yards Shadow Board Audit",
        "head_branch": "main", "status": "completed", "conclusion": "success",
        "path": WORKFLOW_PATH,
    }
    if any(run.get(k) != v for k, v in expected.items()):
        raise ValueError("source must be the completed successful main shadow workflow")
    if workflow.get("path") != WORKFLOW_PATH or run.get("workflow_id") != workflow.get("id"):
        raise ValueError("source workflow identity mismatch")
    for field in ("repository", "head_repository"):
        if (run.get(field) or {}).get("full_name") != repository:
            raise ValueError("source repository mismatch")
    sha = str(run.get("head_sha") or "")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("invalid source head SHA")
    return sha


def validate_artifact_code(board: dict, source_sha: str) -> None:
    if board.get("code_sha") != source_sha or (board.get("snapshot") or {}).get("code_sha") != source_sha:
        raise ValueError("artifact code SHA does not match verified source run")
