#!/usr/bin/env python3
"""FC-MLB-001B drill transport: fetch environment A's artifacts in environment B ONLY after GitHub's artifact and
workflow-run metadata agree exactly with the committed b_drill/B_DRILL_RECORD.json (Codex pre-drill audit,
MUST FIX 1 + 2). Every mismatch fails closed; nothing is inferred.

Digest representation (frozen): bare 64-character lowercase hex SHA-256 of the artifact zip. GitHub's API reports
`sha256:<hex>` and actions/upload-artifact outputs bare hex; both are normalized by `normalize_digest` before any
comparison, and the committed record stores bare hex.

Binding, for BOTH the files artifact and the tape artifact:
  * GET /repos/{repo}/actions/artifacts/{id}: id == committed id; name == committed name; not expired;
    workflow_run.id == record_run_id; workflow_run.head_sha == record_commit; digest == committed zip digest;
  * GET /repos/{repo}/actions/runs/{record_run_id}: id == record_run_id; head_sha == record_commit;
    repository.full_name == committed repository == this repository;
  * download by that exact id; the zip's own sha256 == committed zip digest.
The drill files' SHA256SUMS and the tape's byte size + sha256 are still checked afterwards by b_drill.py verify.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactBindingError(Exception):
    pass


def normalize_digest(d):
    """'sha256:<hex>' or '<hex>' (any case) -> bare lowercase hex; anything else -> ArtifactBindingError."""
    s = (d or "").strip().lower()
    if s.startswith("sha256:"):
        s = s[len("sha256:"):]
    if not HEX64.match(s):
        raise ArtifactBindingError(f"not a sha256 digest: {d!r}")
    return s


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raise ArtifactBindingError(f"GitHub API {exc.code} for {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ArtifactBindingError(f"GitHub API unreachable for {url}: {exc}") from exc


def http_download(url, token, dest):
    """The API answers 302 to blob storage; the token is never forwarded to that host."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        try:
            r = urllib.request.build_opener(_NoRedirect).open(req, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                r = urllib.request.urlopen(exc.headers["Location"], timeout=900)
            else:
                raise ArtifactBindingError(f"artifact download {exc.code} for {url}") from exc
        with r, open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
    except (urllib.error.URLError, OSError) as exc:
        raise ArtifactBindingError(f"artifact download failed for {url}: {exc}") from exc
    return dest


def expected(rec, kind):
    """(id, name, committed zip digest) for kind 'files' | 'tape' from the committed record."""
    if kind == "files":
        a = rec["files_artifact"]
        return int(a["id"]), a["name"], normalize_digest(a["zip_sha256"])
    if kind == "tape":
        a = rec["tape"]
        return int(a["artifact_id"]), a["artifact_name"], normalize_digest(a["zip_sha256"])
    raise ArtifactBindingError(f"unknown artifact kind {kind!r}")


def binding_problems(rec, kind, art, run, repository):
    """Exact agreement of GitHub's artifact + run metadata with the committed record. [] = bound."""
    aid, name, zdig = expected(rec, kind)
    p = []
    want_repo = rec.get("repository")
    if not want_repo or want_repo != repository:
        p.append(f"repository: committed {want_repo!r} != this repository {repository!r}")
    if art.get("id") != aid:
        p.append(f"artifact id {art.get('id')!r} != committed {aid}")
    if art.get("name") != name:
        p.append(f"artifact name {art.get('name')!r} != committed {name!r}")
    if art.get("expired") is not False:
        p.append(f"artifact expired/unavailable (expired={art.get('expired')!r}, expires_at={art.get('expires_at')!r})")
    wr = art.get("workflow_run") or {}
    if wr.get("id") != int(rec["record_run_id"]):
        p.append(f"artifact workflow_run.id {wr.get('id')!r} != record_run_id {rec['record_run_id']!r}")
    if wr.get("head_sha") != rec["record_commit"]:
        p.append(f"artifact workflow_run.head_sha {wr.get('head_sha')!r} != record_commit {rec['record_commit']!r}")
    try:
        if normalize_digest(art.get("digest")) != zdig:
            p.append(f"artifact metadata digest {art.get('digest')!r} != committed zip digest {zdig}")
    except ArtifactBindingError as exc:
        p.append(f"artifact metadata digest unusable: {exc}")
    if run.get("id") != int(rec["record_run_id"]):
        p.append(f"run id {run.get('id')!r} != record_run_id {rec['record_run_id']!r}")
    if run.get("head_sha") != rec["record_commit"]:
        p.append(f"run head_sha {run.get('head_sha')!r} != record_commit {rec['record_commit']!r}")
    if (run.get("repository") or {}).get("full_name") != repository:
        p.append(f"run repository {(run.get('repository') or {}).get('full_name')!r} != {repository!r}")
    return p


def fetch_bound(rec, kind, token, repository, out_zip, get_json=http_json, download=http_download):
    """Metadata binding first, then download by exact id, then the zip's own digest. Returns the binding summary."""
    aid, name, zdig = expected(rec, kind)
    art = get_json(f"{API}/repos/{repository}/actions/artifacts/{aid}", token)
    run = get_json(f"{API}/repos/{repository}/actions/runs/{int(rec['record_run_id'])}", token)
    problems = binding_problems(rec, kind, art, run, repository)
    if problems:
        raise ArtifactBindingError(f"{kind} artifact metadata does not bind to the committed record: {problems}")
    download(f"{API}/repos/{repository}/actions/artifacts/{aid}/zip", token, out_zip)
    got = file_sha256(out_zip)
    if got != zdig:
        raise ArtifactBindingError(f"{kind} artifact zip digest {got} != committed {zdig}")
    return {"kind": kind, "artifact_id": aid, "artifact_name": name, "zip_sha256": got,
            "record_run_id": run["id"], "record_commit": run["head_sha"], "repository": repository,
            "expires_at": art.get("expires_at")}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="FC-MLB-001B: fetch a drill artifact only after exact metadata binding")
    ap.add_argument("--record", required=True)
    ap.add_argument("--kind", choices=("files", "tape"), required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        raise ArtifactBindingError("GH_TOKEN/GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    print(json.dumps(fetch_bound(json.load(open(a.record)), a.kind, token, repository, a.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ArtifactBindingError, KeyError, ValueError) as exc:
        print(f"::error title=B ARTIFACT BINDING::FAIL CLOSED: {exc}", file=sys.stderr)
        sys.exit(3)
