#!/usr/bin/env python3
"""canonical_run.py -- interruption-safe canonical historical backfill runner.

WHY THIS EXISTS: the 2024-04-01..2026-08-25 canonical reconstruction (PID
1633, branch claude/realized-hit-rate-sprint-01) died when its surrounding
container/session restarted, with no supervisor to resume it. Forensic
audit (2026-08-27) proved the partial artifact it left behind
(backtest/rows_backfill_v2.jsonl, 450,621 rows across 385 dates,
2024-04-01..2025-04-20) was actually clean -- zero truncation, zero
duplicate candidate identities, one consistent code_git_sha throughout,
and the state-file's own row counts matched the real file byte-for-byte.
So the DATA survived; what was missing was a system that could prove that
without a human re-deriving it by hand, and that could resume without a
human watching the process.

backtest/engine.py's existing run_backtest()/state_path() machinery already
gets several things right (dedupes against actual file content rather than
just trusting bookkeeping, batches each date into one write() to narrow the
crash window, tags every row with code_git_sha). This module does not
replace simulate_date() -- the actual MLB-fetching/scoring/grading logic --
it replaces the OUTER loop and its persistence model, because appending
every date into one ever-growing JSONL as the sole source of truth is
exactly the failure mode Phase 3D of the governing mission spec calls out:
a single monolithic file can only ever be "fully valid" or "who knows",
never checkpointed per unit of work with its own checksum.

DESIGN, one requirement at a time:

  A. IMMUTABLE RUN IDENTITY -- RunManifest, written once at creation
     (manifest.json), never mutated except for a small `progress` summary
     block. Every identity field the mission lists is present. A resume
     that finds a code SHA mismatch fails closed (raises CodeIdentityDrift)
     unless the caller explicitly passes allow_sha_drift=True, matching the
     project's existing check_regime_consistency() philosophy of "loud
     warning, not a silent blend."

  B. ISOLATION / OWNERSHIP -- acquire_lock()/release_lock()/is_lock_stale()
     plus LeaseHeartbeat. A lock records the owner's full process identity
     (pid, hostname, pid_start_ticks, boot_id) so its liveness can later be
     verified rather than guessed. Staleness rules, in strict precedence:
     same-host owner verifiably alive -> NEVER stale at any heartbeat age;
     same-host owner verifiably gone (exited, PID recycled onto a different
     process, or machine rebooted) -> stale immediately; liveness
     unknowable (different host/container, no /proc) -> heartbeat age
     decides, since a foreign PID number means nothing locally. A
     LeaseHeartbeat thread refreshes the lease during operations of
     unbounded length (Statcast warmup, one long date) so a busy owner is
     never mistaken for a dead one. A stale lock is always reclaimable --
     crash recovery is preserved in every branch.

     This ordering is a real bug fix, not a precaution: the live canonical
     run's own lock.json showed 933 seconds between acquired_at and its
     first heartbeat_at against a 900-second threshold, because heartbeats
     were emitted only on date boundaries and Statcast warmup precedes the
     first date. For 33 seconds a second process would have been told it
     could take the lock from a healthy multi-hour job.

  C. BOUNDED DURABLE CHECKPOINTS -- one pair of files per date:
     checkpoints/<date>.jsonl (the rows) and checkpoints/<date>.meta.json
     (status ok/no_games/error, row count, sha256 of the .jsonl, timing).
     A crash loses at most the one date in flight.

  D. ATOMICITY -- every checkpoint write is temp-file -> fsync -> os.replace
     (atomic rename on the same filesystem) for BOTH files, data file
     first, meta file second. The meta file's existence with a checksum
     that verifies is the single source of truth for "this date is done";
     a data file with no matching meta, or a meta whose checksum doesn't
     match the data file's current bytes, is treated as not-done and
     rebuilt from scratch, never trusted partially.

  E. DUPLICATE PREVENTION -- candidate identity is
     (date, game_pk, player_id, prop_type, line), the same shape the
     2026-08-27 forensic audit used to prove the legacy artifact had zero
     duplicates. plan_remaining() only re-runs a date whose checkpoint is
     missing or invalid/error; re-running an already-valid date is a no-op
     unless force=True, and even then it deterministically REPLACES that
     one date's checkpoint pair atomically rather than appending, so a
     repeat run can never double a date's rows.

  F. MISSING-DATE DETECTION -- load_run_state() answers, for every
     requested date, exactly one of: ok / no_games / error / never_run /
     partial (a temp file present with no valid meta). validate_complete()
     fails loudly if any requested date does not resolve to ok/no_games.

  G/H. CORRUPTION DETECTION + CHECKSUMS -- validate_checkpoint() re-hashes
     the data file and compares to the meta's recorded sha256, checks every
     line parses, and checks the row count matches. Any mismatch marks
     that date invalid regardless of what the meta file's status field
     claims.

  I. DETERMINISTIC RECONCILIATION -- assemble() concatenates checkpoints in
     strict date-sorted order (never directory-listing order, which is not
     guaranteed stable across filesystems) and computes both a byte
     checksum of the assembled file and an order-independent logical
     fingerprint (sha256 of the sorted list of
     (date, status, row_count, per-date sha256) tuples) so equality can be
     proven even if some future filesystem detail changes assembly's byte
     layout.

  J. RESUME -- run() always starts by loading the manifest, validating code
     identity, validating every existing checkpoint, and computing
     plan_remaining() from that ground truth -- never from an in-memory
     assumption about what a prior invocation already did.

  K. DURABILITY BEYOND ONE PROCESS -- investigated directly (see
     CANONICAL_REBUILD_INFRASTRUCTURE_NOTES.md alongside this file):
     /home/user (the path PID 1633's own output lived at) demonstrably
     SURVIVED the exact restart that killed the process -- the 437MB
     partial artifact and its state file were both intact and readable
     after the fact. So local disk under the repo's own worktree already
     provides real durability against this project's one proven failure
     mode; what was missing was resumability, not storage. This module
     still pushes the run manifest (never the bulk row data -- see the
     docstring on push_manifest_snapshot()) to a dedicated remote branch
     as cheap defense-in-depth against a *second*, different failure this
     project has not yet suffered (full local volume loss), without
     committing hundreds of MB into git history.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import recommendation  # noqa: E402 -- git_sha(), version constants
from backtest import generation_regime as _gr  # noqa: E402 -- repo identity + regime

SCHEMA_VERSION = 1
LOCK_STALE_SECONDS = 15 * 60  # a heartbeat older than this, on any host, is reclaimable
CANDIDATE_IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")


# ══════════════════════════════════════════════════════════════════════════
#  SMALL UTILITIES
# ══════════════════════════════════════════════════════════════════════════

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path, data_bytes):
    """temp-file -> fsync -> os.replace. Same directory as the target so
    os.replace is a same-filesystem rename, never a cross-device copy."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "wb") as f:
        f.write(data_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path, obj):
    _atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def date_range(start, end):
    from backtest.engine import date_range as _dr
    return _dr(start, end)


# ══════════════════════════════════════════════════════════════════════════
#  RUN DIRECTORY LAYOUT
# ══════════════════════════════════════════════════════════════════════════

def run_dir_for(base_dir, run_id):
    return os.path.join(base_dir, run_id)


def manifest_path(run_dir):
    return os.path.join(run_dir, "manifest.json")


def lock_path(run_dir):
    return os.path.join(run_dir, "lock.json")


def checkpoints_dir(run_dir):
    return os.path.join(run_dir, "checkpoints")


def checkpoint_data_path(run_dir, date):
    return os.path.join(checkpoints_dir(run_dir), f"{date}.jsonl")


def checkpoint_meta_path(run_dir, date):
    return os.path.join(checkpoints_dir(run_dir), f"{date}.meta.json")


def assembled_dir(run_dir):
    return os.path.join(run_dir, "assembled")


# ══════════════════════════════════════════════════════════════════════════
#  A. IMMUTABLE RUN IDENTITY
# ══════════════════════════════════════════════════════════════════════════

class CodeIdentityDrift(Exception):
    """Raised when a resume finds the active code SHA differs from the
    manifest's pinned SHA and the caller did not explicitly opt in."""


class LockHeldElsewhere(Exception):
    """Raised when a live (non-stale) lock is held by a different owner."""


def build_run_identity(start, end, out_target, *, sport="mlb",
                       evidence_regime="canonical_historical_model_data",
                       weather_mode="no_weather", command=None,
                       source_provider="mlb_statsapi", repo_identity=None,
                       extra_config=None):
    sha = recommendation.git_sha(short=False)
    if sha is None:
        raise RuntimeError(
            "refusing to start a canonical run outside a real git checkout -- "
            "code identity cannot be pinned, which violates requirement A")
    return {
        "run_id": f"canonical-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "evidence_regime": evidence_regime,
        "requested_start_date": start,
        "requested_end_date": end,
        "command": command or " ".join(sys.argv),
        "weather_mode": weather_mode,
        "config": extra_config or {},
        "code_git_sha": sha,
        # 2026-08-27: was hardcoded to "werriesjacob1-cmyk/PROJECT-GRIDIRON",
        # which is the repository's PRE-RENAME name. It survived unnoticed
        # because the configured git remote still uses that URL and GitHub
        # transparently redirects it, so every push succeeded. Canonical
        # provenance has to name the real repository, and the authority for
        # that is GitHub's own API, which reports full_name=
        # "werriesjacob1-cmyk/Full-Count" for this repo's pull requests.
        # Manifests already written with the old value are corrected by an
        # explicit, additive correction record -- never by editing the
        # manifest, which would launder the provenance. See
        # generation_regime.build_repository_identity_correction().
        "repository_identity": repo_identity or _gr.CANONICAL_REPOSITORY_IDENTITY,
        "model_artifact_versions": {
            "model_version": recommendation.MODEL_VERSION,
            "selection_policy_version": recommendation.SELECTION_POLICY_VERSION,
            "calibration_version": recommendation.CALIBRATION_VERSION,
            "feature_version": recommendation.FEATURE_VERSION,
        },
        "source_provider": source_provider,
        "output_target": out_target,
        "created_at": _now_iso(),
        "candidate_identity_fields": list(CANDIDATE_IDENTITY_FIELDS),
    }


def create_manifest(run_dir, identity):
    if os.path.exists(manifest_path(run_dir)):
        raise FileExistsError(
            f"manifest already exists at {manifest_path(run_dir)} -- "
            "create_manifest() is for a brand-new run_id only; use load_manifest() to resume")
    _atomic_write_json(manifest_path(run_dir), identity)
    return identity


def load_manifest(run_dir):
    p = manifest_path(run_dir)
    if not os.path.exists(p):
        raise FileNotFoundError(f"no manifest at {p} -- this is not a valid canonical run directory")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def verify_code_identity(manifest, *, allow_sha_drift=False):
    current = recommendation.git_sha(short=False)
    pinned = manifest.get("code_git_sha")
    if current == pinned:
        return {"consistent": True, "pinned": pinned, "current": current}
    if not allow_sha_drift:
        raise CodeIdentityDrift(
            f"active code SHA {current!r} does not match this run's pinned SHA {pinned!r}. "
            f"Resuming would silently mix code regimes across dates already checkpointed "
            f"under {pinned!r} and new dates about to be checkpointed under {current!r}. "
            f"Pass allow_sha_drift=True only if you have independently verified the two "
            f"SHAs are behaviorally equivalent for backtest generation, and understand new "
            f"checkpoints will carry a different code_git_sha than old ones (provenance.py's "
            f"require_single_regime() will correctly flag the mix at assembly/validation time).")
    return {"consistent": False, "pinned": pinned, "current": current}


# ══════════════════════════════════════════════════════════════════════════
#  B. ISOLATION / OWNERSHIP
# ══════════════════════════════════════════════════════════════════════════

def _pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours -- still alive
    except OSError:
        return False
    return True


def _proc_start_ticks(pid):
    """Process start time in clock ticks since boot, from /proc/<pid>/stat
    field 22. Returns None where unavailable (non-Linux, permission,
    already-exited).

    This is what makes same-host PID liveness trustworthy enough to
    outrank heartbeat age: a bare os.kill(pid, 0) cannot distinguish "the
    original owner is still running" from "the OS recycled that PID onto
    an unrelated process", and a recycled PID would otherwise let a
    genuinely dead run hold its lock forever."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            data = f.read()
    except (OSError, TypeError, ValueError):
        return None
    # comm (field 2) is parenthesized and may itself contain spaces or ')',
    # so split after the LAST ')' rather than tokenizing the whole line.
    close = data.rfind(b")")
    if close == -1:
        return None
    fields = data[close + 2:].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])  # field 22 overall; 20th after the comm block
    except ValueError:
        return None


def _boot_id():
    """Identifies this specific boot. Start-ticks are measured from boot,
    so they are only comparable within one boot."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def owner_process_identity():
    """The identity a lock records so its liveness can later be verified
    without ambiguity."""
    pid = os.getpid()
    return {"pid": pid, "hostname": socket.gethostname(),
            "pid_start_ticks": _proc_start_ticks(pid), "boot_id": _boot_id()}


def _same_host_owner_alive(lock):
    """True only if the ORIGINAL owner process recorded in `lock` is still
    running on this host. Returns None when liveness cannot be determined
    (different host, or /proc unavailable), so callers can fall back to
    heartbeat age rather than guessing."""
    if lock.get("hostname") != socket.gethostname():
        return None
    recorded_boot = lock.get("boot_id")
    if recorded_boot is not None and _boot_id() is not None and recorded_boot != _boot_id():
        # Machine rebooted since the lock was taken: the owner cannot
        # possibly still be running, whatever PID currently exists.
        return False
    pid = lock.get("pid")
    if not _pid_alive(pid):
        return False
    recorded_ticks = lock.get("pid_start_ticks")
    if recorded_ticks is None:
        # Pre-hardening lock, or a platform without /proc. PID exists and
        # we have nothing that contradicts it; treat as alive (the caller
        # still has heartbeat age as a secondary signal for the
        # can't-verify case -- see is_lock_stale).
        return True
    current_ticks = _proc_start_ticks(pid)
    if current_ticks is None:
        return True
    return current_ticks == recorded_ticks


def is_lock_stale(lock):
    """Whether a lock may be reclaimed by a different owner.

    2026-08-27 -- THIS ORDERING IS THE FIX, and the bug it replaces was
    not hypothetical. The previous implementation tested heartbeat age
    FIRST and only then looked at PID liveness, so a perfectly healthy
    same-host owner was declared stale purely for being busy. The live
    canonical run proved it: its own lock.json shows 933 seconds between
    `acquired_at` and its first `heartbeat_at` (the Statcast warmup runs
    before the first date completes, and heartbeats were emitted only on
    date boundaries), against a 900-second threshold. For 33 seconds a
    second process would have been told it could take the lock away from
    a running, correct, irreplaceable multi-hour job.

    The invariant now: DIRECT EVIDENCE BEATS INFERENCE.

      * Same host, original owner verifiably alive  -> never stale, at any
        heartbeat age. Time is a proxy for liveness; when liveness itself
        is observable, the proxy is not needed and must not override it.
      * Same host, owner verifiably gone (exited, PID recycled onto a
        different process, or machine rebooted) -> stale immediately,
        without waiting out the clock. This half matters as much as the
        first: it is what keeps a crash from bricking the run.
      * Liveness unknowable (different host/container, no /proc) -> a PID
        number from another machine means nothing here, so heartbeat age
        is the only honest signal and is used.

    Crash recovery is preserved in every branch -- see the adversarial
    tests in test_canonical_run.py.
    """
    if lock is None:
        return True

    alive = _same_host_owner_alive(lock)
    if alive is True:
        return False
    if alive is False:
        return True

    heartbeat = lock.get("heartbeat_at") or lock.get("acquired_at")
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        return True  # unparseable heartbeat -- cannot trust it, treat as stale
    return age > LOCK_STALE_SECONDS


def read_lock(run_dir):
    p = lock_path(run_dir)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None  # unreadable lock is not a live lock; treat as absent/stale


def acquire_lock(run_dir, run_id, *, owner_token=None):
    """Refuses to bulldoze a live lock held by someone else. Reclaims a
    stale one. Never leaves the system permanently unrecoverable: a lock
    file surviving a crash is exactly what is_lock_stale() exists to
    neutralize on the next attempt, not a lock the operator must manually
    delete."""
    existing = read_lock(run_dir)
    if (existing is not None and existing.get("owner_token") != owner_token
            and not is_lock_stale(existing)):
        raise LockHeldElsewhere(
            f"run directory {run_dir} is locked by a live owner: {existing}. "
            f"If you are certain that owner is gone, wait past LOCK_STALE_SECONDS "
            f"({LOCK_STALE_SECONDS}s since its last heartbeat) and retry.")
    token = owner_token or uuid.uuid4().hex
    lock = {
        "run_id": run_id, "owner_token": token,
        "acquired_at": _now_iso(), "heartbeat_at": _now_iso(),
        **owner_process_identity(),
    }
    _atomic_write_json(lock_path(run_dir), lock)
    return lock


def heartbeat_lock(run_dir, lock):
    lock = dict(lock)
    lock["heartbeat_at"] = _now_iso()
    _atomic_write_json(lock_path(run_dir), lock)
    return lock


# Lease refresh interval. Comfortably under LOCK_STALE_SECONDS so that
# even several consecutive missed ticks cannot age a live lease out.
LEASE_HEARTBEAT_SECONDS = 60


class LeaseHeartbeat:
    """Keeps a run's lock lease fresh during an operation of unbounded
    duration, independently of date boundaries.

    2026-08-27, the other half of the is_lock_stale() fix. Even with
    liveness now outranking age on the same host, a run whose owner is on
    a DIFFERENT host/container still falls back to heartbeat age -- and a
    long phase would still age it out there. More simply: emitting a
    heartbeat only every N completed dates means the very first date of a
    run is preceded by however long StatcastStore.load() takes, which on
    the live run was 933 seconds of total silence. A lease should be a
    statement about the process being alive, not about it having finished
    a unit of work.

    Deliberately narrow:
      * writes ONLY lock.json, via the same atomic temp+rename every other
        writer uses -- it can never touch checkpoints, manifests, model
        state, or backtest state;
      * daemon thread, so it cannot keep a dying interpreter alive and
        cannot outlive the process it vouches for (a heartbeat that could
        outlive its owner would be exactly the "hides a dead process"
        failure this must avoid);
      * a write failure is swallowed and retried on the next tick rather
        than killing a multi-hour run over a transient fs error -- but it
        is never masked as success: the thread stops updating and the
        lease ages out naturally, which is the safe direction;
      * stop() is idempotent and joins with a timeout, so no thread leaks
        on either the normal or the exception path.
    """

    def __init__(self, run_dir, lock, interval=LEASE_HEARTBEAT_SECONDS):
        self.run_dir = run_dir
        self.lock = dict(lock)
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self.ticks = 0
        self.errors = 0

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.lock = heartbeat_lock(self.run_dir, self.lock)
                self.ticks += 1
            except Exception:
                # See class docstring: never fatal, never masked.
                self.errors += 1

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._loop, name="canonical-run-lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval / 4))
            self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False  # never swallow the caller's exception


def release_lock(run_dir, lock):
    p = lock_path(run_dir)
    current = read_lock(run_dir)
    if current is not None and current.get("owner_token") == lock.get("owner_token") and os.path.exists(p):
        os.remove(p)


# ══════════════════════════════════════════════════════════════════════════
#  C/D/G/H. CHECKPOINT WRITE + VALIDATE
# ══════════════════════════════════════════════════════════════════════════

VALID_STATUSES = ("ok", "no_games", "error")


def write_checkpoint(run_dir, date, rows, status, *, elapsed=None, extra=None,
                     source_code_git_sha=None):
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    data_path = checkpoint_data_path(run_dir, date)
    blob = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    _atomic_write(data_path, blob)  # data file first
    checksum = hashlib.sha256(blob).hexdigest()
    meta = {
        "date": date, "status": status, "row_count": len(rows),
        "sha256": checksum, "elapsed_seconds": elapsed,
        "written_at": _now_iso(),
        "code_git_sha": source_code_git_sha or recommendation.git_sha(short=False),
        "extra": extra or {},
    }
    _atomic_write_json(checkpoint_meta_path(run_dir, date), meta)  # meta second: its
    # existence + matching checksum is the sole "this date is done" signal
    return meta


def validate_checkpoint(run_dir, date):
    """Returns (True, meta) if this date's checkpoint is genuinely valid,
    else (False, reason_string). Never trusts the meta's own claims without
    re-deriving them from the actual data file on disk."""
    data_path = checkpoint_data_path(run_dir, date)
    meta_path = checkpoint_meta_path(run_dir, date)
    if not os.path.exists(meta_path):
        return False, "no meta file" if not os.path.exists(data_path) else "data file present but no meta (partial/interrupted write)"
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"meta file corrupt: {exc}"
    if meta.get("status") not in VALID_STATUSES:
        return False, f"meta has invalid status {meta.get('status')!r}"
    if meta["status"] in ("no_games",):
        # a no_games date legitimately has no data file at all -- explicit,
        # never inferred from a data file simply being absent (requirement C)
        if os.path.exists(data_path) and os.path.getsize(data_path) > 0:
            return False, "status is no_games but a non-empty data file exists"
        return True, meta
    if not os.path.exists(data_path):
        return False, "meta claims a data checkpoint but the data file is missing"
    actual_checksum = _sha256_file(data_path)
    if actual_checksum != meta.get("sha256"):
        return False, f"checksum mismatch: meta says {meta.get('sha256')}, file hashes to {actual_checksum}"
    n_rows = 0
    idents = set()
    with open(data_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                return False, f"unparseable line {line_no}: {exc}"
            n_rows += 1
            ident = tuple(row.get(k) for k in CANDIDATE_IDENTITY_FIELDS)
            if ident in idents:
                return False, f"duplicate candidate identity within checkpoint: {ident}"
            idents.add(ident)
    if n_rows != meta.get("row_count"):
        return False, f"row count mismatch: meta says {meta.get('row_count')}, file has {n_rows}"
    return True, meta


# ══════════════════════════════════════════════════════════════════════════
#  E/F/J. STATE + PLANNING
# ══════════════════════════════════════════════════════════════════════════

def load_run_state(run_dir, requested_dates):
    """Ground-truth status per requested date, proven by re-validating every
    checkpoint on disk right now -- never a cached in-memory belief."""
    state = {}
    cdir = checkpoints_dir(run_dir)
    for d in requested_dates:
        meta_exists = os.path.exists(checkpoint_meta_path(run_dir, d))
        data_exists = os.path.exists(checkpoint_data_path(run_dir, d))
        if not meta_exists and not data_exists:
            state[d] = {"resolved": "never_run"}
            continue
        ok, result = validate_checkpoint(run_dir, d)
        if ok:
            state[d] = {"resolved": result["status"], "meta": result}
        elif meta_exists:
            state[d] = {"resolved": "error", "reason": result, "meta": _safe_read_meta(run_dir, d)}
        else:
            state[d] = {"resolved": "partial", "reason": result}
    return state


def _safe_read_meta(run_dir, date):
    try:
        with open(checkpoint_meta_path(run_dir, date), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def plan_remaining(state, *, force=False):
    """Dates that still need real work: never_run, partial (an interrupted
    write left a data file with no valid meta), or error -- unless force,
    in which case every requested date is replanned (a deterministic full
    rebuild of this run, not an append)."""
    if force:
        return sorted(state.keys())
    return sorted(d for d, s in state.items() if s["resolved"] not in ("ok", "no_games"))


def _status_counts(state):
    counts = {}
    for s in state.values():
        counts[s["resolved"]] = counts.get(s["resolved"], 0) + 1
    return counts


def validate_complete(state):
    """Requirement F: fail loudly on ANY unexplained gap."""
    bad = {d: s for d, s in state.items() if s["resolved"] not in ("ok", "no_games")}
    if bad:
        raise RuntimeError(
            f"canonical run is NOT complete: {len(bad)} date(s) unresolved -- "
            f"{ {d: s['resolved'] for d, s in sorted(bad.items())[:10]} }"
            f"{' ...' if len(bad) > 10 else ''}")
    return True


# ══════════════════════════════════════════════════════════════════════════
#  I. DETERMINISTIC ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════

def logical_fingerprint(state, requested_dates):
    """Order-independent identity: sha256 of the sorted, canonical
    (date, status, row_count, per-date-sha256) tuples. Equal fingerprints
    prove equal logical content even if two assemblies differ in on-disk
    byte layout for reasons unrelated to the data itself."""
    parts = []
    for d in sorted(requested_dates):
        s = state[d]
        meta = s.get("meta") or {}
        parts.append((d, s["resolved"], meta.get("row_count", 0), meta.get("sha256", "")))
    canonical = json.dumps(parts, sort_keys=True)
    return _sha256_text(canonical)


def assemble(run_dir, manifest, *, requested_dates=None):
    """Requirement I. Fails closed (via validate_complete) rather than
    assembling a canonical artifact with silent gaps."""
    requested_dates = requested_dates or date_range(
        manifest["requested_start_date"], manifest["requested_end_date"])
    state = load_run_state(run_dir, requested_dates)
    validate_complete(state)

    out_dir = assembled_dir(run_dir)
    os.makedirs(out_dir, exist_ok=True)
    rows_path = os.path.join(out_dir, "rows.jsonl")
    tmp_rows = rows_path + f".{uuid.uuid4().hex}.tmp"
    total_rows = 0
    with open(tmp_rows, "wb") as out:
        for d in sorted(requested_dates):
            if state[d]["resolved"] != "ok":
                continue
            with open(checkpoint_data_path(run_dir, d), "rb") as chunk:
                data = chunk.read()
                out.write(data)
                total_rows += state[d]["meta"]["row_count"]
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp_rows, rows_path)

    byte_checksum = _sha256_file(rows_path)
    fingerprint = logical_fingerprint(state, requested_dates)
    summary = {
        "run_id": manifest["run_id"],
        "assembled_at": _now_iso(),
        "requested_start_date": manifest["requested_start_date"],
        "requested_end_date": manifest["requested_end_date"],
        "total_dates": len(requested_dates),
        "ok_dates": sum(1 for s in state.values() if s["resolved"] == "ok"),
        "no_games_dates": sum(1 for s in state.values() if s["resolved"] == "no_games"),
        "total_rows": total_rows,
        "rows_path": rows_path,
        "byte_sha256": byte_checksum,
        "logical_fingerprint": fingerprint,
        "code_git_sha": manifest["code_git_sha"],
    }
    _atomic_write_json(os.path.join(out_dir, "manifest.json"), summary)
    return summary


# ══════════════════════════════════════════════════════════════════════════
#  K. DURABILITY BEYOND ONE PROCESS (manifest-only remote snapshot)
# ══════════════════════════════════════════════════════════════════════════

def push_manifest_snapshot(run_dir, *, branch="canonical-run-manifests", remote="origin"):
    """Pushes ONLY the small manifest + per-date meta files (never the bulk
    .jsonl row data -- typically kilobytes vs. hundreds of MB, honoring
    the mission's explicit 'do not commit hundreds of MB/GB into ordinary
    git history') to a dedicated branch, so a run's identity and checkpoint
    completion ledger survive even a total loss of local disk.

    2026-08-27, found the hard way: an earlier version of this function
    called plain `git add` + `git commit` in REPO_ROOT -- which, when
    REPO_ROOT IS the pinned canonical-run worktree (exactly the intended
    call site), silently advances that worktree's own HEAD. The very next
    run() invocation then correctly refused to proceed
    (verify_code_identity() caught the drift it was designed to catch),
    but the run itself was blocked by the tool meant to protect it. This
    version uses git PLUMBING ONLY -- hash-object/write-tree/commit-tree
    against a scratch GIT_INDEX_FILE, never `git commit`, never `git
    checkout` -- so it cannot move HEAD, the real index, or the working
    tree in ANY repo it is run from, pinned or not. Only new objects are
    written to the (shared, worktree-independent) object database and a
    branch ref is updated on the REMOTE via push; nothing local-and-
    checked-out is ever touched.

    Best-effort: failures are reported, never fatal to the run itself --
    the run's real progress lives in run_dir regardless of whether this
    push succeeds."""
    import subprocess
    import tempfile
    try:
        git_common_dir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10)
        if git_common_dir.returncode != 0:
            return {"pushed": False, "reason": "not a git checkout"}
        git_dir = git_common_dir.stdout.strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.normpath(os.path.join(REPO_ROOT, git_dir))

        meta_files = []
        for root, _, files in os.walk(run_dir):
            for fn in files:
                if fn.endswith(".meta.json") or fn == "manifest.json" or fn == "lock.json":
                    meta_files.append(os.path.relpath(os.path.join(root, fn), REPO_ROOT))
        if not meta_files:
            return {"pushed": False, "reason": "no manifest/meta files found under run_dir"}

        scratch_index = tempfile.NamedTemporaryFile(delete=False).name
        env = dict(os.environ, GIT_DIR=git_dir, GIT_INDEX_FILE=scratch_index,
                  GIT_WORK_TREE=REPO_ROOT)
        try:
            parent_proc = subprocess.run(
                ["git", "rev-parse", f"refs/remotes/{remote}/{branch}"],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=15)
            parent = parent_proc.stdout.strip() if parent_proc.returncode == 0 else None
            if parent:
                subprocess.run(["git", "read-tree", parent], cwd=REPO_ROOT, env=env,
                               check=True, capture_output=True, timeout=15)
            subprocess.run(["git", "add", "--", *meta_files], cwd=REPO_ROOT, env=env,
                           check=True, capture_output=True, timeout=30)
            tree = subprocess.run(["git", "write-tree"], cwd=REPO_ROOT, env=env,
                                  capture_output=True, text=True, timeout=15).stdout.strip()
            commit_args = ["git", "commit-tree", tree, "-m",
                          f"Canonical run manifest snapshot {_now_iso()}"]
            if parent:
                commit_args += ["-p", parent]
            commit_proc = subprocess.run(commit_args, cwd=REPO_ROOT, env=env,
                                         capture_output=True, text=True, timeout=15)
            if commit_proc.returncode != 0:
                return {"pushed": False, "reason": commit_proc.stderr.strip()[:500]}
            commit_sha = commit_proc.stdout.strip()
            push = subprocess.run(
                ["git", "push", remote, f"{commit_sha}:refs/heads/{branch}"],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)
            return {"pushed": push.returncode == 0,
                   "reason": None if push.returncode == 0 else push.stderr.strip()[:500]}
        finally:
            if os.path.exists(scratch_index):
                os.remove(scratch_index)
    except Exception as exc:  # best-effort by design -- see docstring
        return {"pushed": False, "reason": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
#  SALVAGE: IMPORT THE FORENSICALLY-VALIDATED LEGACY PARTIAL ARTIFACT
# ══════════════════════════════════════════════════════════════════════════

def import_legacy(run_dir, legacy_jsonl_path, legacy_state_path, *, legacy_code_git_sha):
    """One-time salvage of the dead PID-1633 run's partial output into this
    module's per-date checkpoint format. ONLY call this after independently
    re-proving the legacy file's integrity (parseability, checksums,
    row-count-vs-manifest agreement, single consistent code_git_sha,
    zero duplicate candidate identities) -- the 2026-08-27 forensic audit
    did exactly that for backtest/rows_backfill_v2.jsonl and found it
    clean, which is the only reason this function exists at all rather
    than discarding the partial run and starting over.

    Imported checkpoints are honestly tagged with their true origin
    (legacy_code_git_sha, not this run's own manifest SHA) so
    provenance.require_single_regime() and this module's own
    verify_code_identity() can still detect a real mismatch later if one
    ever existed -- salvage must never launder a different regime into
    looking native to the new run."""
    with open(legacy_state_path, encoding="utf-8") as f:
        legacy_state = json.load(f)
    legacy_dates = legacy_state.get("dates", {})

    by_date_rows = {}
    with open(legacy_jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            by_date_rows.setdefault(row["date"], []).append(row)

    imported = {"ok": 0, "no_games": 0, "skipped_already_present": 0}
    for d, meta in sorted(legacy_dates.items()):
        status = meta.get("status")
        if status not in ("ok", "no_games"):
            continue  # only salvage dates the legacy run itself considered resolved
        existing_ok, _ = validate_checkpoint(run_dir, d)
        if existing_ok:
            imported["skipped_already_present"] += 1
            continue
        rows = by_date_rows.get(d, [])
        if status == "ok" and len(rows) != meta.get("rows", -1):
            raise RuntimeError(
                f"legacy state disagrees with legacy file for {d}: "
                f"state says {meta.get('rows')} rows, file has {len(rows)} -- "
                f"refusing to import an internally-inconsistent legacy date")
        write_checkpoint(
            run_dir, d, rows if status == "ok" else [], status,
            elapsed=meta.get("seconds"),
            extra={"games": meta.get("games"), "candidates": meta.get("candidates"),
                  "ungraded": meta.get("ungraded"), "ungraded_reasons": meta.get("ungraded_reasons"),
                  "imported_from": "legacy_rows_backfill_v2"},
            source_code_git_sha=legacy_code_git_sha,
        )
        imported[status] += 1
    return imported


# ══════════════════════════════════════════════════════════════════════════
#  MAIN DRIVER
# ══════════════════════════════════════════════════════════════════════════

def run(run_dir, manifest, *, use_weather=True, use_bullpen=True,
       keep_unpriced=False, apply_policy=False, sleep=1.0, force=False,
       allow_sha_drift=False, verbose=True, store=None, heartbeat_every=5,
       max_dates=None, durability=None, environment=None, lineage=None,
       cache_mode=None):
    """The interruption-safe outer loop. Safe to call repeatedly (crash,
    restart, call again with the same run_dir/manifest) -- every call
    starts from load_run_state()'s ground truth, never in-memory belief.

    `durability` is a canonical_durability.DurabilityPolicy. It pushes
    completed dates -- ROWS INCLUDED, gzipped -- to the durable remote branch
    on a bounded cadence. It is OFF unless a policy is passed, so that only a
    deliberate canonical launch writes to the shared durable branch; the CLI
    below passes one.

    This is the part that was missing on 2026-08-27. The loop below was
    already interruption-safe against process death: every date is checkpointed
    atomically and load_run_state() re-derives ground truth from disk. What it
    was not safe against was the DISK going away, which is what happened. Local
    checkpoint safety is worth nothing once the container is reclaimed."""
    from backtest.engine import StatcastStore, dparse, shift, simulate_date

    from backtest import canonical_durability as _cd

    verify_code_identity(manifest, allow_sha_drift=allow_sha_drift)
    # DEFAULT OFF, deliberately, and the reason is a bug this very change
    # introduced: with durability defaulting ON, running the ordinary test
    # suite pushed 34 synthetic test runs to the real durable branch within
    # about three minutes. Any library caller -- a test, a notebook, an
    # analysis script -- would do the same. Remote pushes are a side effect on
    # shared state, so they are opt-in at the one place a human actually means
    # to launch a canonical run: the CLI in __main__, which passes
    # durability=DurabilityPolicy(). Everything else is safe by default.
    if durability is None or durability is False:
        durability = _cd.DurabilityPolicy(enabled=bool(durability))
    if environment is None:
        environment = _cd.environment_identity()

    # Explicit caller lineage wins. Otherwise, once StatcastStore has loaded,
    # we opportunistically bind the exact persisted Statcast bytes as PARTIAL
    # lineage. This is intentionally never marked complete here because
    # canonical simulation consumes additional mutable upstream sources.
    effective_lineage = list(lineage) if lineage is not None else None

    def _push_durable(final=False):
        res = _cd.push_durable_checkpoint(
            run_dir, manifest, environment=environment,
            lineage=effective_lineage, lineage_complete=False,
            cache_mode=cache_mode,
            state_summary=_status_counts(load_run_state(run_dir, requested_dates)))
        durability.note_pushed(res)
        if verbose:
            if res.get("pushed"):
                print(f"    durable: pushed {res['dates_written']} date(s), "
                      f"{res['bytes_written']}B gz -> {_cd.DURABLE_BRANCH}", flush=True)
            else:
                # Loud on purpose. A run whose durable pushes are silently
                # failing has exactly the durability of the run we lost.
                print(f"    !! DURABLE PUSH FAILED ({res.get('reason')}) -- this run's "
                      f"progress currently exists ONLY in this container", flush=True)
        return res

    lock = acquire_lock(run_dir, manifest["run_id"])
    # Covers EVERY long phase below -- Statcast warmup and each individual
    # simulate_date() alike -- rather than only date boundaries. See
    # LeaseHeartbeat's docstring for the live incident this prevents.
    lease = LeaseHeartbeat(run_dir, lock).start()
    try:
        requested_dates = date_range(manifest["requested_start_date"], manifest["requested_end_date"])
        state = load_run_state(run_dir, requested_dates)
        remaining = plan_remaining(state, force=force)
        if max_dates is not None:
            remaining = remaining[:max_dates]
        if verbose:
            print(f"canonical run {manifest['run_id']}: {len(requested_dates)} requested, "
                 f"{len(remaining)} remaining this invocation", flush=True)
        if not remaining:
            return {"remaining": 0, "state": state}

        if store is None:
            store = StatcastStore(dparse(remaining[0]).year, shift(remaining[-1], -1), verbose=verbose)
            store.load()

        if effective_lineage is None:
            statcast_record = _cd.statcast_lineage_from_cache_report(
                getattr(store, "cache_report", None),
                year=getattr(store, "year", dparse(remaining[0]).year),
                through=getattr(store, "through", shift(remaining[-1], -1)),
                cache_mode=cache_mode,
            )
            if statcast_record is not None:
                effective_lineage = [statcast_record]
                if verbose:
                    print(
                        "    provenance: bound persisted Statcast bytes as PARTIAL "
                        "source lineage; canonical certification still requires the "
                        "remaining mutable sources",
                        flush=True,
                    )

        for i, d in enumerate(remaining, 1):
            if verbose:
                print(f"[{i}/{len(remaining)}] {d}", flush=True)
            t0 = time.time()
            res = simulate_date(d, store, use_weather=use_weather, use_bullpen=use_bullpen,
                                keep_unpriced=keep_unpriced, verbose=verbose,
                                apply_policy=apply_policy)
            elapsed = round(time.time() - t0, 1)
            status = {"ok": "ok", "no_games": "no_games", "failed": "error"}[res.status]
            extra = {"games": res.n_games, "candidates": res.n_candidates,
                    "ungraded": res.n_ungraded, "ungraded_reasons": dict(res.ungraded_reasons),
                    "reason": res.reason}
            write_checkpoint(run_dir, d, res.rows if status == "ok" else [], status,
                             elapsed=elapsed, extra=extra)
            durability.note_date_completed()
            if durability.should_push():
                _push_durable()
            if i % heartbeat_every == 0:
                # Retained as a cheap progress-boundary refresh. The lease
                # thread is what actually guarantees freshness now, so this
                # is no longer load-bearing -- it just keeps the on-disk
                # lease aligned with observable progress for an operator
                # reading lock.json.
                lock = heartbeat_lock(run_dir, lock)
                lease.lock = dict(lock)
            if i < len(remaining):
                time.sleep(sleep)
        lock = heartbeat_lock(run_dir, lock)
        # Always push at the end of an invocation, so an operator who chunks a
        # run with --max-dates never leaves the tail undurable.
        if durability.should_push(final=True):
            _push_durable(final=True)
        final_state = load_run_state(run_dir, requested_dates)
        return {"remaining": 0, "state": final_state,
                "durability": durability.describe()}
    finally:
        # Stop the lease BEFORE releasing, so no tick can resurrect a lock
        # file the release is about to remove.
        lease.stop()
        release_lock(run_dir, lock)


def prepare_existing_run(base_dir, run_id, *, resume_from_remote=False):
    """Resolve an existing run for CLI use, including a true fresh-clone restore.

    The old CLI called load_manifest() before honoring --resume-from-remote,
    which made the documented fresh-container recovery path impossible: the
    local manifest was exactly what a reclaimed container no longer had.

    This helper performs only recovery/control-plane work. It does not bypass
    verify_code_identity(); generation still runs under the manifest's pinned
    scientific SHA or fails closed later in run().
    """
    rd = run_dir_for(base_dir, run_id)
    restore_report = None

    if resume_from_remote and not os.path.exists(manifest_path(rd)):
        from backtest import canonical_durability as _cd
        fetched = _cd.fetch_durable_branch()
        if not fetched.get("ok"):
            raise RuntimeError(
                f"could not fetch durable branch for {run_id}: {fetched}")
        restore_report = _cd.restore_from_durable(rd, run_id)

    mf = load_manifest(rd)
    return rd, mf, restore_report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--base-dir", default=os.path.join(REPO_ROOT, "backtest", "canonical_runs"))
    ap.add_argument("--run-id", help="resume an existing run_id instead of creating a new one")
    ap.add_argument("--no-weather", action="store_true")
    ap.add_argument("--no-bullpen", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-sha-drift", action="store_true")
    ap.add_argument("--max-dates", type=int, default=None,
                    help="process at most this many remaining dates then exit (for supervised chunking)")
    ap.add_argument("--assemble", action="store_true", help="assemble the final artifact instead of running")
    ap.add_argument("--no-durable-push", action="store_true",
                    help="do NOT push completed dates to the durable remote branch. "
                         "Only for local experiments -- a real canonical run without this "
                         "has no protection against container loss.")
    ap.add_argument("--durable-every-dates", type=int, default=10,
                    help="push durably after this many completed dates (default 10)")
    ap.add_argument("--durable-every-seconds", type=int, default=900,
                    help="push durably after this many seconds (default 900)")
    ap.add_argument("--resume-from-remote", action="store_true",
                    help="before running, restore this run's completed dates from the durable "
                         "remote branch. Use after a container loss: it needs nothing local "
                         "except a clone. Fails closed on any identity or checksum mismatch.")
    ap.add_argument("--cache-mode", choices=("fresh_source", "frozen_cache"), default="frozen_cache",
                    help="declare the pybaseball/Statcast source vintage this run used")
    args = ap.parse_args()

    pre_restore = None
    if args.run_id:
        rd, mf, pre_restore = prepare_existing_run(
            args.base_dir, args.run_id,
            resume_from_remote=args.resume_from_remote)
    else:
        identity = build_run_identity(
            args.start, args.end, out_target=os.path.join(args.base_dir, "{run_id}", "assembled", "rows.jsonl"),
            weather_mode="no_weather" if args.no_weather else "with_weather")
        rd = run_dir_for(args.base_dir, identity["run_id"])
        os.makedirs(rd, exist_ok=True)
        mf = create_manifest(rd, identity)
        print(f"created run {mf['run_id']} at {rd}", flush=True)

    if args.assemble:
        summary = assemble(rd, mf)
        print(json.dumps(summary, indent=2))
    else:
        from backtest import canonical_durability as _cd

        if args.resume_from_remote:
            if pre_restore is not None:
                rep = pre_restore
                fetched = {"ok": True, "note": "fetched during fresh-clone preparation"}
            else:
                fetched = _cd.fetch_durable_branch()
                print(f"durable fetch: {fetched}", flush=True)
                if not fetched.get("ok"):
                    raise RuntimeError(
                        f"could not fetch durable branch for {mf['run_id']}: {fetched}")
                rep = _cd.restore_from_durable(rd, mf["run_id"], manifest=mf)
            print(f"restored {len(rep['restored'])} date(s) from the durable branch, "
                  f"{len(rep['skipped_present'])} already present locally, "
                  f"{len(rep['failed'])} failed", flush=True)
            if rep["failed"]:
                print(json.dumps(rep["failed"], indent=2), flush=True)

        policy = _cd.DurabilityPolicy(
            every_n_dates=args.durable_every_dates,
            every_seconds=args.durable_every_seconds,
            enabled=not args.no_durable_push)
        if not policy.enabled:
            print("!! durable push DISABLED -- this run's progress will exist only in "
                  "this container and will not survive its reclamation", flush=True)

        result = run(rd, mf, use_weather=not args.no_weather, use_bullpen=not args.no_bullpen,
                     sleep=args.sleep, force=args.force, allow_sha_drift=args.allow_sha_drift,
                     max_dates=args.max_dates, durability=policy,
                     cache_mode=args.cache_mode)
        print(f"invocation complete. {result['remaining']} dates still remaining.")
        print(f"durability: {json.dumps(result.get('durability'))}")
