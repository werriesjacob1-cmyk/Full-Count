#!/usr/bin/env python3
"""Build the public-safe NFL research-shadow website payload.

This is a one-way projection from a sealed prospective shadow artifact into a
small browser payload. It does not score, grade, mutate eligibility, or decide
what is a public pick. The source artifact remains the scientific evidence.

Safety invariants:
- only prospective NFL shadow artifacts are accepted;
- a validated public selector must remain false;
- only SHADOW_ONLY / QUARANTINED decision states are admitted;
- outcome/settlement fields are forbidden in the source records;
- output is an explicit whitelist (never a pass-through of the evidence row);
- the original snapshot SHA/code SHA/source vintage are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_ANALYSIS = "NFL_LIVE_PASSING_YARDS_SHADOW_BOARD_AUDIT"
EXPECTED_STATUS = "RESEARCH_ONLY_NO_PUBLICATION"
ALLOWED_DECISIONS = {"SHADOW_ONLY", "QUARANTINED"}
FORBIDDEN_OUTCOME_FIELDS = {
    "actual", "actual_value", "result", "grade", "hit", "miss", "push",
    "settled", "settlement", "outcome", "won", "lost",
}


def _strict_utc(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is missing")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO-8601: {text!r}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{label} must carry a timezone")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _opponent(row: dict) -> str:
    team = str(row.get("team") or "").strip().upper()
    away = str(row.get("event_away_team") or "").strip().upper()
    home = str(row.get("event_home_team") or "").strip().upper()
    if not team or not away or not home or away == home or team not in (away, home):
        raise ValueError(
            f"cannot derive opponent for {row.get('player_name')!r}: "
            f"team={team!r}, away={away!r}, home={home!r}"
        )
    return home if team == away else away


def _public_record(row: dict) -> dict:
    overlap = FORBIDDEN_OUTCOME_FIELDS.intersection(row)
    if overlap:
        raise ValueError(
            f"prospective source row {row.get('observation_id')!r} contains outcome fields: "
            f"{sorted(overlap)}"
        )
    decision = row.get("decision_status")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"unsupported decision_status {decision!r}")
    if row.get("market") != "passing_yards":
        raise ValueError(f"unexpected market {row.get('market')!r}")
    if row.get("binding_status") != "BOUND":
        raise ValueError("website records must already have exact durable identity binding")

    reasons = sorted({str(x) for x in (row.get("quarantine_reasons") or []) if str(x)})
    if decision == "SHADOW_ONLY" and reasons:
        raise ValueError("SHADOW_ONLY row cannot carry quarantine reasons")
    if decision == "QUARANTINED" and not reasons:
        raise ValueError("QUARANTINED row must explain why it is quarantined")

    return {
        "id": str(row.get("observation_id") or ""),
        "event_id": str(row.get("event_id") or ""),
        "event_name": str(row.get("event_name") or ""),
        "kickoff_at": _strict_utc(row.get("event_open_date"), "event_open_date"),
        "team": str(row.get("team") or "").strip().upper(),
        "opponent": _opponent(row),
        "player_name": str(row.get("player_name") or "").strip(),
        "gsis_id": str(row.get("gsis_id") or "").strip(),
        "market": "passing_yards",
        "line": row.get("line"),
        "over_odds": row.get("over_odds"),
        "under_odds": row.get("under_odds"),
        "market_fair_over_probability": row.get("market_fair_over_probability"),
        "market_fair_under_probability": row.get("market_fair_under_probability"),
        "market_hold": row.get("market_hold"),
        "model_name": str(row.get("model_name") or ""),
        "model_projection": row.get("model_projection"),
        "model_history_n": row.get("model_history_n"),
        "model_over_probability": row.get("model_over_probability"),
        "model_under_probability": row.get("model_under_probability"),
        "line_gap": row.get("line_gap"),
        "research_direction": row.get("research_direction"),
        "research_edge": row.get("research_edge"),
        "decision_status": decision,
        "quarantine_reasons": reasons,
        "availability_status": row.get("availability_status"),
        "role_continuity_status": row.get("role_continuity_status"),
        "captured_at": _strict_utc(row.get("captured_at"), "captured_at"),
        "source": str(row.get("source") or ""),
    }


def build_public_payload(board: dict, *, source_board_sha256: str, source_run_id=None, publisher_code_sha=None) -> dict:
    if board.get("analysis") != EXPECTED_ANALYSIS:
        raise ValueError(f"unexpected analysis {board.get('analysis')!r}")
    if board.get("status") != EXPECTED_STATUS:
        raise ValueError(f"unexpected board status {board.get('status')!r}")
    if (board.get("model") or {}).get("public_selector_validated") is not False:
        raise ValueError("refusing website projection after public_selector_validated changed")

    snapshot = board.get("snapshot") or {}
    if snapshot.get("sport") != "NFL":
        raise ValueError(f"unexpected snapshot sport {snapshot.get('sport')!r}")
    if snapshot.get("record_count") != len(snapshot.get("records") or []):
        raise ValueError("snapshot record_count does not match records")
    if not snapshot.get("snapshot_sha256"):
        raise ValueError("snapshot is missing its evidence seal")
    if snapshot.get("code_sha") != board.get("code_sha"):
        raise ValueError("board and sealed snapshot disagree on code SHA")

    records = [_public_record(row) for row in snapshot.get("records") or []]
    ids = [row["id"] for row in records]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("website payload requires unique non-empty observation ids")
    records.sort(key=lambda row: (row["kickoff_at"], row["event_id"], row["team"], row["player_name"]))

    counts = Counter(row["decision_status"] for row in records)
    quarantine_counts = Counter(
        reason for row in records for reason in row["quarantine_reasons"]
    )
    created_at = _strict_utc(board.get("created_at"), "created_at")
    source_vintage = _strict_utc(snapshot.get("source_vintage"), "source_vintage")
    return {
        "schema_version": 1,
        "sport": "NFL",
        "surface": "prospective_research_shadow",
        "publication_status": "RESEARCH_ONLY_NOT_PUBLIC_PICKS",
        "slate_date": str(snapshot.get("slate_date") or board.get("target_local_date") or ""),
        "created_at": created_at,
        "source_vintage": source_vintage,
        "code_sha": str(board.get("code_sha") or ""),
        "snapshot_sha256": str(snapshot.get("snapshot_sha256") or ""),
        "source_board_sha256": source_board_sha256,\n        "source_run_id": source_run_id,\n        "publisher_code_sha": publisher_code_sha,
        "model": {
            "name": (board.get("model") or {}).get("name"),
            "public_selector_validated": False,
            "decision_surface": (board.get("model") or {}).get("decision_surface"),
            "historical_benchmark_check": (board.get("model") or {}).get("historical_benchmark_check"),
            "pooled_residual_n": (board.get("model") or {}).get("pooled_residual_n"),
        },
        "source_health": {
            "pregame_target_events": (board.get("fan_duel") or {}).get("pregame_target_events"),
            "primary_candidates": (board.get("fan_duel") or {}).get("normalized_primary_candidates"),
            "market_failure_count": len((board.get("fan_duel") or {}).get("market_failures") or []),
            "binding_status_counts": (board.get("identity") or {}).get("binding_status_counts") or {},
            "excluded_unbound_count": (board.get("identity") or {}).get("excluded_unbound_count"),
            "bound_inactive_reports": (board.get("availability") or {}).get("bound_inactive_reports"),
            "inactive_report_failure_count": len((board.get("availability") or {}).get("report_failures") or []),
        },
        "summary": {
            "games": len({row["event_id"] for row in records}),
            "candidates": len(records),
            "shadow_only": counts.get("SHADOW_ONLY", 0),
            "quarantined": counts.get("QUARANTINED", 0),
            "quarantine_reasons": dict(sorted(quarantine_counts.items())),
        },
        "records": records,
        "disclaimer": (
            "Prospective research shadow only. These rows are not published picks, "
            "the selector is not validated, and quarantined rows are explicitly ineligible."
        ),
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="sealed shadow board JSON")
    parser.add_argument("--output", required=True, help="public-safe data.json path")\n    parser.add_argument("--source-run-id", default=None)\n    parser.add_argument("--publisher-code-sha", default=None)
    args = parser.parse_args()

    raw = Path(args.input).read_bytes()
    try:
        board = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"shadow board JSON is unreadable: {exc}") from exc
    payload = build_public_payload(\n        board,\n        source_board_sha256=_sha256_bytes(raw),\n        source_run_id=args.source_run_id,\n        publisher_code_sha=args.publisher_code_sha,\n    )
    atomic_json(Path(args.output), payload)
    print(json.dumps({
        "output": args.output,
        "slate_date": payload["slate_date"],
        "games": payload["summary"]["games"],
        "candidates": payload["summary"]["candidates"],
        "shadow_only": payload["summary"]["shadow_only"],
        "quarantined": payload["summary"]["quarantined"],
        "snapshot_sha256": payload["snapshot_sha256"],
        "source_board_sha256": payload["source_board_sha256"],\n        "source_run_id": payload["source_run_id"],\n        "publisher_code_sha": payload["publisher_code_sha"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
