#!/usr/bin/env python3
"""Workstream C evaluation: F1/F5/F6/F7/F10 -> team_context_challenger.

Two stages (commit order proves the holdout was not used for fitting):

  --stage dev    fit every volume model and exponent on DEV_2016_2022 only,
                 write team_context_dev_params.json + a DEV-only report.
  --stage final  load the FROZEN params (refit on DEV and require equality),
                 score DEV, HOLDOUT_2023_2025 (exploratory, once) and
                 FRESH_2026 with harness.evaluate_against_controls, and
                 write team_context_report.json.

Run from the repository root:
  PYTHONPATH=. python3 engineering/nfl_tier1_team_context_20260924/evaluate_team_context.py --stage dev
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from nfl.research.tier1 import harness  # noqa: E402
from nfl.research.tier1 import team_context_challenger as tcc  # noqa: E402
from nfl.research.tier1.contract import UNKNOWN, status_record  # noqa: E402
from nfl.research.tier1.team_context_data import (load_schedule, player_positions, read_csv,  # noqa: E402
                                                  summarize_pbp, team_game_context, write_csv,
                                                  TEAM_GAME_FIELDS, POS_FIELDS)
from nfl.research.tier1.team_context_features import (defense_prior_features,  # noqa: E402
                                                      team_prior_features)

SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
WEEKLY_CACHE = Path("/tmp/claude-0/nflverse_cache")
WORK = Path("/tmp/claude-0/nfl_tier1_c")
AUDIT = ROOT / "engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"
CURRENT_2026 = SHARED / "stats_player_week_2026.csv"
PBP_SEASONS = range(2016, 2027)
MARKETS = ("passing_yards", "receptions", "receiving_yards")
PARAMS_PATH = HERE / "team_context_dev_params.json"
DEV = harness.PARTITIONS["DEV_2016_2022"]


def volume_keys() -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for r in range(len(tcc.TEAM_FACTORS) + 1):
        for combo in itertools.combinations(tcc.TEAM_FACTORS, r):
            out.append(("+".join(combo) or "VOLUME_BASE", combo))
    return out


def verify_manifest() -> dict:
    manifest = {}
    for line in (SHARED / "MANIFEST.sha256").read_text().splitlines():
        sha, rel = line.split()
        manifest[rel] = sha
    used = [f"pbp/play_by_play_{s}.csv.gz" for s in PBP_SEASONS] + ["schedules/games.csv"]
    out = {}
    for rel in used:
        actual = harness.sha256_file(SHARED / rel)
        if actual != manifest[rel]:
            raise SystemExit(f"{rel}: sha256 {actual} != manifest {manifest[rel]}")
        out[rel] = actual
    out["stats_player_week_2026.csv"] = harness.sha256_file(CURRENT_2026)
    return out


def load_everything():
    hashes = verify_manifest()
    rows, prov = harness.load_player_weeks(WEEKLY_CACHE, AUDIT, first_season=2015,
                                           current_season_csv=CURRENT_2026)
    positions = player_positions(rows)
    team_games, pos_rows = [], []
    cache = WORK / "cache"
    for season in PBP_SEASONS:
        src = SHARED / f"pbp/play_by_play_{season}.csv.gz"
        tag = hashes[f"pbp/play_by_play_{season}.csv.gz"][:16]
        tpath, ppath = cache / f"team_{season}_{tag}.csv", cache / f"pos_{season}_{tag}.csv"
        if not (tpath.exists() and ppath.exists()):
            t, p = summarize_pbp(src, positions)
            write_csv(tpath, t)
            write_csv(ppath, p)
        team_games += read_csv(tpath, TEAM_GAME_FIELDS)
        pos_rows += read_csv(ppath, POS_FIELDS)
    schedule = load_schedule(SHARED / "schedules/games.csv")
    coords_doc = json.loads((WORK / "stadiums/stadium_coordinates.json").read_text())
    hashes["wikipedia_stadium_coordinates_raw.json"] = coords_doc["raw_sha256"]
    context = team_game_context(schedule, coords_doc["table"])
    weather_path = WORK / "mos_weather_table.json"
    hashes["mos_weather_table.json"] = harness.sha256_file(weather_path)
    weather = json.loads(weather_path.read_text())
    weather_status = {}
    for (game_id, _team), ctx in context.items():
        rec = weather.get(game_id, {"status": "NOT_FETCHED"})
        weather_status[rec["status"]] = weather_status.get(rec["status"], 0) + 1
        for f in ("f6_fcst_wind_kt", "f6_fcst_pop6", "f6_fcst_temp_f", "f6_fcst_runtime"):
            ctx[f] = rec.get(f, UNKNOWN) if rec["status"] == "OK" else UNKNOWN
    hashes["weather_status_team_rows"] = weather_status
    return dict(hashes=hashes, rows=rows, prov=prov, positions=positions, team_games=team_games,
                pos_rows=pos_rows, schedule=schedule, context=context, coords=coords_doc)


def build_team_rows(d):
    targets = sorted({(k[0], k[1], v["season"], v["week"]) for k, v in d["context"].items()
                      if v["game_type"] == "REG" and v["season"] >= 2016}, key=lambda t: (t[2], t[3], t[0], t[1]))
    tf = team_prior_features(d["team_games"], targets)
    dfeat = defense_prior_features(d["team_games"], d["pos_rows"], targets)
    actual = {(g["game_id"], g["team"]): g for g in d["team_games"]}
    team_rows = []
    for game_id, team, season, week in targets:
        a = actual.get((game_id, team))
        if a is None:
            continue
        team_rows.append({"game_id": game_id, "team": team, "season": season, "week": week,
                          "season_type": "REG", "ctx": d["context"][(game_id, team)],
                          "tf": tf[(game_id, team)], "dropbacks": a["dropbacks"],
                          "pass_yards": a["pass_yards"]})
    return targets, tf, dfeat, actual, team_rows


def fit_all_volume(team_rows):
    return {target: {name: tcc.fit_volume_model(team_rows, target, combo)
                     for name, combo in volume_keys()} for target in ("dropbacks", "pass_yards")}


def volume_predictions(d, targets, tf, coefs):
    out = {}
    for target, models in coefs.items():
        combos = dict(volume_keys())
        out[target] = {name: {} for name in models}
        for game_id, team, _s, _w in targets:
            for name, coef in models.items():
                e, _why = tcc.predict_volume(d["context"][(game_id, team)], tf[(game_id, team)],
                                             target, combos[name], coef)
                out[target][name][(game_id, team)] = e
    return out


def team_level_eval(team_rows, vpred, partitions):
    """Team volume intermediate: E_S vs the strictly-prior per-game mean."""
    report = {}
    for target in ("dropbacks", "pass_yards"):
        scored = []
        for r in team_rows:
            base = r["tf"][f"base_{target}_pg"]
            scored.append({"season": r["season"], "week": r["week"], "game_id": r["game_id"],
                           "player_id": r["team"], "actual": r[target],
                           "b0": None if base == UNKNOWN else base})
        report[target] = {}
        for name in ("VOLUME_BASE", "F1", "F5", "F6", "F7", "F5+F6+F7", "F1+F5+F6+F7"):
            ch = {(r["season"], r["week"], r["game_id"], r["team"]): vpred[target][name][(r["game_id"], r["team"])]
                  for r in team_rows if vpred[target][name][(r["game_id"], r["team"])] != UNKNOWN}
            report[target][name] = harness.evaluate(scored, ch, "receptions", partitions=partitions)
            report[target][name]["uses_market_input"] = "F1" in name
    return report


def market_setup(d, market, team_actual, vpred, dfeat):
    scored = [r for r in harness.b0_rolling_mean(d["rows"], market) if r["season"] >= 2016]
    windows = tcc.b0_windows(d["rows"], market)
    parity = tcc.check_b0_parity(scored, windows, d["rows"], market)
    target = tcc.MARKET_TEAM_TARGET[market]
    recs = tcc.player_inputs(scored, windows, market, team_actual, vpred[target], dfeat, d["positions"])
    k = harness.fit_scale_control(scored, market)
    return scored, recs, k, parity


def summarize_bias(recs, preds, partitions):
    out = {}
    for name, (a, b) in partitions.items():
        rows = [r for r in recs if a <= r["season"] <= b]
        if rows:
            out[name] = {"mean_pred_minus_actual": statistics.fmean(preds[r["key"]] - r["actual"] for r in rows),
                         "b0_mean_minus_actual": statistics.fmean(r["b0"] - r["actual"] for r in rows)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("dev", "final"), required=True)
    args = ap.parse_args()
    d = load_everything()
    targets, tf, dfeat, team_actual, team_rows = build_team_rows(d)
    coefs = fit_all_volume(team_rows)
    partitions = {"DEV_2016_2022": DEV} if args.stage == "dev" else harness.PARTITIONS

    if args.stage == "final":
        frozen = json.loads(PARAMS_PATH.read_text())
        for target in coefs:
            for name in coefs[target]:
                if max(abs(a - b) for a, b in zip(coefs[target][name], frozen["volume_coefficients"][target][name])) > 1e-6:
                    raise SystemExit(f"volume model {target}/{name} drifted from frozen DEV params")
        coefs = frozen["volume_coefficients"]
    vpred = volume_predictions(d, targets, tf, coefs)
    team_actual_map = {k: {"dropbacks": v["dropbacks"], "pass_yards": v["pass_yards"]}
                       for k, v in team_actual.items()}

    report = {"stage": args.stage, "workstream": "C",
              "sources_sha256": d["hashes"], "weekly_provenance": d["prov"],
              "stadium_coordinates": {k: d["coords"][k] for k in ("source", "retrieved_at", "raw_sha256")},
              "pre_declared": {"team_window": 10, "defense_window": 16, "ratio_clip": tcc.RATIO_CLIP,
                               "alpha_grid": [tcc.ALPHA_GRID[0], tcc.ALPHA_GRID[-1], 0.05],
                               "beta_grid": [tcc.BETA_GRID[0], tcc.BETA_GRID[-1], 0.05],
                               "pseudo_games_grid": tcc.PSEUDO_GAMES_GRID,
                               "configs": {k: list(v) for k, v in tcc.CONFIGS.items()}},
              "f1_label": "CLOSING_LINE_PROXY_RETROSPECTIVE",
              "team_level": team_level_eval(team_rows, vpred, partitions),
              "markets": {}}
    params = {"volume_coefficients": coefs, "exponents": {}, "scale_k": {}}
    if args.stage == "final":
        params = json.loads(PARAMS_PATH.read_text())

    for market in MARKETS:
        scored, recs, k, parity = market_setup(d, market, team_actual_map, vpred, dfeat)
        dev_recs = [r for r in recs if DEV[0] <= r["season"] <= DEV[1]]
        mrep = {"scale_control_k": k, "b0_parity_rows_checked": parity, "configs": {}}
        if args.stage == "dev":
            params["scale_k"][market] = k
            params["exponents"][market] = {c: tcc.fit_config(dev_recs, c, k) for c in tcc.CONFIGS}
        else:
            if abs(params["scale_k"][market] - k) > 1e-12:
                raise SystemExit("scale control k drifted")
        eval_scored = [r for r in scored if any(a <= r["season"] <= b for a, b in partitions.values())]
        vb_preds = None
        for config in tcc.CONFIGS:
            p = params["exponents"][market][config]
            preds, fallbacks = tcc.predict_config(recs, config, p, k)
            preds = {key: v for key, v in preds.items()
                     if any(a <= key[0] <= b for a, b in partitions.values())}
            if config == "VOLUME_BASE":
                vb_preds = preds
            res = harness.evaluate_against_controls(eval_scored, preds, market)
            if args.stage == "dev":
                res["vs_b0"]["partitions"] = {k2: v for k2, v in res["vs_b0"]["partitions"].items() if k2 in partitions}
                res["vs_scale_control"]["partitions"] = {k2: v for k2, v in res["vs_scale_control"]["partitions"].items() if k2 in partitions}
            if config != "VOLUME_BASE" and config not in ("F10",) and vb_preds is not None:
                shifted = [{**r, "b0": vb_preds.get(harness.row_key(r))} for r in eval_scored]
                res["vs_volume_base"] = harness.evaluate(shifted, preds, market, partitions=partitions)
            fb_counts: dict[str, int] = {}
            for key, why in fallbacks.items():
                if any(a <= key[0] <= b for a, b in partitions.values()):
                    fb_counts[why or "ACTIVE"] = fb_counts.get(why or "ACTIVE", 0) + 1
            attrib = tcc.attribution_rows([r for r in recs if any(a <= r["season"] <= b for a, b in partitions.values())], config, p)
            res.update({"params": p, "uses_market_input": tcc.uses_market_input(config),
                        "fallback_counts": fb_counts,
                        "attribution_rows_changed": harness.attribution(tcc.changed_by(attrib)),
                        "bias": summarize_bias([r for r in recs if r["key"] in preds], preds, partitions)})
            mrep["configs"][config] = res
            if args.stage == "final" and config == "ALL":
                fresh = [r for r in recs if r["season"] == 2026]
                mrep["fresh_2026_attribution_rows"] = [
                    {"key": list(r["key"]), "b0": r["b0"], "pred": preds.get(r["key"]), "actual": r["actual"],
                     "fallback": fallbacks[r["key"]],
                     "log_contrib": {f: round(v, 5) for f, v in attrib.get(r["key"], {}).items()}}
                    for r in fresh]
        report["markets"][market] = mrep
        print(market, "k", k, flush=True)

    if args.stage == "dev":
        PARAMS_PATH.write_text(json.dumps(params, indent=2, sort_keys=True) + "\n")
        out = HERE / "team_context_dev_report.json"
    else:
        out = HERE / "team_context_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
