#!/usr/bin/env python3
"""FROZEN SHADOW CHAMPION 2026.08.15 -- preregistration v3, section 8.

The experimental champion is NOT whatever production runs. It is the
production MLB pipeline frozen at one exact commit (SHADOW_PIN: code, feature
logic, calibrators, signal weights, recommendation/selection policy), run by
the research runner itself at the slate's cutoff, against live data.

* Pinned: the entire git tree of SHADOW_PIN (verified by tree id), including
  backtest/calibrators_by_market.json, backtest/reliability_bands.json and
  results/signal_measurement.json (learned artifacts).
* Live overlay (observational data only, copied from the current main ref at
  run time and hashed): exactly LIVE_OVERLAY_TEMPLATE, the production game-line
  snapshot that the pinned code's line_movement() feature reads. Nothing else.
* The shadow board is the pinned pipeline's own frozen full board; its
  provenance.git_sha must equal SHADOW_PIN[:10] and its model labels must be
  the pinned ones.

Production stays free to change model, selector and calibration: none of that
reaches this tree. Production's actual published picks are reported separately
and never stand in for the champion.

The drill on 2026-10-01 (pin 7d3ebacd55, 164 s, network fetches only from
statsapi / FanGraphs / Savant / Rotowire / weather / FanDuel) traced every
file the pipeline opened: reads outside code and the pinned artifacts were
data/odds/odds_{date}.json (feature) and data/players/*.json (read only by the
post-freeze parlay/HTML renderers).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

SHADOW_PIN = "7d3ebacd55c34c6ec78030bdeca70799e56d4764"
SHADOW_TREE = "02526a74f6669069127d5a72d771a437cb5561bf"
SHADOW_LABELS = {"model_version": "2026.08.15", "selection_policy_version": "1.0.0",
                 "calibration_version": "1.0.0", "feature_version": "1.0.0"}
SHADOW_ID = "FROZEN_SHADOW_POLICY_2026.08.15@7d3ebacd55"
PINNED_ARTIFACT_SHA256 = {
    "backtest/calibrators_by_market.json": "3423b1c24e237af9c51a682118e1e90ccb1ab0f2247d6b5564c07dc6a0584dfd",
    "backtest/reliability_bands.json": "5536e914d095190d4cb3e38f703d425587d01beec2086d5c0e7b77e32ba87c4f",
    "results/signal_measurement.json": "0450d8d1d16909c8fc7c91e7cea75e19b379c36521c1a86152c5dbf2cb3ebec0",
    "recommendation.py": "a05424df25d4c9566bcafd8643d12b3e1c00efe1ec28509995a417a743ec2c73",
    "generate_picks.py": "d8749d38f92ead4426d43462d2e2321388fac38225314ecb28962e469fba6556",
    "board_freeze.py": "5c060007a827c2f28bffc9668f8373b1d0500e60a48c3d55166078cbd87dc9c0",
}
LIVE_OVERLAY_TEMPLATE = "data/odds/odds_{date}.json"
# Board fields that legitimately differ between the live run and its replay: wall-clock
# stamps of the run itself and the board hash computed over them. Nothing else may differ.
REPLAY_TIMESTAMP_FIELDS = ("board_generated_at", "sealed_at", "board_sha256")
REPLAY_RECORD_TIMESTAMP_FIELDS = ("generation_timestamp",)
NETRECORD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "netrecord.py")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], stderr=subprocess.STDOUT).decode().strip()


def _utc(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("naive timestamp")
    return t.astimezone(timezone.utc)


def filter_overlay(raw_bytes, overlay_cutoff):
    """Keep only game-line snapshots observed at or before the cutoff (deterministic rule:
    every snapshot whose taken_at parses and is <= cutoff, in file order). Later or
    unparseable snapshots are dropped and listed. Returns (sealed_bytes, report)."""
    payload = json.loads(raw_bytes)
    snaps = payload.get("snapshots") or []
    kept = [x for x in snaps if _safe_le(x.get("taken_at"), overlay_cutoff)]
    payload["snapshots"] = kept
    sealed = json.dumps(payload, indent=2).encode()
    return sealed, {"kept": len(kept),
                    "dropped_post_cutoff_or_invalid": [x.get("taken_at") for x in snaps if x not in kept],
                    "latest_kept_taken_at": max((x["taken_at"] for x in kept), key=_utc) if kept else None}


def _safe_le(ts, cutoff):
    try:
        return _utc(ts) <= cutoff
    except (TypeError, ValueError):
        return False


def build_tree(repo, workdir, date, live_ref="origin/main", overlay_cutoff=None):
    """Fresh detached worktree at SHADOW_PIN plus the one sealed overlay file. Returns provenance."""
    if os.path.exists(workdir):
        raise FileExistsError(f"{workdir} exists; shadow trees are never reused")
    if _git(repo, "rev-parse", f"{SHADOW_PIN}^{{tree}}") != SHADOW_TREE:
        raise RuntimeError("SHADOW_PIN tree id mismatch")
    _git(repo, "worktree", "add", "--detach", workdir, SHADOW_PIN)
    _git(workdir, "sparse-checkout", "disable")     # the pinned tree is always complete
    if _git(workdir, "rev-parse", "HEAD^{tree}") != SHADOW_TREE or _git(workdir, "status", "--porcelain"):
        raise RuntimeError("shadow worktree is not the exact pinned tree")
    for rel, want in PINNED_ARTIFACT_SHA256.items():
        if sha256_file(os.path.join(workdir, rel)) != want:
            raise RuntimeError(f"pinned artifact drift: {rel}")
    # A pinned tree carries production's committed board files; the shadow run must write its own.
    for f in glob.glob(os.path.join(workdir, "output", "board_freeze_*.json")):
        os.remove(f)
    cutoff = overlay_cutoff or datetime.now(timezone.utc)
    rel = LIVE_OVERLAY_TEMPLATE.format(date=date)
    target = os.path.join(workdir, rel)
    if os.path.exists(target):
        os.remove(target)                     # never the pinned (stale) copy
    overlay = {"path": rel, "source_ref": _git(repo, "rev-parse", live_ref), "overlay_cutoff": cutoff.isoformat()}
    try:
        raw = subprocess.check_output(["git", "-C", repo, "show", f"{live_ref}:{rel}"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        raw = None
    if raw is None:
        overlay["status"] = "ABSENT_ON_LIVE_REF"   # pinned line_movement(): missing file -> {} (exists at the pin)
        overlay["sealed_sha256"] = None
    else:
        sealed, rep = filter_overlay(raw, cutoff)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(sealed)
        overlay.update({"status": "SEALED", "source_sha256": hashlib.sha256(raw).hexdigest(),
                        "sealed_sha256": hashlib.sha256(sealed).hexdigest(), **rep})
    return {"shadow_id": SHADOW_ID, "pin": SHADOW_PIN, "tree": SHADOW_TREE, "overlay": overlay}


def install_sealed_overlay(workdir, date, overlay_bytes):
    """Replay: put back exactly the sealed overlay bytes (or none, if none was sealed)."""
    target = os.path.join(workdir, LIVE_OVERLAY_TEMPLATE.format(date=date))
    if os.path.exists(target):
        os.remove(target)
    if overlay_bytes is not None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(overlay_bytes)


def run_pipeline(workdir, tape_path, mode, timeout_s=1800, script="generate_picks.py", lock_path=None):
    """Run the pinned generate_picks.py under netrecord (record live / replay sealed). UTC,
    fixed hash seed. Returns the freshly written shadow board path.

    FC-MLB-001A (A1): every record and every replay runs in its OWN fresh isolated root
    (empty HOME / pybaseball cache / TMPDIR, venv from the hash lock, injected git core.abbrev=10 (R4),
    explicit environment, audit-hook guard, strace -f process-tree trace). The run's environment fingerprint
    (with the classified trace) is written to <tape>.<mode>.env.json and the raw trace to <tape>.<mode>.strace;
    any isolation breach or guard violation fails the run closed. Frozen-R5 trace violations are RECORDED
    (process_trace.frozen_r5_violations), not waived: the verifiers report them as a failed check."""
    import isolation as ISO
    trace_path = tape_path + f".{mode}.strace"
    with ISO.IsolatedRun(mode, workdir, lock_path=lock_path or ISO.SHADOW_LOCK) as iso:
        # R5: the whole process tree runs under strace -f (subprocesses + native/OS reads), not just the audit hook
        proc = subprocess.run([*ISO.trace_command(trace_path), iso.python, NETRECORD, "--mode", mode, "--tape", tape_path,
                               "--", script], cwd=workdir, env=iso.env, timeout=timeout_s,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        fp = dict(iso.fingerprint)
        fp["process_trace"] = ISO.summarize_trace(trace_path, workdir, iso.root, tape_path)
    rep_path = tape_path + f".{mode}.report.json"
    if not os.path.exists(rep_path):
        raise RuntimeError(f"pipeline produced no {mode} report (rc {proc.returncode}): "
                           f"{proc.stderr.decode(errors='replace').strip()[-600:]}")
    rep = json.load(open(rep_path))
    fp["guard"] = rep.get("guard") or {"violations": ["GUARD_NOT_ACTIVE"]}
    fp["guard"]["reads_by_surface"] = {_surface_label(k, workdir): v for k, v in fp["guard"].get("reads_by_surface", {}).items()}
    fp["n_http"], fp["replay_misses"], fp["unconsumed"] = rep["n_http"], len(rep["replay_misses"]), rep["unconsumed"]
    with open(tape_path + f".{mode}.env.json", "w") as fh:
        json.dump(fp, fh, indent=1, sort_keys=True)
    if fp["guard"]["violations"]:
        raise RuntimeError(f"A1 guard: hidden local state read outside permitted surfaces: {fp['guard']['violations'][:10]}")
    if mode == "replay" and (rep["replay_misses"] or rep["unconsumed"]):
        raise RuntimeError(f"replay not exact: misses={len(rep['replay_misses'])} unconsumed={rep['unconsumed']}")
    if proc.returncode != 0:
        raise RuntimeError(f"pipeline exited rc {proc.returncode} in {mode}: "
                           f"{proc.stderr.decode(errors='replace').strip()[-600:]}")
    boards = glob.glob(os.path.join(workdir, "output", "board_freeze_*.json"))
    if len(boards) != 1:
        raise RuntimeError(f"expected exactly one fresh shadow board, found {len(boards)}")
    return boards[0]


def _surface_label(prefix, workdir):
    """Stable, machine-independent names for permitted surfaces (paths differ across containers)."""
    p = prefix.rstrip("/")
    if p == os.path.abspath(workdir):
        return "PINNED_TREE"
    if "/v3a1_record_" in p or "/v3a1_replay_" in p:
        return "RUN_ROOT"
    if p == os.path.dirname(os.path.abspath(__file__)):
        return "AMENDMENT_CODE"
    if p.endswith(("/lib/python3.11", "/lib/python3.12")) or "/lib/python3." in p:
        return "STDLIB"
    return p if p.startswith(("/dev", "/proc", "/sys", "/etc", "/usr/share", "/usr/lib/ssl")) else "SEALED_UNIT_DIR"


def verify_shadow_board(board):
    """The board must come from the pinned pipeline with the pinned labels."""
    prov = board.get("provenance") or {}
    if str(prov.get("git_sha") or "")[:10] != SHADOW_PIN[:10]:
        raise ValueError(f"shadow board git_sha {prov.get('git_sha')} is not the pin {SHADOW_PIN[:10]}")
    for k, v in SHADOW_LABELS.items():
        if prov.get(k) != v:
            raise ValueError(f"shadow board {k}={prov.get(k)} is not {v}")
    return board


def replay_view(board):
    b = {k: v for k, v in board.items() if k not in REPLAY_TIMESTAMP_FIELDS}
    b["records"] = [{k: v for k, v in r.items() if k not in REPLAY_RECORD_TIMESTAMP_FIELDS}
                    for r in board.get("records") or []]
    return b


def replay_equivalent(sealed_board, replayed_board):
    return replay_view(sealed_board) == replay_view(replayed_board)


def remove_tree(repo, workdir):
    subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", workdir], check=False)
    shutil.rmtree(workdir, ignore_errors=True)
