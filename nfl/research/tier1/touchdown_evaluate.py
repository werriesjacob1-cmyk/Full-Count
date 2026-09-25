#!/usr/bin/env python3
"""Workstream D evaluation: F4_RED_ZONE -> touchdown_opportunity_challenger.

Stages (commit order proves pre-declaration):

  --stage fit   DEV_2016_2022 only: fit conversion rates, scale c per
                variant and the closing-line proxy; print the PARAMS to paste
                into touchdown_consumer.PARAMS; report DEV-only results.
  --stage full  Refit on DEV and REFUSE to run unless it reproduces the
                committed PARAMS; then score every partition once, build the
                live 2026 week-3 rows, and write the JSON report.

Run from the repository root:
  python3 -m nfl.research.tier1.touchdown_evaluate --stage full \
      --out engineering/nfl_tier1_touchdown_20260924/touchdown_report.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from nfl.research.tier1 import contract as C
from nfl.research.tier1 import harness as H
from nfl.research.tier1 import touchdown_consumer as TC
from nfl.research.tier1 import touchdown_features as TF

DEFAULTS = {
    "cache": "/tmp/claude-0/nflverse_cache",
    "audit": "engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json",
    "current": "/tmp/claude-0/nfl_tier1_shared/stats_player_week_2026.csv",
    "pbp": "/tmp/claude-0/nfl_tier1_shared/pbp",
    "games": "/tmp/claude-0/nfl_tier1_shared/schedules/games.csv",
}
PBP_SEASONS = range(2016, 2027)
DEV = H.PARTITIONS["DEV_2016_2022"]
MARKET = "anytime_td"
C_GRID = [s / 100 for s in range(30, 201)]
GAMMA_GRID = [g / 20 for g in range(0, 41)]
PARAM_TOLERANCE = 5e-4

# Live target (ATL@GB, TNF 2026-09-24 20:15 ET).
LIVE = {"target_season": 2026, "target_week": 3, "game_id": "2026_03_ATL_GB",
        "teams": ("ATL", "GB"), "kickoff_utc": "2026-09-25T00:15:00Z"}
# Real availability of the newest sources the live rows use: the upstream
# nflverse release assets' HTTP Last-Modified, verified to be byte-identical
# (SHA-256) to the shared files by an independent re-download at
# 2026-09-24T21:02:35Z (pbp) and 2026-09-24T21:05:38Z (weekly stats).
LIVE_SOURCE_AVAILABILITY = {
    "play_by_play_2026.csv.gz": {
        "sha256": "6643f82adb1158c8367fb806cfc531a079ce321ca8e2e21012d196dc1a51ece8",
        "upstream_last_modified_utc": "2026-09-24T14:12:31Z",
        "redownload_verified_utc": "2026-09-24T21:02:35Z"},
    "stats_player_week_2026.csv": {
        "sha256": "736bdddef4779023f7eb1831a1f2c8627181aee60cc280d5f0464cf5f41a8e67",
        "upstream_last_modified_utc": "2026-09-24T14:13:56Z",
        "redownload_verified_utc": "2026-09-24T21:05:38Z"},
}
LIVE_INFORMATION_CUTOFF = "2026-09-24T14:13:56Z"  # max of the two Last-Modified times


def load(args) -> dict[str, Any]:
    rows, prov = H.load_player_weeks(Path(args.cache), Path(args.audit), first_season=2015,
                                     current_season_csv=Path(args.current))
    parsed, pbp_hashes = TF.load_pbp(Path(args.pbp), PBP_SEASONS)
    hist_rows = [r for r in rows if r["season"] >= 2016]
    feats, diag = TF.build_features(TF.historical_requests(hist_rows), hist_rows, parsed)
    scored = [r for r in H.b0_rolling_mean(rows, MARKET) if r["season"] >= 2016]
    return {"rows": rows, "prov": prov, "parsed": parsed, "pbp_hashes": pbp_hashes,
            "features": feats, "feature_diag": diag, "scored": scored, "hist_rows": hist_rows}


def closing_implied(games_csv: Path, scored: list[dict]) -> tuple[dict[tuple, float], str]:
    by_game: dict[str, dict] = {}
    with games_csv.open(newline="", encoding="utf-8") as handle:
        for g in csv.DictReader(handle):
            if g["spread_line"] not in ("", "NA") and g["total_line"] not in ("", "NA"):
                by_game[g["game_id"]] = g
    out = {}
    for r in scored:
        g = by_game.get(r["game_id"])
        if g is None:
            continue
        total, spread = float(g["total_line"]), float(g["spread_line"])
        home = TF.canonical_team(g["home_team"]) == TF.canonical_team(r["team"])
        out[H.row_key(r)] = (total + spread) / 2 if home else (total - spread) / 2
    return out, H.sha256_file(games_csv)


def in_dev(r) -> bool:
    return DEV[0] <= r["season"] <= DEV[1]


def fit_params(data: dict[str, Any], implied: dict[tuple, float]) -> dict[str, Any]:
    """Every parameter from DEV_2016_2022 rows only."""
    parsed = data["parsed"]
    counts = defaultdict(lambda: defaultdict(float))
    tds = defaultdict(lambda: defaultdict(float))
    for r in data["hist_rows"]:
        if not in_dev(r) or TF.td_role(r) <= 0:
            continue
        pg = parsed.player_games.get((r["game_id"], r["player_id"]))
        if pg is None:
            continue
        pos = TF.position_group(r["position"])
        for b in TF.BUCKETS:
            counts[pos][b] += pg["counts"][b]
            tds[pos][b] += pg["tds"][b]
    conv = {pos: {b: round(tds[pos][b] / counts[pos][b], 6) if counts[pos][b] else 0.0
                  for b in TF.BUCKETS} for pos in TC.POSITIONS}
    conv_all = {pos: {k: round(sum(tds[pos][f"{k}_{z}"] for z in TF.ZONES)
                               / max(1.0, sum(counts[pos][f"{k}_{z}"] for z in TF.ZONES)), 6)
                      for k in TF.TYPES} for pos in TC.POSITIONS}
    params = {"fit_partition": "DEV_2016_2022", "conv": conv, "conv_all": conv_all,
              "scale_c": {}, "closing_mean_implied": None, "closing_gamma": None}
    dev = [r for r in data["scored"] if in_dev(r)]
    dev_imp = [implied[H.row_key(r)] for r in dev if H.row_key(r) in implied]
    params["closing_mean_implied"] = round(statistics.fmean(dev_imp), 4)
    params["m_frac"] = _fit_m_frac(data, params, dev)
    params["w_blend"] = _fit_w_blend(data, params, dev)
    for variant in TC.VARIANTS:
        lam, y, ratio = [], [], []
        for r in dev:
            key = H.row_key(r)
            row = data["features"].get(key)
            if row is None:
                continue
            if variant == "closing_line_proxy":
                if key not in implied:
                    continue
                raw = TC.raw_lambda(row["features"], TC.PRIMARY, params)
                ratio.append(implied[key] / params["closing_mean_implied"])
            else:
                raw = TC.raw_lambda(row["features"], variant, params)
            if raw is None:
                if variant == "closing_line_proxy":
                    ratio.pop()
                continue
            lam.append(raw)
            y.append(r["actual"])
        lam_a, y_a = np.array(lam), np.array(y)
        gammas = GAMMA_GRID if variant == "closing_line_proxy" else [0.0]
        best = None
        for gamma in gammas:
            base = lam_a * (np.array(ratio) ** gamma if variant == "closing_line_proxy" else 1.0)
            for c in C_GRID:
                p = np.clip(1 - np.exp(-c * base), 1e-6, 1 - 1e-6)
                loss = float(np.mean(-(y_a * np.log(p) + (1 - y_a) * np.log(1 - p))))
                if best is None or loss < best[0]:
                    best = (loss, c, gamma)
        params["scale_c"][variant] = best[1]
        if variant == "closing_line_proxy":
            params["closing_gamma"] = best[2]
    return params


M_FRAC_GRID = (2.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)


def _dev_logloss(data, params, dev, variant) -> float:
    lam, y = [], []
    for r in dev:
        row = data["features"].get(H.row_key(r))
        raw = TC.raw_lambda(row["features"], variant, params) if row else None
        if raw is not None:
            lam.append(raw)
            y.append(r["actual"])
    lam_a, y_a = np.array(lam), np.array(y)
    best = None
    for c in C_GRID:
        p = np.clip(1 - np.exp(-c * lam_a), 1e-6, 1 - 1e-6)
        loss = float(np.mean(-(y_a * np.log(p) + (1 - y_a) * np.log(1 - p))))
        best = loss if best is None else min(best, loss)
    return best


def _fit_m_frac(data, params, dev) -> float:
    scores = {m: _dev_logloss(data, {**params, "m_frac": m}, dev, "rz_zone_split")
              for m in M_FRAC_GRID}
    print(json.dumps({"m_frac_dev_logloss": scores}), file=sys.stderr)
    return min(scores, key=scores.get)


W_GRID = tuple(w / 20 for w in range(0, 21))


def _fit_w_blend(data, params, dev) -> float:
    scores = {w: _dev_logloss(data, {**params, "w_blend": w}, dev, "rz_blend") for w in W_GRID}
    print(json.dumps({"w_blend_dev_logloss": scores}), file=sys.stderr)
    return min(scores, key=scores.get)


def params_match(a: dict, b: dict) -> list[str]:
    bad = []
    for pos in TC.POSITIONS:
        for bkt in TF.BUCKETS:
            if abs(a["conv"][pos][bkt] - b["conv"][pos][bkt]) > PARAM_TOLERANCE:
                bad.append(f"conv.{pos}.{bkt}")
        for k in TF.TYPES:
            if abs(a["conv_all"][pos][k] - b["conv_all"][pos][k]) > PARAM_TOLERANCE:
                bad.append(f"conv_all.{pos}.{k}")
    for v in TC.VARIANTS:
        if abs(a["scale_c"][v] - b["scale_c"][v]) > 1e-9:
            bad.append(f"scale_c.{v}")
    for k in ("closing_mean_implied", "closing_gamma", "m_frac", "w_blend"):
        if abs(a[k] - b[k]) > PARAM_TOLERANCE:
            bad.append(k)
    return bad


def calibration(scored, preds, partition) -> list[dict]:
    lo, hi = H.PARTITIONS[partition]
    pairs = sorted((preds[H.row_key(r)], r["actual"]) for r in scored
                   if lo <= r["season"] <= hi and r["b0"] is not None and H.row_key(r) in preds)
    if not pairs:
        return []
    out = []
    for d in range(10):
        chunk = pairs[d * len(pairs) // 10:(d + 1) * len(pairs) // 10]
        if chunk:
            out.append({"decile": d + 1, "n": len(chunk),
                        "mean_pred": statistics.fmean(p for p, _ in chunk),
                        "actual_rate": statistics.fmean(a for _, a in chunk)})
    return out


def two_plus_report(scored, lams, td_counts) -> dict:
    out = {"label": "RESEARCH_DISTRIBUTION_NOT_VALIDATED_2PLUS_PRICING", "partitions": {}}
    violations = 0
    for name, (lo, hi) in H.PARTITIONS.items():
        p1s, p2s, a2 = [], [], []
        for r in scored:
            key = H.row_key(r)
            if not (lo <= r["season"] <= hi) or key not in lams:
                continue
            p1, p2 = TC.p_anytime(lams[key]), TC.p_two_plus(lams[key])
            violations += p2 > p1 + 1e-15
            p1s.append(p1)
            p2s.append(p2)
            a2.append(1.0 if td_counts.get(key, 0) >= 2 else 0.0)
        if p2s:
            out["partitions"][name] = {
                "n": len(p2s), "mean_p1": statistics.fmean(p1s), "mean_p2": statistics.fmean(p2s),
                "actual_2plus_rate": statistics.fmean(a2),
                "brier_2plus": statistics.fmean((p - a) ** 2 for p, a in zip(p2s, a2))}
    out["coherence_violations_p2_gt_p1"] = violations
    return out


def settlement_gap(args, scored) -> dict:
    """Rows where a non-rush/rec TD (return/defensive/fumble recovery) would
    settle FanDuel's anytime-TD market as a WIN but the harness outcome is 0."""
    wanted = {H.row_key(r) for r in scored}
    gap = defaultdict(lambda: {"rows": 0, "other_td_only": 0})
    files = [Path(args.cache) / f"stats_player_week_{s}.csv" for s in range(2016, 2026)]
    files.append(Path(args.current))
    for path in files:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for raw in csv.DictReader(handle):
                if not raw.get("player_id"):
                    continue
                key = (int(float(raw["season"])), int(float(raw["week"])), raw["game_id"],
                       raw["player_id"].strip())
                if key not in wanted:
                    continue
                part = next(n for n, (lo, hi) in H.PARTITIONS.items() if lo <= key[0] <= hi)
                other = sum(float(raw.get(c) or 0) if raw.get(c) not in ("", "NA") else 0.0
                            for c in ("special_teams_tds", "def_tds", "fumble_recovery_tds"))
                rr = sum(float(raw.get(c) or 0) if raw.get(c) not in ("", "NA") else 0.0
                         for c in ("rushing_tds", "receiving_tds"))
                gap[part]["rows"] += 1
                gap[part]["other_td_only"] += other > 0 and rr == 0
    return {k: {**v, "share": v["other_td_only"] / v["rows"] if v["rows"] else None}
            for k, v in gap.items()}


def live_rows(data: dict[str, Any]) -> dict[str, Any]:
    reqs = TF.live_requests(data["hist_rows"], target_season=LIVE["target_season"],
                            target_week=LIVE["target_week"], game_id=LIVE["game_id"],
                            teams=LIVE["teams"])
    feats, _diag = TF.build_features(reqs, data["hist_rows"], data["parsed"],
                                     information_cutoff=LIVE_INFORMATION_CUTOFF)
    names = {r["player_id"]: r["player_name"] for r in data["hist_rows"]}
    out = []
    for key, row in sorted(feats.items(), key=lambda kv: (kv[1]["team"], kv[0][3])):
        C.validate_feature_row(row, prediction_cutoff=LIVE["kickoff_utc"])
        lam = TC.raw_lambda(row["features"], TC.PRIMARY, TC.PARAMS)
        entry = {"feature_row": row, "player_name": names.get(key[3], ""),
                 "consumer": TC.CONSUMER_ID, "variant": TC.PRIMARY}
        if lam is None:
            entry.update({"p_anytime_rush_rec_td": C.UNKNOWN, "fallback": "UNKNOWN_INPUTS"})
        else:
            lam *= TC.PARAMS["scale_c"][TC.PRIMARY]
            entry.update({"lambda": lam, "p_anytime_rush_rec_td": TC.p_anytime(lam),
                          "p_two_plus_research_distribution": TC.p_two_plus(lam)})
        out.append(entry)
    return {"target": LIVE, "information_cutoff": LIVE_INFORMATION_CUTOFF,
            "information_cutoff_basis": "upstream nflverse release-asset HTTP Last-Modified "
                                        "(release time), SHA-256 verified by re-download",
            "sources": LIVE_SOURCE_AVAILABILITY, "n_players": len(out), "rows": out,
            "label": "RESEARCH_ONLY_NOT_A_PICK"}


FANDUEL_RULES = {
    "source": "FanDuel Sportsbook House Rules (Massachusetts filing), "
              "https://massgaming.com/wp-content/uploads/FanDuel-House-Rules-8.24.23.pdf",
    "sha256": "2a8cb81b2cc616ef06796a9e4b68e2af634b772411e5252a4e3c9596c9bfa845",
    "retrieved_utc": "2026-09-24T21:26Z",
    "caveat": "2023-08-24 filing; the current fanduel.com house-rules pages returned HTTP 403 to "
              "this session, so the 2026 wording is not verified",
    "quoted_rules": [
        "Only when a player does not play a snap in that game are the selections voided.",
        "For touchdown scorer markets, the winning selection is the player who possesses the ball "
        "in the endzone. For example - on a pass TD play, the receiver in the endzone is graded as "
        "the winner, not the QB.",
        "In the event of an abandoned game, bets stand on scores that have taken place already "
        "(and overtime counts for these markets)."],
    "differences_vs_research_outcome": [
        "VOID: FanDuel voids only a player who plays ZERO snaps. The research population is "
        "players with >=1 carry or target in the game (the harness role), which is known only "
        "after the game. A player who plays snaps but gets no touch is a FanDuel LOSS but is "
        "absent here, so research probabilities are conditional on a touch and run HIGH relative "
        "to FanDuel's settled population. Paired comparisons are unaffected (same rows).",
        "SCOPE: FanDuel counts any TD where the player possesses the ball in the end zone, "
        "including kick/punt return, defensive and fumble-recovery TDs (the repo grader "
        "player_prop_grader/box_score_outcomes agrees). The research outcome counts rushing and "
        "receiving TDs only; see settlement_gap_other_td_only for the measured share.",
        "PASSING TDs: excluded by both (the receiver, not the QB, wins).",
        "TWO-POINT CONVERSIONS: not touchdowns for either; excluded from features too.",
        "OVERTIME: counts for FanDuel; nflverse weekly stats include overtime.",
        "LIVE: the challenger predicts P(TD | plays and gets a touch); it does not model the "
        "chance of being inactive (F8 is Workstream B)."],
}


def status_annotations(report: dict) -> dict:
    prim = report["variants"][report["primary_variant"]]["summary"]
    vol = report["primary_vs_volume_only_control"][report["primary_variant"]]
    hold, fresh = vol["HOLDOUT_2023_2025"], vol["FRESH_2026"]
    beats_scale = (prim["HOLDOUT_2023_2025"]["vs_scale_control"]["ci95"][1] < 0
                   and prim["FRESH_2026"]["vs_scale_control"]["delta_logloss"] <= 0)
    beats_volume = hold["paired_delta_ci95"][1] < 0 and fresh["paired_delta_mean"] <= 0
    milestone = "VALIDATED" if beats_scale and beats_volume else "BUILT"
    evaluation = {
        "primary_variant": report["primary_variant"],
        "vs_scale_control": {p: prim[p]["vs_scale_control"] for p in prim},
        "vs_b0": {p: prim[p]["vs_b0"] for p in prim},
        "vs_volume_only_control": {p: {k: v[p].get(k) for k in
                                       ("n_matched", "activation_share", "paired_delta_mean",
                                        "paired_delta_ci95", "n_game_clusters")} for p in v}
        if (v := vol) else {},
        "claim": ("historically supported, not prospectively validated" if milestone == "VALIDATED"
                  else "built; not supported by the declared criterion"),
    }
    record = C.status_record(
        TF.FACTOR_ID, milestone=milestone, consumer=TC.CONSUMER_ID,
        evidence=("engineering/nfl_tier1_touchdown_20260924/touchdown_report.json; primary "
                  "rz_blend declared at commit 47d35ab540 before holdout scoring"),
        blockers=[
            "harness anytime_td B0 is degenerate for log loss (B0 = 0 on ~36% of rows while "
            "those rows score at 11-15%); beating B0/scale control is not F4 evidence -- the "
            "factor-specific test is vs the no-red-zone volume_only control",
            "the incremental gain over volume_only is small (holdout log loss -0.0017)",
            "probabilities are under-dispersed by decile and conditional on the player getting "
            "a touch; not a validated anytime-TD pricing system",
            "HOLDOUT_2023_2025 previously inspected by other NFL experiments (exploratory)"],
        activation={p: prim[p]["vs_b0"]["activation"] for p in prim}
        | {f"vs_volume_only_{p}": vol[p].get("activation_share") for p in vol},
        evaluation=evaluation)
    return {"status_records": [record], "fanduel_settlement": FANDUEL_RULES}


def _summary(ev: dict) -> dict:
    out = {}
    for which in ("vs_b0", "vs_scale_control"):
        for name, p in ev[which]["partitions"].items():
            if "metrics" not in p:
                continue
            out.setdefault(name, {})[which] = {
                "n_matched": p["n_matched"], "activation": round(p["activation_share"], 4),
                "delta_logloss": round(p["paired_delta_mean"], 5),
                "ci95": [round(x, 5) for x in (p["paired_delta_ci95"] or [])],
                "logloss": {k: round(v, 5) for k, v in p["metrics"]["log_loss"].items()},
                "brier": {k: round(v, 5) for k, v in p["metrics"]["brier"].items()}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("fit", "full", "status"), required=True)
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k}", default=v)
    ap.add_argument("--out", default="engineering/nfl_tier1_touchdown_20260924/touchdown_report.json")
    args = ap.parse_args()

    if args.stage == "status":
        path = Path(args.out)
        report = json.loads(path.read_text(encoding="utf-8"))
        report.update(status_annotations(report))
        path.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
        print(json.dumps(report["status_records"], indent=1))
        return
    data = load(args)
    implied, games_sha = closing_implied(Path(args.games), data["scored"])
    fitted = fit_params(data, implied)
    if args.stage == "fit":
        dev_only = {"DEV_2016_2022": DEV}
        report = {"params": fitted, "dev": {}}
        k = H.fit_scale_control(data["scored"], MARKET)
        shifted = [{**r, "b0": (k * r["b0"] if r["b0"] is not None else None)} for r in data["scored"]]
        for v in TC.VARIANTS:
            preds, _l, counts = TC.predict(data["features"], data["scored"], v, fitted, implied=implied)
            report["dev"][v] = {
                "counts": counts,
                "vs_b0": H.evaluate(data["scored"], preds, MARKET, partitions=dev_only)["partitions"],
                "vs_scale": H.evaluate(shifted, preds, MARKET, partitions=dev_only)["partitions"]}
        vol, _l, _c = TC.predict(data["features"], data["scored"], "volume_only", fitted)
        base = [{**r, "b0": vol.get(H.row_key(r))} for r in data["scored"]]
        for v in ("rz_blend", "rz_share_x_team", "rz_zone_split"):
            preds, _l, _c = TC.predict(data["features"], data["scored"], v, fitted, implied=implied)
            report["dev"][v]["vs_volume_only"] = H.evaluate(
                base, preds, MARKET, partitions=dev_only)["partitions"]
        print(json.dumps(report, indent=1, default=str))
        return

    drift = params_match(TC.PARAMS, fitted) if TC.PARAMS["conv"] else ["PARAMS empty"]
    if drift:
        raise SystemExit(f"committed PARAMS do not match the DEV refit: {drift[:10]}")
    scored = data["scored"]
    td_counts = {H.row_key(r): r["rushing_tds"] + r["receiving_tds"] for r in data["rows"]}
    results: dict[str, Any] = {}
    all_preds: dict[str, dict] = {}
    all_lams: dict[str, dict] = {}
    for v in TC.VARIANTS:
        preds, lams, counts = TC.predict(data["features"], scored, v, TC.PARAMS, implied=implied)
        ev = H.evaluate_against_controls(scored, preds, MARKET)
        results[v] = {"prediction_counts": counts, "summary": _summary(ev), "harness": ev}
        all_preds[v], all_lams[v] = preds, lams
    vol = all_preds["volume_only"]
    vs_volume = {}
    for v in ("rz_blend", "rz_share_x_team", "rz_direct", "rz_zone_split", "rz_team_conv"):
        base = [{**r, "b0": vol.get(H.row_key(r))} for r in scored]
        vs_volume[v] = H.evaluate(base, all_preds[v], MARKET)
    b0_zero = {}
    for name, (lo, hi) in H.PARTITIONS.items():
        rows = [r for r in scored if lo <= r["season"] <= hi and r["b0"] is not None]
        zero = [r for r in rows if r["b0"] == 0]
        b0_zero[name] = {"n": len(rows), "b0_exactly_zero": len(zero),
                         "td_rate_when_b0_zero": (statistics.fmean(r["actual"] for r in zero)
                                                  if zero else None)}
    report = {
        "workstream": "D", "factor": TF.FACTOR_ID, "consumer": TC.CONSUMER_ID,
        "primary_variant": TC.PRIMARY, "market": MARKET,
        "outcome_definition": "at least one RUSHING or RECEIVING TD (harness anytime_td); passing "
                              "TDs out of scope; return/defensive/fumble-recovery TDs NOT included",
        "params_declared": TC.PARAMS, "params_dev_refit_matches": True,
        "variants": {v: {k: r[k] for k in ("prediction_counts", "summary")} for v, r in results.items()},
        "harness_full": {v: r["harness"] for v, r in results.items()},
        "primary_vs_volume_only_control": {v: e["partitions"] for v, e in vs_volume.items()},
        "calibration_primary": {p: calibration(scored, all_preds[TC.PRIMARY], p) for p in H.PARTITIONS},
        "calibration_b0": {p: calibration(scored, {H.row_key(r): r["b0"] for r in scored
                                                   if r["b0"] is not None}, p) for p in H.PARTITIONS},
        "two_plus_td": two_plus_report(scored, all_lams[TC.PRIMARY], td_counts),
        "b0_degeneracy": b0_zero,
        "settlement_gap_other_td_only": settlement_gap(args, scored),
        "feature_build_diagnostics": data["feature_diag"],
        "pbp_parse_diagnostics": dict(data["parsed"].diagnostics),
        "exclusion_rules_doc": "nfl/research/tier1/touchdown_features.py module docstring",
        "closing_line_label": TC.CLOSING_LABEL,
        "sources": {"pbp_sha256": data["pbp_hashes"], "games_csv_sha256": games_sha,
                    "weekly_provenance": data["prov"]},
        "live": live_rows(data),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({v: r["summary"] for v, r in results.items()}, indent=1))
    print(json.dumps({v: {p: {k: e.get(k) for k in ("n_matched", "paired_delta_mean", "paired_delta_ci95")}
                          for p, e in ev["partitions"].items()} for v, ev in vs_volume.items()}, indent=1))


if __name__ == "__main__":
    main()
