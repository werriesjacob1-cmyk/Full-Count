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

STORAGE_CONTRACT = "fc-mlb-001b-r2-cas-1"     # content-addressed, create-only, read-back-verified (put_verified)
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


class ObjectConflict(StoreError):
    """The content address is occupied by bytes that are NOT the tape (substitution/corruption): fail closed."""
    code = "OBJECT_CONFLICT"


class StoreAuthError(StoreError):
    """Credentials rejected (401/403). Never retried, never read as 'missing'."""
    code = "STORE_AUTH_FAILED"


class TapeExpired(TapeMissing):
    """A temporary (Actions artifact) tape whose retention ran out: the evidence can no longer be replayed."""
    code = "TAPE_EXPIRED"


class StoreUnavailable(StoreError):
    """Network interruption / 5xx / 429 that persisted through the bounded retries."""
    code = "STORE_UNAVAILABLE"


TRANSIENT_HTTP = (429, 500, 502, 503, 504)
RETRY_DELAYS_S = (1.0, 2.0, 4.0)          # bounded: at most len+1 attempts per request


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

    def locator(self, sha256, size):
        return {"store": self.kind, "root": self.root, "key": key_for(sha256), "sha256": sha256, "bytes": size}

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
    """R2 through its S3 endpoint. Path-style URLs: {endpoint}/{bucket}/{key}. Region `auto`.

    Failure semantics (every one fails closed; nothing is ever read as success):
      401/403 -> StoreAuthError (no retry)        404 -> TapeMissing           412 on PUT -> ObjectExists
      429/5xx, connection reset/refused, timeout, truncated body (IncompleteRead) -> retried with bounded backoff,
      then StoreUnavailable. PUT is create-only + payload-bound, so a retry after a lost response can only create
      the object or meet 412 -- never a second, different object (see put_verified)."""
    kind = "r2"

    def __init__(self, endpoint, bucket, access_key=None, secret_key=None, region="auto", public_base=None,
                 sleep=None, timeout_s=600):
        self.endpoint, self.bucket, self.region = endpoint.rstrip("/"), bucket, region
        self.access_key, self.secret_key, self.public_base = access_key, secret_key, public_base
        self.sleep = sleep if sleep is not None else __import__("time").sleep
        self.timeout_s = timeout_s
        self.attempts = []                       # (method, outcome) audit trail of every request attempt

    @classmethod
    def from_env(cls):
        need = ("V3B_R2_ENDPOINT", "V3B_R2_BUCKET", "V3B_R2_ACCESS_KEY_ID", "V3B_R2_SECRET_ACCESS_KEY")
        if not all(os.environ.get(k) for k in need):
            raise StoreError(f"R2 store not configured ({', '.join(need)})")
        return cls(os.environ["V3B_R2_ENDPOINT"], os.environ["V3B_R2_BUCKET"], os.environ["V3B_R2_ACCESS_KEY_ID"],
                   os.environ["V3B_R2_SECRET_ACCESS_KEY"], public_base=os.environ.get("V3B_R2_PUBLIC_BASE"))

    def _url(self, key):
        return f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(key, safe='/')}"

    def _req(self, method, key, payload_sha, headers=None, data=None):
        if not (self.access_key and self.secret_key):
            raise StoreAuthError("R2 credentials missing")
        amz = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        h = sigv4_headers(method, self._url(key), self.region, self.access_key, self.secret_key, payload_sha, amz, headers)
        h.pop("host")
        return urllib.request.Request(self._url(key), data=data, method=method, headers=h)

    def _with_retries(self, method, key, attempt):
        """Run attempt() with bounded retries on transient failures only; map every failure to a StoreError."""
        import http.client
        import socket
        last = None
        for i in range(len(RETRY_DELAYS_S) + 1):
            try:
                out = attempt()
                self.attempts.append((method, "ok"))
                return out
            except urllib.error.HTTPError as exc:
                self.attempts.append((method, exc.code))
                if exc.code in (401, 403):
                    raise StoreAuthError(f"{method} {key}: HTTP {exc.code} (credentials rejected)") from exc
                if exc.code == 404:
                    raise TapeMissing(f"{method} {key}: HTTP 404") from exc
                if exc.code == 412:
                    raise ObjectExists(f"{key} already exists (create-only)") from exc
                if exc.code not in TRANSIENT_HTTP:
                    raise StoreError(f"{method} {key}: HTTP {exc.code}") from exc
                last = exc
            except (urllib.error.URLError, http.client.HTTPException, ConnectionError, socket.timeout,
                    TimeoutError) as exc:
                self.attempts.append((method, type(exc).__name__))
                last = exc
            if i < len(RETRY_DELAYS_S):
                self.sleep(RETRY_DELAYS_S[i])
        raise StoreUnavailable(f"{method} {key}: still failing after {len(RETRY_DELAYS_S) + 1} attempts: {last!r}")

    def put(self, path, sha256, size):
        """Create-only, payload-bound upload of exactly `size` bytes hashing to `sha256` (checked BEFORE sending)."""
        key = key_for(sha256)
        _verify(path, sha256, size)

        def attempt():
            with open(path, "rb") as fh:
                req = self._req("PUT", key, sha256, {"if-none-match": "*", "content-length": str(size),
                                                     "content-type": "application/gzip"}, data=fh)
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    if r.status not in (200, 201):
                        raise StoreError(f"PUT {key}: HTTP {r.status}")
                    return r.headers.get("ETag")
        etag = self._with_retries("PUT", key, attempt)
        return {"store": self.kind, "endpoint": self.endpoint, "bucket": self.bucket, "key": key, "sha256": sha256,
                "bytes": size, "public_base": self.public_base, "etag": etag}

    def get(self, key, dest):
        def attempt():
            if self.public_base:
                req = urllib.request.Request(f"{self.public_base.rstrip('/')}/{urllib.parse.quote(key, safe='/')}")
            else:
                req = self._req("GET", key, EMPTY_SHA256)
            import http.client
            got = 0
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r, open(dest, "wb") as fh:
                want = r.headers.get("Content-Length")
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    fh.write(chunk)
                    got += len(chunk)
            if want is not None and got != int(want):   # urllib can return a short body silently: retry it
                raise http.client.IncompleteRead(b"", int(want) - got)
            return dest
        return self._with_retries("GET", key, attempt)

    def locator(self, sha256, size):
        return {"store": self.kind, "endpoint": self.endpoint, "bucket": self.bucket, "key": key_for(sha256),
                "sha256": sha256, "bytes": size, "public_base": self.public_base}


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


# ---- TEMPORARY authoritative prospective store: GitHub Actions artifacts (Jacob, 2026-10-06) ---------------------
# Same evidence-integrity contract as R2 (content address = whole-tape sha256; exact size; create-only per run;
# verified read-back BEFORE the seal is published; fetch by exact identity; hash before replay; missing / expired /
# size / hash mismatch fail closed; NO fallback to runner-local caches or any other source). The transport is not
# trusted; the sealed sha256 + size are authoritative. NOT archival: artifacts expire (90 days on this repository),
# so every artifact-backed tape MUST be migrated byte-for-byte to durable storage (R2) before the migration
# threshold (retention_monitor.py; milestone FC-MLB-001C). The R2 code above stays ready for that.
GHA_KIND = "github-actions-artifact"
GHA_CONTRACT = "fc-v3-gha-artifact-temp-1"


def gha_names(sha256):
    """(artifact name, file name inside it), both bound to the whole-tape sha256."""
    key_for(sha256)
    return f"v3-tape-{sha256}", f"fc-v3-tape-{sha256}.json.gz"


class ActionsArtifactStage:
    """Stages the tape inside a GitHub Actions job for upload by the workflow (only an Actions job can create an
    artifact). The locator binds repository + run + artifact name + sha256 + size BEFORE upload; the workflow then
    uploads, and artifact_api.confirm_upload proves the uploaded bytes (read-back) before the seal is published."""
    kind = GHA_KIND

    def __init__(self, stage_dir, repository, run_id, run_attempt, retention_days):
        if not (repository and run_id and run_attempt and retention_days):
            raise StoreError("Actions artifact store needs repository, run id, run attempt and retention days")
        self.stage_dir, self.repository = os.path.abspath(stage_dir), repository
        self.run_id, self.run_attempt, self.retention_days = int(run_id), int(run_attempt), int(retention_days)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("V3B_ARTIFACT_STAGE_DIR") or "", os.environ.get("GITHUB_REPOSITORY"),
                   os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_RUN_ATTEMPT"),
                   os.environ.get("V3B_ARTIFACT_RETENTION_DAYS"))

    def locator(self, sha256, size):
        name, fname = gha_names(sha256)
        return {"store": self.kind, "storage_contract": GHA_CONTRACT, "repository": self.repository,
                "run_id": self.run_id, "run_attempt": self.run_attempt, "artifact_name": name, "file_name": fname,
                "key": key_for(sha256), "sha256": sha256, "bytes": size, "retention_days": self.retention_days,
                "durability": "TEMPORARY (Actions artifact retention); R2 migration required"}

    def stage(self, path, sha256, size):
        _verify(path, sha256, size)
        os.makedirs(self.stage_dir, exist_ok=True)
        if os.listdir(self.stage_dir):
            raise StoreError("artifact stage dir is not empty: exactly one tape per artifact")
        dest = os.path.join(self.stage_dir, gha_names(sha256)[1])
        shutil.copyfile(path, dest)
        _verify(dest, sha256, size)
        return dict(self.locator(sha256, size), write_status="STAGED_FOR_UPLOAD", verified_readback=False)


def _gha_local(loc):
    """The verifier never downloads by itself from a random place: the exact artifact (by id) is downloaded by
    artifact_api into V3B_ARTIFACT_DOWNLOAD_DIR; anything else -> missing. No fallback."""
    d = os.environ.get("V3B_ARTIFACT_DOWNLOAD_DIR")
    p = os.path.join(d, loc["file_name"]) if d and loc.get("file_name") else None
    if loc.get("file_name") != gha_names(loc["sha256"])[1]:
        raise StoreError("artifact file name is not bound to the sealed sha256")
    if not p or not os.path.isfile(p):
        raise TapeMissing(f"{loc.get('artifact_name')}: artifact not downloaded (missing or expired)")
    return ArtifactTransport(p)


def store_from_locator(loc, repo=None):
    kind = loc.get("store")
    if kind == "localfs":
        return LocalFSStore(os.environ.get("V3B_LOCALFS_STORE_ROOT") or loc["root"])
    if kind == "r2":
        # the SEALED endpoint/bucket are used; only credentials (read token) or a public read base come from env
        return R2Store(loc["endpoint"], loc["bucket"], os.environ.get("V3B_R2_ACCESS_KEY_ID"),
                       os.environ.get("V3B_R2_SECRET_ACCESS_KEY"),
                       public_base=os.environ.get("V3B_R2_PUBLIC_BASE") or loc.get("public_base"))
    if kind == GHA_KIND:
        return _gha_local(loc)
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
    return _fetch_via(store_from_locator(loc, repo), loc, dest_dir)


def _fetch_via(store, loc, dest_dir=None):
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="v3b_tape_")
    dest = os.path.join(dest_dir, "shadow_tape.json.gz")
    try:
        store.get(loc["key"], dest)
    except StoreError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any other failure is still a failure to retrieve: fail closed
        raise StoreUnavailable(f"{loc['key']}: retrieval failed: {exc!r}") from exc
    return _verify(dest, loc["sha256"], int(loc["bytes"]))


def put_verified(store, path, sha256, size):
    """The ONLY write path for sealed evidence. Ends in exactly one of two states:
      * an object at the content address whose bytes were read back and hashed to (sha256, size); or
      * an exception (nothing may be sealed).
    CREATED: this call stored it. EXISTS_VERIFIED: the address was already occupied (e.g. an earlier attempt whose
    response was lost) AND its bytes are proven identical. Occupied by other bytes -> ObjectConflict."""
    _verify(path, sha256, size)                                   # local bytes are the identity BEFORE any send
    try:
        loc = store.put(path, sha256, size)
        status = "CREATED"
    except ObjectExists:
        loc = store.locator(sha256, size) if hasattr(store, "locator") else {
            "store": store.kind, "key": key_for(sha256), "sha256": sha256, "bytes": size, "root": getattr(store, "root", None)}
        status = "EXISTS_VERIFIED"
    try:
        _fetch_via(store, loc)                                    # read back by identity (writer's own credentials)
    except (TapeHashMismatch, TapeSizeMismatch) as exc:
        raise ObjectConflict(f"{loc['key']}: the content address holds different bytes ({exc.code}); refusing") from exc
    return dict(loc, write_status=status, verified_readback=True)
