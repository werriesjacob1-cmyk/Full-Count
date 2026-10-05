#!/usr/bin/env python3
"""FC-MLB-001B tape store (criteria TASKS/FC-MLB-001B.md §6): content-addressed, create-only, verify-before-use.

Identity is the content: a tape is addressed ONLY by the SHA-256 of its exact bytes and its exact size. The seal
records {key, sha256, bytes, store}; nothing else about the store is trusted. Every read path downloads the exact
object and hashes it BEFORE it may be replayed:

    object unavailable  -> TapeMissing        (fail closed)
    size differs        -> TapeSizeMismatch   (fail closed)
    sha256 differs      -> TapeHashMismatch   (fail closed)

Backends
  R2Store        Cloudflare R2 through the S3 API, stdlib SigV4 (no third-party client). Writes are create-only
                 (`If-None-Match: *`) and payload-bound (`x-amz-content-sha256` = the tape's sha256, which the
                 server verifies). The runner token needs only object write + read on the one bucket; bucket-lock
                 rules are administered with a separate privilege (see STORAGE_DESIGN_B.md). AUTHORITATIVE for
                 prospective units once Jacob authorizes the account actions. Not activated here.
  LocalFSStore   isolated mock/local store with the same create-only/content-addressed semantics (tests, drills).
  ArtifactTransport  drill-only, NON-authoritative transport (FC-MLB-001B certification drill only): the complete
                 tape as ONE GitHub Actions artifact named with its full sha256, downloaded by exact artifact id
                 into a local file by environment B. The bytes are verified like any other store (size + sha256
                 before use); the transport is never trusted. Never used for prospective units, never in git.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

KEY_PREFIX = "v3/tapes/sha256/"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class StoreError(RuntimeError):
    code = "STORE_ERROR"


class TapeMissing(StoreError):
    code = "TAPE_MISSING"


class TapeHashMismatch(StoreError):
    code = "TAPE_HASH_MISMATCH"


class TapeSizeMismatch(StoreError):
    code = "TAPE_SIZE_MISMATCH"


class ObjectExists(StoreError):
    code = "OBJECT_EXISTS"


def key_for(sha256):
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise StoreError(f"not a sha256 hex digest: {sha256!r}")
    return f"{KEY_PREFIX}{sha256}.json.gz"


def file_identity(path):
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _verify(path, sha256, size):
    got_sha, got_size = file_identity(path)
    if got_size != size:
        raise TapeSizeMismatch(f"retrieved object is {got_size} bytes, sealed size is {size}")
    if got_sha != sha256:
        raise TapeHashMismatch(f"retrieved object sha256 {got_sha} != sealed {sha256}")
    return path


# ---- local mock / isolated store --------------------------------------------------------------------------------
class LocalFSStore:
    kind = "localfs"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def _p(self, key):
        return os.path.join(self.root, key)

    def put(self, path, sha256, size):
        key = key_for(sha256)
        _verify(path, sha256, size)
        dest = self._p(key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            fd = os.open(dest + ".lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)   # create-only, like If-None-Match: *
        except FileExistsError as exc:
            raise ObjectExists(f"{key} already exists (create-only store; no overwrite)") from exc
        os.close(fd)
        tmp = dest + ".part"
        shutil.copyfile(path, tmp)
        os.chmod(tmp, 0o444)
        os.replace(tmp, dest)
        return {"store": self.kind, "root": self.root, "key": key, "sha256": sha256, "bytes": size}

    def get(self, key, dest):
        src = self._p(key)
        if not os.path.isfile(src):
            raise TapeMissing(f"{key} not present in {self.kind} store")
        shutil.copyfile(src, dest)
        return dest


# ---- Cloudflare R2 (S3 API, SigV4) ---------------------------------------------------------------------------------
def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(method, url, region, access_key, secret_key, payload_sha256, amz_date, headers=None,
                  service="s3"):
    """AWS Signature Version 4 (header auth). Returns the full header dict including Authorization."""
    u = urllib.parse.urlsplit(url)
    hdrs = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    hdrs.update({"host": u.netloc, "x-amz-content-sha256": payload_sha256, "x-amz-date": amz_date})
    signed = ";".join(sorted(hdrs))
    canonical_headers = "".join(f"{k}:{hdrs[k]}\n" for k in sorted(hdrs))
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    canonical_query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                               for k, v in sorted(q))
    canonical_uri = urllib.parse.quote(u.path or "/", safe="/-_.~")
    creq = "\n".join([method, canonical_uri, canonical_query, canonical_headers, signed, payload_sha256])
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = _sign(("AWS4" + secret_key).encode(), day)
    k = _sign(_sign(_sign(k, region), service), "aws4_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return hdrs


class R2Store:
    """R2 through its S3 endpoint. Path-style URLs: {endpoint}/{bucket}/{key}. Region `auto`."""
    kind = "r2"

    def __init__(self, endpoint, bucket, access_key=None, secret_key=None, region="auto", public_base=None):
        self.endpoint, self.bucket, self.region = endpoint.rstrip("/"), bucket, region
        self.access_key, self.secret_key, self.public_base = access_key, secret_key, public_base

    @classmethod
    def from_env(cls):
        need = ("V3B_R2_ENDPOINT", "V3B_R2_BUCKET")
        if not all(os.environ.get(k) for k in need):
            raise StoreError(f"R2 store not configured ({', '.join(need)})")
        return cls(os.environ["V3B_R2_ENDPOINT"], os.environ["V3B_R2_BUCKET"], os.environ.get("V3B_R2_ACCESS_KEY_ID"),
                   os.environ.get("V3B_R2_SECRET_ACCESS_KEY"), public_base=os.environ.get("V3B_R2_PUBLIC_BASE"))

    def _url(self, key):
        return f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(key, safe='/')}"

    def _req(self, method, key, payload_sha, headers=None, data=None):
        if not (self.access_key and self.secret_key):
            raise StoreError("R2 credentials missing")
        amz = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        h = sigv4_headers(method, self._url(key), self.region, self.access_key, self.secret_key, payload_sha, amz, headers)
        h.pop("host")
        return urllib.request.Request(self._url(key), data=data, method=method, headers=h)

    def put(self, path, sha256, size):
        key = key_for(sha256)
        _verify(path, sha256, size)
        with open(path, "rb") as fh:
            req = self._req("PUT", key, sha256, {"if-none-match": "*", "content-length": str(size),
                                                 "content-type": "application/gzip"}, data=fh)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    if r.status not in (200, 201):
                        raise StoreError(f"PUT {key}: HTTP {r.status}")
            except urllib.error.HTTPError as exc:
                if exc.code == 412:
                    raise ObjectExists(f"{key} already exists (create-only)") from exc
                raise StoreError(f"PUT {key}: HTTP {exc.code}") from exc
        return {"store": self.kind, "endpoint": self.endpoint, "bucket": self.bucket, "key": key, "sha256": sha256,
                "bytes": size, "public_base": self.public_base}

    def get(self, key, dest):
        if self.public_base:
            req = urllib.request.Request(f"{self.public_base.rstrip('/')}/{urllib.parse.quote(key, safe='/')}")
        else:
            req = self._req("GET", key, EMPTY_SHA256)
        try:
            with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise TapeMissing(f"{key}: HTTP {exc.code}") from exc
            raise StoreError(f"GET {key}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise TapeMissing(f"{key}: store unreachable ({exc.reason})") from exc
        return dest


# ---- drill-only transport: one GitHub Actions artifact (outside git) -------------------------------------------
ARTIFACT_KIND = "actions-artifact-drill-transport"


def transport_asset_name(sha256):
    """The single transport file/artifact name is bound to the whole-tape sha256."""
    key_for(sha256)
    return f"fc-mlb-001b-drill-tape-{sha256}.json.gz"


class ArtifactTransport:
    """Reads the tape from the local file environment B downloaded (by exact artifact id). NON-authoritative."""
    kind = ARTIFACT_KIND

    def __init__(self, local_path):
        self.local_path = local_path

    def get(self, key, dest):
        if not self.local_path or not os.path.isfile(self.local_path):
            raise TapeMissing(f"{key}: drill transport file not present ({self.local_path})")
        shutil.copyfile(self.local_path, dest)
        return dest


def store_from_locator(loc, repo=None):
    kind = loc.get("store")
    if kind == "localfs":
        return LocalFSStore(os.environ.get("V3B_LOCALFS_STORE_ROOT") or loc["root"])
    if kind == "r2":
        # the SEALED endpoint/bucket are used; only credentials (read token) or a public read base come from env
        return R2Store(loc["endpoint"], loc["bucket"], os.environ.get("V3B_R2_ACCESS_KEY_ID"),
                       os.environ.get("V3B_R2_SECRET_ACCESS_KEY"),
                       public_base=os.environ.get("V3B_R2_PUBLIC_BASE") or loc.get("public_base"))
    if kind == ARTIFACT_KIND:
        if not loc.get("local_path") or not os.path.isfile(loc["local_path"]):
            raise TapeMissing(f"{loc.get('key')}: drill transport artifact was not downloaded ({loc.get('local_path')})")
        if os.path.basename(loc.get("local_path") or "") != transport_asset_name(loc["sha256"]):
            raise StoreError("drill transport file name is not bound to the sealed sha256")
        return ArtifactTransport(loc.get("local_path"))
    raise StoreError(f"unknown tape store {kind!r}")


def fetch_verified(loc, dest_dir=None, repo=None):
    """Retrieve the sealed tape by exact identity and verify size + sha256 BEFORE returning it. Fail closed."""
    for f in ("key", "sha256", "bytes"):
        if f not in loc:
            raise StoreError(f"sealed tape locator lacks {f}")
    if loc["key"] != key_for(loc["sha256"]):
        raise StoreError("sealed key is not the content address of the sealed sha256")
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="v3b_tape_")
    dest = os.path.join(dest_dir, "shadow_tape.json.gz")
    store_from_locator(loc, repo).get(loc["key"], dest)
    return _verify(dest, loc["sha256"], int(loc["bytes"]))
