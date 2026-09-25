"""Workstream C live builder: F1/F5/F6/F7/F10 feature rows for a target week.

Usage (repository root):
  PYTHONPATH=. python3 -m nfl.research.tier1.team_context_live \\
      --season 2026 --week 3 --out /tmp/claude-0/nfl_tier1_c/live_2026_w03

Every row carries a real `information_cutoff`: the latest availability time
of any source it used (FanDuel capture time, MOS retrieval time, and the
mtime of the shared nflverse files, which is when this environment received
them). A row is only emitted for a game whose kickoff is after that cutoff.

F1 live = FanDuel primary spread/total captured now through the existing
`nfl.archive.sources.fanduel_nfl` fetcher and `nfl.normalize.fanduel_game_lines`
normalizer (raw payload SHA-256 kept). It is market input:
``uses_market_input=True`` -- never independent evidence of value against
FanDuel's own player prices.

F6 live weather = the SAME definition as history (GFS MOS 12Z run of the day
before the ET game day). A run not yet issued is NOT replaced by another run;
the row says NOT_YET_ISSUED with the time a re-run will succeed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nfl.research.tier1 import harness
from nfl.research.tier1 import team_context_challenger as tcc
from nfl.research.tier1.contract import UNKNOWN, validate_feature_row
from nfl.research.tier1.team_context_data import (POS_FIELDS, STADIUMS, TEAM_GAME_FIELDS,
                                                  kickoff_utc, load_schedule, player_positions,
                                                  read_csv, summarize_pbp, team_game_context)
from nfl.research.tier1.team_context_features import (contract_rows, defense_prior_features,
                                                      team_prior_features)
from nfl.research.tier1.team_context_weather import (STATION_BY_TITLE, fetch_mos, mos_features,
                                                     mos_runtime_for)

SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
WORK = Path("/tmp/claude-0/nfl_tier1_c")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def capture_fanduel_lines(out_dir: Path, games: list[dict]) -> dict[str, Any]:
    """One live FanDuel capture: (game_id, team) -> F1 features with captured_at."""
    import requests

    from nfl.archive.provenance import CHECKED_AND_FOUND
    from nfl.archive.sources import fanduel_nfl
    from nfl.normalize.fanduel_game_lines import normalize_payload
    from nfl.prospective.live_game_market_shadow import TEAM_FULL_TO_ABBR

    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Full-Count NFL tier1 team-context research"})
    root = fanduel_nfl._fetch_first_healthy_host(
        "root_nfl_page", f"content-managed-page?page=CUSTOM&customPageId=nfl&_ak={fanduel_nfl.AK}",
        {"feed": "nfl root", "audit": "tier1_team_context"}, session)
    result: dict[str, Any] = {"root_outcome": root.outcome, "events": [], "lines": {}}
    if root.outcome != CHECKED_AND_FOUND:
        result["root_failure"] = root.failure_reason
        return result
    (out_dir / "fanduel_root.json").write_bytes(root.body)
    result["root_sha256"] = hashlib.sha256(root.body).hexdigest()
    result["root_observed_at"] = root.observed_at
    by_teams = {(g["away_team"], g["home_team"]): g for g in games}
    for event in fanduel_nfl.discover_events(root.body):
        parts = event["name"].split(" @ ")
        try:
            away, home = TEAM_FULL_TO_ABBR[parts[0].strip()], TEAM_FULL_TO_ABBR[parts[1].strip()]
        except (KeyError, IndexError):
            continue
        game = by_teams.get((away, home))
        if game is None:
            continue
        rec: dict[str, Any] = {"event_id": str(event["event_id"]), "game_id": game["game_id"],
                               "name": event["name"], "open_date": event.get("open_date")}
        fetched = fanduel_nfl._fetch_first_healthy_host(
            f"event_{event['event_id']}_no_tab", f"event-page?eventId={event['event_id']}&_ak={fanduel_nfl.AK}",
            {"event_id": event["event_id"], "audit": "tier1_team_context"}, session)
        if fetched.outcome != CHECKED_AND_FOUND:
            rec["status"] = f"FETCH_{fetched.outcome}"
            result["events"].append(rec)
            continue
        sha = hashlib.sha256(fetched.body).hexdigest()
        (out_dir / f"event_{event['event_id']}.json").write_bytes(fetched.body)
        norm = normalize_payload(json.loads(fetched.body), captured_at=fetched.observed_at,
                                 source_payload_sha256=sha, source_artifact=fetched.artifact,
                                 source_url=fetched.url)
        by_market = {c["market"]: c for c in norm["candidates"]}
        spread, total = by_market.get("spread"), by_market.get("game_total")
        rec.update({"payload_sha256": sha, "captured_at": fetched.observed_at,
                    "markets": sorted(by_market)})
        if spread is None or total is None:
            rec["status"] = "SPREAD_OR_TOTAL_NOT_NORMALIZED"
            rec["rejections"] = norm.get("rejections", [])[:6]
            result["events"].append(rec)
            continue
        home_margin = -float(spread["home_line"])  # FanDuel handicap: negative = favourite
        tot = float(total.get("line", total.get("over_line", 0.0)) or 0.0)
        if tot <= 0:
            rec["status"] = "TOTAL_LINE_MISSING"
            result["events"].append(rec)
            continue
        rec["status"] = "CAPTURED"
        rec.update({"home_margin": home_margin, "total": tot})
        for team, margin in ((home, home_margin), (away, -home_margin)):
            result["lines"][f"{game['game_id']}|{team}"] = {
                "f1_team_margin": margin, "f1_total": tot,
                "f1_implied_team_total": tot / 2.0 + margin / 2.0,
                "f1_label": "LIVE_FANDUEL_PREGAME_TIMESTAMPED",
                "f1_captured_at": fetched.observed_at, "f1_payload_sha256": sha}
        result["events"].append(rec)
    return result


def live_weather(games: list[dict], context, now: datetime, cache: Path) -> dict[str, Any]:
    out = {}
    for g in games:
        title = STADIUMS.get(g["stadium"], (None,))[0]
        roof = context[(g["game_id"], g["home_team"])]["f6_roof_type"]
        if roof != "outdoors":
            out[g["game_id"]] = {"status": "NOT_OUTDOORS", "roof_type": roof}
            continue
        station = STATION_BY_TITLE.get(title)
        if station is None:
            out[g["game_id"]] = {"status": "NO_MOS_STATION", "stadium": title}
            continue
        runtime = mos_runtime_for(g["gameday"])
        issue = datetime.strptime(runtime, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) + timedelta(hours=5)
        if issue > now:
            out[g["game_id"]] = {"status": "NOT_YET_ISSUED", "station": station, "runtime": runtime,
                                 "rerun_after_utc": _iso(issue)}
            continue
        meta = fetch_mos(station, runtime, cache)
        feats = mos_features(meta.get("rows", []), kickoff_utc(g["gameday"], g["gametime"]), runtime)
        out[g["game_id"]] = {"status": "OK" if feats["f6_fcst_wind_kt"] != UNKNOWN else "MOS_NOT_COVERING_KICKOFF",
                             "station": station, "raw_sha256": meta.get("raw_sha256"),
                             "retrieved_at": meta.get("retrieved_at"), **feats}
    return out


PARAMS_PATH = Path(__file__).resolve().parents[3] / (
    "engineering/nfl_tier1_team_context_20260924/team_context_dev_params.json")
LIVE_CONFIGS = ("VOLUME_BASE", "F10", "ALL_NO_MARKET", "ALL")


def live_player_predictions(season: int, week: int, week_games: list[dict], context, tf, dfeat,
                            team_games: list[dict], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Research-only team_context_challenger outputs for players on week-`week` teams.

    B0 = mean of the last five role appearances (min three) through the
    latest completed week -- the harness rule. hist_y = mean team volume in
    those window games. F1 in the live ctx is the timestamped FanDuel line
    substituted for the closing line the model was fitted on (flagged).
    """
    rows, _prov = harness.load_player_weeks(Path("/tmp/claude-0/nflverse_cache"),
                                            Path(__file__).resolve().parents[3] / "engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json",
                                            first_season=season - 1,
                                            current_season_csv=SHARED / "stats_player_week_2026.csv")
    if any(r["season"] == season and r["week"] >= week for r in rows):
        raise SystemExit("weekly stats contain the target week: not a pre-game build")
    positions = player_positions(rows)
    team_actual = {(g["game_id"], g["team"]): g for g in team_games}
    opp_of = {}
    for g in week_games:
        opp_of[g["home_team"]] = (g["game_id"], g["away_team"])
        opp_of[g["away_team"]] = (g["game_id"], g["home_team"])
    out = []
    combos = {"+".join(c) or "VOLUME_BASE": c for r in range(5)
              for c in __import__("itertools").combinations(tcc.TEAM_FACTORS, r)}
    for market in ("passing_yards", "receptions", "receiving_yards"):
        actual_fn, role_fn = harness.MARKETS[market]
        target = tcc.MARKET_TEAM_TARGET[market]
        hist: dict[str, list] = {}
        last_team: dict[str, tuple] = {}
        for r in rows:
            if role_fn(r) > 0:
                hist.setdefault(r["player_id"], []).append((actual_fn(r), r["game_id"], norm(r["team"])))
                last_team[r["player_id"]] = (r["season"], norm(r["team"]), r)
        vpred = {}
        for name, coef in params["volume_coefficients"][target].items():
            vpred[name] = {}
            for team, (game_id, _opp) in opp_of.items():
                e, _why = tcc.predict_volume(context[(game_id, team)], tf[(game_id, team)], target,
                                             combos[name], coef)
                vpred[name][(game_id, team)] = e
        k = params["scale_k"][market]
        for pid, window in hist.items():
            s_last, team, raw = last_team[pid]
            window = window[-5:]
            if s_last != season or team not in opp_of or len(window) < 3:
                continue
            game_id, opp = opp_of[team]
            b0 = sum(a for a, _g, _t in window) / len(window)
            vals = [team_actual.get((g, t), {}).get(target) for _a, g, t in window]
            key = (season, week, game_id, pid)
            fake = [{"season": season, "week": week, "game_id": game_id, "player_id": pid, "team": team,
                     "opponent_team": opp, "position": raw["position"], "b0": b0, "actual": 0.0}]
            recs = tcc.player_inputs(fake, {key: [(g, t) for _a, g, t in window]}, market,
                                     {k2: {target: v[target]} for k2, v in team_actual.items()},
                                     vpred, dfeat, positions)
            rec = recs[0]
            entry = {"market": market, "player_id": pid, "player_name": raw["player_name"],
                     "position": positions.get(pid, raw["position"]), "team": team, "opponent": opp,
                     "game_id": game_id, "b0": b0, "scale_k": k,
                     "hist_team_volume": rec["hist_y"], "configs": {}}
            for config in LIVE_CONFIGS:
                p = params["exponents"][market][config]
                preds, fb = tcc.predict_config([rec], config, p, k)
                att = tcc.attribution_rows([rec], config, p)[key]
                entry["configs"][config] = {"prediction": preds[key], "fallback": fb[key],
                                            "uses_market_input": tcc.uses_market_input(config),
                                            "log_contrib": {f: round(v, 5) for f, v in att.items()}}
            out.append(entry)
    return out


def norm(team: str) -> str:
    from nfl.research.tier1.team_context_data import norm_team
    return norm_team(team)


def build(season: int, week: int, out: Path, *, capture: bool = True) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out.mkdir(parents=True, exist_ok=True)
    games_path = SHARED / "schedules/games.csv"
    schedule = load_schedule(games_path)
    coords = json.loads((WORK / "stadiums/stadium_coordinates.json").read_text())
    context = team_game_context(schedule, coords["table"])
    week_games = [g for g in schedule if g["season"] == season and g["week"] == week and g["game_type"] == "REG"]

    # strictly-prior team/defense history from the cached PBP summaries (built by evaluate_team_context)
    team_games, pos_rows, pbp_sources = [], [], {}
    for s in range(2016, season + 1):
        path = SHARED / f"pbp/play_by_play_{s}.csv.gz"
        tag = harness.sha256_file(path)
        tpath, ppath = WORK / f"cache/team_{s}_{tag[:16]}.csv", WORK / f"cache/pos_{s}_{tag[:16]}.csv"
        if not tpath.exists():
            raise SystemExit(f"missing PBP summary cache {tpath}; run evaluate_team_context.py first")
        team_games += read_csv(tpath, TEAM_GAME_FIELDS)
        pos_rows += read_csv(ppath, POS_FIELDS)
        pbp_sources[path.name] = {"sha256": tag, "received_at": _iso(_mtime(path))}
    newest_week = max((g["week"] for g in team_games if g["season"] == season), default=0)
    if newest_week >= week:
        raise SystemExit("PBP contains the target week: not a pre-game build")
    targets = [(g["game_id"], t, season, week) for g in week_games for t in (g["home_team"], g["away_team"])]
    tf = team_prior_features(team_games, targets)
    dfeat = defense_prior_features(team_games, pos_rows, targets)

    fd = capture_fanduel_lines(out / "fanduel", week_games) if capture else {"lines": {}, "events": []}
    weather = live_weather(week_games, context, now, WORK / "mos")
    f1_live = {tuple(k.split("|")): v for k, v in fd["lines"].items()}
    f6_weather = {}
    for g in week_games:
        w = weather[g["game_id"]]
        for t in (g["home_team"], g["away_team"]):
            f6_weather[(g["game_id"], t)] = {
                "f6_weather_status": w["status"],
                "f6_fcst_wind_kt": w.get("f6_fcst_wind_kt", UNKNOWN),
                "f6_fcst_pop6": w.get("f6_fcst_pop6", UNKNOWN),
                "f6_fcst_temp_f": w.get("f6_fcst_temp_f", UNKNOWN),
                "f6_fcst_runtime": w.get("runtime", w.get("f6_fcst_runtime", UNKNOWN)) or UNKNOWN}
            context[(g["game_id"], t)].update({k: v for k, v in f6_weather[(g["game_id"], t)].items()})

    rows, manifest = [], []
    source_ids = {
        "F1": ["fanduel_nfl:event-page" if fd["lines"] else "fanduel_nfl:not_captured"],
        "F5": sorted(f"nflfastR:{k}" for k in pbp_sources),
        "F6": ["nflverse:games.csv:roof,surface", "iem:gfs_mos"],
        "F7": ["nflverse:games.csv:rest,gameday,stadium", "wikipedia:coordinates"],
        "F10": sorted(f"nflfastR:{k}" for k in pbp_sources) + ["nflverse:weekly_stats:position"]}
    base_times = [_mtime(games_path)] + [datetime.fromisoformat(v["received_at"].replace("Z", "+00:00"))
                                         for v in pbp_sources.values()]
    for g in week_games:
        ko = kickoff_utc(g["gameday"], g["gametime"])
        times = list(base_times)
        for t in (g["home_team"], g["away_team"]):
            line = f1_live.get((g["game_id"], t))
            if line:
                times.append(datetime.fromisoformat(line["f1_captured_at"].replace("Z", "+00:00")))
        w = weather[g["game_id"]]
        if w.get("retrieved_at"):
            times.append(datetime.fromisoformat(w["retrieved_at"].replace("Z", "+00:00")))
        cutoff = _iso(max(times))
        status = "EMITTED" if max(times) < ko else "KICKOFF_PASSED_NOT_EMITTED"
        manifest.append({"game_id": g["game_id"], "kickoff_utc": _iso(ko), "information_cutoff": cutoff,
                         "status": status, "weather": w.get("status"),
                         "f1_live": bool(f1_live.get((g["game_id"], g["home_team"])))})
        if status != "EMITTED":
            continue
        game_rows = contract_rows(context, tf, dfeat, [(g["game_id"], g["home_team"]), (g["game_id"], g["away_team"])],
                                  source_ids=source_ids, information_cutoff=cutoff,
                                  f1_override={k: {kk: vv for kk, vv in v.items() if kk not in ("f1_captured_at",)}
                                               for k, v in f1_live.items()},
                                  f6_weather=f6_weather)
        for r in game_rows:
            validate_feature_row(r, prediction_cutoff=_iso(ko))
        rows += game_rows

    # player-level research outputs with the frozen DEV params (F1 = live line substituted)
    player_preds, params_sha = [], None
    if PARAMS_PATH.exists():
        params = json.loads(PARAMS_PATH.read_text())
        params_sha = harness.sha256_file(PARAMS_PATH)
        for (game_id, team), line in f1_live.items():
            context[(game_id, team)]["f1_implied_team_total_closing"] = line["f1_implied_team_total"]
            context[(game_id, team)]["f1_team_margin_closing"] = line["f1_team_margin"]
        emitted = [g for g, m in zip(week_games, manifest) if m["status"] == "EMITTED"]
        player_preds = live_player_predictions(season, week, emitted, context, tf, dfeat, team_games, params)

    body = {"schema_version": 1, "workstream": "C", "research_only": True, "public_eligible": False,
            "target_season": season, "target_week": week, "generated_at": _iso(now),
            "pbp_sources": pbp_sources, "games_csv_sha256": harness.sha256_file(games_path),
            "stadium_coordinates_raw_sha256": coords["raw_sha256"],
            "fanduel": {k: v for k, v in fd.items() if k != "lines"}, "weather": weather,
            "games": manifest, "rows": rows,
            "player_predictions": {"params_sha256": params_sha,
                                   "note": ("research-only; F1 uses the live FanDuel line in place of the "
                                            "closing line the volume model was fitted on; configs with "
                                            "uses_market_input=True are not independent of FanDuel prices"),
                                   "rows": player_preds}}
    text = json.dumps(body, indent=1, sort_keys=True, default=str)
    (out / f"team_context_live_{season}_w{week:02d}.json").write_text(text + "\n")
    body["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=3)
    ap.add_argument("--out", type=Path, default=WORK / "live_2026_w03")
    ap.add_argument("--no-capture", action="store_true")
    args = ap.parse_args()
    body = build(args.season, args.week, args.out, capture=not args.no_capture)
    print(json.dumps({"sha256": body["sha256"], "rows": len(body["rows"]), "games": body["games"],
                      "fanduel_events": [(e["game_id"], e.get("status")) for e in body["fanduel"]["events"]]},
                     indent=1))


if __name__ == "__main__":
    main()
