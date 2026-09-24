"""Manual, isolated real-source receptions pricing capture. Never publishes picks.

Run python -m nfl.research.price_aware_offer_capture --output NEW_DIRECTORY.
Uses the existing B0 demo's pinned history recipe without changing live B0.
All records remain unavailable for wagering: no same-day inactive certification
or jurisdiction-specific book action rules are asserted by this runner.
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import requests

from nfl.archive.provenance import CHECKED_AND_FOUND, utcnow
from nfl.archive.sources import fanduel_nfl as fd
from nfl.normalize.player_prop_markets import normalize_payload
from nfl.normalize.player_prop_roster_binding import bind_player_prop_candidate, _event_teams
from nfl.prospective import receptions_challenger_live_demo as demo
from nfl.research.price_aware_offers import frozen_distribution, evaluate_offer, write_evidence, _hash, _time
from nfl.research.receptions_shadow import current_b0_projection, score_shadow_candidate

ROOT = Path(__file__).resolve().parents[2]
GAME = "2026_03_ATL_GB"
SCHEDULE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ROSTER = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_2026.csv"


def download(url: str, dest: Path) -> dict:
    with urlopen(Request(url, headers={"User-Agent": "FullCount-Research/1.0"}), timeout=60) as response:
        body = response.read()
    observed = utcnow()
    with dest.open("xb") as handle:
        handle.write(body)
    return {"url": url, "observed_at": observed, "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "file": dest.name}


def read_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def canonical_game(rows: list[dict], game_id: str) -> dict:
    matches = [r for r in rows if r.get("game_id") == game_id]
    if len(matches) != 1:
        raise ValueError("schedule identity not unique")
    row = matches[0]
    if (row["season"], row["week"], row["away_team"], row["home_team"], row["gameday"]) != (
            "2026", "3", "ATL", "GB", "2026-09-24"):
        raise ValueError("unexpected schedule binding")
    local = datetime.fromisoformat(row["gameday"]+"T"+row["gametime"]).replace(
        tzinfo=ZoneInfo("America/New_York"))
    return {"game_id": game_id, "away_team": row["away_team"], "home_team": row["home_team"],
            "local_date": row["gameday"], "kickoff": local.astimezone(timezone.utc).isoformat(),
            "source_row": row}


def verify_event(event: dict, game: dict) -> None:
    if _event_teams(event["name"]) != (game["away_team"], game["home_team"]):
        raise ValueError("book/schedule teams mismatch")
    if abs((_time(event["open_date"])-_time(game["kickoff"])).total_seconds()) > 300:
        raise ValueError("book/schedule kickoff mismatch exceeds five minutes")


def market_state(raw: dict) -> str:
    if raw.get("marketStatus") == "SUSPENDED":
        return "SUSPENDED"
    if raw.get("marketStatus") != "OPEN" or raw.get("inPlay") is not False:
        return "IDENTITY_UNRESOLVED"
    return "AVAILABLE"


def strict_observed_prices(candidate: dict, market: dict) -> bool:
    """Normalizer uses int(); reject truncation/coercion against original bytes."""
    fields = (("over_selection_id", "over_odds"), ("under_selection_id", "under_odds")) if candidate["shape"] == "primary" else (("selection_id", "yes_odds"),)
    for sid, price in fields:
        matches = [r for r in market.get("runners", []) if str(r.get("selectionId")) == candidate[sid]]
        if len(matches) != 1 or matches[0].get("runnerStatus") != "ACTIVE":
            return False
        observed = matches[0].get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOddsInt")
        if type(observed) is not int or observed != candidate[price] or abs(observed) < 100:
            return False
    return True


def capture(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    cache = output / "raw"
    cache.mkdir()
    manifest, records, census, rejected = [], [], {}, []
    report = {"schema_version": 1, "research_only": True, "bettable": False,
              "canonical_game_id": GAME, "started_at": utcnow(), "records": records,
              "sources": manifest, "rejections": rejected,
              "limitations": ["UNKNOWN_GAME_COVERAGE", "CURRENT_2026_ROLE_NOT_MODELED",
                             "BOOK_ACTION_RULES_NOT_CERTIFIED", "NO_MODEL_PROMOTION",
                             "ONE_BOOK_ONLY", "QUOTE_ORIGIN_TIMESTAMP_NOT_PROVIDED"]}
    try:
        manifest.append(download(SCHEDULE, cache/"schedule.csv"))
        game = canonical_game(read_csv(cache/"schedule.csv"), GAME)
        report["game"] = game
        manifest.append(download(ROSTER, cache/"roster.csv"))
        roster = read_csv(cache/"roster.csv")
        if not roster or not {"gsis_id", "full_name", "season", "team", "position"} <= roster[0].keys():
            raise ValueError("invalid roster schema")
        audit = json.loads((ROOT/"engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json").read_text())
        pins = {r["season"]: r for r in audit["seasons"]}
        history_manifest = []
        for season in demo.HISTORY_SEASONS:
            url = audit["source"]["canonical_asset_template"].format(season=season)
            entry = download(url, cache/f"stats_player_week_{season}.csv")
            manifest.append(entry)
            if entry["sha256"] != pins[season]["sha256"] or entry["bytes"] != pins[season]["bytes"]:
                raise ValueError(f"history pin changed: {season}")
            history_manifest.append(entry)
        # Existing authoritative manual connection, single-threaded CLI only.
        old_cache = demo.HISTORY_CACHE_DIR
        try:
            demo.HISTORY_CACHE_DIR = cache
            prior = demo._load_prior_by_player_2025()
            residuals = demo._load_pooled_residuals()
        finally:
            demo.HISTORY_CACHE_DIR = old_cache
        report["history_contract"] = {"recipe": "existing receptions_challenger_live_demo",
            "prior_season": 2025, "residual_n": len(residuals), "residual_sha256": _hash(residuals),
            "source_sha256": _hash(history_manifest), "sources": history_manifest,
            "limitation": "2025 priors match frozen live comparator; 2026 current usage is not modeled"}
        report["residuals"] = residuals
        with requests.Session() as session:
            root = fd._fetch_first_healthy_host("root", f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={fd.AK}", {}, session)
            fetched = [root]
            archive_source(root, output, manifest)
            if root.outcome != CHECKED_AND_FOUND:
                raise ValueError("SOURCE_FAILED: sportsbook root")
            matches = []
            for event in fd.discover_events(root.body):
                try:
                    verify_event(event, game)
                    matches.append(event)
                except ValueError:
                    pass
            if len(matches) != 1:
                raise ValueError("IDENTITY_UNRESOLVED: book/schedule event is not unique")
            event = matches[0]
            report["book_event"] = event
            eid = event["event_id"]
            baseline = fd._fetch_first_healthy_host("baseline", f"event-page?eventId={eid}&_ak={fd.AK}", {}, session)
            archive_source(baseline, output, manifest)
            fetched.append(baseline)
            baseline_ids = fd._market_ids(baseline.body)
            for tab in fd._tab_slugs_for_event(baseline.body):
                source = fd._fetch_first_healthy_host(tab, f"event-page?eventId={eid}&tab={tab}&_ak={fd.AK}", {"tab": tab}, session)
                fd._classify_tab_payload(source, baseline_ids)
                archive_source(source, output, manifest)
                fetched.append(source)
        candidates = {}
        for source in fetched[1:]:
            if source.outcome != CHECKED_AND_FOUND:
                rejected.append({"source": source.artifact, "reason": source.outcome})
                continue
            payload = json.loads(source.body)
            normalized = normalize_payload(payload, captured_at=source.observed_at)
            rejected.extend(normalized["rejections"])
            markets = payload["attachments"]["markets"]
            by_id = {str(m.get("marketId", k)): m for k,m in markets.items()}
            for mid, market in by_id.items():
                if str(market.get("eventId")) != str(eid):
                    continue
                census[mid] = {"market_id": mid, "type": market.get("marketType"), "name": market.get("marketName"),
                    "availability": market_state(market), "runner_count": len(market.get("runners", [])),
                    "source": source.artifact, "captured_at": source.observed_at}
            for c in normalized["candidates"]:
                if c["market"] not in {"receptions", "receptions_alt"} or c["event_id"] != str(eid):
                    continue
                verify_event({"name": c["event_name"], "open_date": c["event_open_date"]}, game)
                raw_market = by_id[c["market_id"]]
                if not strict_observed_prices(c, raw_market):
                    rejected.append({"candidate": c, "reason": "INVALID_RAW_PRICE_OR_SELECTION"})
                    continue
                bound = bind_player_prop_candidate(c, roster, season=2026)
                bound.update(canonical_game_id=GAME, canonical_kickoff=game["kickoff"],
                             market_availability=market_state(raw_market),
                             quote_timestamp=None, quote_timestamp_status="NOT_PROVIDED",
                             availability_status="UNKNOWN_GAME_COVERAGE", decision_status="QUARANTINED")
                key = (c["market_id"], c.get("selection_id", "PAIR"))
                candidates[key] = (bound, source)
        for bound, source in candidates.values():
            try:
                if bound["binding_status"] != "BOUND":
                    raise ValueError("IDENTITY_UNRESOLVED")
                history = prior.get(bound["gsis_id"], [])
                projection = current_b0_projection(history)
                generated = utcnow()
                dist = frozen_distribution(projection=projection["projection"], event_id=bound["event_id"],
                    gsis_id=bound["gsis_id"], canonical_game_id=GAME,
                    feature_cutoff=generated,
                    source_available_at=max(s["observed_at"] for s in history_manifest),
                    generated_at=generated, history_sha256=_hash(history_manifest))
                record = evaluate_offer(bound, dist, as_of=generated,
                                        raw_source_sha256=hashlib.sha256(source.body).hexdigest())
                record["history_rows_used"] = history[-5:]
                record["b0_projection"] = projection
                if bound["shape"] == "primary" and bound["line"] % 1 == .5:
                    record["authoritative_b0_comparator"] = score_shadow_candidate(
                        projection=projection["projection"], line=bound["line"],
                        over_odds=bound["over_odds"], under_odds=bound["under_odds"], residuals=residuals)
                record["record_sha256"] = _hash({k:v for k,v in record.items() if k != "record_sha256"})
                records.append(record)
            except (KeyError, ValueError, TypeError) as exc:
                rejected.append({"candidate": bound, "decision_status": "NO_PLAY", "reason": str(exc)})
        report["market_census"] = list(census.values())
        report["status"] = "QUARANTINED_RESEARCH_CAPTURE" if records else "NO_PLAY"
        report["counts"] = dict(Counter(r["decision_status"] for r in records))
        report["normalization_support"] = "Receptions priced; other observed families archived only by this runner"
        report["code_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), ROOT/"nfl/research/price_aware_offers.py",
                      Path(demo.__file__), ROOT/"nfl/research/receptions_shadow.py",
                      ROOT/"nfl/research/receptions_frozen_challenger.py")}
    except Exception as exc:
        report["status"] = "NO_PLAY"
        report["failure"] = f"{type(exc).__name__}: {exc}"
    report["completed_at"] = utcnow()
    report["snapshot_sha256"] = _hash(report)
    write_evidence(output/"snapshot.json", report)
    return report


def archive_source(source, output: Path, manifest: list) -> None:
    entry = source.manifest_entry()
    # Lossless self-contained book bytes, small enough to commit for review.
    if source.body is not None:
        filename = source.artifact + ".raw.json"
        write_evidence(output/filename, {"encoding": "gzip+base64", "sha256": entry["sha256"],
            "data": base64.b64encode(gzip.compress(source.body, mtime=0)).decode()})
        entry["raw_file"] = filename
    manifest.append(entry)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = capture(args.output)
    print(json.dumps({k: result.get(k) for k in ("status", "failure", "counts", "snapshot_sha256")}))

