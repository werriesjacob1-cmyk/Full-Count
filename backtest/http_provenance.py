#!/usr/bin/env python3
"""Thread-safe HTTP provenance for canonical historical generation.

Designed to be transparent to scoring code:
- disabled by default;
- wraps requests.sessions.Session.request only when explicitly installed;
- records only allow-listed historical source hosts;
- returns the original Response object unchanged;
- records failures as provenance because fallbacks are scientifically relevant.

Per-date logical fingerprints intentionally exclude retrieval timestamps and
thread arrival order. They bind the multiset of scientific request/response
observations, not scheduler timing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests


DEFAULT_ALLOWED_HOSTS = {
    "statsapi.mlb.com",
    "www.mlb.com",
    "mlb.com",
}

_LOCK = threading.RLock()
_ACTIVE = None
_INSTALLED = False
_ORIGINAL_SESSION_REQUEST = None


class HttpProvenanceError(RuntimeError):
    pass


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _canonical_headers(headers):
    headers = headers or {}
    wanted = {}
    for key in ("User-Agent", "Accept", "Accept-Language", "Accept-Encoding"):
        value = headers.get(key)
        if value is not None:
            wanted[key.lower()] = str(value)
    return wanted


def _prepared_identity(method, url, kwargs):
    try:
        req = requests.Request(
            method=method,
            url=url,
            params=kwargs.get("params"),
            data=kwargs.get("data"),
            json=kwargs.get("json"),
            headers=kwargs.get("headers"),
        ).prepare()
        final_url = req.url or str(url)
        headers = _canonical_headers(req.headers)
        body = req.body
        if body is None:
            body_sha = None
        elif isinstance(body, bytes):
            body_sha = _sha256(body)
        else:
            body_sha = _sha256(str(body).encode("utf-8"))
        return {
            "method": str(method).upper(),
            "url": final_url,
            "headers": headers,
            "request_body_sha256": body_sha,
        }
    except Exception:
        return {
            "method": str(method).upper(),
            "url": str(url),
            "headers": _canonical_headers(kwargs.get("headers")),
            "request_body_sha256": None,
        }


def _logical_entry(entry):
    return {
        "method": entry.get("method"),
        "url": entry.get("url"),
        "request_headers": entry.get("request_headers"),
        "request_body_sha256": entry.get("request_body_sha256"),
        "status_code": entry.get("status_code"),
        "response_sha256": entry.get("response_sha256"),
        "response_bytes": entry.get("response_bytes"),
        "exception_type": entry.get("exception_type"),
    }


def _logical_fingerprint(entries):
    encoded = sorted(
        json.dumps(_logical_entry(entry), sort_keys=True, separators=(",", ":"))
        for entry in entries
    )
    return _sha256("\n".join(encoded).encode("utf-8"))


class ResponseLedger:
    def __init__(self, root_dir, *, allowed_hosts=None, archive_bodies=False):
        self.root_dir = os.path.abspath(root_dir)
        self.allowed_hosts = set(allowed_hosts or DEFAULT_ALLOWED_HOSTS)
        self.archive_bodies = bool(archive_bodies)
        self._current_date = None
        self._entries = []
        self._sequence = 0
        os.makedirs(self.root_dir, exist_ok=True)
        if self.archive_bodies:
            os.makedirs(self._blob_dir(), exist_ok=True)

    def _blob_dir(self):
        return os.path.join(self.root_dir, "blobs")

    def start_date(self, date):
        with _LOCK:
            if self._current_date is not None:
                raise HttpProvenanceError(
                    f"cannot start {date}: ledger date {self._current_date} is still active"
                )
            self._current_date = str(date)
            self._entries = []
            self._sequence = 0

    def active_date(self):
        with _LOCK:
            return self._current_date

    def should_record(self, url):
        try:
            host = (urlparse(str(url)).hostname or "").lower()
        except Exception:
            return False
        return host in self.allowed_hosts

    def _archive_body(self, content_sha256, content):
        if not self.archive_bodies or not content_sha256:
            return None
        path = os.path.join(self._blob_dir(), f"{content_sha256}.gz")
        if os.path.exists(path):
            return os.path.relpath(path, self.root_dir)
        tmp = path + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                gz.write(content)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, path)
        return os.path.relpath(path, self.root_dir)

    def record_response(self, response):
        request = getattr(response, "request", None)
        url = getattr(request, "url", None) or getattr(response, "url", None)
        if not self.should_record(url):
            return
        with _LOCK:
            if self._current_date is None:
                raise HttpProvenanceError(
                    f"allow-listed HTTP response observed outside active canonical date: {url}"
                )
            content = bytes(getattr(response, "content", b"") or b"")
            response_sha = _sha256(content)
            self._sequence += 1
            headers = _canonical_headers(getattr(request, "headers", None))
            body = getattr(request, "body", None)
            if body is None:
                body_sha = None
            elif isinstance(body, bytes):
                body_sha = _sha256(body)
            else:
                body_sha = _sha256(str(body).encode("utf-8"))
            entry = {
                "sequence": self._sequence,
                "observed_date": self._current_date,
                "retrieved_at": _now_iso(),
                "thread_id": threading.get_ident(),
                "method": getattr(request, "method", "GET"),
                "url": str(url),
                "request_headers": headers,
                "request_body_sha256": body_sha,
                "status_code": int(getattr(response, "status_code", 0) or 0),
                "response_sha256": response_sha,
                "response_bytes": len(content),
                "response_content_type": (
                    getattr(response, "headers", {}) or {}
                ).get("Content-Type"),
                "exception_type": None,
            }
            archived = self._archive_body(response_sha, content)
            if archived:
                entry["archived_body"] = archived
            self._entries.append(entry)

    def record_exception(self, method, url, kwargs, exc):
        identity = _prepared_identity(method, url, kwargs)
        if not self.should_record(identity["url"]):
            return
        with _LOCK:
            if self._current_date is None:
                raise HttpProvenanceError(
                    "allow-listed HTTP exception observed outside active canonical date: "
                    f"{identity['url']}"
                )
            self._sequence += 1
            self._entries.append({
                "sequence": self._sequence,
                "observed_date": self._current_date,
                "retrieved_at": _now_iso(),
                "thread_id": threading.get_ident(),
                "method": identity["method"],
                "url": identity["url"],
                "request_headers": identity["headers"],
                "request_body_sha256": identity["request_body_sha256"],
                "status_code": None,
                "response_sha256": None,
                "response_bytes": None,
                "response_content_type": None,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
            })

    def finish_date(self, date):
        with _LOCK:
            date = str(date)
            if self._current_date != date:
                raise HttpProvenanceError(
                    f"finish_date({date}) does not match active {self._current_date!r}"
                )
            entries = list(self._entries)
            raw = b"".join(
                (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for entry in entries
            )
            ledger_path = os.path.join(self.root_dir, f"{date}.ledger.jsonl")
            tmp = ledger_path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, ledger_path)
            logical = _logical_fingerprint(entries)
            summary = {
                "date": date,
                "request_count": len(entries),
                "success_2xx": sum(
                    1 for entry in entries
                    if isinstance(entry.get("status_code"), int)
                    and 200 <= entry["status_code"] < 300
                ),
                "http_non_2xx": sum(
                    1 for entry in entries
                    if isinstance(entry.get("status_code"), int)
                    and not (200 <= entry["status_code"] < 300)
                ),
                "exceptions": sum(1 for entry in entries if entry.get("exception_type")),
                "response_bytes_total": sum(
                    int(entry.get("response_bytes") or 0) for entry in entries
                ),
                "ledger_file": os.path.basename(ledger_path),
                "ledger_file_sha256": _sha256(raw),
                "logical_fingerprint": logical,
                "allowed_hosts": sorted(self.allowed_hosts),
                "archive_bodies": self.archive_bodies,
            }
            summary_path = os.path.join(self.root_dir, f"{date}.summary.json")
            tmp_summary = summary_path + ".tmp"
            with open(tmp_summary, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_summary, summary_path)
            self._current_date = None
            self._entries = []
            self._sequence = 0
            return summary

    def abort_date(self, date, reason):
        with _LOCK:
            date = str(date)
            if self._current_date != date:
                return
            entries = list(self._entries)
            raw = b"".join(
                (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for entry in entries
            )
            path = os.path.join(self.root_dir, f"{date}.aborted.ledger.jsonl")
            with open(path, "wb") as handle:
                handle.write(raw)
            meta = {
                "date": date,
                "aborted_at": _now_iso(),
                "reason": str(reason)[:1000],
                "request_count": len(entries),
                "ledger_file_sha256": _sha256(raw),
                "logical_fingerprint": _logical_fingerprint(entries),
            }
            with open(
                os.path.join(self.root_dir, f"{date}.aborted.summary.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(meta, handle, indent=2, sort_keys=True)
                handle.write("\n")
            self._current_date = None
            self._entries = []
            self._sequence = 0


def set_active_ledger(ledger):
    global _ACTIVE
    with _LOCK:
        _ACTIVE = ledger


def get_active_ledger():
    with _LOCK:
        return _ACTIVE


def install_requests_hook():
    global _INSTALLED, _ORIGINAL_SESSION_REQUEST
    with _LOCK:
        if _INSTALLED:
            return
        _ORIGINAL_SESSION_REQUEST = requests.sessions.Session.request

        def wrapped(session, method, url, **kwargs):
            ledger = get_active_ledger()
            try:
                response = _ORIGINAL_SESSION_REQUEST(session, method, url, **kwargs)
            except Exception as exc:
                if ledger is not None:
                    ledger.record_exception(method, url, kwargs, exc)
                raise
            if ledger is not None:
                ledger.record_response(response)
            return response

        requests.sessions.Session.request = wrapped
        _INSTALLED = True


def uninstall_requests_hook():
    global _INSTALLED, _ORIGINAL_SESSION_REQUEST
    with _LOCK:
        if not _INSTALLED:
            return
        requests.sessions.Session.request = _ORIGINAL_SESSION_REQUEST
        _ORIGINAL_SESSION_REQUEST = None
        _INSTALLED = False
