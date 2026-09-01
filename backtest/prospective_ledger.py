"""Durable append-only ledger for prospective Hits PA-v1 shadow evidence.

Locked protocol section 10. The sole evidence copy may NOT be a local or
gitignored JSONL, and this container has been reclaimed repeatedly during this
project's own development -- so "durable" here means "survives the machine",
not "survives the process".

ARCHITECTURE

Events are appended to a JSONL file inside a dedicated research-data branch
worktree (default ``research-ledger/prospective-hits-pa-v1``) and pushed. That
branch is chosen over ``main`` deliberately: a five-minute refresh cadence
writing to main would produce continuous generated churn on the branch that
serves the customer site.

The registry at ``data/public_top_picks/registry.json`` is NOT touched. It is
publication/lifecycle truth and explicitly not the receipts ledger; receipts
LINK to it by identity and stay separate.

APPEND-ONLY SEMANTICS

  * idempotent key -- ``receipt_id`` = sha256(epoch_id, canonical_prop_id).
  * no silent overwrite -- re-appending the SAME id with the SAME content hash
    is a no-op that reports ``duplicate``. Re-appending the same id with a
    DIFFERENT content hash raises: that is a changed receipt, and a decisive
    receipt may never be edited. A later price is a new epoch's receipt or it
    is nothing.
  * logical append -- a settlement is a SEPARATE event type referencing the
    receipt id. Nothing ever rewrites a pregame event in place.
  * crash safety -- each append is written to a temp file in the same
    directory, fsync'd, then atomically renamed into place, so an interrupted
    write cannot leave a half-line in the ledger.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.prospective_receipt import (  # noqa: E402
    assert_no_outcome,
    canonical_json,
    content_sha,
)

LEDGER_BRANCH = "research-ledger/prospective-hits-pa-v1"
LEDGER_RELPATH = "prospective/hits_pa_v1/receipts.jsonl"

EVENT_PREGAME_RECEIPT = "pregame_receipt"
EVENT_SETTLEMENT = "settlement"
EVENT_EPOCH_BOUND = "epoch_bound"
EVENT_TYPES = (EVENT_PREGAME_RECEIPT, EVENT_SETTLEMENT, EVENT_EPOCH_BOUND)


class LedgerConflict(ValueError):
    """An existing ledger event would have to change to accept this append."""


def _atomic_append(path, lines):
    """Append complete lines durably. Never leaves a partial record.

    Read-modify-write through a same-directory temp file plus fsync plus
    atomic rename. Slower than an O_APPEND write, and chosen anyway: an
    O_APPEND write that is interrupted mid-buffer leaves a truncated JSON line
    that silently corrupts every later read of the ledger.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing = b""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            existing = fh.read()
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    payload = existing + b"".join(
        (line.rstrip("\n") + "\n").encode("utf-8") for line in lines)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_events(path):
    """Every event in the ledger, oldest first. Missing file is empty."""
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerConflict(
                    f"{path}:{lineno} is not valid JSON: {exc}") from None
    return events


def _index(events):
    """(event_type, idempotent_key) -> content hash, for conflict detection."""
    out = {}
    for ev in events:
        out[(ev.get("event_type"), ev.get("idempotent_key"))] = \
            ev.get("event_content_sha256")
    return out


def make_event(event_type, idempotent_key, body, *, writer=None):
    """Wrap a body as a hashed ledger event.

    ``recorded_at`` sits OUTSIDE the hashed body, for the same reason the PA-v1
    artifact keeps its wall-clock provenance outside its scientific hash: the
    same logical event written twice must hash identically, and a timestamp
    would make every rewrite look like a conflict.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    if event_type == EVENT_PREGAME_RECEIPT:
        assert_no_outcome(body)
    event = {
        "event_type": event_type,
        "idempotent_key": idempotent_key,
        "body": body,
    }
    event["event_content_sha256"] = content_sha(
        {"event": event["event_type"], "key": idempotent_key, "body": body})
    event["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    event["writer"] = writer or "prospective_ledger"
    return event


def append_events(path, events):
    """Append events, refusing any silent overwrite.

    Returns {"appended": n, "duplicates": n}. Raises LedgerConflict if an
    event's idempotent key already exists with DIFFERENT content -- that is an
    attempted edit of a sealed receipt, not a retry.
    """
    existing = _index(read_events(path))
    fresh, duplicates = [], 0
    seen = dict(existing)
    for ev in events:
        key = (ev.get("event_type"), ev.get("idempotent_key"))
        prior = seen.get(key)
        if prior is None:
            fresh.append(canonical_json(ev))
            seen[key] = ev.get("event_content_sha256")
        elif prior == ev.get("event_content_sha256"):
            duplicates += 1
        else:
            raise LedgerConflict(
                f"{key[0]} {key[1]} already recorded with content {prior}; "
                f"refusing to overwrite with {ev.get('event_content_sha256')}. "
                f"A decisive receipt is immutable -- a changed price or state "
                f"is a new epoch, not an edit.")
    if fresh:
        _atomic_append(path, fresh)
    return {"appended": len(fresh), "duplicates": duplicates}


# ── remote durability ───────────────────────────────────────────────────

def _git(args, cwd, timeout=120):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def ensure_ledger_worktree(repo_root, worktree_path, branch=LEDGER_BRANCH,
                           remote="origin"):
    """Materialize the research-data branch as a worktree, creating it as an
    ORPHAN if it does not exist yet.

    Orphan, not a branch off main: the ledger shares no history with the
    product code, so it should not carry main's tree, and rebasing it onto
    main later must never be possible by accident.
    """
    if os.path.isdir(os.path.join(worktree_path, ".git")) or \
            os.path.isfile(os.path.join(worktree_path, ".git")):
        return {"created": False, "path": worktree_path}
    _git(["fetch", remote, branch], repo_root)
    remote_ref = f"{remote}/{branch}"
    has_remote = _git(["rev-parse", "--verify", remote_ref], repo_root).returncode == 0
    if has_remote:
        res = _git(["worktree", "add", worktree_path, "-B", branch, remote_ref], repo_root)
    else:
        res = _git(["worktree", "add", "--detach", worktree_path], repo_root)
        if res.returncode == 0:
            _git(["checkout", "--orphan", branch], worktree_path)
            _git(["rm", "-rf", "--quiet", "."], worktree_path)
    if res.returncode != 0:
        raise LedgerConflict(f"could not create ledger worktree: {res.stderr.strip()}")
    return {"created": True, "path": worktree_path, "from_remote": has_remote}


def commit_and_push(worktree_path, message, branch=LEDGER_BRANCH,
                    remote="origin", attempts=4):
    """Commit the ledger and push it, with exponential backoff on transient
    network failure. Returns a report; never raises on push failure, because
    losing the remote copy must be reported loudly, not crash the caller that
    already has the local durable write."""
    add = _git(["add", "-A"], worktree_path)
    if add.returncode != 0:
        return {"committed": False, "pushed": False, "error": add.stderr.strip()}
    status = _git(["status", "--porcelain"], worktree_path)
    if not status.stdout.strip():
        return {"committed": False, "pushed": False, "reason": "nothing to commit"}
    commit = _git(["-c", "user.name=Full Count Research",
                   "-c", "user.email=noreply@anthropic.com",
                   "commit", "-q", "-m", message], worktree_path)
    if commit.returncode != 0:
        return {"committed": False, "pushed": False, "error": commit.stderr.strip()}
    delay, last = 2, ""
    for attempt in range(attempts):
        push = _git(["push", "-u", remote, branch], worktree_path)
        if push.returncode == 0:
            return {"committed": True, "pushed": True, "attempts": attempt + 1}
        last = push.stderr.strip()
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2
    return {"committed": True, "pushed": False, "error": last, "attempts": attempts}
