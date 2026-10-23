"""Archived source observations shared by every S1 input (sportsbook pages,
MLB schedule/lineups, pitcher game logs).

A source record binds one request to the exact delivered response body
(sha256 over ``requests.Response.content``: the decoded HTTP body, not the
wire bytes), its host, and the observation start/completion times. Anything
used as pregame evidence must come from a verified record whose completion
time is at or before the decision cutoff; pre-parsed or unarchived inputs are
never treated as verified.
"""
from datetime import datetime


class SourceError(ValueError):
    pass


def ts(v, field="timestamp"):
    if not isinstance(v, str) or not v:
        raise SourceError(f"{field} missing")
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceError(f"{field} malformed: {v!r}") from exc
    if t.tzinfo is None:
        raise SourceError(f"{field} must be timezone-aware: {v!r}")
    return t


def fetch(archive, fetch_bytes, clock, *, source, request):
    rec = {"source": source, "request": request, "started_at": clock(), "completed_at": None, "host": None,
           "raw_sha256": None, "bytes": None, "status": None}
    try:
        host, raw = fetch_bytes(request)
        sha, _ = archive.put(raw)
        rec.update(host=host, raw_sha256=sha, bytes=len(raw), status="OK")
    except Exception as exc:  # noqa: BLE001 - recorded as a failed observation
        rec["status"] = f"FAILED: {type(exc).__name__}: {exc}"
    rec["completed_at"] = clock()
    return rec


def verify(rec, archive, *, source=None, request=None, not_after=None):
    """Return the archived bytes of an OK record, or raise."""
    if not isinstance(rec, dict) or rec.get("status") != "OK":
        raise SourceError(f"source record not OK: {None if not isinstance(rec, dict) else rec.get('status')}")
    if source is not None and rec.get("source") != source:
        raise SourceError(f"source {rec.get('source')!r} != expected {source!r}")
    if request is not None and rec.get("request") != request:
        raise SourceError(f"request {rec.get('request')!r} != expected {request!r}")
    started, completed = ts(rec.get("started_at"), "started_at"), ts(rec.get("completed_at"), "completed_at")
    if completed < started:
        raise SourceError("source observation completed before it started")
    if not_after is not None and completed > ts(not_after, "cutoff"):
        raise SourceError(f"source observation completed {rec['completed_at']} after cutoff {not_after}")
    raw = archive.get(rec.get("raw_sha256"))
    if len(raw) != rec.get("bytes"):
        raise SourceError("archived size does not match the source record")
    return raw
