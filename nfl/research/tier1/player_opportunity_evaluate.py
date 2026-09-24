#!/usr/bin/env python3
"""Evaluate workstream B (F2, F3, F8, F9) through `player_opportunity_challenger`.

Two phases so commit order proves the holdout was not used for tuning:

    PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_evaluate --phase dev
        fits every declared parameter on DEV_2016_2022 only, scores DEV only,
        writes frozen_params.json + dev_report.json (commit these first);
    PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_evaluate --phase full
        loads the frozen parameters (no refit), scores every partition ONCE,
        writes evaluation_report.json.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from nfl.research import role_intelligence_features as rif
from nfl.research.tier1 import contract, harness
from nfl.research.tier1 import player_opportunity_challenger as C
from nfl.research.tier1 import player_opportunity_features as F

SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
OUT_DIR = Path("engineering/nfl_tier1_player_opportunity_20260924")
DEFAULTS = {
    "weekly_cache": Path("/tmp/claude-0/nflverse_cache"),
    "audit_manifest": Path("engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json"),
    "current_season_csv": SHARED / "stats_player_week_2026.csv",
    "snap_dir": SHARED / "snap_counts",
    "injury_dir": SHARED / "injuries",
    "players_csv": Path("/tmp/claude-0/nfl_tier1_B/players.csv"),
    "games_csv": SHARED / "schedules" / "games.csv",
}
SEASONS = range(2016, 2027)


def source_hashes(paths: dict[str, Path]) -> dict[str, str]:
    out = {}
    for name in ("current_season_csv", "players_csv", "games_csv", "audit_manifest"):
        out[str(paths[name])] = harness.sha256_file(paths[name])
    for season in SEASONS:
        for d, stem in ((paths["snap_dir"], "snap_counts"), (paths["injury_dir"], "injuries")):
            p = d / f"{stem}_{season}.csv"
            if p.exists():
                out[str(p)] = harness.sha256_file(p)
    return out


def kickoffs(games_csv: Path) -> dict[tuple[int, int, str], datetime]:
    out = {}
    et = ZoneInfo("America/New_York")
    with games_csv.open(encoding="utf-8") as h:
        for g in csv.DictReader(h):
            if not g["gameday"] or not g["gametime"]:
                continue
            ko = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=et)
            for team in (g["away_team"], g["home_team"]):
                out[(int(g["season"]), int(g["week"]), F.norm_team(team))] = ko.astimezone(timezone.utc)
    return out


def injury_timing_diagnostic(ds: C.Dataset, games_csv: Path) -> dict[str, Any]:
    ko = kickoffs(games_csv)
    n_with = n_after = n_no_game = 0
    by_season: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for r in ds.injury_rows:
        if r["date_modified"] == contract.UNKNOWN:
            continue
        k = ko.get((r["season"], r["week"], r["team"]))
        if k is None:
            n_no_game += 1
            continue
        n_with += 1
        mod = datetime.fromisoformat(r["date_modified"].replace("Z", "+00:00"))
        after = mod > k
        n_after += after
        by_season[r["season"]][0] += 1
        by_season[r["season"]][1] += after
    return {"rule": F.INJURY_AVAILABILITY_RULE,
            "rows_with_date_modified": n_with, "rows_modified_after_kickoff": n_after,
            "share_modified_after_kickoff": (n_after / n_with) if n_with else contract.UNKNOWN,
            "rows_without_matching_game": n_no_game,
            "by_season": {s: {"rows": v[0], "after_kickoff": v[1]} for s, v in sorted(by_season.items())},
            "seasons_without_date_modified": sorted({r["season"] for r in ds.injury_rows
                                                     if r["date_modified"] == contract.UNKNOWN}),
            "note": ("date_modified is the source's last-edit time, not the filing time; a row edited "
                     "after kickoff may still carry the pregame designation. Reported as a leakage-risk "
                     "diagnostic, not corrected.")}


def subset_eval(ds: C.Dataset, market: str, preds: dict, keys: set, k: float, dev_only: bool) -> dict:
    rows = [r for r in ds.scored[market] if harness.row_key(r) in keys and (C._in_dev(r) or not dev_only)]
    control = harness.scale_control_predictions(rows, k)
    shifted = [{**r, "b0": control.get(harness.row_key(r))} for r in rows]
    return {"scale_control_k_global": k, "vs_b0": harness.evaluate(rows, preds, market),
            "vs_scale_control": harness.evaluate(shifted, preds, market)}


def mass_balance(ds: C.Dataset, params: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # (a) our own redistribution accounting over every team-week with an OUT teammate.
    for market in C.MARKETS:
        p = params["F9"][market]
        dim = "carries" if market == "rushing_yards" else "targets"
        tot = Counter()
        max_single = 0.0
        for tw in ds.f9_team_weeks.values():
            if not tw["absent"]:
                continue
            res = C.f9_gains(tw, dim, p["retention"], p["same_position_weight"])
            tot["team_weeks"] += 1
            for key in ("retained_budget", "allocated", "unallocated_residual", "over_allocation"):
                tot[key] += res[key]
            vac = sum(a["target_share" if dim == "targets" else "carry_share"] for a in tw["absent"]
                      if C._num(a["target_share" if dim == "targets" else "carry_share"]))
            if vac > 0 and res["gains"]:
                max_single = max(max_single, max(res["gains"].values()) / vac)
        out[market] = {**{k: (v if k == "team_weeks" else round(v, 6)) for k, v in tot.items()},
                       "max_single_player_share_of_vacated": round(max_single, 6)}
    # (b) the reused #183 diagnostic on the top-usage-player events.
    shares: dict[tuple[str, str], list] = defaultdict(list)
    for r in ds.player_weeks:
        if r["season_type"] != "REG":
            continue
        shares[(r["player_id"], "target_share")].append((r["season"], r["week"], r["target_share"]))
    tgt_team: dict[tuple, float] = defaultdict(float)
    car_team: dict[tuple, float] = defaultdict(float)
    for r in ds.player_weeks:
        tgt_team[(r["game_id"], r["team"])] += r["targets"]
        car_team[(r["game_id"], r["team"])] += r["carries"]
    for r in ds.player_weeks:
        if r["season_type"] == "REG" and car_team[(r["game_id"], r["team"])] > 0:
            shares[(r["player_id"], "carry_share")].append(
                (r["season"], r["week"], r["carries"] / car_team[(r["game_id"], r["team"])]))
    for v in shares.values():
        v.sort()
    for dim, etype, market in (("target_share", "WR_ABSENCE", "receptions"),
                               ("carry_share", "RB_ABSENCE", "rushing_yards")):
        p = params["F9"][market]
        predicted = {}
        events = [e for e in ds.events if e["event_type"] == etype]
        for e in events:
            tw = ds.f9_team_weeks.get((e["season"], e["week"], e["team"]))
            if not tw:
                continue
            gains = C.f9_gains(tw, "carries" if dim == "carry_share" else "targets",
                               p["retention"], p["same_position_weight"])["gains"]
            pred = {}
            for pid, g in gains.items():
                prior = rif.most_recent_prior_share(shares.get((pid, dim), []), e["season"], e["week"])
                if prior is not None:
                    pred[pid] = prior + g
            if pred:
                predicted[(e["season"], e["week"], e["team"], e["removed_player_id"])] = pred
        diag = rif.compute_mass_balance_diagnostics(events, shares, predicted, dim)
        diag.pop("rows", None)
        out[f"rif_mass_balance_{etype}"] = diag
    return out


def evaluate_all(ds: C.Dataset, params: dict, *, dev_only: bool) -> dict[str, Any]:
    report: dict[str, Any] = {"markets": {}}
    for market in C.MARKETS:
        scored = ds.scored[market]
        if dev_only:
            scored = [r for r in scored if C._in_dev(r)]
        mrep: dict[str, Any] = {"n_scored_rows": len(scored), "scale_control_k": params["k"][market],
                                "ablations": {}, "f2_scenarios": {}, "subsets": {}}
        for ablation in C.ABLATIONS:
            if ablation != "ALL" and market not in C.FACTOR_MARKETS[ablation]:
                continue
            preds, changed, fb = C.predict(ds, market, params, ablation)
            ev = harness.evaluate_against_controls(scored, preds, market)
            mrep["ablations"][ablation] = {"evaluation": ev, "attribution": harness.attribution(changed),
                                           "fallbacks": fb}
            if ablation == "F9_ABSENCE_REDISTRIBUTION":
                keys = {k for k, f in ds.features.items()
                        if f["F9_ABSENCE_REDISTRIBUTION"]["features"]["n_teammates_listed_out"] > 0
                        and f["F9_ABSENCE_REDISTRIBUTION"]["features"]["max_overlap_with_absent"] > 0}
                mrep["subsets"]["F9_within_observed_absence_scenarios"] = subset_eval(
                    ds, market, preds, keys, params["k"][market], dev_only)
            if ablation == "F8_INJURY_PRACTICE":
                keys = {k for k, f in ds.features.items()
                        if f["F8_INJURY_PRACTICE"]["features"]["category"] not in (contract.UNKNOWN, F.NOT_LISTED)}
                sub = subset_eval(ds, market, preds, keys, params["k"][market], dev_only)
                mrep["subsets"]["F8_listed_or_returning_players"] = sub
        for scenario in C.F2_SCENARIOS:
            preds, changed, _fb = C.predict(ds, market, params, "F2_SNAP_SHARE_ROLE", f2_only_scenario=scenario)
            keys = {k for k, f in ds.features.items()
                    if f["F2_SNAP_SHARE_ROLE"]["features"]["scenario"] == scenario}
            mrep["f2_scenarios"][scenario] = {
                "alpha": params["F2"][market]["alphas"][scenario],
                "within_scenario": subset_eval(ds, market, preds, keys, params["k"][market], dev_only)}
        preds, changed, _fb = C.predict(ds, market, params, "F2_SNAP_SHARE_ROLE", f2_broad=True)
        mrep["f2_broad_reference"] = {"alpha": params["F2"][market]["broad_alpha"],
                                      "evaluation": harness.evaluate_against_controls(scored, preds, market),
                                      "attribution": harness.attribution(changed)}
        report["markets"][market] = mrep
    return report


def headline(report: dict) -> dict:
    """Flat table: factor x market x partition -> (activation, n, delta vs B0/scale, CI)."""
    out = {}
    for market, mrep in report["markets"].items():
        for ablation, a in mrep["ablations"].items():
            for part, e in a["evaluation"]["vs_scale_control"]["partitions"].items():
                if not e.get("n_matched"):
                    continue
                b0e = a["evaluation"]["vs_b0"]["partitions"][part]
                out[f"{ablation}|{market}|{part}"] = {
                    "n_matched": e["n_matched"], "activation_vs_scale": round(e["activation_share"], 4),
                    "delta_vs_b0": round(b0e["paired_delta_mean"], 4),
                    "ci_vs_b0": [round(x, 4) for x in b0e["paired_delta_ci95"] or []],
                    "delta_vs_scale": round(e["paired_delta_mean"], 4),
                    "ci_vs_scale": [round(x, 4) for x in e["paired_delta_ci95"] or []],
                    "challenger_bias": round(e["metrics"]["error"]["challenger"], 4),
                    "mae_challenger": round(e["metrics"]["abs_error"]["challenger"], 4),
                    "mae_scale": round(e["metrics"]["abs_error"]["b0"], 4),
                    "mae_b0": round(b0e["metrics"]["abs_error"]["b0"], 4)}
    return out


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("dev", "full"), required=True)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(list(argv) if argv is not None else None)
    paths = dict(DEFAULTS)
    ds = C.build_dataset(weekly_cache=paths["weekly_cache"], audit_manifest=paths["audit_manifest"],
                         current_season_csv=paths["current_season_csv"], snap_dir=paths["snap_dir"],
                         injury_dir=paths["injury_dir"], players_csv=paths["players_csv"], seasons=SEASONS)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frozen = args.out_dir / "frozen_params.json"
    if args.phase == "dev":
        params = C.fit_params(ds)
        frozen.write_text(json.dumps(params, indent=1, sort_keys=True, default=str) + "\n")
        report = evaluate_all(ds, params, dev_only=True)
        name = "dev_report.json"
    else:
        params = json.loads(frozen.read_text())
        params["F2"] = {m: {**v, "alphas": {s: float(a) for s, a in v["alphas"].items()}}
                        for m, v in params["F2"].items()}
        if params["k"] != ds.k:
            raise SystemExit(f"scale control drift: frozen {params['k']} vs rebuilt {ds.k}")
        report = evaluate_all(ds, params, dev_only=False)
        report["mass_balance"] = mass_balance(ds, params)
        report["injury_timing"] = injury_timing_diagnostic(ds, paths["games_csv"])
        name = "evaluation_report.json"
    report.update({"consumer": C.CONSUMER, "phase": args.phase, "params": params,
                   "provenance": ds.provenance, "source_sha256": source_hashes(paths),
                   "diagnostics": ds.diagnostics, "headline": headline(report),
                   "notes": {"routes": F.ROUTES_NOTE, "injury_rule": F.INJURY_AVAILABILITY_RULE,
                             "absence_trigger": F.ABSENCE_TRIGGER_RULE,
                             "selection_effect": ("Only role-positive REG rows (the player recorded a "
                                                  "target/carry) are scored; ruled-out or inactive players "
                                                  "never enter the population.")}})
    (args.out_dir / name).write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    for key, row in report["headline"].items():
        print(key, row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
