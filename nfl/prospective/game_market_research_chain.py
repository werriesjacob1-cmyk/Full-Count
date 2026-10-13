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
  BOOTSTRAP_REFUSED       bootstrap requested but the epoch has ANY sealed history
                          (artifacts or run history), or a non-anchor epoch
  EXPIRED_CHAIN_HEAD      newest chain artifact has expired
  MISSING_EXPECTED_ARTIFACT  run history shows a sealed upload whose link is
                          no longer listed (incl. ALL artifacts deleted)
  SEQUENCE_GAP            head sequence != number of sealed uploads in history
  HISTORY_UNVERIFIABLE    run/step history cannot be read (never permits restart)
  DIGEST_MISMATCH / UNSAFE_ARTIFACT / MANIFEST_INVALID / TAMPERED /
  INCOMPLETE / UNLISTED_FILE / NON_MONOTONIC / CHAIN_FORK /
  DUPLICATE_OBSERVATION / ANCHOR_MISMATCH

A new epoch is possible ONLY by (1) committing a new anchor ``epoch_id`` and
the matching artifact/step names in the workflow (reviewed code change) and
(2) a ``workflow_dispatch`` with ``bootstrap_epoch`` equal to it; both are
auditable. An existing epoch is NEVER restarted. Scheduled runs never
bootstrap. Artifact/run retention is finite: this guarantees fail-closed
behaviour, not permanent durability.

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
# Sealed-link packaging (2026-10-13). Links 1-7 uploaded the whole working dir,
# including ``prior/`` (every downloaded earlier link, each with its own
# ``prior/``), so artifacts nested recursively and doubled every run. A sealed
# link is now ONLY the manifest plus ``ledger/``, built in a clean ``package/``
# dir, and the manifest declares that layout. Legacy links (no declaration) stay
# loadable byte-for-byte; a link that declares the layout must satisfy it, and a
# later link may never drop the declaration.
PACKAGE_DIR = "package"
ARTIFACT_LAYOUT = "MANIFEST_AND_LEDGER_ONLY_V1"
WORKFLOW_FILE = "nfl-live-game-market-shadow.yml"
LINK_DEPTH = 3
UPLOAD_STEP_PREFIX = "Upload sealed evidence-chain link"
# The two 2026-10-12 PR-branch drill uploads (runs 38310850808, 38311050193)
# predate epoch-tagged step names; they are honoured for epoch-1 only.
LEGACY_UPLOAD_STEP_EPOCH = "fc-nfl-004-epoch-1"


def upload_step_name(epoch_id: str) -> str:
    """Workflow step whose success in ANY run proves a sealed link was uploaded."""
    return f"{UPLOAD_STEP_PREFIX} [{epoch_id}]"


def _upload_markers(epoch_id: str) -> set[str]:
    names = {upload_step_name(epoch_id)}
    if epoch_id == LEGACY_UPLOAD_STEP_EPOCH:
        names.add(UPLOAD_STEP_PREFIX)
    return names


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
            "sealed_at": _now(), "file_count": len(files), "files": files,
            "artifact_layout": ARTIFACT_LAYOUT}
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
    if manifest.get("artifact_layout") is not None:
        _check_layout(Path(chain_dir), manifest)
    return manifest


def _check_layout(chain_dir: Path, manifest: Mapping[str, Any]) -> None:
    """A link that declares the packaged layout holds exactly the manifest and
    ``ledger/`` -- no ``prior/`` copies, no stray files, no symlinks."""
    if manifest.get("artifact_layout") != ARTIFACT_LAYOUT:
        raise ChainError("ARTIFACT_LAYOUT", f"unknown artifact layout {manifest.get('artifact_layout')!r}")
    top = sorted(p.name for p in chain_dir.iterdir())
    if top != sorted([MANIFEST_NAME, "ledger"]):
        raise ChainError("ARTIFACT_LAYOUT", f"sealed link must contain only {MANIFEST_NAME} and ledger/, got {top}")
    for p in (chain_dir / "ledger").rglob("*"):
        if p.is_symlink() or not (p.is_dir() or p.is_file()):
            raise ChainError("ARTIFACT_LAYOUT", f"unexpected entry in sealed ledger: {p}")
        if p.is_dir() and p.name == "prior":
            raise ChainError("ARTIFACT_LAYOUT", f"nested prior-chain copy in sealed ledger: {p}")


def verify_sealed_package(package: Path, anchor: Mapping[str, Any]) -> dict[str, Any]:
    """Strict check for a link this code seals: verified AND in the packaged layout."""
    manifest = verify_chain_dir(package, anchor)
    if manifest.get("artifact_layout") != ARTIFACT_LAYOUT:
        raise ChainError("ARTIFACT_LAYOUT", "newly sealed link must declare the packaged layout")
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

    def sealed_runs(self, branch: str, epoch_id: str, since: str) -> list[dict]:
        """Runs (any conclusion) on ``branch`` since ``since`` whose epoch upload step
        succeeded. Run/step history survives artifact deletion and expiry."""
        markers = _upload_markers(epoch_id)
        out, page = [], 1
        try:
            while True:
                data = self._get(
                    f"https://api.github.com/repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs",
                    branch=branch, per_page=100, page=page, created=f">={since[:10]}").json()
                runs = data.get("workflow_runs", [])
                for r in runs:
                    if (r.get("created_at") or "") < since or r.get("status") != "completed":
                        continue
                    jobs = self._get(f"https://api.github.com/repos/{self.repo}/actions/runs/{r['id']}/jobs",
                                     per_page=100).json().get("jobs", [])
                    if any(st.get("name") in markers and st.get("conclusion") == "success"
                           for j in jobs for st in j.get("steps", [])):
                        out.append({"id": r["id"], "created_at": r.get("created_at"),
                                    "conclusion": r.get("conclusion")})
                if len(runs) < 100:
                    return out
                page += 1
        except ChainError:
            raise
        except Exception as exc:  # noqa: BLE001 - unknown history must never permit a restart
            raise ChainError("HISTORY_UNVERIFIABLE", f"workflow run history unreadable: {exc}") from exc

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
    """Prepare ``work/ledger`` from the verified chain head (or an explicit bootstrap).

    Run history is consulted FIRST (Codex P1, review 6133401456): a successful
    epoch upload step in any past run on this branch proves sealed evidence
    existed, even if every artifact has since been deleted or has expired.
    """
    epoch = anchor["epoch_id"]
    name = artifact_name(epoch)
    ledger = Path(work) / "ledger"
    if ledger.exists() and any(ledger.rglob("*")):
        raise ChainError("NON_MONOTONIC", "working ledger is not empty")
    ledger.mkdir(parents=True, exist_ok=True)
    history = [r for r in api.sealed_runs(branch, epoch, anchor["created_at"])
               if str(r["id"]) != str(current_run_id)]
    arts = [a for a in api.chain_artifacts(name)
            if (a.get("workflow_run") or {}).get("head_branch") == branch
            and str((a.get("workflow_run") or {}).get("id")) != str(current_run_id)]
    arts.sort(key=lambda a: (a.get("created_at") or "", a.get("id") or 0), reverse=True)
    bootstrap_epoch = (bootstrap_epoch or "").strip()

    if bootstrap_epoch:
        if bootstrap_epoch != epoch:
            raise ChainError("BOOTSTRAP_REFUSED", (
                f"bootstrap_epoch {bootstrap_epoch!r} is not the committed anchor epoch {epoch!r}"))
        if arts or history:
            raise ChainError("BOOTSTRAP_REFUSED", (
                f"epoch {epoch} already has sealed evidence on {branch!r} "
                f"({len(history)} sealed run(s), {len(arts)} artifact(s) listed). An epoch is "
                f"never restarted, even if its artifacts are gone: commit a NEW anchor epoch_id "
                f"(LIFECYCLE_DESIGN.md, new-epoch recovery) and bootstrap that."))
        _seed_baseline(anchor, repo_root, ledger)
        return {"mode": "BOOTSTRAP", "epoch_id": epoch, "sequence": 0,
                "previous_manifest_sha256": anchor["anchor_sha256"], "head_artifact_id": None,
                "files": ledger_files(ledger)}

    if not arts:
        if history:
            raise ChainError("MISSING_EXPECTED_ARTIFACT", (
                f"{len(history)} sealed run(s) of epoch {epoch} on {branch!r} exist but no chain "
                f"artifact remains (deleted or purged). Evidence was lost; continuing or "
                f"re-bootstrapping this epoch is refused."))
        raise ChainError("NO_CHAIN", (
            f"no '{name}' artifact for branch {branch!r}. Refusing to start a new chain "
            f"silently. To start epoch {epoch} explicitly, dispatch the workflow "
            f"with bootstrap_epoch={epoch}."))
    head = arts[0]
    if head.get("expired"):
        raise ChainError("EXPIRED_CHAIN_HEAD", (
            f"newest chain artifact {head['id']} ({head.get('created_at')}) has expired; the "
            f"chain cannot be continued without losing evidence"))
    listed_runs = {str((a.get("workflow_run") or {}).get("id")) for a in arts}
    for r in history:  # every sealed upload must still be listed (expired allowed below the head)
        if str(r["id"]) not in listed_runs:
            raise ChainError("MISSING_EXPECTED_ARTIFACT", (
                f"sealed run {r['id']} ({r.get('created_at')}, {r.get('conclusion')}) uploaded a "
                f"chain link that is no longer listed (deleted)"))
    newest_sealed = max((r.get("created_at") or "" for r in history), default="")
    if newest_sealed and str((head.get("workflow_run") or {}).get("id")) not in {
            str(r["id"]) for r in history if (r.get("created_at") or "") == newest_sealed}:
        raise ChainError("MISSING_EXPECTED_ARTIFACT", (
            "the newest sealed run did not produce the visible chain head"))

    head_dir = api.download(head, Path(work) / "prior")
    manifest = verify_chain_dir(head_dir, anchor)
    if manifest["sequence"] != len(history):
        raise ChainError("SEQUENCE_GAP", (
            f"head sequence {manifest['sequence']} != {len(history)} sealed upload(s) in run "
            f"history: links or runs are missing"))

    # Linear, monotonic links over the newest live artifacts.
    newer = manifest
    for older_art in [a for a in arts[1:LINK_DEPTH] if not a.get("expired")]:
        older = verify_chain_dir(api.download(older_art, Path(work) / "prior"), anchor)
        if older["sequence"] >= newer["sequence"]:
            raise ChainError("CHAIN_FORK", f"sequence not increasing: {older['sequence']} -> {newer['sequence']}")
        if older["sequence"] == newer["sequence"] - 1 and newer["previous_manifest_sha256"] != older["manifest_sha256"]:
            raise ChainError("CHAIN_FORK", f"link {newer['sequence']} does not point at {older['sequence']}")
        if older.get("artifact_layout") and not newer.get("artifact_layout"):
            raise ChainError("ARTIFACT_LAYOUT", (
                f"link {newer['sequence']} dropped the packaged layout declared by link {older['sequence']}"))
        for rel, sha in older["files"].items():
            if newer["files"].get(rel) != sha:
                raise ChainError("NON_MONOTONIC", f"evidence dropped or changed after seq {older['sequence']}: {rel}")
        newer = older

    shutil.copytree(head_dir / "ledger", ledger, dirs_exist_ok=True)
    check_duplicate_observations(ledger)
    return {"mode": "CONTINUE", "epoch_id": epoch, "sequence": manifest["sequence"],
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
    package = Path(work) / PACKAGE_DIR
    if package.exists():
        raise ChainError("ARTIFACT_LAYOUT", f"package dir already exists: {package}")
    package.mkdir()
    shutil.copytree(ledger, package / "ledger")
    (package / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    verify_sealed_package(package, anchor)
    return manifest
