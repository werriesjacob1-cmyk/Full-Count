#!/usr/bin/env python3
"""GitHub Actions artifact access for the TEMPORARY authoritative V3 tape store (tape_store.GHA_KIND).

Runs inside a GitHub Actions job (GITHUB_TOKEN with `actions: read`). Every path fails closed:
  * the artifact is identified EXACTLY: (repository, run id, artifact name bound to the sha256), then by artifact id;
  * an expired artifact -> TAPE_EXPIRED; absent or ambiguous -> TAPE_MISSING;
  * the downloaded zip must hash to the digest GitHub recorded for it, and contain exactly the one tape file;
  * the tape inside must have exactly the sealed size and sha256 (tape_store._verify) -- nothing else is trusted.
`confirm_upload` is the read-back proof written as tape_artifact.json BEFORE a prospective seal is published; it also
records the artifact's creation and expiry times, so retention can be monitored (retention_monitor.py).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tape_store as TS  # noqa: E402

API = "https://api.github.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise TS.StoreAuthError(f"GitHub API {exc.code} for {url}") from exc
        if exc.code in (404, 410):
            raise TS.TapeMissing(f"GitHub API {exc.code} for {url}") from exc
        raise TS.StoreUnavailable(f"GitHub API {exc.code} for {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TS.StoreUnavailable(f"GitHub API unreachable: {exc}") from exc


def http_download(url, token, dest):
    """Artifact zip: the API answers 302 to blob storage; the token is NEVER forwarded to that host."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        try:
            r = urllib.request.build_opener(_NoRedirect).open(req, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                r = urllib.request.urlopen(exc.headers["Location"], timeout=600)
            elif exc.code == 410:
                raise TS.TapeExpired(f"artifact gone (410): {url}") from exc
            elif exc.code in (401, 403):
                raise TS.StoreAuthError(f"artifact download {exc.code}") from exc
            elif exc.code == 404:
                raise TS.TapeMissing(f"artifact download 404: {url}") from exc
            else:
                raise TS.StoreUnavailable(f"artifact download {exc.code}") from exc
        with r, open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
    except (urllib.error.URLError, OSError) as exc:
        if isinstance(exc, TS.StoreError):
            raise
        raise TS.StoreUnavailable(f"artifact download failed: {exc}") from exc
    return dest


def find_artifact(loc, token, get_json=http_json):
    """The exact artifact for a sealed locator: same repository, same run, the sha256-bound name; exactly one."""
    url = f"{API}/repos/{loc['repository']}/actions/runs/{loc['run_id']}/artifacts?name={loc['artifact_name']}&per_page=100"
    arts = [a for a in get_json(url, token).get("artifacts") or [] if a.get("name") == loc["artifact_name"]]
    if loc.get("artifact_id") is not None:
        arts = [a for a in arts if a.get("id") == loc["artifact_id"]]
    if len(arts) != 1:
        raise TS.TapeMissing(f"{loc['artifact_name']} in run {loc['run_id']}: found {len(arts)} matching artifacts")
    a = arts[0]
    if a.get("expired"):
        raise TS.TapeExpired(f"{loc['artifact_name']} (id {a.get('id')}) expired at {a.get('expires_at')}")
    return a


def fetch(loc, token, dest_dir, get_json=http_json, download=http_download):
    """Download the exact artifact and return the path of the verified tape (size + sha256 checked)."""
    a = find_artifact(loc, token, get_json)
    work = tempfile.mkdtemp(prefix="v3gha_")
    try:
        z = download(f"{API}/repos/{loc['repository']}/actions/artifacts/{a['id']}/zip", token, os.path.join(work, "a.zip"))
        if a.get("digest") and "sha256:" + TS.file_identity(z)[0] != a["digest"]:
            raise TS.TapeHashMismatch(f"artifact zip does not hash to GitHub's recorded digest {a['digest']}")
        with zipfile.ZipFile(z) as zf:
            names = [i.filename for i in zf.infolist() if not i.is_dir()]
            if names != [loc["file_name"]]:
                raise TS.TapeHashMismatch(f"artifact must contain exactly {loc['file_name']}; has {names}")
            os.makedirs(dest_dir, exist_ok=True)
            out = os.path.join(dest_dir, loc["file_name"])
            with zf.open(loc["file_name"]) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    except zipfile.BadZipFile as exc:
        raise TS.TapeHashMismatch(f"artifact zip unreadable: {exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    TS._verify(out, loc["sha256"], int(loc["bytes"]))
    return out, a


def confirm_upload(loc, token, out_path, get_json=http_json, download=http_download, now=None):
    """Read-back proof of the uploaded tape BEFORE sealing: downloads it by exact identity and re-hashes it."""
    if loc.get("store") != TS.GHA_KIND:
        raise TS.StoreError("not an Actions-artifact locator")
    d = tempfile.mkdtemp(prefix="v3ghaconfirm_")
    try:
        _, a = fetch(loc, token, d, get_json, download)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    rec = {"storage_contract": TS.GHA_CONTRACT, "repository": loc["repository"], "run_id": loc["run_id"],
           "run_attempt": loc["run_attempt"], "artifact_id": a["id"], "artifact_name": a["name"],
           "file_name": loc["file_name"], "zip_digest": a.get("digest"), "zip_bytes": a.get("size_in_bytes"),
           "created_at": a.get("created_at"), "expires_at": a.get("expires_at"),
           "retention_days_configured": loc["retention_days"], "sha256": loc["sha256"], "bytes": loc["bytes"],
           "key": loc["key"], "verified_readback": True,
           "confirmed_at": (now or datetime.now(timezone.utc)).isoformat()}
    with open(out_path, "w") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    return rec


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Actions-artifact tape: confirm an upload, or fetch for verification")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("confirm")
    c.add_argument("--locator", required=True, help="JSON file: the sealed tape_store locator (from the manifest)")
    c.add_argument("--out", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--locator", required=True)
    f.add_argument("--dest", required=True)
    a = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise TS.StoreAuthError("GITHUB_TOKEN required")
    loc = json.load(open(a.locator))
    if a.cmd == "confirm":
        print(json.dumps(confirm_upload(loc, token, a.out), indent=1, sort_keys=True))
    else:
        path, art = fetch(loc, token, a.dest)
        print(json.dumps({"tape": path, "artifact_id": art["id"], "expires_at": art.get("expires_at")}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TS.StoreError as exc:
        print(f"FAIL CLOSED {exc.code}: {exc}", file=sys.stderr)
        sys.exit(3)
