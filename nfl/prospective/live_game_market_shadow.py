#!/usr/bin/env python3
"""Build a full-slate prospective NFL spread/total B0 shadow board.

Live inputs:
- FanDuel NFL root + per-event no-tab payloads for current primary markets.
- Current nflverse/nfldata games.csv only for schedule identity and completed
  prior scoring rows.

Scientific boundary:
B0 is a research control, not a promoted model. For a target 2026 REG game this
runner uses all completed 2025 REG games plus only 2026 REG games from STRICTLY
EARLIER WEEKS. It deliberately ignores any same-week completed game rather than
infer finality from a moving schedule feed. This is slightly conservative but
point-in-time safe for the Sunday slate.

Every discovered pregame event is represented in the output manifest as either
BOARD_BUILT or NO_PLAY. Events never disappear silently.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from nfl.archive.provenance import CHECKED_AND_FOUND
from nfl.archive.sources import fanduel_nfl
from nfl.normalize.fanduel_game_lines import normalize_payload
from nfl.prospective.game_market_shadow_board import build_game_market_shadow_board
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot
from nfl.research.game_market_b0 import build_b0_predictions
from nfl.research.scoring_prior_features import build_prior_scoring_features

CT = ZoneInfo("America/Chicago")
CURRENT_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
TARGET_SEASON = 2026
HISTORY_SEASON = 2025

TEAM_FULL_TO_ABBR = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}

PRIMARY_MARKETS = ("moneyline", "spread", "game_total")


def _parse_iso(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local_date(value):
    dt = _parse_iso(value)
    return dt.astimezone(CT).date().isoformat() if dt else None


def _event_teams(name):
    parts = str(name or "").split(" @ ")
    if len(parts) != 2:
        raise ValueError(f"not an NFL game event name: {name!r}")
    away_full, home_full = (part.strip() for part in parts)
    try:
        away = TEAM_FULL_TO_ABBR[away_full]
        home = TEAM_FULL_TO_ABBR[home_full]
    except KeyError as exc:
        raise ValueError(f"unmapped NFL team name: {exc.args[0]}") from exc
    return away_full, home_full, away, home


def _load_games_csv(body):
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise ValueError("current games.csv is empty")
    required = {
        "game_id", "season", "game_type", "week", "gameday",
        "away_team", "home_team", "away_score", "home_score",
    }
    missing = sorted(required.difference(rows[0].keys()))
    if missing:
        raise ValueError("games.csv missing required columns: " + ",".join(missing))
    return rows


def _int_score(value):
    text = str(value or "").strip()
    if not text:
        return None
    number = float(text)
    if not number.is_integer() or number < 0:
        raise ValueError(f"invalid score {value!r}")
    return int(number)


def build_live_b0_prediction(schedule_rows, event):
    """Return one B0 PREGAME prediction for one exact FanDuel event."""
    away_full, home_full, away, home = _event_teams(event["name"])
    local_date = _local_date(event["open_date"])
    matches = []
    for row in schedule_rows:
        if str(row.get("season")) != str(TARGET_SEASON):
            continue
        if str(row.get("game_type") or "").upper() != "REG":
            continue
        if str(row.get("gameday") or "") != local_date:
            continue
        if str(row.get("away_team") or "").upper() != away:
            continue
        if str(row.get("home_team") or "").upper() != home:
            continue
        matches.append(row)
    if len(matches) != 1:
        raise ValueError(
            f"schedule identity expected exactly one row for {event['name']} "
            f"{local_date}, got {len(matches)}"
        )
    target = matches[0]
    target_week = int(target["week"])

    scoring_rows = []
    for row in schedule_rows:
        game_type = str(row.get("game_type") or "").upper()
        if game_type != "REG":
            continue
        try:
            season = int(row["season"])
            week = int(row["week"])
        except (TypeError, ValueError):
            continue
        include = season == HISTORY_SEASON or (
            season == TARGET_SEASON and week < target_week
        )
        if not include:
            continue
        home_score = _int_score(row.get("home_score"))
        away_score = _int_score(row.get("away_score"))
        if home_score is None or away_score is None:
            continue
        scoring_rows.append({
            "game_id": str(row["game_id"]),
            "season": season,
            "week": week,
            "game_type": "REG",
            "home_team": str(row["home_team"]).upper(),
            "away_team": str(row["away_team"]).upper(),
            "final_status": "FINAL",
            "home_score": home_score,
            "away_score": away_score,
        })

    scoring_rows.append({
        "game_id": str(target["game_id"]),
        "season": TARGET_SEASON,
        "week": target_week,
        "game_type": "REG",
        "home_team": home,
        "away_team": away,
        "final_status": "PREGAME",
        "home_score": None,
        "away_score": None,
    })
    features = build_prior_scoring_features(scoring_rows, rolling_window=5)
    predictions = build_b0_predictions(features, min_prior_games=3)
    target_predictions = [
        row for row in predictions if row["game_id"] == str(target["game_id"])
    ]
    if len(target_predictions) != 1:
        raise ValueError(
            f"expected one B0 prediction for {target['game_id']}, "
            f"got {len(target_predictions)}"
        )
    prediction = dict(target_predictions[0])
    prediction.update({
        "event_id": str(event["event_id"]),
        "home_team_full": home_full,
        "away_team_full": away_full,
        "schedule_local_date": local_date,
        "same_week_completed_games_deliberately_excluded": True,
    })
    return prediction


def _failure_rows(event_id, normalized):
    candidates = normalized["candidates"]
    present = {row["market"] for row in candidates}
    reasons = {}
    for row in normalized["rejections"]:
        market = row.get("market")
        if market in PRIMARY_MARKETS:
            reasons.setdefault(market, []).append(str(row.get("reason") or "REJECTED"))
    failures = []
    for family in PRIMARY_MARKETS:
        if family in present:
            continue
        reason_values = sorted(set(reasons.get(family) or ["PRIMARY_MARKET_NOT_PRESENT"]))
        failures.append({
            "event_id": str(event_id),
            "market": family,
            "reason": "|".join(reason_values),
        })
    return failures


def _manifest_hash(body):
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run(*, target_date, output_dir, code_sha):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Full-Count NFL live game-market shadow"})

    schedule_response = session.get(CURRENT_GAMES_URL, timeout=60)
    schedule_response.raise_for_status()
    schedule_body = schedule_response.content
    schedule_sha = hashlib.sha256(schedule_body).hexdigest()
    schedule_rows = _load_games_csv(schedule_body)
    (output / "games_current.csv").write_bytes(schedule_body)

    root = fanduel_nfl._fetch_first_healthy_host(
        "root_nfl_page",
        f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={fanduel_nfl.AK}",
        {"feed": "nfl root", "audit": "game_market_shadow"},
        session,
    )
    if root.outcome != CHECKED_AND_FOUND:
        raise SystemExit(
            f"FanDuel root not conclusive: {root.outcome} {root.failure_reason}"
        )
    (output / "fanduel_root.json").write_bytes(root.body)
    root_observed = _parse_iso(root.observed_at)
    events = [
        event for event in fanduel_nfl.discover_events(root.body)
        if _local_date(event.get("open_date")) == target_date
        and (_parse_iso(event.get("open_date")) or datetime.min.replace(tzinfo=timezone.utc))
            > root_observed
    ]
    if not events:
        raise SystemExit(f"no future FanDuel NFL events for CT date {target_date}")

    event_results = []
    for event in events:
        event_id = str(event["event_id"])
        result = {
            "event_id": event_id,
            "event_name": event["name"],
            "open_date": event.get("open_date"),
        }
        try:
            fetched = fanduel_nfl._fetch_first_healthy_host(
                f"event_{event_id}_no_tab",
                f"event-page?eventId={event_id}&_ak={fanduel_nfl.AK}",
                {
                    "event_id": event_id,
                    "event_name": event["name"],
                    "open_date": event.get("open_date"),
                    "audit": "game_market_shadow",
                },
                session,
            )
            if fetched.outcome != CHECKED_AND_FOUND:
                raise ValueError(
                    f"event payload not conclusive: {fetched.outcome} "
                    f"{fetched.failure_reason}"
                )
            payload_sha = hashlib.sha256(fetched.body).hexdigest()
            payload_path = output / f"event_{event_id}.json"
            payload_path.write_bytes(fetched.body)
            normalized = normalize_payload(
                json.loads(fetched.body),
                captured_at=fetched.observed_at,
                source_payload_sha256=payload_sha,
                source_artifact=fetched.artifact,
                source_url=fetched.url,
            )
            failures = _failure_rows(event_id, normalized)
            market_sealed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            snapshot = seal_game_market_snapshot(
                normalized["candidates"],
                failures,
                event_id=event_id,
                sealed_at=market_sealed_at,
            )
            prediction = build_live_b0_prediction(schedule_rows, event)
            board_sealed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            board = build_game_market_shadow_board(
                snapshot,
                prediction,
                model_code_sha=code_sha,
                source_vintage=f"nfldata-master:{schedule_sha}",
                sealed_at=board_sealed_at,
            )
            (output / f"market_snapshot_{event_id}.json").write_text(
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (output / f"prediction_{event_id}.json").write_text(
                json.dumps(prediction, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (output / f"shadow_board_{event_id}.json").write_text(
                json.dumps(board, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result.update({
                "status": "BOARD_BUILT",
                "game_id": prediction["game_id"],
                "market_snapshot_sha256": snapshot["snapshot_sha256"],
                "board_sha256": board["board_sha256"],
                "decisions": {
                    row["market"]: row["decision_status"]
                    for row in board["records"]
                },
            })
        except Exception as exc:
            result.update({
                "status": "NO_PLAY",
                "reason": f"{type(exc).__name__}: {exc}",
            })
        event_results.append(result)

    body = {
        "schema_version": 1,
        "sport": "NFL",
        "evidence_class": "PROSPECTIVE_SHADOW_FULL_SLATE",
        "research_only": True,
        "public_eligible": False,
        "target_local_date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "code_sha": code_sha,
        "schedule_source_url": CURRENT_GAMES_URL,
        "schedule_source_sha256": schedule_sha,
        "discovered_event_count": len(events),
        "accounted_event_count": len(event_results),
        "events": sorted(event_results, key=lambda row: row["event_id"]),
    }
    if body["accounted_event_count"] != body["discovered_event_count"]:
        raise SystemExit("full-slate accounting invariant failed")
    manifest = {**body, "manifest_sha256": _manifest_hash(body)}
    (output / "slate_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main():
    override = str(os.environ.get("TARGET_LOCAL_DATE") or "").strip()
    if override:
        target_date = override
    else:
        today = datetime.now(CT).date()
        days_until_sunday = (6 - today.weekday()) % 7
        target_date = (today + timedelta(days=days_until_sunday)).isoformat()
    output_dir = os.environ.get("OUTPUT_DIR") or "/tmp/nfl-game-market-shadow"
    code_sha = str(os.environ.get("FULL_COUNT_CODE_SHA") or "UNKNOWN").strip()
    manifest = run(
        target_date=target_date,
        output_dir=output_dir,
        code_sha=code_sha,
    )
    print(json.dumps({
        "target_local_date": manifest["target_local_date"],
        "discovered_event_count": manifest["discovered_event_count"],
        "accounted_event_count": manifest["accounted_event_count"],
        "board_built": sum(
            row["status"] == "BOARD_BUILT" for row in manifest["events"]
        ),
        "no_play": sum(
            row["status"] == "NO_PLAY" for row in manifest["events"]
        ),
        "manifest_sha256": manifest["manifest_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
