"""Fail-closed rolling evidence chain for FC-NFL-004 research freezes.

Codex review 6129802534 (P1): a missing, expired or wrong-branch artifact
used to make the lifecycle replay ZERO prior records and silently start a new
"rolling" ledger, and the committed freezes were never seeded. This module
replaces that with an anchored, hash-linked, monotonic chain using the same
storage (GitHub Actions artifacts, read-only API access) and no new write
permission.

Anchor
  ``data/nfl_game_market_research/LEDGER_ANCHOR.json`` (committed) names the
  epoch and the sha256 of every committed baseline record (the 30 freezes of
  2026-10-12). The baseline is part of every chain state, byte-for-byte.

Chain state
  Each sealed run uploads ONE artifact named ``<prefix>-<epoch_id>`` holding
  ``ledger/`` and ``LEDGER_MANIFEST.json``: epoch, anchor hash, monotonic
  ``sequence``, ``previous_manifest_sha256``, and the sha256 of every file.

``load_chain`` (start of a run) fails closed with an explicit code:
  NO_CHAIN                no chain artifact for this epoch/branch and no
                          explicit bootstrap requested
  BOOTSTRAP_REFUSED       bootstrap requested but a chain (even expired) exists
  EXPIRED_CHAIN_HEAD      newest chain artifact has expired
  MISSING_EXPECTED_ARTIFACT  the latest successful run after the epoch began
                          has no (or an expired/newer-than-head) chain artifact
  DIGEST_MISMATCH / UNSAFE_ARTIFACT / MANIFEST_INVALID / TAMPERED /
  INCOMPLETE / UNLISTED_FILE / NON_MONOTONIC / CHAIN_FORK /
  DUPLICATE_OBSERVATION / ANCHOR_MISMATCH

A new epoch is possible ONLY by (1) committing a new anchor ``epoch_id``
(reviewed code change) and (2) a ``workflow_dispatch`` with
``bootstrap_epoch`` equal to it; both are auditable. Scheduled runs never
bootstrap.

``seal_chain`` (end of a run) re-verifies the working ledger, requires it to
be a byte-identical superset of the loaded state, and writes the next manifest.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from nfl.prospective.game_market_research_freeze import canonical_sha256, load_research_freeze
from nfl.prospective.game_market_research_lifecycle import _verify_file

ANCHOR_SCHEMA = "fc-nfl-004-ledger-anchor-v1"
MANIFEST_SCHEMA = "fc-nfl-004-ledger-manifest-v1"
ARTIFACT_PREFIX = "nfl-game-market-research-ledger"
MANIFEST_NAME = "LEDGER_MANIFEST.json"
WORKFLOW_FILE = "nfl-live-game-market-shadow.yml"
LINK_DEPTH = 3


class ChainError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact_name(epoch_id: str) -> str:
    return f"{ARTIFACT_PREFIX}-{epoch_id}"


def ledger_files(ledger: Path) -> dict[str, str]:
    """relpath -> sha256 for every evidence file, each verified on its own hash."""
    out = {}
    for path in sorted(Path(ledger).rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(ledger).as_posix()
        if not rel.endswith(".json"):
            raise ChainError("UNLISTED_FILE", f"non-evidence file in ledger: {rel}")
        try:
            _verify_file(path)
        except Exception as exc:  # noqa: BLE001 - any self-hash/parse failure is tampering
            raise ChainError("TAMPERED", f"{rel}: {exc}") from exc
        out[rel] = _sha_file(path)
    return out


# ---------------------------------------------------------------- anchor
def build_anchor(baseline: Path, *, epoch_id: str, reason: str, created_at: str,
                 baseline_path: str) -> dict[str, Any]:
    files = ledger_files(baseline)
    body = {"schema": ANCHOR_SCHEMA, "epoch_id": epoch_id, "created_at": created_at,
            "reason": reason, "baseline_path": baseline_path, "file_count": len(files),
            "files": files}
    return {**body, "anchor_sha256": canonical_sha256(body)}


def load_anchor(anchor_path: Path, repo_root: Path) -> dict[str, Any]:
    anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    body = {k: v for k, v in anchor.items() if k != "anchor_sha256"}
    if anchor.get("schema") != ANCHOR_SCHEMA or canonical_sha256(body) != anchor.get("anchor_sha256"):
        raise ChainError("ANCHOR_MISMATCH", "anchor schema or self-hash invalid")
    base = Path(repo_root) / anchor["baseline_path"]
    for rel, sha in anchor["files"].items():
        p = base / rel
        if not p.exists() or _sha_file(p) != sha:
            raise ChainError("ANCHOR_MISMATCH", f"committed baseline file changed or missing: {rel}")
    return anchor


# -------------------------------------------------------------- manifest
def build_manifest(*, ledger: Path, anchor: Mapping[str, Any], sequence: int,
                   previous_manifest_sha256: str, run: Mapping[str, Any]) -> dict[str, Any]:
    files = ledger_files(ledger)
    body = {"schema": MANIFEST_SCHEMA, "epoch_id": anchor["epoch_id"],
            "anchor_sha256": anchor["anchor_sha256"], "sequence": sequence,
            "previous_manifest_sha256": previous_manifest_sha256, "run": dict(run),
            "sealed_at": _now(), "file_count": len(files), "files": files}
    return {**body, "manifest_sha256": canonical_sha256(body)}


def verify_chain_dir(chain_dir: Path, anchor: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one extracted chain artifact; return its manifest."""
    mpath = Path(chain_dir) / MANIFEST_NAME
    if not mpath.exists():
        raise ChainError("MANIFEST_INVALID", "artifact has no ledger manifest")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if manifest.get("schema") != MANIFEST_SCHEMA or canonical_sha256(body) != manifest.get("manifest_sha256"):
        raise ChainError("MANIFEST_INVALID", "manifest schema or self-hash invalid")
    if manifest["epoch_id"] != anchor["epoch_id"] or manifest["anchor_sha256"] != anchor["anchor_sha256"]:
        raise ChainError("MANIFEST_INVALID", "manifest belongs to a different epoch/anchor")
    ledger = Path(chain_dir) / "ledger"
    actual = ledger_files(ledger) if ledger.exists() else {}
    for rel, sha in manifest["files"].items():
        if rel not in actual:
            raise ChainError("INCOMPLETE", f"manifest file missing from artifact: {rel}")
        if actual[rel] != sha:
            raise ChainError("TAMPERED", f"file bytes differ from manifest: {rel}")
    extra = sorted(set(actual) - set(manifest["files"]))
    if extra:
        raise ChainError("UNLISTED_FILE", f"files not covered by manifest: {extra[:3]}")
    for rel, sha in anchor["files"].items():
        if manifest["files"].get(rel) != sha:
            raise ChainError("NON_MONOTONIC", f"anchored baseline record absent or changed: {rel}")
    return manifest


def check_duplicate_observations(ledger: Path) -> None:
    seen: dict[str, str] = {}
    for path in sorted(Path(ledger).rglob("*.json")):
        if path.parent.name == "grades":
            continue
        rec = load_research_freeze(path)
        key = rec["market_snapshot_sha256"]
        if key in seen:
            raise ChainError("DUPLICATE_OBSERVATION",
                             f"snapshot {key[:12]} frozen twice: {seen[key]} and {path.name}")
        seen[key] = path.name


# ------------------------------------------------------------- GitHub I/O
class GitHubArtifacts:
    """Read-only Actions API access (artifacts, runs)."""

    def __init__(self, *, repo: str, token: str, session):
        self.repo, self.session = repo, session
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def _get(self, url, **params):
        resp = self.session.get(url, params=params or None, headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp

    def chain_artifacts(self, name: str) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._get(f"https://api.github.com/repos/{self.repo}/actions/artifacts",
                             name=name, per_page=100, page=page).json()
            batch = [a for a in data.get("artifacts", []) if a.get("name") == name]
            out += batch
            if len(data.get("artifacts", [])) < 100:
                return out
            page += 1

    def successful_runs(self, branch: str) -> list[dict]:
        data = self._get(f"https://api.github.com/repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs",
                         branch=branch, status="success", per_page=20).json()
        return data.get("workflow_runs", [])

    def run_artifacts(self, run_id) -> list[dict]:
        return self._get(f"https://api.github.com/repos/{self.repo}/actions/runs/{run_id}/artifacts",
                         per_page=100).json().get("artifacts", [])

    def download(self, artifact: Mapping[str, Any], dest: Path) -> Path:
        digest = str(artifact.get("digest") or "")
        if not digest.startswith("sha256:"):
            raise ChainError("DIGEST_MISMATCH", f"artifact {artifact['id']} has no sha256 digest")
        blob = self._get(artifact["archive_download_url"]).content
        if hashlib.sha256(blob).hexdigest() != digest.split(":", 1)[1]:
            raise ChainError("DIGEST_MISMATCH", f"artifact {artifact['id']} bytes differ from digest")
        out = Path(dest) / f"artifact_{artifact['id']}"
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for info in zf.infolist():
                n = PurePosixPath(info.filename)
                if n.is_absolute() or ".." in n.parts:
                    raise ChainError("UNSAFE_ARTIFACT", f"unsafe path {info.filename}")
            zf.extractall(out)
        return out


# ------------------------------------------------------------------ load
def _seed_baseline(anchor, repo_root: Path, ledger: Path) -> None:
    base = Path(repo_root) / anchor["baseline_path"]
    for rel in anchor["files"]:
        target = Path(ledger) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / rel, target)


def load_chain(*, api: GitHubArtifacts, anchor: Mapping[str, Any], repo_root: Path, branch: str,
               current_run_id: str, bootstrap_epoch: str, work: Path) -> dict[str, Any]:
    """Prepare ``work/ledger`` from the verified chain head (or an explicit bootstrap)."""
    name = artifact_name(anchor["epoch_id"])
    ledger = Path(work) / "ledger"
    if ledger.exists() and any(ledger.rglob("*")):
        raise ChainError("NON_MONOTONIC", "working ledger is not empty")
    ledger.mkdir(parents=True, exist_ok=True)
    arts = [a for a in api.chain_artifacts(name)
            if (a.get("workflow_run") or {}).get("head_branch") == branch
            and str((a.get("workflow_run") or {}).get("id")) != str(current_run_id)]
    arts.sort(key=lambda a: (a.get("created_at") or "", a.get("id") or 0), reverse=True)
    bootstrap_epoch = (bootstrap_epoch or "").strip()

    if not arts:
        if bootstrap_epoch != anchor["epoch_id"]:
            raise ChainError("NO_CHAIN", (
                f"no '{name}' artifact for branch {branch!r}. Refusing to start a new chain "
                f"silently. To start epoch {anchor['epoch_id']} explicitly, dispatch the workflow "
                f"with bootstrap_epoch={anchor['epoch_id']}."))
        _seed_baseline(anchor, repo_root, ledger)
        return {"mode": "BOOTSTRAP", "epoch_id": anchor["epoch_id"], "sequence": 0,
                "previous_manifest_sha256": anchor["anchor_sha256"], "head_artifact_id": None,
                "files": ledger_files(ledger)}

    if bootstrap_epoch:
        raise ChainError("BOOTSTRAP_REFUSED", (
            f"{len(arts)} chain artifact(s) already exist for epoch {anchor['epoch_id']} on "
            f"{branch!r}; a new epoch requires a new committed anchor epoch_id"))
    head = arts[0]
    if head.get("expired"):
        raise ChainError("EXPIRED_CHAIN_HEAD", (
            f"newest chain artifact {head['id']} ({head.get('created_at')}) has expired; the "
            f"chain cannot be continued without losing evidence"))

    # The latest successful run after the epoch began must have produced the head (or
    # an older link): a deleted/expired artifact from a successful run is missing evidence.
    epoch_start = min(a.get("created_at") or "" for a in arts)
    for r in api.successful_runs(branch):
        if str(r.get("id")) == str(current_run_id) or (r.get("created_at") or "") < epoch_start:
            continue
        mine = [a for a in api.run_artifacts(r["id"]) if a.get("name") == name]
        if not mine or any(a.get("expired") for a in mine):
            raise ChainError("MISSING_EXPECTED_ARTIFACT", (
                f"successful run {r['id']} ({r.get('created_at')}) has no live '{name}' artifact"))
        if max(a.get("created_at") or "" for a in mine) > (head.get("created_at") or ""):
            raise ChainError("MISSING_EXPECTED_ARTIFACT", (
                f"run {r['id']} produced a chain artifact newer than the visible head"))
        break

    head_dir = api.download(head, Path(work) / "prior")
    manifest = verify_chain_dir(head_dir, anchor)

    # Linear, monotonic links over the newest live artifacts.
    newer = manifest
    for older_art in [a for a in arts[1:LINK_DEPTH] if not a.get("expired")]:
        older = verify_chain_dir(api.download(older_art, Path(work) / "prior"), anchor)
        if older["sequence"] >= newer["sequence"]:
            raise ChainError("CHAIN_FORK", f"sequence not increasing: {older['sequence']} -> {newer['sequence']}")
        if older["sequence"] == newer["sequence"] - 1 and newer["previous_manifest_sha256"] != older["manifest_sha256"]:
            raise ChainError("CHAIN_FORK", f"link {newer['sequence']} does not point at {older['sequence']}")
        for rel, sha in older["files"].items():
            if newer["files"].get(rel) != sha:
                raise ChainError("NON_MONOTONIC", f"evidence dropped or changed after seq {older['sequence']}: {rel}")
        newer = older

    shutil.copytree(head_dir / "ledger", ledger, dirs_exist_ok=True)
    check_duplicate_observations(ledger)
    return {"mode": "CONTINUE", "epoch_id": anchor["epoch_id"], "sequence": manifest["sequence"],
            "previous_manifest_sha256": manifest["manifest_sha256"], "head_artifact_id": head["id"],
            "files": manifest["files"]}


# ------------------------------------------------------------------ seal
def seal_chain(*, state: Mapping[str, Any], anchor: Mapping[str, Any], work: Path,
               run: Mapping[str, Any]) -> dict[str, Any]:
    ledger = Path(work) / "ledger"
    current = ledger_files(ledger)
    for rel, sha in state["files"].items():
        if current.get(rel) != sha:
            raise ChainError("NON_MONOTONIC", f"loaded evidence dropped or changed during run: {rel}")
    check_duplicate_observations(ledger)
    manifest = build_manifest(ledger=ledger, anchor=anchor, sequence=int(state["sequence"]) + 1,
                              previous_manifest_sha256=state["previous_manifest_sha256"], run=run)
    (Path(work) / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    verify_chain_dir(work, anchor)
    return manifest
