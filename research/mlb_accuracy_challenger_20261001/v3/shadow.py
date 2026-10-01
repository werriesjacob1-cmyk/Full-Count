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

import hashlib
import os
import shutil
import subprocess

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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], stderr=subprocess.STDOUT).decode().strip()


def build_tree(repo, workdir, date, live_ref="origin/main"):
    """Fresh detached worktree at SHADOW_PIN plus the one live overlay file. Returns provenance."""
    if os.path.exists(workdir):
        raise FileExistsError(f"{workdir} exists; shadow trees are never reused")
    if _git(repo, "rev-parse", f"{SHADOW_PIN}^{{tree}}") != SHADOW_TREE:
        raise RuntimeError("SHADOW_PIN tree id mismatch")
    _git(repo, "worktree", "add", "--detach", workdir, SHADOW_PIN)
    for rel, want in PINNED_ARTIFACT_SHA256.items():
        if sha256_file(os.path.join(workdir, rel)) != want:
            raise RuntimeError(f"pinned artifact drift: {rel}")
    rel = LIVE_OVERLAY_TEMPLATE.format(date=date)
    overlay = {"path": rel, "source_ref": _git(repo, "rev-parse", live_ref), "sha256": None}
    try:
        blob = subprocess.check_output(["git", "-C", repo, "show", f"{live_ref}:{rel}"], stderr=subprocess.DEVNULL)
        os.makedirs(os.path.dirname(os.path.join(workdir, rel)), exist_ok=True)
        with open(os.path.join(workdir, rel), "wb") as fh:
            fh.write(blob)
        overlay["sha256"] = hashlib.sha256(blob).hexdigest()
    except subprocess.CalledProcessError:
        overlay["status"] = "ABSENT_ON_LIVE_REF"   # the pinned feature then returns {} by design
    return {"shadow_id": SHADOW_ID, "pin": SHADOW_PIN, "tree": SHADOW_TREE, "overlay": overlay}


def run_pipeline(workdir, timeout_s=1800):
    """Run the pinned generate_picks.py in the shadow tree (UTC). Returns the shadow board path."""
    env = dict(os.environ, TZ="UTC", PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(["python3", "generate_picks.py"], cwd=workdir, env=env, check=True, timeout=timeout_s,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    date = subprocess.check_output(["python3", "-c", "import mlb_daily as m; print(m.TODAY)"], cwd=workdir,
                                   env=env).decode().strip()
    return os.path.join(workdir, "output", f"board_freeze_{date}.json")


def verify_shadow_board(board):
    """The board must come from the pinned pipeline with the pinned labels."""
    prov = board.get("provenance") or {}
    if str(prov.get("git_sha") or "")[:10] != SHADOW_PIN[:10]:
        raise ValueError(f"shadow board git_sha {prov.get('git_sha')} is not the pin {SHADOW_PIN[:10]}")
    for k, v in SHADOW_LABELS.items():
        if prov.get(k) != v:
            raise ValueError(f"shadow board {k}={prov.get(k)} is not {v}")
    return board


def remove_tree(repo, workdir):
    subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", workdir], check=False)
    shutil.rmtree(workdir, ignore_errors=True)
