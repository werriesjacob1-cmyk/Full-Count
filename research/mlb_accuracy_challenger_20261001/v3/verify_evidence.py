#!/usr/bin/env python3
"""The ONE mandatory evidence verifier for prereg v3 (sections 4-8). Research only.

Given a checkout of the evidence ref, it trusts nothing a caller or a stored
JSON field asserts:
  1. CHAIN.json is walked from the anchored genesis (prereg v3 sha256); every
     entry's seal.json must rebuild to the entry hash and link to its
     predecessor; every seals/ directory must be in the chain, each unit once.
  2. Every artifact named in the seal is re-hashed from its bytes.
  3. Capture, shadow board, schedule and manifest are re-verified, and the
     manifest is REBUILT from the sealed board + capture + schedule (every quote
     re-resolved from the sealed capture bytes); it must hash identically.
  4. The sealed game-line overlay must hash to the provenance and contain no
     snapshot later than its cutoff; the shadow tape must hash to the provenance.
  5. The Issue #91 receipt is re-fetched from the GitHub API (server created_at);
     RFC 3161 tokens are verified cryptographically from their stored bytes and
     their genTime is read from the token. Both must precede the earliest
     covered first pitch.
  6. The shadow board is REPRODUCED by replaying the sealed tape through the
     frozen pinned pipeline; it must be replay-equivalent to the sealed board.
Any failure -> the unit is not usable. An unavailable external check ->
CONFIRMATORY_REJECT_EVIDENCE_UNVERIFIED.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import capture as CP  # noqa: E402
import harness as H  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402

PREREG_COMMIT = "15fb1d539c16d368bd8835254d93bb8f0ccd4609"
PREREG_SHA256 = "5eb56f2837e25d29c5821043955eefe52c0d5f0513e9b6099ac5facb2ff63442"
GENESIS_SEAL_SHA256 = PREREG_SHA256
REQUIRED_ARTIFACTS = ("shadow_board.json.gz", "capture.json.gz", "schedule.json", "manifest.json.gz",
                      "shadow_tape.json.gz")
OPTIONAL_ARTIFACTS = ("overlay.json",      # present iff an overlay was sealed
                      "shadow_env.json")   # FC-MLB-001A: record-environment fingerprint (absent on legacy units)
GITHUB_API = "https://api.github.com/repos/werriesjacob1-cmyk/Full-Count"
FROZEN_COEFFICIENTS_PATH = os.path.join(os.path.dirname(HERE), "frozen_coefficients.json")
FROZEN_COEFFICIENTS_SHA256 = "3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009"   # prereg v3 s10


class CoefficientIntegrityError(Exception):
    pass


def load_frozen_coefficients():
    """The preregistered frozen challenger coefficients, or an exception. The bytes are read once,
    hashed, and parsed from those same bytes; no other path, file or object is ever accepted."""
    try:
        with open(FROZEN_COEFFICIENTS_PATH, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise CoefficientIntegrityError(f"frozen coefficient artifact missing: {exc}") from exc
    got = hashlib.sha256(raw).hexdigest()
    if got != FROZEN_COEFFICIENTS_SHA256:
        raise CoefficientIntegrityError(f"frozen coefficient sha256 {got} != {FROZEN_COEFFICIENTS_SHA256}")
    return json.loads(raw)


class EvidenceError(Exception):
    pass


class EvidenceUnverified(Exception):
    """An external check could not be performed (network/API). Never treated as a pass."""


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def fetch_issue_comment(comment_id):
    """The verifier's own GitHub API read (server-observed created_at). Raises EvidenceUnverified."""
    req = urllib.request.Request(f"{GITHUB_API}/issues/comments/{int(comment_id)}",
                                 headers={"Accept": "application/vnd.github+json"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        raise EvidenceUnverified(f"GitHub comment {comment_id} not retrievable: {exc}") from exc


def _repo_root():
    return subprocess.check_output(["git", "-C", HERE, "rev-parse", "--show-toplevel"]).decode().strip()


def replay_shadow(unit_dir, board, overlay_bytes, detail=None):
    """Reproduce the shadow board from sealed inputs only, in a fresh isolated environment (FC-MLB-001A).
    Returns True iff replay-equivalent. A1 units must also replay under the identical locked environment
    as recorded (shadow_env.json); legacy units have no fingerprint and must replay exactly regardless."""
    import isolation as ISO
    detail = detail if detail is not None else {}
    repo = _repo_root()
    work = tempfile.mkdtemp(prefix="v3replay_")
    tree = os.path.join(work, "tree")
    try:
        SH.build_tree(repo, tree, board["date"], live_ref=SH.SHADOW_PIN)   # no live read at all
        SH.install_sealed_overlay(tree, board["date"], overlay_bytes)
        tape = os.path.join(work, "tape.json.gz")
        shutil.copy(os.path.join(unit_dir, "shadow_tape.json.gz"), tape)
        try:
            out = json.load(open(SH.run_pipeline(tree, tape, "replay")))
        except (RuntimeError, ISO.IsolationError) as exc:
            detail["replay_error"] = str(exc)[:500]
            return False
        finally:
            if os.path.exists(tape + ".replay.env.json"):
                detail["replay_env"] = json.load(open(tape + ".replay.env.json"))
        rec = os.path.join(unit_dir, "shadow_env.json")
        if os.path.exists(rec):
            problems = ISO.check_replay_compatible(json.load(open(rec)), detail.get("replay_env") or {})
            if problems:
                detail["environment_mismatch"] = problems
                return False
        else:
            detail["legacy_unit"] = "no sealed record-environment fingerprint (pre-A1)"
        return SH.replay_equivalent(board, out)
    finally:
        SH.remove_tree(repo, tree)
        shutil.rmtree(work, ignore_errors=True)


def load_chain(root):
    """Ordered, verified seal records from the anchored genesis. Raises EvidenceError."""
    path = os.path.join(root, "CHAIN.json")
    if not os.path.exists(path):
        raise EvidenceError("CHAIN.json missing")
    chain = json.load(open(path))
    if not chain or chain[0].get("index") != 0 or chain[0].get("unit") != "GENESIS" \
            or chain[0].get("seal_sha256") != GENESIS_SEAL_SHA256:
        raise EvidenceError("chain does not start at the anchored v3 genesis")
    seals, prev, units = [], GENESIS_SEAL_SHA256, set()
    for i, entry in enumerate(chain[1:], start=1):
        if entry.get("index") != i:
            raise EvidenceError(f"chain index break at {i}")
        sp = os.path.join(root, "seals", entry["unit"], "seal.json")
        if not os.path.exists(sp):
            raise EvidenceError(f"seal for {entry['unit']} missing")
        s = json.load(open(sp))
        if CP.canonical_sha256({k: v for k, v in s.items() if k != "seal_sha256"}) != s.get("seal_sha256") \
                or s["seal_sha256"] != entry.get("seal_sha256"):
            raise EvidenceError(f"seal {entry['unit']} modified or not the chained record")
        if s.get("prev_seal_sha256") != prev:
            raise EvidenceError(f"chain link broken at {entry['unit']} (rewrite, removal or reorder)")
        if f"{s['date']}_{s['window']}" != entry["unit"] or entry["unit"] in units:
            raise EvidenceError(f"unit {entry['unit']} mislabelled or sealed twice")
        units.add(entry["unit"])
        prev = s["seal_sha256"]
        seals.append(s)
    present = set(os.listdir(os.path.join(root, "seals"))) if os.path.isdir(os.path.join(root, "seals")) else set()
    if present - units:
        raise EvidenceError(f"unchained seal directories present: {sorted(present - units)}")
    return seals


def _load(unit_dir, name):
    p = os.path.join(unit_dir, name)
    return json.load(gzip.open(p)) if name.endswith(".gz") else json.load(open(p))


def verify_unit(root, seal):
    """Returns (status, detail, manifest_or_None). Raises EvidenceUnverified for unavailable externals."""
    unit = f"{seal['date']}_{seal['window']}"
    d = os.path.join(root, "seals", unit)
    arts = seal.get("artifacts_sha256") or {}
    if not set(REQUIRED_ARTIFACTS) <= set(arts) or set(arts) - set(REQUIRED_ARTIFACTS) - set(OPTIONAL_ARTIFACTS):
        return "ARTIFACT_SET_INVALID", sorted(arts), None
    for name, want in arts.items():
        p = os.path.join(d, name)
        if not os.path.exists(p):
            return "ARTIFACT_MISSING", name, None
        if sha256_bytes(open(p, "rb").read()) != want:
            return "ARTIFACT_HASH_MISMATCH", name, None
    board, cap, sched, man = (_load(d, n) for n in ("shadow_board.json.gz", "capture.json.gz", "schedule.json",
                                                     "manifest.json.gz"))
    try:
        SH.verify_shadow_board(board)
        if H.canonical_board_hash(board) != board.get("board_sha256") or board["board_sha256"] != seal["shadow_board_sha256"]:
            return "SHADOW_BOARD_HASH_MISMATCH", None, None
        CP.verify_capture(cap)
        if cap["capture_sha256"] != seal["capture_sha256"]:
            return "CAPTURE_NOT_SEALED_ONE", None, None
        if CP.canonical_sha256(sched) != seal["schedule_sha256"]:
            return "SCHEDULE_HASH_MISMATCH", None, None
        M3.verify_manifest(man)
        if man["manifest_sha256"] != seal["manifest_sha256"]:
            return "MANIFEST_NOT_SEALED_ONE", None, None
        rebuilt = M3.build_manifest(board, cap, sched, window=man["window"], cutoff_utc=man["cutoff_utc"],
                                    shadow_provenance=man["shadow_provenance"])
    except ValueError as exc:
        return "SEALED_INPUT_INVALID", str(exc), None
    if rebuilt["manifest_sha256"] != man["manifest_sha256"]:
        return "MANIFEST_NOT_REPRODUCIBLE", "re-resolving quotes from the sealed capture changed the manifest", None
    prov = man.get("shadow_provenance") or {}
    ov = prov.get("overlay") or {}
    overlay_bytes = open(os.path.join(d, "overlay.json"), "rb").read() if "overlay.json" in arts else None
    if (overlay_bytes is None) != (ov.get("sealed_sha256") is None) or \
            (overlay_bytes is not None and sha256_bytes(overlay_bytes) != ov["sealed_sha256"]):
        return "OVERLAY_NOT_SEALED_ONE", None, None
    if overlay_bytes is not None:
        cutoff = M3.utc(ov["overlay_cutoff"])
        if any(not SH._safe_le(s.get("taken_at"), cutoff) for s in json.loads(overlay_bytes).get("snapshots") or []):
            return "OVERLAY_POST_CUTOFF_ROW", None, None
        if cutoff > M3.utc(man["cutoff_utc"]):
            return "OVERLAY_CUTOFF_AFTER_MANIFEST_CUTOFF", None, None
    if prov.get("tape_sha256") != arts["shadow_tape.json.gz"]:
        return "TAPE_NOT_SEALED_ONE", None, None
    rc = json.load(open(os.path.join(d, "receipts.json"))) if os.path.exists(os.path.join(d, "receipts.json")) else {}
    if not rc.get("github_comment_id"):
        return "MISSING_EXTERNAL_RECEIPT", "receipts.json has no GitHub comment id", None
    comment = fetch_issue_comment(rc["github_comment_id"])         # raises EvidenceUnverified
    status, detail = SL.verify_receipts_raw(seal, comment, rc.get("tsa"),
                                            earliest_first_pitch_utc=man["earliest_first_pitch_utc"])
    if status != "ON_TIME":
        return status, detail, None
    rdetail = {}
    if not replay_shadow(d, board, overlay_bytes, rdetail):
        return "SHADOW_NOT_REPRODUCIBLE", {k: v for k, v in rdetail.items() if k != "replay_env"}, None
    return "VERIFIED", detail, man
