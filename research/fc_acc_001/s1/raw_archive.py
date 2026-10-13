"""Content-addressed, write-once archive of raw source responses.

Each object is stored gzip-compressed at <root>/<sha[:2]>/<sha>.gz where sha is
the sha256 of the ORIGINAL (uncompressed) bytes. Identical bytes are stored once
(bounded growth); an existing object is never rewritten, and every read
re-hashes the decompressed bytes.
"""
import gzip
import hashlib
import os
import json
import re


class ArchiveError(ValueError):
    pass


_SHA = re.compile(r"[0-9a-f]{64}")


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _inside_git_worktree(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


class RawArchive:
    """``max_bytes`` bounds total stored size (fail closed when exceeded). A root
    inside a git work tree is refused unless ``allow_in_repo`` is set, so raw
    sportsbook payloads cannot silently grow the repository."""

    def __init__(self, root, *, max_bytes=None, allow_in_repo=False):
        if _inside_git_worktree(root) and not allow_in_repo:
            raise ArchiveError(f"raw archive root {root!r} is inside a git work tree; "
                               "raw payloads must live outside the repository")
        self.root = root
        self.max_bytes = max_bytes

    def _path(self, sha):
        if not _SHA.fullmatch(str(sha)):
            raise ArchiveError(f"invalid sha256 {sha!r}")
        return os.path.join(self.root, sha[:2], sha + ".gz")

    def put(self, raw):
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise ArchiveError("raw response must be non-empty bytes")
        sha = sha256_bytes(bytes(raw))
        path = self._path(sha)
        if os.path.exists(path):
            if self.get(sha) != bytes(raw):          # never silently differs
                raise ArchiveError(f"archive object {sha} corrupt")
            return sha, False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with gzip.GzipFile(tmp, "wb", mtime=0) as fh:   # mtime=0: deterministic bytes
            fh.write(bytes(raw))
        if self.max_bytes is not None and self.size_bytes() > self.max_bytes:
            os.remove(tmp)
            raise ArchiveError(f"raw archive would exceed its {self.max_bytes}-byte bound")
        try:
            os.link(tmp, path)                           # atomic create; never replaces an object
        except FileExistsError:
            pass                                         # a concurrent writer stored the same bytes
        finally:
            os.remove(tmp)
        if self.get(sha) != bytes(raw):
            raise ArchiveError(f"archive object {sha} corrupt")
        return sha, True

    def get(self, sha):
        path = self._path(sha)
        if not os.path.exists(path):
            raise ArchiveError(f"raw response {sha} missing from archive")
        with gzip.open(path, "rb") as fh:
            raw = fh.read()
        if sha256_bytes(raw) != sha:
            raise ArchiveError(f"raw response {sha} does not hash to its address")
        return raw

    def put_json(self, obj):
        """Store canonical JSON bytes; returns (sha, created)."""
        return self.put(canonical_bytes(obj))

    def get_json(self, sha):
        return json.loads(self.get(sha))

    def has(self, sha):
        return os.path.exists(self._path(sha))

    def size_bytes(self):
        total = 0
        for d, _, files in os.walk(self.root):
            total += sum(os.path.getsize(os.path.join(d, f)) for f in files)
        return total
