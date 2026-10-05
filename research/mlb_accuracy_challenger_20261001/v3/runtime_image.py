#!/usr/bin/env python3
"""FC-MLB-001B: the pinned runtime image (criteria TASKS/FC-MLB-001B.md §4).

Record and replay of the frozen shadow pipeline run inside ONE explicitly pinned Linux runtime: the Docker
Official Image `python:3.11-bookworm`, linux/amd64, identified by its immutable OCI manifest digest. Nothing
is trusted by name or tag:

  * the manifest is fetched BY DIGEST and its bytes must hash to MANIFEST_DIGEST;
  * the config and every layer blob must hash to the digests the manifest lists;
  * layers are applied in order with OCI whiteout semantics into a fresh directory (read-only at use);
  * the extracted runtime gets a deterministic identity (`rootfs_tree_sha256`: path, type, mode, size,
    content sha256 / link target of every entry) that is recorded in evidence and re-checked before use.

The registry is only a transport: the same digest is served by the public ECR mirror of Docker Official Images
and by Docker Hub; whichever answers, bytes that do not hash to the pinned digests are refused (fail closed).
The image provides the exact interpreter (CPython 3.11.17), the system libraries, and `git` (needed by the
frozen R4 provenance call). The host's libraries, caches and fonts are never used by the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import urllib.error
import urllib.request

IMAGE_LABEL = "python:3.11-bookworm (Docker Official Image), linux/amd64"
MANIFEST_DIGEST = "sha256:9fd630803ec3446920ed6b64d20150bd724c1751e71817293a4230522c1a384a"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
EXPECTED_PYTHON = "3.11.17"
# transports serving the same content-addressed repository (bytes are always verified against the digests)
SOURCES = (("https://public.ecr.aws", "docker/library/python",
            "https://public.ecr.aws/token/?scope=repository:docker/library/python:pull&service=public.ecr.aws"),
           ("https://registry-1.docker.io", "library/python",
            "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"))


class RuntimeImageError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _open(url, token=None, accept=None, follow=True):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if accept:
        h["Accept"] = accept
    req = urllib.request.Request(url, headers=h)
    if follow:
        return urllib.request.urlopen(req, timeout=120)
    try:
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=120)
    except urllib.error.HTTPError as exc:          # blob redirect to object storage: follow WITHOUT the token
        if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
            return urllib.request.urlopen(exc.headers["Location"], timeout=300)
        raise


def _digest_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _fetch_blob(base, repo, token, digest, dest):
    """Download a content-addressed blob to dest; keep it only if it hashes to `digest`."""
    if os.path.exists(dest) and _digest_of(dest) == digest:
        return dest
    tmp = dest + ".part"
    with _open(f"{base}/v2/{repo}/blobs/{digest}", token, follow=False) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 20)
    if _digest_of(tmp) != digest:
        os.remove(tmp)
        raise RuntimeImageError(f"blob {digest} from {base}: bytes do not match the digest")
    os.replace(tmp, dest)
    return dest


def fetch_image(cache_dir):
    """Fetch (or reuse, after re-verification) the pinned manifest, config and layers. Returns the verified
    manifest, config and local layer paths. Fails closed if no source serves bytes matching the digests."""
    os.makedirs(cache_dir, exist_ok=True)
    errors = []
    for base, repo, token_url in SOURCES:
        try:
            token = json.load(_open(token_url))["token"]
            mb = _open(f"{base}/v2/{repo}/manifests/{MANIFEST_DIGEST}", token, MANIFEST_MEDIA_TYPE).read()
            if "sha256:" + hashlib.sha256(mb).hexdigest() != MANIFEST_DIGEST:
                raise RuntimeImageError(f"manifest from {base} does not hash to the pinned digest")
            man = json.loads(mb)
            cfg_path = _fetch_blob(base, repo, token, man["config"]["digest"],
                                   os.path.join(cache_dir, man["config"]["digest"].replace(":", "_")))
            layers = [_fetch_blob(base, repo, token, lay["digest"], os.path.join(cache_dir, lay["digest"].replace(":", "_")))
                      for lay in man["layers"]]
            return {"manifest": man, "manifest_bytes": mb, "config": json.load(open(cfg_path)), "layers": layers,
                    "source": base}
        except (OSError, ValueError, KeyError, urllib.error.URLError, RuntimeImageError) as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeImageError(f"pinned runtime image unavailable from every source (fail closed): {errors}")


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def apply_layer(layer_path, root):
    """Apply one gzip tar layer with OCI whiteout semantics (`.wh.<name>` deletes, `.wh..wh..opq` empties the
    directory's lower-layer content) and the stdlib `tar` extraction filter (no absolute/escaping paths)."""
    with tarfile.open(layer_path, "r:gz") as tf:
        members = tf.getmembers()
        regular = []
        for m in members:
            d, base = os.path.split(m.name)
            if base == ".wh..wh..opq":
                target = os.path.join(root, d)
                if os.path.isdir(target):
                    for child in os.listdir(target):
                        _remove(os.path.join(target, child))
            elif base.startswith(".wh."):
                _remove(os.path.join(root, d, base[4:]))
            elif m.isdev():
                continue                                    # device nodes are never taken from an image layer
            else:
                regular.append(m)
        for m in regular:                                   # replace, never write through, existing entries
            p = os.path.join(root, m.name)
            if (os.path.islink(p) or os.path.isfile(p)) and not m.isdir():
                os.remove(p)
        tf.extractall(root, members=regular, filter="tar")


def rootfs_tree_sha256(root):
    """Deterministic identity of an extracted runtime: every entry's relative path, type, permission bits,
    and content sha256 (files) or link target (symlinks). Ownership and timestamps are not identity."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(dirnames + filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name))
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                rec = f"L {rel} {os.readlink(p)}"
            elif stat.S_ISDIR(st.st_mode):
                rec = f"D {rel} {st.st_mode & 0o7777:o}"
            elif stat.S_ISREG(st.st_mode):
                fh = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        fh.update(chunk)
                rec = f"F {rel} {st.st_mode & 0o7777:o} {st.st_size} {fh.hexdigest()}"
            else:
                rec = f"O {rel} {stat.S_IFMT(st.st_mode):o}"
            h.update(rec.encode("utf-8", "surrogateescape") + b"\n")
    return h.hexdigest()


def ensure_rootfs(cache_dir):
    """Return (rootfs_dir, identity). Extraction happens once per pinned digest into a fresh directory; every
    call re-derives `rootfs_tree_sha256` and refuses a cached runtime whose content drifted (fail closed)."""
    img = fetch_image(os.path.join(cache_dir, "blobs"))
    root = os.path.join(cache_dir, "rootfs_" + MANIFEST_DIGEST.split(":")[1][:16])
    marker = root + ".identity.json"
    if not os.path.exists(marker):
        if os.path.exists(root):
            shutil.rmtree(root)
        os.makedirs(root)
        for layer in img["layers"]:
            apply_layer(layer, root)
        tree = rootfs_tree_sha256(root)
        with open(marker, "w") as fh:
            json.dump({"rootfs_tree_sha256": tree}, fh)
    expected = json.load(open(marker))["rootfs_tree_sha256"]
    tree = rootfs_tree_sha256(root)
    if tree != expected:
        raise RuntimeImageError(f"extracted runtime drifted: {tree} != {expected} (fail closed)")
    env = dict(e.split("=", 1) for e in img["config"]["config"].get("Env", []))
    if env.get("PYTHON_VERSION") != EXPECTED_PYTHON:
        raise RuntimeImageError(f"image python {env.get('PYTHON_VERSION')} != pinned {EXPECTED_PYTHON}")
    for need in ("usr/local/bin/python3.11", "usr/bin/git", "usr/bin/env", "bin"):
        if not os.path.lexists(os.path.join(root, need)):
            raise RuntimeImageError(f"pinned runtime lacks required {need}")
    identity = {"image": IMAGE_LABEL, "manifest_digest": MANIFEST_DIGEST,
                "config_digest": img["manifest"]["config"]["digest"],
                "layer_digests": [lay["digest"] for lay in img["manifest"]["layers"]],
                "python_version": env.get("PYTHON_VERSION"), "rootfs_tree_sha256": tree}
    return root, identity


if __name__ == "__main__":
    import sys
    r, ident = ensure_rootfs(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/v3b_runtime"))
    print(json.dumps({"rootfs": r, **ident}, indent=1))
