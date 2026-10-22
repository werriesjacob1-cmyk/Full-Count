#!/usr/bin/env python3
"""FC-NFL-004 live NFL spread/total research pathway: freeze, then grade.

``freeze`` (pre-kickoff):
  FanDuel NFL root -> every future event in the window -> per-event payload ->
  ``normalize_payload`` -> ``seal_game_market_snapshot`` -> nflverse identity
  binding (team aliases + exact ET kickoff) -> strictly-prior B0 -> QB state ->
  ``build_research_freeze`` (B0/F1/M0/C1 distributions, no-vig/EV, gates) ->
  create-only write under the ledger root -> slate artifact (JSON + Markdown).
  Every discovered event is accounted FROZEN or NOT_FROZEN with a reason.

``chain-load`` / ``chain-seal`` (``game_market_research_chain``): load the
anchored, hash-linked rolling ledger (fail closed on missing/expired/tampered
history; explicit bootstrap only), and seal the next monotonic link.
``replay`` / ``grade`` (``game_market_research_lifecycle``): replay ledgers
byte-for-byte into a working ledger, then grade
every ungraded freeze from an explicit ESPN final (or write an explicit
terminal disposition). The movement reference is the latest LATER pre-kickoff
research vintage, which is not a verified closing price.

``verify-slate``: re-derive discovery from the archived FanDuel root bytes and
check every slate invariant.

Research only. Never public, never a pick, never promotes a model.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import CHECKED_AND_FOUND
from nfl.archive.sources import fanduel_nfl
from nfl.normalize.fanduel_game_lines import normalize_payload
from nfl.prospective.game_identity import bind_nflverse_game_identity
from nfl.prospective.game_market_research_freeze import (
    build_research_freeze,
    canonical_sha256,
    qb_state_from_schedule,
    write_research_freeze,
)
from nfl.prospective.game_market_research_chain import (
    ChainError,
    GitHubArtifacts,
    build_anchor,
    load_anchor,
    load_chain,
    seal_chain,
)
from nfl.prospective.game_market_research_lifecycle import (
    ESPN_SCOREBOARD,
    grade_ledger,
    merge_ledgers,
)
from nfl.prospective.game_market_slate import (
    COVERAGE_TOLERANCE_MINUTES,
    DISCOVERY_FAILURE,
    artifact_sha,
    build_discovery,
    reconcile_expected,
    slate_status,
    verify_slate_artifact,
)
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
FANDUEL_KICKOFF_LAG_TOLERANCE_MINUTES = COVERAGE_TOLERANCE_MINUTES
DEFAULT_ANCHOR = Path("data/nfl_game_market_research/LEDGER_ANCHOR.json")
DEFAULT_LEDGER = Path("data/nfl_game_market_research/frozen")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _archive(raw_dir: Path, name: str, body: bytes) -> str:
    """Gzip one raw source response (deterministic gzip header); return its sha256."""
    digest = _sha(body)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{name}_{digest[:12]}.gz"
    if not path.exists():
        path.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
    return digest


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

    root = fanduel_nfl._fetch_first_healthy_host(
        "root_nfl_page", f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={fanduel_nfl.AK}",
        {"feed": "nfl root", "audit": "fc_nfl_004"}, session)
    if root.outcome != CHECKED_AND_FOUND:
        raise SystemExit(f"DISCOVERY_FAILURE: FanDuel root not conclusive: {root.outcome} {root.failure_reason}")
    observed = _parse_iso(root.observed_at)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = Path(raw_dir) if raw_dir else Path(artifact_dir).parent / "raw" / stamp
    _archive(raw_dir, "games_schedule", sched_body)
    _archive(raw_dir, "fanduel_root", root.body)

    discovery = build_discovery(root_body=root.body, root_observed_at=root.observed_at, root_url=root.url,
                                window_days=window_days, discover=fanduel_nfl.discover_events)
    coverage = reconcile_expected(discovery, schedule_rows, TEAM_FULL_TO_ABBR,
                                  tolerance_minutes=FANDUEL_KICKOFF_LAG_TOLERANCE_MINUTES)
    process_ids = coverage["process_event_ids"]
    binding = {
        **{k: discovery[k] for k in ("root_sha256", "root_bytes", "root_observed_at", "root_url",
                                     "discovery_sha256")},
        "process_event_ids": process_ids,
        "process_event_ids_sha256": canonical_sha256(sorted(process_ids)),
    }
    events = {e["event_id"]: e for e in discovery["all_events"]}

    results = []
    for eid in process_ids:
        event = events[eid]
        res = {"event_id": eid, "event_name": event["name"], "open_date": event.get("open_date")}
        try:
            fetched = fanduel_nfl._fetch_first_healthy_host(
                f"event_{eid}_no_tab", f"event-page?eventId={eid}&_ak={fanduel_nfl.AK}",
                {"event_id": eid, "event_name": event["name"], "audit": "fc_nfl_004"}, session)
            if fetched.outcome != CHECKED_AND_FOUND:
                raise ValueError(f"SOURCE_UNAVAILABLE: {fetched.outcome} {fetched.failure_reason}")
            payload_sha = _archive(raw_dir, f"fanduel_event_{eid}", fetched.body)
            normalized = normalize_payload(json.loads(fetched.body), captured_at=fetched.observed_at,
                                           source_payload_sha256=payload_sha,
                                           source_artifact=fetched.artifact, source_url=fetched.url)
            snapshot = seal_game_market_snapshot(
                normalized["candidates"], _failure_rows(eid, normalized), event_id=eid, sealed_at=_now())
            try:
                identity = bind_nflverse_game_identity(
                    snapshot, schedule_rows, team_aliases=TEAM_FULL_TO_ABBR, source_url=CURRENT_GAMES_URL,
                    source_sha256=schedule_source["sha256"], observed_at=sched_obs,
                    kickoff_tolerance_minutes=FANDUEL_KICKOFF_LAG_TOLERANCE_MINUTES)
            except ValueError as exc:
                identity = None
                res["identity_error"] = str(exc)
            prediction = build_live_b0_prediction(schedule_rows, event)
            qb = qb_state_from_schedule(schedule_rows, prediction["game_id"])
            record = build_research_freeze(
                market_snapshot=snapshot, identity_binding=identity, b0_prediction=prediction,
                qb_state=qb, params=params, code_sha=code_sha, schedule_source=schedule_source,
                frozen_at=_now(), discovery=binding)
            path = write_research_freeze(ledger, record)
            res.update({"status": "FROZEN", "path": str(path), "record_sha256": record["record_sha256"],
                        "discovery_sha256": discovery["discovery_sha256"],
                        "summary": _event_summary(record)})
        except Exception as exc:  # noqa: BLE001 - every event must be accounted for
            res.update({"status": "NOT_FROZEN", "reason": f"{type(exc).__name__}: {exc}"})
        results.append(res)

    status = slate_status(len(coverage["expected_games"]), len(process_ids),
                          len(coverage["expected_not_discovered"]))
    body = {
        "schema": "fc-nfl-004-slate-artifact-v2", "research_only": True, "public_eligible": False,
        "generated_at": _now(), "code_sha": code_sha, "params_sha256": params_sha256(params),
        "schedule_source": schedule_source, "raw_dir": str(raw_dir),
        "discovery": discovery, "coverage": coverage, "slate_status": status,
        "discovered_events": len(process_ids), "accounted_events": len(results),
        "frozen_events": sum(r["status"] == "FROZEN" for r in results),
        "events": results,
    }
    if body["discovered_events"] != body["accounted_events"]:
        raise SystemExit("slate accounting invariant failed")
    artifact = {**body, "artifact_sha256": artifact_sha(body)}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"slate_{stamp}.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    (artifact_dir / f"slate_{stamp}.md").write_text(render_markdown(artifact))
    verify_slate_artifact(artifact, root.body, sched_body, fanduel_nfl.discover_events)
    if status == DISCOVERY_FAILURE:
        raise SystemExit(f"DISCOVERY_FAILURE: {len(coverage['expected_games'])} scheduled games, 0 discovered")
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
        f"schedule `{artifact['schedule_source']['sha256'][:12]}` · FanDuel root "
        f"`{artifact['discovery']['root_sha256'][:12]}` observed {artifact['discovery']['root_observed_at']}",
        f"- slate status **{artifact['slate_status']}** · scheduled games in window "
        f"{len(artifact['coverage']['expected_games'])} · processed {artifact['discovered_events']} · "
        f"accounted {artifact['accounted_events']} · frozen {artifact['frozen_events']}",
        *[f"- expected but not discovered: {m['game_id']} ({m['reason']})"
          for m in artifact['coverage']['expected_not_discovered']],
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
def espn_scoreboard_fetcher(session=None):
    session = session or requests.Session()

    def fetch_day(day: str) -> tuple[bytes, str, str]:
        url = ESPN_SCOREBOARD.format(d=day)
        got = fetch("espn_scoreboard", f"scoreboard_{day}", url, context={"audit": "fc_nfl_004"},
                    session=session)
        if got.outcome != CHECKED_AND_FOUND:
            raise RuntimeError(f"{got.outcome} {got.failure_reason}")
        return got.body, url, got.observed_at
    return fetch_day


def _read_maybe_gz(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    return gzip.decompress(raw) if Path(path).suffix == ".gz" else raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("freeze", "grade", "replay", "verify-slate",
                                     "chain-load", "chain-seal", "anchor-build"))
    ap.add_argument("--ledger", type=Path, default=Path(os.environ.get("FC_NFL_LEDGER") or DEFAULT_LEDGER))
    ap.add_argument("--artifact-dir", type=Path, default=Path("data/nfl_game_market_research/slates"))
    ap.add_argument("--raw-dir", type=Path)
    ap.add_argument("--window-days", type=float, default=8.0)
    ap.add_argument("--sources", type=Path, nargs="*", default=[])
    ap.add_argument("--slate", type=Path)
    ap.add_argument("--root", type=Path, help="archived FanDuel root (.gz or raw)")
    ap.add_argument("--schedule", type=Path, help="archived nflverse schedule (.gz or raw)")
    ap.add_argument("--work", type=Path, help="chain working dir (ledger/ + manifest)")
    ap.add_argument("--state", type=Path, help="chain state file written by chain-load")
    ap.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR)
    ap.add_argument("--epoch-id")
    ap.add_argument("--reason")
    args = ap.parse_args()
    if args.mode == "freeze":
        code_sha = str(os.environ.get("FULL_COUNT_CODE_SHA") or "").strip()
        art = freeze_slate(ledger=args.ledger, artifact_dir=args.artifact_dir, code_sha=code_sha,
                           window_days=args.window_days, raw_dir=args.raw_dir)
        print(json.dumps({"slate_status": art["slate_status"], "processed": art["discovered_events"],
                          "frozen": art["frozen_events"], "artifact_sha256": art["artifact_sha256"]}))
    elif args.mode == "grade":
        print(json.dumps(grade_ledger(ledger=args.ledger, fetch_scoreboard=espn_scoreboard_fetcher()), indent=2))
    elif args.mode == "replay":
        print(json.dumps(merge_ledgers(args.sources, args.ledger)))
    elif args.mode == "anchor-build":
        anchor = build_anchor(args.ledger, epoch_id=args.epoch_id, reason=args.reason,
                              created_at=_now(), baseline_path=args.ledger.as_posix())
        args.anchor.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"epoch_id": anchor["epoch_id"], "file_count": anchor["file_count"],
                          "anchor_sha256": anchor["anchor_sha256"]}))
    elif args.mode == "chain-load":
        anchor = load_anchor(args.anchor, Path("."))
        api = GitHubArtifacts(repo=os.environ["GITHUB_REPOSITORY"], token=os.environ["GITHUB_TOKEN"],
                              session=requests.Session())
        try:
            state = load_chain(api=api, anchor=anchor, repo_root=Path("."),
                               branch=os.environ["LEDGER_BRANCH"],
                               current_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                               bootstrap_epoch=os.environ.get("BOOTSTRAP_EPOCH", ""), work=args.work)
        except ChainError as exc:
            print(f"::error::EVIDENCE CHAIN BLOCKED -- {exc}")
            raise SystemExit(2)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print(json.dumps({k: state[k] for k in ("mode", "epoch_id", "sequence", "head_artifact_id")}
                         | {"files": len(state["files"])}))
    elif args.mode == "chain-seal":
        anchor = load_anchor(args.anchor, Path("."))
        state = json.loads(args.state.read_text())
        run = {k: os.environ.get(v) for k, v in (("run_id", "GITHUB_RUN_ID"),
               ("run_attempt", "GITHUB_RUN_ATTEMPT"), ("head_sha", "GITHUB_SHA"),
               ("branch", "LEDGER_BRANCH"))}
        try:
            manifest = seal_chain(state=state, anchor=anchor, work=args.work, run=run)
        except ChainError as exc:
            print(f"::error::EVIDENCE CHAIN SEAL REFUSED -- {exc}")
            raise SystemExit(2)
        print(json.dumps({"sequence": manifest["sequence"], "files": manifest["file_count"],
                          "manifest_sha256": manifest["manifest_sha256"]}))
    else:
        artifact = json.loads(args.slate.read_text())
        print(verify_slate_artifact(artifact, _read_maybe_gz(args.root), _read_maybe_gz(args.schedule),
                                    fanduel_nfl.discover_events))


if __name__ == "__main__":
    main()
