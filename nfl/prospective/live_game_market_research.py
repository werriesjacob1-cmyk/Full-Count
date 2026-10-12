#!/usr/bin/env python3
"""FC-NFL-004 live NFL spread/total research pathway: freeze, then grade.

``freeze`` (pre-kickoff):
  FanDuel NFL root -> every future event in the window -> per-event payload ->
  ``normalize_payload`` -> ``seal_game_market_snapshot`` -> nflverse identity
  binding (team aliases + exact ET kickoff) -> strictly-prior B0 -> QB state ->
  ``build_research_freeze`` (B0/F1/M0/C1 distributions, no-vig/EV, gates) ->
  create-only write under the ledger root -> slate artifact (JSON + Markdown).
  Every discovered event is accounted FROZEN or NOT_FROZEN with a reason.

``grade`` (post-game):
  every ungraded freeze -> ESPN scoreboard -> ``extract_espn_final_outcome``
  (explicit STATUS_FINAL, bound to the same identity hash) ->
  ``grade_research_freeze`` with the latest LATER freeze of the same event as
  the closing reference -> create-only grade document.

Research only. Never public, never a pick, never promotes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import CHECKED_AND_FOUND
from nfl.archive.sources import fanduel_nfl
from nfl.normalize.fanduel_game_lines import normalize_payload
from nfl.prospective.espn_final_outcome import extract_espn_final_outcome
from nfl.prospective.game_identity import bind_nflverse_game_identity
from nfl.prospective.game_market_research_freeze import (
    build_research_freeze,
    load_research_freeze,
    qb_state_from_schedule,
    write_research_freeze,
)
from nfl.prospective.game_market_research_grade import grade_research_freeze, write_grade
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot
from nfl.prospective.live_game_market_shadow import (
    CURRENT_GAMES_URL,
    TEAM_FULL_TO_ABBR,
    _failure_rows,
    _load_games_csv,
    _parse_iso,
    build_live_b0_prediction,
)
from nfl.research.game_market_models import load_params, params_sha256

ET = ZoneInfo("America/New_York")
# Live FanDuel NFL marketTime trails the scheduled kickoff by one minute.
FANDUEL_KICKOFF_LAG_TOLERANCE_MINUTES = 2
DEFAULT_LEDGER = Path("data/nfl_game_market_research/frozen")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def freeze_slate(*, ledger: Path, artifact_dir: Path, code_sha: str, window_days: float,
                 raw_dir: Path | None = None) -> dict:
    params = load_params()
    session = requests.Session()
    session.headers.update({"User-Agent": "Full-Count NFL game-market research"})
    sched = session.get(CURRENT_GAMES_URL, timeout=60)
    sched.raise_for_status()
    sched_body = sched.content
    sched_obs = _now()
    schedule_rows = _load_games_csv(sched_body)
    schedule_source = {"url": CURRENT_GAMES_URL, "sha256": _sha(sched_body),
                       "bytes": len(sched_body), "observed_at": sched_obs}
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"games_{schedule_source['sha256'][:12]}.csv").write_bytes(sched_body)

    root = fanduel_nfl._fetch_first_healthy_host(
        "root_nfl_page", f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={fanduel_nfl.AK}",
        {"feed": "nfl root", "audit": "fc_nfl_004"}, session)
    if root.outcome != CHECKED_AND_FOUND:
        raise SystemExit(f"FanDuel root not conclusive: {root.outcome} {root.failure_reason}")
    observed = _parse_iso(root.observed_at)
    horizon = observed + timedelta(days=window_days)
    events = [e for e in fanduel_nfl.discover_events(root.body)
              if (k := _parse_iso(e.get("open_date"))) is not None and observed < k <= horizon]

    results = []
    for event in sorted(events, key=lambda e: (e.get("open_date"), str(e["event_id"]))):
        eid = str(event["event_id"])
        res = {"event_id": eid, "event_name": event["name"], "open_date": event.get("open_date")}
        try:
            fetched = fanduel_nfl._fetch_first_healthy_host(
                f"event_{eid}_no_tab", f"event-page?eventId={eid}&_ak={fanduel_nfl.AK}",
                {"event_id": eid, "event_name": event["name"], "audit": "fc_nfl_004"}, session)
            if fetched.outcome != CHECKED_AND_FOUND:
                raise ValueError(f"SOURCE_UNAVAILABLE: {fetched.outcome} {fetched.failure_reason}")
            payload_sha = _sha(fetched.body)
            if raw_dir:
                (raw_dir / f"fanduel_event_{eid}_{payload_sha[:12]}.json").write_bytes(fetched.body)
            normalized = normalize_payload(json.loads(fetched.body), captured_at=fetched.observed_at,
                                           source_payload_sha256=payload_sha,
                                           source_artifact=fetched.artifact, source_url=fetched.url)
            snapshot = seal_game_market_snapshot(
                normalized["candidates"], _failure_rows(eid, normalized), event_id=eid, sealed_at=_now())
            try:
                binding = bind_nflverse_game_identity(
                    snapshot, schedule_rows, team_aliases=TEAM_FULL_TO_ABBR, source_url=CURRENT_GAMES_URL,
                    source_sha256=schedule_source["sha256"], observed_at=sched_obs,
                    kickoff_tolerance_minutes=FANDUEL_KICKOFF_LAG_TOLERANCE_MINUTES)
            except ValueError as exc:
                binding = None
                res["identity_error"] = str(exc)
            prediction = build_live_b0_prediction(schedule_rows, event)
            qb = qb_state_from_schedule(schedule_rows, prediction["game_id"])
            record = build_research_freeze(
                market_snapshot=snapshot, identity_binding=binding, b0_prediction=prediction,
                qb_state=qb, params=params, code_sha=code_sha, schedule_source=schedule_source,
                frozen_at=_now())
            path = write_research_freeze(ledger, record)
            res.update({"status": "FROZEN", "path": str(path), "record_sha256": record["record_sha256"],
                        "summary": _event_summary(record)})
        except Exception as exc:  # noqa: BLE001 - every event must be accounted for
            res.update({"status": "NOT_FROZEN", "reason": f"{type(exc).__name__}: {exc}"})
        results.append(res)

    body = {
        "schema": "fc-nfl-004-slate-artifact-v1", "research_only": True, "public_eligible": False,
        "generated_at": _now(), "code_sha": code_sha, "params_sha256": params_sha256(params),
        "fanduel_root_observed_at": root.observed_at, "schedule_source": schedule_source,
        "window_days": window_days, "discovered_events": len(events), "accounted_events": len(results),
        "events": results,
    }
    if body["discovered_events"] != body["accounted_events"]:
        raise SystemExit("slate accounting invariant failed")
    artifact = {**body, "artifact_sha256": _sha(json.dumps(body, sort_keys=True).encode())}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    (artifact_dir / f"slate_{stamp}.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    (artifact_dir / f"slate_{stamp}.md").write_text(render_markdown(artifact))
    return artifact


def _event_summary(record: dict) -> dict:
    cs = record["config_summaries"]
    pick = lambda c, k: (cs.get(c) or {}).get(k)  # noqa: E731
    sels = []
    for s in record["selections"]:
        gi = s.get("grading_identity") or {}
        m = s.get("models") or {}
        sels.append({
            "market": s["market"], "side": gi.get("side"), "line": gi.get("line"),
            "odds": gi.get("american_odds"), "no_vig": s.get("no_vig_probability"),
            "C1_p": (m.get("C1") or {}).get("p_win_conditional"), "C1_ev": (m.get("C1") or {}).get("ev_per_unit"),
            "F1_p": (m.get("F1") or {}).get("p_win_conditional"), "F1_ev": (m.get("F1") or {}).get("ev_per_unit"),
            "decision": s["decision"], "reasons": s["reasons"], "selection_key": s.get("selection_key"),
        })
    binding = record.get("identity_binding") or {}
    return {
        "game_id": binding.get("nflverse_game_id"), "kickoff": record.get("scheduled_kickoff"),
        "frozen_at": record["frozen_at"],
        "fair_home_spread": {c: pick(c, "fair_home_spread") for c in ("F1", "M0", "C1")},
        "fair_total": {c: pick(c, "fair_total") for c in ("F1", "M0", "C1")},
        "fair_home_win_probability": {c: pick(c, "fair_home_win_probability") for c in ("F1", "M0", "C1")},
        "qb_state": {s: record["input_state"]["qb_state"].get(s, {}).get("status") for s in ("home", "away")},
        "selections": sels,
    }


def render_markdown(artifact: dict) -> str:
    lines = [
        f"# NFL spread/total research slate — {artifact['generated_at']}",
        "",
        "RESEARCH ONLY. Not picks. Not public. `RESEARCH_LEAN` is a measurement label.",
        "",
        f"- code `{artifact['code_sha']}` · params `{artifact['params_sha256'][:12]}` · "
        f"schedule `{artifact['schedule_source']['sha256'][:12]}` · FanDuel root observed "
        f"{artifact['fanduel_root_observed_at']}",
        f"- events discovered {artifact['discovered_events']}, accounted {artifact['accounted_events']}",
        "",
        "| Game | Kickoff (UTC) | Fair home spread F1 / C1 | Fair total F1 / C1 | Market | Side | Line | Odds | No-vig | C1 p | C1 EV | F1 p | Decision / reasons |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    leans = 0
    for ev in artifact["events"]:
        if ev["status"] != "FROZEN":
            lines.append(f"| {ev['event_name']} | {ev.get('open_date')} | | | | | | | | | | | NOT_FROZEN: {ev['reason']} |")
            continue
        s = ev["summary"]
        for i, sel in enumerate(x for x in s["selections"] if x["market"] in ("spread", "game_total")):
            head = (f"{s['game_id']} | {s['kickoff']} | {s['fair_home_spread']['F1']} / {s['fair_home_spread']['C1']} | "
                    f"{s['fair_total']['F1']} / {s['fair_total']['C1']}") if i == 0 else " | | | "
            fmt = lambda v: "" if v is None else f"{v:.3f}"  # noqa: E731
            leans += sel["decision"] == "RESEARCH_LEAN"
            lines.append(
                f"| {head} | {sel['market']} | {sel['side']} | {sel['line']} | {sel['odds']} | {fmt(sel['no_vig'])} | "
                f"{fmt(sel['C1_p'])} | {fmt(sel['C1_ev'])} | {fmt(sel['F1_p'])} | {sel['decision']}: "
                f"{', '.join(sel['reasons']) or '-'} |")
    lines += ["", f"Research leans: **{leans}**. Zero is a valid result; gates are never loosened.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def grade_ledger(*, ledger: Path, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    session = requests.Session()
    by_event: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(Path(ledger).rglob("*.json")):
        if path.parent.name == "grades":
            continue
        rec = load_research_freeze(path)
        by_event.setdefault(rec["event_id"], []).append((path, rec))
    reports = []
    for event_id, items in by_event.items():
        items.sort(key=lambda pr: pr[1]["frozen_at"])
        for i, (path, rec) in enumerate(items):
            gpath = path.parent / "grades" / (path.stem + ".grade.json")
            if gpath.exists():
                continue
            binding = rec.get("identity_binding")
            if not binding or not rec.get("scheduled_kickoff"):
                reports.append({"freeze": str(path), "status": "NOT_GRADABLE", "reason": "IDENTITY_UNBOUND"})
                continue
            kickoff = datetime.fromisoformat(rec["scheduled_kickoff"])
            if now < kickoff + timedelta(hours=3):
                reports.append({"freeze": str(path), "status": "PENDING", "reason": "GAME_NOT_YET_COMPLETE"})
                continue
            day = kickoff.astimezone(ET).strftime("%Y%m%d")
            url = ESPN_SCOREBOARD.format(d=day)
            got = fetch("espn_scoreboard", f"scoreboard_{day}", url, context={"audit": "fc_nfl_004"}, session=session)
            if got.outcome != CHECKED_AND_FOUND:
                reports.append({"freeze": str(path), "status": "PENDING", "reason": f"SOURCE_UNAVAILABLE {got.outcome}"})
                continue
            espn_events = [e for e in json.loads(got.body).get("events", []) if str(e.get("id")) == binding.get("nflverse_espn")]
            if len(espn_events) != 1:
                reports.append({"freeze": str(path), "status": "PENDING", "reason": "ESPN_EVENT_NOT_FOUND"})
                continue
            try:
                outcome = extract_espn_final_outcome(binding, espn_events[0], source_url=url,
                                                     payload_sha256=_sha(got.body), observed_at=got.observed_at)
            except ValueError as exc:
                reports.append({"freeze": str(path), "status": "PENDING", "reason": str(exc)})
                continue
            later = [r for _, r in items[i + 1:]]
            closing = later[-1]["market_snapshot"] if later else None
            grade = grade_research_freeze(rec, outcome, closing_snapshot=closing, graded_at=_now())
            write_grade(gpath, grade)
            reports.append({"freeze": str(path), "status": "GRADED", "grade": str(gpath),
                            "grade_sha256": grade["grade_sha256"]})
    return reports


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("freeze", "grade"))
    ap.add_argument("--ledger", type=Path, default=Path(os.environ.get("FC_NFL_LEDGER") or DEFAULT_LEDGER))
    ap.add_argument("--artifact-dir", type=Path, default=Path("data/nfl_game_market_research/slates"))
    ap.add_argument("--raw-dir", type=Path)
    ap.add_argument("--window-days", type=float, default=8.0)
    args = ap.parse_args()
    if args.mode == "freeze":
        code_sha = str(os.environ.get("FULL_COUNT_CODE_SHA") or "").strip()
        art = freeze_slate(ledger=args.ledger, artifact_dir=args.artifact_dir, code_sha=code_sha,
                           window_days=args.window_days, raw_dir=args.raw_dir)
        print(json.dumps({"discovered": art["discovered_events"], "frozen": sum(e["status"] == "FROZEN" for e in art["events"]),
                          "artifact_sha256": art["artifact_sha256"]}))
    else:
        print(json.dumps(grade_ledger(ledger=args.ledger), indent=2))


if __name__ == "__main__":
    main()
