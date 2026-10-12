"""Prospective lifecycle for FC-NFL-004 research freezes, without write access.

Storage is GitHub Actions artifacts only (existing mechanism; ``contents: read``
plus read-only ``actions: read``). Each scheduled run:

1. ``fetch_prior_ledgers``: download the newest non-expired rolling ledger
   artifacts of this workflow/branch by exact artifact id and verify the
   GitHub-reported digest;
2. ``merge_ledgers``: replay their frozen records/grades/dispositions into a
   working ledger. Every file is re-verified; identical files dedupe; a
   same-path/different-bytes collision fails closed. Frozen records are copied
   byte-for-byte, never rewritten;
3. the run freezes new vintages into the same ledger;
4. ``grade_ledger``: grade every ungraded freeze whose game is final (ESPN
   STATUS_FINAL bound to the same identity hash), using the latest LATER
   pre-kickoff vintage of the same event as the movement reference, or write an
   explicit terminal disposition;
5. the merged ledger is re-uploaded as the next rolling artifact.

Durability limit (NOT solved here): artifacts expire after 90 days. The chain
survives only while a run happens at least every 90 days and the artifact API
is reachable. Permanent retention needs a write-capable persistence path, which
requires Jacob's separate authorization (see LIFECYCLE_DESIGN.md).
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from nfl.prospective.espn_final_outcome import extract_espn_final_outcome
from nfl.prospective.game_market_research_freeze import (
    canonical_sha256,
    load_research_freeze,
)
from nfl.prospective.game_market_research_grade import grade_research_freeze, write_grade

ET = ZoneInfo("America/New_York")
LEDGER_ARTIFACT = "nfl-game-market-research-ledger"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d}"
GRADE_AFTER = timedelta(hours=3)
TERMINAL_AFTER = timedelta(days=7)
DISPOSITION_SCHEMA = "fc-nfl-004-grade-disposition-v1"


class LedgerLifecycleError(ValueError):
    """Raised when replayed evidence is inconsistent or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _verify_file(path: Path) -> str:
    """Return the kind of a ledger file after verifying its own hash."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if path.parent.name == "grades":
        key = "grade_sha256" if path.name.endswith(".grade.json") else "disposition_sha256"
        body = {k: v for k, v in data.items() if k != key}
        if canonical_sha256(body) != data.get(key):
            raise LedgerLifecycleError(f"hash mismatch: {path}")
        return "grade" if key == "grade_sha256" else "disposition"
    load_research_freeze(path)
    return "freeze"


def merge_ledgers(sources: list[Path], dest: Path) -> dict[str, Any]:
    """Replay verified ledger files from ``sources`` into ``dest`` (create-only)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    counts = {"freeze": 0, "grade": 0, "disposition": 0, "identical_skipped": 0}
    for src in sources:
        src = Path(src)
        for path in sorted(src.rglob("*.json")):
            rel = path.relative_to(src)
            kind = _verify_file(path)
            target = dest / rel
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise LedgerLifecycleError(f"conflicting evidence at {rel}")
                counts["identical_skipped"] += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            counts[kind] += 1
    return counts


def _safe_extract(blob: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts:
                raise LedgerLifecycleError(f"unsafe path in artifact: {info.filename}")
        zf.extractall(dest)


def fetch_prior_ledgers(*, repo: str, branch: str, token: str, dest: Path, session,
                        limit: int = 3) -> list[dict[str, Any]]:
    """Download the newest ``limit`` non-expired rolling ledgers for ``branch``.

    Read-only REST calls. Each zip's sha256 must equal the API-reported digest.
    Returns the source manifest (artifact id, run id, head sha, digest, dir).
    """
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    resp = session.get(f"https://api.github.com/repos/{repo}/actions/artifacts",
                       params={"name": LEDGER_ARTIFACT, "per_page": 100}, headers=headers, timeout=60)
    resp.raise_for_status()
    found = [a for a in resp.json().get("artifacts", [])
             if a.get("name") == LEDGER_ARTIFACT and not a.get("expired")
             and (a.get("workflow_run") or {}).get("head_branch") == branch]
    found.sort(key=lambda a: a.get("created_at") or "", reverse=True)
    manifest = []
    for art in found[:limit]:
        digest = str(art.get("digest") or "")
        if not digest.startswith("sha256:"):
            raise LedgerLifecycleError(f"artifact {art['id']} has no sha256 digest")
        blob = session.get(art["archive_download_url"], headers=headers, timeout=120)
        blob.raise_for_status()
        if hashlib.sha256(blob.content).hexdigest() != digest.split(":", 1)[1]:
            raise LedgerLifecycleError(f"artifact {art['id']} digest mismatch")
        out = Path(dest) / f"artifact_{art['id']}"
        _safe_extract(blob.content, out)
        manifest.append({"artifact_id": art["id"], "run_id": art["workflow_run"].get("id"),
                         "head_sha": art["workflow_run"].get("head_sha"), "digest": digest,
                         "created_at": art.get("created_at"), "expires_at": art.get("expires_at"),
                         "dir": str(out)})
    return manifest


def _write_disposition(path: Path, record: Mapping[str, Any], disposition: str, detail: str) -> Path:
    body = {"schema": DISPOSITION_SCHEMA, "research_only": True, "event_id": record["event_id"],
            "freeze_record_sha256": record["record_sha256"], "disposition": disposition,
            "detail": detail, "decided_at": _now()}
    doc = {**body, "disposition_sha256": canonical_sha256(body)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def grade_ledger(*, ledger: Path, fetch_scoreboard: Callable[[str], tuple[bytes, str, str]],
                 now: datetime | None = None) -> list[dict]:
    """Grade or dispose every ungraded freeze. ``fetch_scoreboard(day)`` returns
    (body, url, observed_at) or raises when the source is unavailable."""
    now = now or datetime.now(timezone.utc)
    by_event: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(Path(ledger).rglob("*.json")):
        if path.parent.name == "grades":
            continue
        rec = load_research_freeze(path)
        by_event.setdefault(rec["event_id"], []).append((path, rec))
    reports = []
    for items in by_event.values():
        items.sort(key=lambda pr: pr[1]["frozen_at"])
        for i, (path, rec) in enumerate(items):
            gdir = path.parent / "grades"
            gpath = gdir / (path.stem + ".grade.json")
            dpath = gdir / (path.stem + ".disposition.json")
            if gpath.exists() or dpath.exists():
                continue
            binding = rec.get("identity_binding")
            if not binding or not rec.get("scheduled_kickoff"):
                _write_disposition(dpath, rec, "NOT_GRADABLE_IDENTITY_UNBOUND", "freeze has no identity binding")
                reports.append({"freeze": str(path), "status": "DISPOSED", "reason": "IDENTITY_UNBOUND"})
                continue
            kickoff = datetime.fromisoformat(rec["scheduled_kickoff"])
            if now < kickoff + GRADE_AFTER:
                reports.append({"freeze": str(path), "status": "PENDING", "reason": "GAME_NOT_YET_COMPLETE"})
                continue
            terminal = now >= kickoff + TERMINAL_AFTER
            day = kickoff.astimezone(ET).strftime("%Y%m%d")
            try:
                body, url, observed = fetch_scoreboard(day)
            except Exception as exc:  # noqa: BLE001 - source outage is never a grade
                reports.append({"freeze": str(path), "status": "PENDING", "reason": f"SOURCE_UNAVAILABLE {exc}"})
                continue
            espn = [e for e in json.loads(body).get("events", []) if str(e.get("id")) == binding.get("nflverse_espn")]
            if len(espn) != 1:
                if terminal:
                    _write_disposition(dpath, rec, "AUTHORITATIVE_EVENT_NOT_FOUND",
                                       f"ESPN {binding.get('nflverse_espn')} absent on {day} after 7 days")
                    reports.append({"freeze": str(path), "status": "DISPOSED", "reason": "ESPN_EVENT_NOT_FOUND"})
                else:
                    reports.append({"freeze": str(path), "status": "PENDING", "reason": "ESPN_EVENT_NOT_FOUND"})
                continue
            espn_date = datetime.fromisoformat(str(espn[0].get("date")).replace("Z", "+00:00"))
            if espn_date != kickoff:
                # Postponed/rescheduled: the frozen prices were for a different kickoff.
                _write_disposition(dpath, rec, "KICKOFF_CHANGED_NOT_GRADED",
                                   f"ESPN kickoff {espn_date.isoformat()} != frozen {kickoff.isoformat()}")
                reports.append({"freeze": str(path), "status": "DISPOSED", "reason": "KICKOFF_CHANGED"})
                continue
            try:
                outcome = extract_espn_final_outcome(binding, espn[0], source_url=url,
                                                     payload_sha256=hashlib.sha256(body).hexdigest(),
                                                     observed_at=observed)
            except ValueError as exc:
                if terminal:
                    _write_disposition(dpath, rec, "NOT_FINAL_AFTER_7_DAYS", str(exc))
                    reports.append({"freeze": str(path), "status": "DISPOSED", "reason": "NOT_FINAL"})
                else:
                    reports.append({"freeze": str(path), "status": "PENDING", "reason": str(exc)})
                continue
            later = [r for _, r in items[i + 1:] if r.get("market_snapshot")]
            reference = later[-1]["market_snapshot"] if later else None
            grade = grade_research_freeze(rec, outcome, later_snapshot=reference, graded_at=_now())
            write_grade(gpath, grade)
            reports.append({"freeze": str(path), "status": "GRADED", "grade": str(gpath),
                            "grade_sha256": grade["grade_sha256"]})
    return reports
