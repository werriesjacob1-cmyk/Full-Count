"""Durable append-only ledger for prospective Hits PA-v1 shadow evidence.

Locked protocol section 10.

═══════════════════════════════════════════════════════════════════════════
EVENT ONTOLOGY — six facts that are NOT the same fact
═══════════════════════════════════════════════════════════════════════════

Mission 1 wrote the PREDEPLOYMENT candidate under the event type
``epoch_bound``, on a body that says ``publicly_converged: False``. That is not
a bound epoch, and because the ledger is correctly immutable the later TRUE
bound state could not be recorded: it collided on the same idempotent key and
was refused. The ledger was behaving correctly; capture had spent stage two's
key on a stage one non-event.

This is the repair, made BEFORE any real evidence exists, which is the only
time an event ontology can be fixed without rewriting history.

  1. SNAPSHOT_CAPTURED       a full-build pregame snapshot exists.
                             Asserts NOTHING about public exposure.
  2. DEPLOYMENT_OBSERVED     a Pages deployment converged publicly.
                             Asserts NOTHING about which snapshot produced it.
  3. PUBLIC_EXPOSURE_BOUND   this exact snapshot provably went public in that
                             exact deployment.
  4. EPOCH_SELECTION_SEALED  for that bound epoch, the champion set and the
                             PA-v1 set at exact matched volume, sealed BEFORE
                             outcomes.
  5. DECISIVE_EPOCH_DESIGNATED  one already-sealed bound epoch is the date's
                             primary. Chosen outcome-blind, AFTER the date.
  6. PREGAME_RECEIPT         one immutable sealed wager expression.
  7. SETTLEMENT              an outcome, referencing a receipt by id AND hash.

Plus two negative-evidence types, because "this date produced nothing" is a
protocol §12 reporting requirement and must be recorded, not inferred from
absence:

  8. EPOCH_FAILED_CLOSED     a bound epoch that could not produce a sound
                             comparison, with the exact reason.
  9. NO_PRIMARY_EPOCH        a slate date with no decisive epoch, with reason.

A snapshot existing does not mean it went public. A deployment going public
does not mean it is the primary epoch. An epoch being primary does not modify
its receipts. Settlement never modifies any pregame event. Every relationship
is by content hash or id, never by mutation.

═══════════════════════════════════════════════════════════════════════════
REMOTE DURABILITY — real optimistic concurrency, not retry-and-hope
═══════════════════════════════════════════════════════════════════════════

The previous implementation retried ``git push`` with backoff. A push rejected
as non-fast-forward is rejected IDENTICALLY on every subsequent attempt: the
local ref never advances. Backoff only ever helped transient network failure.
After four attempts it returned ``{"committed": True, "pushed": False}`` — a
silent local-only ledger, on a container that is about to be destroyed.

At least two, plausibly three, independent GitHub Actions concurrency groups
can push here at once: the capture tap (in Dashboard Refresh, ~20-30x per slate
date), the exposure binder (in Dashboard Pages Deploy), and the settlement
runner (in mlb-daily). They are in different concurrency groups, so nothing
serialises them.

``append_events()`` is a PURE FUNCTION of (remote content, pending events), so
the correct loop re-derives against fresh remote state every attempt:

    fetch -> reset --hard onto remote -> replay pending -> commit -> push
    on non-fast-forward: jittered backoff, loop (which re-fetches)

Never rebase (a JSONL append conflicts textually on the last line, and
re-deriving is always conflict-free). Never force-push over evidence. A
same-key/different-content collision during replay is a HARD FAILURE, not a
retry: that is an attempted edit of a sealed receipt.
"""

from __future__ import annotations

import json
import os
import random
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
LEDGER_DIR = "prospective/hits_pa_v1"

EVENT_SNAPSHOT_CAPTURED = "snapshot_captured"
EVENT_DEPLOYMENT_OBSERVED = "deployment_observed"
EVENT_PUBLIC_EXPOSURE_BOUND = "public_exposure_bound"
EVENT_EPOCH_SELECTION_SEALED = "epoch_selection_sealed"
EVENT_DECISIVE_EPOCH_DESIGNATED = "decisive_epoch_designated"
EVENT_PREGAME_RECEIPT = "pregame_receipt"
EVENT_SETTLEMENT = "settlement"
EVENT_EPOCH_FAILED_CLOSED = "epoch_failed_closed"
EVENT_NO_PRIMARY_EPOCH = "no_primary_epoch"

EVENT_TYPES = (
    EVENT_SNAPSHOT_CAPTURED,
    EVENT_DEPLOYMENT_OBSERVED,
    EVENT_PUBLIC_EXPOSURE_BOUND,
    EVENT_EPOCH_SELECTION_SEALED,
    EVENT_DECISIVE_EPOCH_DESIGNATED,
    EVENT_PREGAME_RECEIPT,
    EVENT_SETTLEMENT,
    EVENT_EPOCH_FAILED_CLOSED,
    EVENT_NO_PRIMARY_EPOCH,
)

# Every event type EXCEPT settlement is pregame evidence and is scanned for
# outcome-shaped fields. Mission 1 scanned only pregame_receipt, which left the
# one type capture actually wrote -- carrying the entire snapshot including
# verbatim `signals` -- unchecked.
PREGAME_EVENT_TYPES = tuple(t for t in EVENT_TYPES if t != EVENT_SETTLEMENT)


class LedgerConflict(ValueError):
    """An existing ledger event would have to change to accept this append."""


class LedgerNotDurable(RuntimeError):
    """Events were written locally but never reached the remote.

    Raised/reported rather than swallowed. On an ephemeral runner a local-only
    commit is worth nothing, and reporting it as `committed` is how silent
    evidence loss happens.
    """


def ledger_relpath(slate_date):
    """One file per slate date.

    Protocol §10 asks for "a dedicated research-data branch/path", not one
    file. A single file would be rewritten whole on every append -- and each
    snapshot event embeds the full eligible+rejected universe, ~160 KB per
    capture at ~20-30 captures per slate date. Partitioning bounds both the
    rewrite cost and the contention window, and weakens nothing: every
    idempotent key is already epoch-scoped and every epoch id is prefixed with
    its slate date.
    """
    return f"{LEDGER_DIR}/{slate_date}.jsonl"


def _atomic_append(path, lines):
    """Append complete lines durably. Never leaves a partial record."""
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
    """Every event in the ledger file, oldest first. Missing file is empty."""
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
    return {(e.get("event_type"), e.get("idempotent_key")):
            e.get("event_content_sha256") for e in events}


def make_event(event_type, idempotent_key, body, *, writer=None):
    """Wrap a body as a hashed ledger event.

    ``recorded_at`` sits OUTSIDE the hashed content for the same reason the
    PA-v1 artifact keeps wall-clock provenance outside its scientific hash: the
    same logical event re-derived on a later replay attempt must hash
    identically, or every retry would look like a conflict.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    if event_type in PREGAME_EVENT_TYPES:
        assert_no_outcome(body)
    event = {
        "event_type": event_type,
        "idempotent_key": idempotent_key,
        "body": body,
    }
    event["event_content_sha256"] = content_sha(
        {"event": event_type, "key": idempotent_key, "body": body})
    event["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    event["writer"] = writer or "prospective_ledger"
    return event


def append_events(path, events):
    """Append events, refusing any silent overwrite.

    PURE with respect to (file content, events): re-running it against fresh
    remote content is what makes the concurrency loop correct.

    Returns {"appended": n, "duplicates": n}. Raises LedgerConflict when an
    idempotent key already exists with DIFFERENT content -- an attempted edit
    of sealed evidence, not a retry.
    """
    seen = _index(read_events(path))
    fresh, duplicates = [], 0
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
                f"Sealed evidence is immutable -- a changed price or state is a "
                f"new epoch, not an edit.")
    if fresh:
        _atomic_append(path, fresh)
    return {"appended": len(fresh), "duplicates": duplicates}


# ── remote durability ───────────────────────────────────────────────────

def _git(args, cwd, timeout=120):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


_RETRYABLE = ("non-fast-forward", "fetch first", "cannot lock ref",
              "stale info", "failed to push some refs", "connection",
              "timed out", "could not resolve", "rpc failed",
              "remote end hung up", "500", "502", "503")


def _is_retryable(stderr):
    low = (stderr or "").lower()
    return any(token in low for token in _RETRYABLE)


def _resolve_onto_remote(worktree, branch, remote):
    """Point the worktree at the CURRENT remote tip, or start an orphan.

    Re-resolved on EVERY attempt. Mission 1 resolved orphan-vs-existing once,
    before any push: if two runners both found no remote branch, both created
    rootless orphans with no common ancestor and the loser could never
    fast-forward, retrying the same unmergeable commit forever.
    """
    _git(["fetch", "--no-tags", remote,
          f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"], worktree)
    if _git(["rev-parse", "--verify", "-q",
             f"refs/remotes/{remote}/{branch}"], worktree).returncode == 0:
        _git(["checkout", "-q", "-B", branch, f"{remote}/{branch}"], worktree)
        _git(["reset", "-q", "--hard", f"{remote}/{branch}"], worktree)
        return "existing"
    _git(["checkout", "-q", "--orphan", branch], worktree)
    _git(["rm", "-rf", "--quiet", "--ignore-unmatch", "."], worktree)
    return "orphan"


def _not_durable(relpath, attempts, error):
    """Every non-durable outcome reports the same way, through one path.

    Mission 1 returned {"committed": True, "pushed": False} on a failed push,
    which reads as partial success. On an ephemeral runner a local commit is
    worth nothing: the container is reclaimed and the events are gone. There is
    no such thing as partially durable, so there is no field here that could be
    mistaken for it, and the note is attached unconditionally rather than on
    one of several return paths.
    """
    return {
        "durable": False,
        "pushed": False,
        "attempts": attempts,
        "error": error,
        "relpath": relpath,
        "note": "events may exist only in a local commit; a local-only ledger "
                "is NOT durable evidence and must not be counted",
    }


def append_and_push(worktree, slate_date, events, *, branch=LEDGER_BRANCH,
                    remote="origin", message=None, max_attempts=6,
                    base_delay=1.0, rng=None):
    """Durably append events to the remote research branch.

    fetch -> reset onto remote -> replay -> commit -> push, looping on a
    fast-forward rejection because the loop re-fetches. The events are held in
    memory and replayed against fresh remote content every attempt, never
    accumulated into a stale local file.

    Returns a report. ``durable`` is True ONLY when the remote actually has the
    events -- either this call pushed them, or a concurrent winner already
    wrote byte-identical events.
    """
    rng = rng or random
    relpath = ledger_relpath(slate_date)
    path = os.path.join(worktree, relpath)
    message = message or f"prospective shadow: {slate_date} ({len(events)} events)"
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        try:
            origin_state = _resolve_onto_remote(worktree, branch, remote)
            # Replay against whatever the remote actually holds RIGHT NOW.
            result = append_events(path, events)
            if result["appended"] == 0:
                # A concurrent writer already recorded byte-identical events.
                # The remote is correct; there is nothing to push.
                return {"durable": True, "pushed": False, "noop": True,
                        "attempts": attempt, "ledger": result,
                        "relpath": relpath, "origin_state": origin_state}
            _git(["add", "--", relpath], worktree)
            commit = _git(["-c", "user.name=Full Count Research",
                           "-c", "user.email=noreply@anthropic.com",
                           "commit", "-q", "-m", message], worktree)
            if commit.returncode != 0:
                last_error = (commit.stderr.strip() or commit.stdout.strip()
                              or "git commit returned non-zero")
                return _not_durable(relpath, attempt, f"commit failed: {last_error}")
            push = _git(["push", remote, f"HEAD:refs/heads/{branch}"], worktree)
            if push.returncode == 0:
                return {"durable": True, "pushed": True, "attempts": attempt,
                        "ledger": result, "relpath": relpath,
                        "origin_state": origin_state}
            last_error = push.stderr.strip()
            if not _is_retryable(last_error) and attempt >= 2:
                break
        except LedgerConflict:
            # Same key, different content. Never retry -- this is an attempted
            # edit of sealed evidence and looping cannot make it legitimate.
            raise
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1)) * rng.uniform(0.5, 1.5)
            time.sleep(min(delay, 15.0))

    return _not_durable(relpath, max_attempts, last_error or "push rejected")


def ensure_ledger_worktree(repo_root, worktree_path, branch=LEDGER_BRANCH,
                           remote="origin"):
    """Materialize the research branch as a worktree.

    Only creates the worktree directory; branch resolution happens inside
    append_and_push on every attempt, which is what makes the orphan race safe.
    """
    if (os.path.isdir(os.path.join(worktree_path, ".git"))
            or os.path.isfile(os.path.join(worktree_path, ".git"))):
        return {"created": False, "path": worktree_path}
    _git(["fetch", "--no-tags", remote, branch], repo_root)
    remote_ref = f"{remote}/{branch}"
    if _git(["rev-parse", "--verify", "-q", remote_ref], repo_root).returncode == 0:
        res = _git(["worktree", "add", worktree_path, "-B", branch, remote_ref],
                   repo_root)
    else:
        res = _git(["worktree", "add", "--detach", worktree_path], repo_root)
        if res.returncode == 0:
            _git(["checkout", "-q", "--orphan", branch], worktree_path)
            _git(["rm", "-rf", "--quiet", "--ignore-unmatch", "."], worktree_path)
    if res.returncode != 0:
        raise LedgerNotDurable(
            f"could not create ledger worktree: {res.stderr.strip()}")
    return {"created": True, "path": worktree_path}
