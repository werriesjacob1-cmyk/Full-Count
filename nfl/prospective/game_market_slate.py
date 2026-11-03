"""Slate discovery provenance and coverage invariant for NFL game-market freezes.

The FanDuel root response that discovered a slate is archived and hashed, and
every frozen record binds that digest. Coverage is checked against an
independent expectation: the nflverse schedule games whose kickoff falls in the
same window. The resulting slate status is explicit:

- COMPLETE: every expected game was discovered and every discovery accounted;
- PARTIAL_COVERAGE: some expected games were not offered/discovered (listed);
- NO_EVENTS: nothing expected and nothing discovered (explicit, not success);
- DISCOVERY_FAILURE: games expected but none discovered -> caller must fail.

Research only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
COMPLETE = "COMPLETE"
PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
NO_EVENTS = "NO_EVENTS"
DISCOVERY_FAILURE = "DISCOVERY_FAILURE"
# Live FanDuel NFL marketTime trails the scheduled kickoff by one minute.
COVERAGE_TOLERANCE_MINUTES = 2


class GameMarketSlateError(ValueError):
    """Raised when slate provenance cannot be verified."""


def _sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _instant(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def build_discovery(
    *,
    root_body: bytes,
    root_observed_at: str,
    root_url: str,
    window_days: float,
    discover: Callable[[bytes], list[dict]],
) -> dict[str, Any]:
    """Re-derivable discovery: every event in the root, and the window subset."""
    observed = _instant(root_observed_at)
    if observed is None:
        raise GameMarketSlateError("root_observed_at must be timezone-aware ISO-8601")
    if not window_days > 0:
        raise GameMarketSlateError("window_days must be positive")
    horizon = observed + timedelta(days=window_days)
    events = sorted(
        ({"event_id": str(e["event_id"]), "name": e.get("name"), "open_date": e.get("open_date")}
         for e in discover(root_body)),
        key=lambda e: e["event_id"])
    window = [e for e in events
              if (k := _instant(e["open_date"])) is not None and observed < k <= horizon]
    ids = sorted(e["event_id"] for e in window)
    body = {
        "root_sha256": hashlib.sha256(root_body).hexdigest(),
        "root_bytes": len(root_body),
        "root_observed_at": root_observed_at,
        "root_url": root_url,
        "window_days": window_days,
        "window_end": horizon.isoformat(),
        "all_events": events,
        "window_event_ids": ids,
        "window_event_ids_sha256": _sha(ids),
    }
    return {**body, "discovery_sha256": _sha(body)}


def _schedule_kickoff(row: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.strptime(
            f"{str(row.get('gameday') or '').strip()} {str(row.get('gametime') or '').strip()}",
            "%Y-%m-%d %H:%M").replace(tzinfo=ET).astimezone(timezone.utc)
    except ValueError:
        return None


def reconcile_expected(
    discovery: Mapping[str, Any],
    schedule_rows: Sequence[Mapping[str, Any]],
    team_full_to_abbr: Mapping[str, str],
    *,
    tolerance_minutes: int,
) -> dict[str, Any]:
    """Match unplayed schedule games kicking off in the window to discovered events.

    Matching uses the same rule as identity binding: same away/home team and a
    FanDuel instant trailing the schedule kickoff by 0..tolerance minutes. All
    root events are searched, so a +1 minute boundary shift cannot hide a game.
    """
    start = _instant(discovery["root_observed_at"])
    end = _instant(discovery["window_end"])
    by_teams: dict[tuple[str, str], list[dict]] = {}
    for ev in discovery["all_events"]:
        parts = str(ev.get("name") or "").split(" @ ")
        if len(parts) != 2:
            continue
        away, home = (team_full_to_abbr.get(p.strip()) for p in parts)
        if away and home:
            by_teams.setdefault((away, home), []).append(ev)

    expected, missing, matched_ids = [], [], set()
    for row in schedule_rows:
        kick = _schedule_kickoff(row)
        if kick is None or not (start < kick <= end):
            continue
        if str(row.get("home_score") or "").strip():
            continue  # already has a score: not an upcoming game
        away, home = str(row.get("away_team") or "").upper(), str(row.get("home_team") or "").upper()
        hits = [ev for ev in by_teams.get((away, home), [])
                if (t := _instant(ev.get("open_date"))) is not None
                and 0 <= (t - kick).total_seconds() <= tolerance_minutes * 60]
        entry = {"game_id": row.get("game_id"), "kickoff": kick.isoformat(),
                 "event_ids": sorted(e["event_id"] for e in hits)}
        expected.append(entry)
        if len(hits) == 1:
            matched_ids.add(hits[0]["event_id"])
        else:
            missing.append({**entry, "reason": "NOT_DISCOVERED" if not hits else "AMBIGUOUS_DISCOVERY"})
    window = set(discovery["window_event_ids"])
    return {
        "expected_games": sorted(expected, key=lambda e: e["game_id"]),
        "expected_not_discovered": sorted(missing, key=lambda e: e["game_id"]),
        "discovered_not_expected": sorted(window - matched_ids),
        "process_event_ids": sorted(window | matched_ids),
    }


def slate_status(expected_count: int, discovered_count: int, missing_count: int) -> str:
    if expected_count == 0 and discovered_count == 0:
        return NO_EVENTS
    if discovered_count == 0:
        return DISCOVERY_FAILURE
    return PARTIAL_COVERAGE if missing_count else COMPLETE


def verify_slate_artifact(
    artifact: Mapping[str, Any],
    root_body: bytes,
    schedule_body: bytes,
    discover: Callable[[bytes], list[dict]],
) -> str:
    """Re-derive the WHOLE slate from archived source bytes and check every invariant.

    Codex P1 (review 6129802534): trusting ``coverage`` from the artifact let a
    copy with emptied ``expected_games``/``expected_not_discovered`` and a
    recomputed self-hash pass. Discovery is rebuilt from the archived FanDuel
    root bytes and coverage from the hash-verified archived schedule bytes; the
    artifact's own coverage, status and counts must equal the rebuilt ones.
    The self-hash is checked too but is never sufficient on its own.
    """
    from nfl.prospective.live_game_market_shadow import TEAM_FULL_TO_ABBR, _load_games_csv

    body = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    if _sha(body) != artifact.get("artifact_sha256"):
        raise GameMarketSlateError("slate artifact hash mismatch")
    d = artifact["discovery"]
    rebuilt = build_discovery(root_body=root_body, root_observed_at=d["root_observed_at"],
                              root_url=d["root_url"], window_days=d["window_days"], discover=discover)
    if rebuilt != d:
        raise GameMarketSlateError("discovery does not re-derive from archived root bytes")
    sched = artifact.get("schedule_source") or {}
    if hashlib.sha256(schedule_body).hexdigest() != sched.get("sha256") or len(schedule_body) != sched.get("bytes"):
        raise GameMarketSlateError("archived schedule bytes do not match the bound schedule digest")
    coverage = reconcile_expected(rebuilt, _load_games_csv(schedule_body), TEAM_FULL_TO_ABBR,
                                  tolerance_minutes=COVERAGE_TOLERANCE_MINUTES)
    if artifact.get("coverage") != coverage:
        raise GameMarketSlateError("coverage does not re-derive from archived schedule and root bytes")
    process = coverage["process_event_ids"]
    accounted = sorted(e["event_id"] for e in artifact["events"])
    if accounted != sorted(process) or len(set(accounted)) != len(accounted):
        raise GameMarketSlateError("accounted events differ from the discovery processing set")
    if any(e["status"] not in ("FROZEN", "NOT_FROZEN") for e in artifact["events"]):
        raise GameMarketSlateError("unaccounted event state")
    expected = slate_status(len(coverage["expected_games"]), len(process),
                            len(coverage["expected_not_discovered"]))
    if artifact["slate_status"] != expected:
        raise GameMarketSlateError("slate status inconsistent with re-derived coverage")
    counts = {"discovered_events": len(process), "accounted_events": len(artifact["events"]),
              "frozen_events": sum(e["status"] == "FROZEN" for e in artifact["events"])}
    for key, value in counts.items():
        if key in artifact and artifact[key] != value:
            raise GameMarketSlateError(f"{key} inconsistent with accounted events")
    for e in artifact["events"]:
        if e["status"] == "FROZEN" and e.get("discovery_sha256") != d["discovery_sha256"]:
            raise GameMarketSlateError("frozen event not bound to this discovery")
    return artifact["artifact_sha256"]


def artifact_sha(body: Mapping[str, Any]) -> str:
    return _sha(body)
