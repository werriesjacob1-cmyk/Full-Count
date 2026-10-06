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
                      "shadow_env.json",   # FC-MLB-001A: record-environment fingerprint (absent on legacy units)
                      "shadow_board_raw.json.gz",   # FC-MLB-001A: literal record pipeline board bytes (newer units)
                      "shadow_record_trace.strace.gz", "shadow_record_setup_trace.strace.gz",   # FC-MLB-001B traces
                      "tape_artifact.json")   # TEMPORARY Actions-artifact store: upload read-back proof + expiry
# FC-MLB-001B units: the scientific payload bytes + record-environment fingerprint are sealed; the complete tape is
# NOT a unit file -- it lives in the content-addressed store named by manifest shadow_provenance.tape_store.
B_REQUIRED_ARTIFACTS = ("shadow_board.json.gz", "capture.json.gz", "schedule.json", "manifest.json.gz",
                        "shadow_payload.json", "shadow_env.json")
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


def _board_field_diffs(a, b, path="board"):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _board_field_diffs(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _board_field_diffs(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def literal_board_identity(unit_dir, replay_raw):
    """FC-MLB-001A: EXACT shadow-board identity. Compares literal bytes, never a canonicalized view:
      sealed  - the record board as sealed (gunzipped shadow_board.json.gz) vs the replay board serialized by the
                identical sealing serializer (runner._dump: json indent=1, sort_keys);
      raw     - when the unit carries shadow_board_raw.json.gz: record pipeline bytes vs replay pipeline bytes.
    Differing field paths are reported (indices collapsed) so a failure names exactly what prevents identity."""
    import re
    rec_sealed = gzip.open(os.path.join(unit_dir, "shadow_board.json.gz")).read()
    rep_board = json.loads(replay_raw)
    rep_sealed = json.dumps(rep_board, indent=1, sort_keys=True).encode()
    h = lambda b: hashlib.sha256(b).hexdigest()
    out = {"record_sealed_board_sha256": h(rec_sealed), "replay_sealed_board_sha256": h(rep_sealed),
           "replay_raw_board_sha256": h(replay_raw), "sealed_identical": rec_sealed == rep_sealed}
    rawp = os.path.join(unit_dir, "shadow_board_raw.json.gz")
    if os.path.exists(rawp):
        rec_raw = gzip.open(rawp).read()
        out.update(record_raw_board_sha256=h(rec_raw), raw_identical=rec_raw == replay_raw)
    else:
        out["record_raw_board"] = "NOT RETAINED (unit predates shadow_board_raw.json.gz); sealed bytes compared"
    paths = _board_field_diffs(json.loads(rec_sealed), rep_board)
    collapsed = {}
    for p in paths:
        k = re.sub(r"\[\d+\]", "[*]", p)
        collapsed[k] = collapsed.get(k, 0) + 1
    out["differing_fields"] = collapsed
    out["identical"] = out["sealed_identical"] and out.get("raw_identical", True)
    return out


def replay_shadow(unit_dir, board, overlay_bytes, detail=None, keep_dir=None):
    """Reproduce the shadow board from sealed inputs only, in a fresh isolated environment (FC-MLB-001A).
    Returns True iff the replay board is LITERALLY identical to the sealed record board (literal_board_identity),
    the replay environment matches the sealed record environment, and the record conforms to the current A1
    mechanism. The canonical (timestamp-stripped) comparison is kept only as a labelled diagnostic.
    keep_dir: retain the replay's raw board bytes, env fingerprint and process trace there."""
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
            raw = open(SH.run_pipeline(tree, tape, "replay"), "rb").read()
        except (RuntimeError, ISO.IsolationError) as exc:
            detail["replay_error"] = str(exc)[:500]
            return False
        finally:
            if os.path.exists(tape + ".replay.env.json"):
                detail["replay_env"] = json.load(open(tape + ".replay.env.json"))
            if keep_dir:
                os.makedirs(keep_dir, exist_ok=True)
                for suffix in (".replay.env.json", ".replay.report.json", ".replay.strace"):
                    if os.path.exists(tape + suffix):
                        if suffix == ".replay.strace":
                            with open(tape + suffix, "rb") as src, gzip.GzipFile(os.path.join(keep_dir, "replay.strace.gz"), "wb", mtime=0) as dst:
                                shutil.copyfileobj(src, dst)
                        else:
                            shutil.copy(tape + suffix, os.path.join(keep_dir, "replay" + suffix))
        if keep_dir:
            with open(os.path.join(keep_dir, "replay_board_raw.json"), "wb") as fh:
                fh.write(raw)
        out = json.loads(raw)
        detail["literal_board"] = literal_board_identity(unit_dir, raw)
        detail["canonical_equivalence_diagnostic_only"] = SH.replay_equivalent(board, out)
        rec = os.path.join(unit_dir, "shadow_env.json")
        problems = []
        if os.path.exists(rec):
            rec_fp = json.load(open(rec))
            problems = ISO.check_replay_compatible(rec_fp, detail.get("replay_env") or {})
            if problems:
                detail["environment_mismatch"] = problems
            conf = ISO.check_record_conformance(rec_fp)
            if conf:
                detail["record_nonconformance"] = conf
                problems = problems + conf
        else:
            detail["legacy_unit"] = "no sealed record-environment fingerprint (pre-A1)"
        return detail["literal_board"]["identical"] and not problems
    finally:
        SH.remove_tree(repo, tree)
        shutil.rmtree(work, ignore_errors=True)


def sealed_b_identity_problems(unit_dir, board, prov):
    """FC-MLB-001B sealed identities (no replay needed): payload bytes == frozen-spec payload of the sealed board and
    == the manifest-bound hash; tape locator is the content address of the manifest-bound tape sha256; the record
    ran in the pinned runtime with zero forbidden reads."""
    import payload as PL
    import runtime_image as RI
    import tape_store as TS
    problems = []
    pb = open(os.path.join(unit_dir, "shadow_payload.json"), "rb").read()
    if prov.get("scientific_payload_spec") != PL.SPEC:
        problems.append(f"payload spec {prov.get('scientific_payload_spec')} != {PL.SPEC}")
    if pb != PL.payload_bytes(board):
        problems.append("sealed payload bytes are not the frozen-spec payload of the sealed board")
    if hashlib.sha256(pb).hexdigest() != prov.get("scientific_payload_sha256"):
        problems.append("sealed payload sha256 != manifest scientific_payload_sha256")
    loc = prov.get("tape_store") or {}
    if loc.get("sha256") != prov.get("tape_sha256") or loc.get("key") != (TS.key_for(prov["tape_sha256"]) if prov.get("tape_sha256") else None) \
            or not isinstance(loc.get("bytes"), int):
        problems.append(f"tape locator is not the content address of the sealed tape: {loc}")
    env = json.load(open(os.path.join(unit_dir, "shadow_env.json")))
    if (env.get("runtime_image") or {}).get("manifest_digest") != RI.MANIFEST_DIGEST or \
            prov.get("runtime_image_digest") != RI.MANIFEST_DIGEST:
        problems.append("record did not run in the pinned runtime image")
    if (env.get("process_trace") or {}).get("forbidden_reads") != 0 or (env.get("setup_trace") or {}).get("forbidden_reads") != 0:
        problems.append("record trace has forbidden/unclassified reads")
    return problems


def temporary_store_problems(unit_dir, arts, prov):
    """A unit whose tape lives in a TEMPORARY Actions artifact must seal the pre-seal upload read-back proof, and the
    proof must name exactly the sealed tape identity."""
    import tape_store as TS
    loc = prov.get("tape_store") or {}
    if loc.get("store") != TS.GHA_KIND:
        return []
    if "tape_artifact.json" not in arts:
        return ["Actions-artifact tape sealed without its upload read-back proof (tape_artifact.json)"]
    proof = json.load(open(os.path.join(unit_dir, "tape_artifact.json")))
    keys = ("repository", "run_id", "artifact_name", "file_name", "sha256", "bytes", "key")
    bad = [k for k in keys if proof.get(k) != loc.get(k)]
    if bad or not proof.get("verified_readback") or proof.get("storage_contract") != TS.GHA_CONTRACT \
            or not proof.get("artifact_id") or not proof.get("expires_at"):
        return [f"upload proof does not prove the sealed tape identity (mismatch: {bad})"]
    return []


def tape_locator_for(root, unit, seal, prov):
    """Where to fetch the sealed tape from. The sealed locator, unless a VALID byte-preserving storage migration
    record (STORAGE_MIGRATIONS/<unit>.json) names a durable copy of the SAME identity. V3B_VERIFY_TAPE_SOURCE=sealed
    forces the original (e.g. to cross-check the artifact before it expires). Identity is checked again on fetch."""
    import tape_migration as MG
    if os.environ.get("V3B_VERIFY_TAPE_SOURCE") == "sealed":
        return None
    return MG.durable_locator(root, unit, seal, prov.get("tape_store") or {})


def replay_shadow_b(unit_dir, board, overlay_bytes, prov, detail=None, keep_dir=None, tape_locator=None, repo=None):
    """FC-MLB-001B replay: fetch the tape BY EXACT IDENTITY (size + sha256 verified before use; missing/mismatch
    fail closed), replay in a fresh pinned-runtime sandbox with no network, regenerate the scientific payload and
    require LITERAL byte + SHA-256 equality with the sealed payload; replay environment must be compatible with the
    sealed record environment. `tape_locator` may name a different transport for the SAME identity (drills)."""
    import payload as PL
    import sandbox as SB
    import tape_store as TS
    detail = detail if detail is not None else {}
    sealed_loc = prov.get("tape_store") or {}
    loc = dict(tape_locator or sealed_loc)
    for f in ("key", "sha256", "bytes"):
        if loc.get(f) != sealed_loc.get(f):
            detail["replay_error"] = f"transport locator {f} differs from the sealed tape identity"
            return False
    repo = repo or _repo_root()
    work = tempfile.mkdtemp(prefix="v3breplay_")
    tree = os.path.join(work, "tree")
    try:
        os.makedirs(os.path.join(work, "tapefetch"))
        try:
            tape = TS.fetch_verified(loc, os.path.join(work, "tapefetch"), repo=repo)
        except TS.StoreError as exc:
            detail["replay_error"] = f"{exc.code}: {exc}"
            return False
        detail["tape_fetched"] = {"store": loc.get("store"), "sha256": loc["sha256"], "bytes": loc["bytes"], "verified": True}
        SH.build_tree(repo, tree, board["date"], live_ref=SH.SHADOW_PIN)     # no live read at all
        SH.install_sealed_overlay(tree, board["date"], overlay_bytes)
        local = os.path.join(work, "tape.json.gz")
        shutil.move(tape, local)
        overlay_rel = SH.LIVE_OVERLAY_TEMPLATE.format(date=board["date"])
        try:
            raw = open(SH.run_pipeline_b(tree, local, "replay", overlay_rel=overlay_rel), "rb").read()
        except (RuntimeError, OSError) as exc:
            detail["replay_error"] = str(exc)[:800]
            return False
        finally:
            if os.path.exists(local + ".replay.env.json"):
                detail["replay_env"] = json.load(open(local + ".replay.env.json"))
            if keep_dir:
                os.makedirs(keep_dir, exist_ok=True)
                for suffix in (".replay.env.json", ".replay.report.json", ".replay.strace", ".replay.setup.strace"):
                    if os.path.exists(local + suffix):
                        if suffix.endswith("strace"):
                            with open(local + suffix, "rb") as src, gzip.GzipFile(os.path.join(keep_dir, "replay" + suffix + ".gz"), "wb", mtime=0) as dst:
                                shutil.copyfileobj(src, dst)
                        else:
                            shutil.copy(local + suffix, os.path.join(keep_dir, "replay" + suffix))
        if keep_dir:
            with open(os.path.join(keep_dir, "replay_board_raw.json"), "wb") as fh:
                fh.write(raw)
        sealed = open(os.path.join(unit_dir, "shadow_payload.json"), "rb").read()
        detail["payload"] = PL.compare(sealed, json.loads(raw))
        if keep_dir:
            with open(os.path.join(keep_dir, "replay_payload.json"), "wb") as fh:
                fh.write(PL.payload_bytes(json.loads(raw)))
        rec_env = json.load(open(os.path.join(unit_dir, "shadow_env.json")))
        problems = SB.check_compatible(rec_env, detail.get("replay_env") or {})
        if problems:
            detail["environment_mismatch"] = problems
        rep = detail.get("replay_env") or {}
        return bool(detail["payload"]["literal_identical"] and not problems and rep.get("replay_misses") == 0
                    and rep.get("unconsumed") == 0)
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
    is_b = "shadow_payload.json" in arts
    req = B_REQUIRED_ARTIFACTS if is_b else REQUIRED_ARTIFACTS
    if not set(req) <= set(arts) or set(arts) - set(req) - set(OPTIONAL_ARTIFACTS):
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
    if is_b:
        problems = sealed_b_identity_problems(d, board, prov)
        if problems:
            return "B_SEALED_IDENTITY_INVALID", problems, None
    elif prov.get("tape_sha256") != arts["shadow_tape.json.gz"]:
        return "TAPE_NOT_SEALED_ONE", None, None
    tape_locator = None
    if is_b:
        problems = temporary_store_problems(d, arts, prov)
        if problems:
            return "TEMP_STORE_PROOF_INVALID", problems, None
        try:
            tape_locator = tape_locator_for(root, unit, seal, prov)
        except Exception as exc:  # noqa: BLE001 -- an invalid migration record is never silently ignored
            return "STORAGE_MIGRATION_INVALID", str(exc), None
    rc = json.load(open(os.path.join(d, "receipts.json"))) if os.path.exists(os.path.join(d, "receipts.json")) else {}
    if not rc.get("github_comment_id"):
        return "MISSING_EXTERNAL_RECEIPT", "receipts.json has no GitHub comment id", None
    comment = fetch_issue_comment(rc["github_comment_id"])         # raises EvidenceUnverified
    status, detail = SL.verify_receipts_raw(seal, comment, rc.get("tsa"),
                                            earliest_first_pitch_utc=man["earliest_first_pitch_utc"])
    if status != "ON_TIME":
        return status, detail, None
    rdetail = {}
    if is_b:
        if not replay_shadow_b(d, board, overlay_bytes, prov, rdetail, tape_locator=tape_locator):
            return "SHADOW_PAYLOAD_NOT_REPRODUCIBLE", {k: v for k, v in rdetail.items() if k != "replay_env"}, None
        return "VERIFIED", detail, man
    if not replay_shadow(d, board, overlay_bytes, rdetail):
        return "SHADOW_NOT_REPRODUCIBLE", {k: v for k, v in rdetail.items() if k != "replay_env"}, None
    return "VERIFIED", detail, man
