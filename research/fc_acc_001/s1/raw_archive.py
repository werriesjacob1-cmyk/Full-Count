"""Content-addressed, write-once archive of raw source responses.

Each object is stored gzip-compressed at <root>/<sha[:2]>/<sha>.gz where sha is
the sha256 of the ORIGINAL (uncompressed) bytes. Identical bytes are stored once
(bounded growth); an existing object is never rewritten, and every read
re-hashes the decompressed bytes.
"""
import gzip
import hashlib
import os
import re


class ArchiveError(ValueError):
    pass


_SHA = re.compile(r"[0-9a-f]{64}")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


class RawArchive:
    def __init__(self, root):
        self.root = root

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
        tmp = path + ".tmp"
        with gzip.GzipFile(tmp, "wb", mtime=0) as fh:   # mtime=0: deterministic bytes
            fh.write(bytes(raw))
        os.replace(tmp, path)
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

    def has(self, sha):
        return os.path.exists(self._path(sha))

    def size_bytes(self):
        total = 0
        for d, _, files in os.walk(self.root):
            total += sum(os.path.getsize(os.path.join(d, f)) for f in files)
        return total
