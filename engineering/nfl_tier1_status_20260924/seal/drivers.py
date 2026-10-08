"""Per-workstream seal drivers. Each runs in a detached worktree of that
workstream's FROZEN commit (cwd + PYTHONPATH = worktree), so every prediction
comes from the frozen code and frozen parameters. Nothing here refits or
changes a parameter; it only calls the frozen live builders and stores the
protocol's primary challenger/comparator predictions directly.

    python3 drivers.py {B|C|D|B0} --season S --week W --games g1,g2 --out file.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def drive_b(season, week, games, out_dir):
    from nfl.research.tier1 import player_opportunity_challenger as C
    from nfl.research.tier1 import player_opportunity_evaluate as E
    from nfl.research.tier1 import player_opportunity_live as L
    params_path = E.OUT_DIR / "frozen_params.json"
    params = json.loads(params_path.read_text())
    body = L.build_live(target_season=season, target_week=week, params=params, paths=dict(E.DEFAULTS))
    raw = out_dir / "ws_b_live.json"
    raw.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n")
    f3 = {r["gsis_id"]: r["features"] for r in body["feature_rows"]
          if r["factor_id"] == "F3_TARGET_AIR_YARDS" and r["game_id"] in games}
    rows = []
    for p in body["research_predictions"]:
        if p["game_id"] not in games or p["market"] not in ("receiving_yards", "receptions"):
            continue
        m = p["market"]
        est = C.f3_estimate(f3[p["gsis_id"]], m, params["F3"]) if p["gsis_id"] in f3 else None
        base = params["k"][m] * p["b0"]
        if _num(est):
            w = params["F3"]["w_" + m]
            pred, fb = w * est + (1 - w) * base, None
        else:
            pred, fb = base, "F3_UNKNOWN_TO_SCALE_BASE"
        rows.append({"hypothesis": "H1" if m == "receiving_yards" else "H1_SECONDARY_receptions",
                     "game_id": p["game_id"], "gsis_id": p["gsis_id"], "team": p["team"], "market": m,
                     "b0": p["b0"], "challenger": "C-F3 (F3 only)", "challenger_prediction": pred,
                     "comparator": "scale_control k*B0", "comparator_prediction": base,
                     "fallback": fb, "exploratory_challenger_all": p["challenger_all"]})
    return {"params_file": str(params_path), "params_sha256": _sha(params_path), "raw_output": raw.name,
            "information_cutoffs": body["information_cutoffs"],
            "information_cutoff_basis": body["information_cutoff_basis"],
            "final_report_team_weeks": body["final_report_team_weeks"],
            "source_sha256": body["source_sha256"], "rows": rows}


def drive_c(season, week, games, out_dir, capture):
    from nfl.research.tier1 import team_context_live as L
    L.LIVE_CONFIGS = tuple(L.LIVE_CONFIGS) + ("F6",)   # store F6-alone directly (frozen exponents)
    try:
        body = L.build(season, week, out_dir / "ws_c", capture=capture)
        capture_note = "FANDUEL_CAPTURED" if capture else "NO_CAPTURE_REQUESTED"
    except Exception as exc:  # F1 is exploratory; never let a capture failure block the seal
        body = L.build(season, week, out_dir / "ws_c", capture=False)
        capture_note = f"FANDUEL_CAPTURE_FAILED: {type(exc).__name__}: {exc}"[:300]
    raw = out_dir / "ws_c" / f"team_context_live_{season}_w{week:02d}.json"
    weather = {g: body["weather"].get(g) for g in games}
    rows = []
    for p in body["player_predictions"]["rows"]:
        if p["game_id"] not in games or p["market"] not in ("receiving_yards", "receptions", "passing_yards"):
            continue
        f6, vb = p["configs"]["F6"], p["configs"]["VOLUME_BASE"]
        rows.append({"hypothesis": "H2" if p["market"] == "receiving_yards" else f"H2_SECONDARY_{p['market']}",
                     "game_id": p["game_id"], "gsis_id": p["player_id"], "player_name": p["player_name"],
                     "team": p["team"], "market": p["market"], "b0": p["b0"],
                     "challenger": "C-F6 (F6 alone)", "challenger_prediction": f6["prediction"],
                     "comparator": "VOLUME_BASE", "comparator_prediction": vb["prediction"],
                     "scale_control": p["scale_k"] * p["b0"], "fallback": f6["fallback"],
                     "f6_log_contrib": f6["log_contrib"],
                     "exploratory": {c: p["configs"][c]["prediction"] for c in ("F10", "ALL_NO_MARKET", "ALL")}})
    return {"params_sha256": body["player_predictions"]["params_sha256"], "raw_output": str(raw.relative_to(out_dir)),
            "raw_sha256": body["sha256"], "pbp_sources": body["pbp_sources"],
            "games_csv_sha256": body["games_csv_sha256"], "fanduel_capture": capture_note,
            "games_manifest": [g for g in body["games"] if g["game_id"] in games],
            "weather": weather, "rows": rows}



def _kickoffs(games_csv: Path, season: int, week: int) -> dict[str, str]:
    import csv
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    out = {}
    with games_csv.open(encoding="utf-8") as fh:
        for g in csv.DictReader(fh):
            if int(g["season"]) == season and int(g["week"]) == week:
                ko = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(
                    tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
                out[g["game_id"]] = ko.isoformat().replace("+00:00", "Z")
    return out


def drive_d(season, week, games, out_dir):
    from nfl.research.tier1 import contract as C
    from nfl.research.tier1 import touchdown_consumer as TC
    from nfl.research.tier1 import touchdown_evaluate as TE
    from nfl.research.tier1 import touchdown_features as TF
    data = TE.load(argparse.Namespace(**TE.DEFAULTS))
    kick = _kickoffs(Path(TE.DEFAULTS["games"]), season, week)
    names = {r["player_id"]: r["player_name"] for r in data["hist_rows"]}
    rows = []
    for gid in sorted(games):
        _s, _w, away, home = gid.split("_")
        reqs = TF.live_requests(data["hist_rows"], target_season=season, target_week=week,
                                game_id=gid, teams=(away, home))
        feats, _diag = TF.build_features(reqs, data["hist_rows"], data["parsed"],
                                         information_cutoff=TE.LIVE_INFORMATION_CUTOFF)
        for key, row in sorted(feats.items(), key=lambda kv: (kv[1]["team"], kv[0][3])):
            C.validate_feature_row(row, prediction_cutoff=kick[gid])
            out = {"hypothesis": "H3", "game_id": gid, "gsis_id": key[3], "player_name": names.get(key[3], ""),
                   "team": row["team"], "market": "anytime_td",
                   "challenger": f"C-F4 ({TC.PRIMARY})", "comparator": "volume_only", "features": row["features"]}
            for variant, field in ((TC.PRIMARY, "challenger"), ("volume_only", "comparator")):
                lam = TC.raw_lambda(row["features"], variant, TC.PARAMS)
                if lam is None:
                    out[field + "_prediction"], out[field + "_fallback"] = C.UNKNOWN, "UNKNOWN_INPUTS"
                else:
                    lam *= TC.PARAMS["scale_c"][variant]
                    out[field + "_lambda"], out[field + "_prediction"] = lam, TC.p_anytime(lam)
            rows.append(out)
    consumer = Path("nfl/research/tier1/touchdown_consumer.py")
    return {"consumer_sha256": _sha(consumer), "information_cutoff": TE.LIVE_INFORMATION_CUTOFF,
            "sources": TE.LIVE_SOURCE_AVAILABILITY, "pbp_sha256": data["pbp_hashes"], "rows": rows}


def drive_b0(season, week, games, out_dir):
    """Authoritative-rule B0 (harness.b0_rolling_mean, incl. the smoothed
    anytime_td frequency) for the next game, via one appended target row per
    player whose latest role appearance is this season with a target team."""
    from nfl.research.tier1 import harness as H
    shared = Path("/tmp/claude-0/nfl_tier1_shared")
    rows, prov = H.load_player_weeks(Path("/tmp/claude-0/nflverse_cache"),
                                     Path("engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"),
                                     first_season=2015, current_season_csv=shared / "stats_player_week_2026.csv")
    rows = [r for r in rows if (r["season"], r["week"]) < (season, week)]
    team_game = {}
    for gid in games:
        _s, _w, away, home = gid.split("_")
        team_game[away], team_game[home] = gid, gid
    # team for the target game = team of the player's latest appearance this season
    # (any role), as the Tier 1 live builders do; b0 depends only on the player's
    # own prior role appearances.
    latest = {}
    for r in rows:
        latest[r["player_id"]] = r
    out = {}
    for market, (_a, role_fn) in H.MARKETS.items():
        last_role = {}
        for r in rows:
            if role_fn(r) > 0:
                last_role[r["player_id"]] = r
        fakes = [{**last_role[pid], "season": season, "week": week, "season_type": "REG",
                  "team": cur["team"], "game_id": team_game[cur["team"]]}
                 for pid, cur in latest.items()
                 if pid in last_role and cur["season"] == season and cur["team"] in team_game]
        for s in H.b0_rolling_mean(rows + fakes, market):
            if (s["season"], s["week"]) == (season, week) and s["b0"] is not None:
                out[f"{market}|{s['player_id']}"] = s["b0"]
    return {"harness_sha256": _sha(Path("nfl/research/tier1/harness.py")), "provenance": prov, "b0": out}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=("B", "C", "D", "B0"))
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--no-capture", action="store_true")
    a = ap.parse_args()
    games = set(a.games.split(","))
    a.out_dir.mkdir(parents=True, exist_ok=True)
    fn = {"B": lambda: drive_b(a.season, a.week, games, a.out_dir),
          "C": lambda: drive_c(a.season, a.week, games, a.out_dir, not a.no_capture),
          "D": lambda: drive_d(a.season, a.week, games, a.out_dir),
          "B0": lambda: drive_b0(a.season, a.week, games, a.out_dir)}[a.which]
    res = fn()
    (a.out_dir / f"driver_{a.which}.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(a.which, "rows", len(res.get("rows", res.get("b0", {}))))


if __name__ == "__main__":
    main()
