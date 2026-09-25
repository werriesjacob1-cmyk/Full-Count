"""Tier 1 workstream B consumer: `player_opportunity_challenger`.

One research challenger for receptions, receiving_yards and rushing_yards,
with an ablation switch per factor (F2, F3, F8, F9, each alone, then ALL).

## Pre-declared consumer form (fixed BEFORE any holdout scoring)

Let k_m be the harness's own DEV-fitted scale control for market m
(`harness.fit_scale_control`, 2016-2022 only) and base = k_m * B0. Every
ablation starts from `base`, so a row the factor does not touch (or where
its input is UNKNOWN) falls back EXACTLY to the scale control and the
report's `vs_scale_control` delta measures only the factor's information.
The fallback is recorded per row (`fallback_reason`).

* F3 (receptions, receiving_yards only):
    E_targets = target_share_last5 * team_targets_per_game_last5
    catch_rate = (rec5 + M * (c0 + c1 * aDOT5)) / (tgt5 + M)
    yds_per_target = (yds5 + M * (y0 + y1 * aDOT5)) / (tgt5 + M)
    f3 = E_targets * catch_rate   (receptions) | E_targets * yds_per_target (yards)
    pred = w * f3 + (1 - w) * base
  M = 20 targets (declared, not fit); c0, c1, y0, y1 = target-weighted DEV
  least squares of the realized per-game rate on prior aDOT; w is a DEV grid
  fit in [0, 1]. UNKNOWN (no prior targets / team volume) -> base.
* F2: pred = base * clamp(snap_last3 / snap_last5, 0.5, 2.0) ** alpha[s]
  where s is a SPECIFIC role-change scenario (`f2_scenario`): rising role with
  a same-group teammate OUT/DOUBTFUL now, rising after a recent teammate
  absence event, rising with no event, falling. alpha[STABLE] = 0 by
  declaration (no blanket multiplier); each scenario's alpha is a DEV grid
  fit in [-1, 1.5]. `F2_BROAD_REFERENCE` (one alpha for every row) is scored
  only to reproduce the earlier negative finding.
* F8: pred = base * m[category] with category = (game status | practice
  status), NOT_LISTED or RETURNING_FROM_OUT; m is a DEV grid fit in
  [0.6, 1.3] for categories with >= 200 DEV rows, else 1.0 (inactive).
* F9: pred = base * (share + gain) / share where gain comes from
  `player_opportunity_features.redistribute` (targets for receptions/
  receiving_yards, carries for rushing_yards); retention and the
  same-position weight are a DEV grid fit.
* ALL: F3-blended base * F2 multiplier * F8 multiplier * F9 multiplier, each
  with its own separately DEV-fitted parameters (no joint refit).

Population caveat (F8 especially): the harness scores only role-positive REG
rows, i.e. players who actually recorded a target/carry. Players ruled out or
inactive never enter the scored population, so F8 is tested only as a
workload/efficiency adjustment for players who played -- a selection effect,
not an availability forecast.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from nfl.research import role_intelligence_data_prep as prep
from nfl.research import role_intelligence_features as rif
from nfl.research.tier1 import contract, harness
from nfl.research.tier1 import player_opportunity_features as F

UNKNOWN = contract.UNKNOWN
CONSUMER = "player_opportunity_challenger"
MARKETS = ("receptions", "receiving_yards", "rushing_yards")
FACTOR_MARKETS = {
    "F2_SNAP_SHARE_ROLE": MARKETS,
    "F3_TARGET_AIR_YARDS": ("receptions", "receiving_yards"),
    "F8_INJURY_PRACTICE": MARKETS,
    "F9_ABSENCE_REDISTRIBUTION": MARKETS,
}
DEV = harness.PARTITIONS["DEV_2016_2022"]
SHRINK_TARGETS_M = 20.0
SNAP_RATIO_CLAMP = (0.5, 2.0)
F2_ALPHA_GRID = [round(-1.0 + 0.1 * i, 2) for i in range(26)]
F2_SCENARIOS = ("RISE_TEAMMATE_OUT_NOW", "RISE_AFTER_RECENT_TEAMMATE_ABSENCE",
                "RISE_NO_TEAMMATE_EVENT", "FALL")
F3_W_GRID = [round(0.05 * i, 2) for i in range(21)]
F8_M_GRID = [round(0.6 + 0.01 * i, 2) for i in range(71)]
F8_MIN_DEV_ROWS = 200
F9_RETENTION_GRID = [round(0.1 * i, 1) for i in range(11)]
F9_SAME_POSITION_GRID = [0.25, 0.5, 0.75, 1.0]
SOURCE_IDS = {
    "F2_SNAP_SHARE_ROLE": ["NFLVERSE_SNAP_COUNTS", "NFLVERSE_PLAYERS_CROSSWALK", "NFLVERSE_INJURIES"],
    "F3_TARGET_AIR_YARDS": ["NFLVERSE_WEEKLY_STATS_AUDITED"],
    "F8_INJURY_PRACTICE": ["NFLVERSE_INJURIES"],
    "F9_ABSENCE_REDISTRIBUTION": ["NFLVERSE_INJURIES", "NFLVERSE_WEEKLY_STATS_AUDITED"],
}


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _in_dev(row: Mapping[str, Any]) -> bool:
    return DEV[0] <= row["season"] <= DEV[1]


# ---------------------------------------------------------------------------
# Dataset (features for every scored row)
# ---------------------------------------------------------------------------

@dataclass
class Dataset:
    player_weeks: list[dict]
    provenance: dict
    scored: dict[str, list[dict]]
    k: dict[str, float]
    features: dict[tuple, dict[str, dict]] = field(default_factory=dict)
    f9_team_weeks: dict[tuple, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    injury_rows: list[dict] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def f2_scenario(snap: Mapping[str, Any], event_now: bool, event_recent: bool) -> str:
    l3, l5, trend = snap["snap_share_last3"], snap["snap_share_last5"], snap["snap_share_trend_last3_minus_prior"]
    if not (_num(l3) and _num(l5) and _num(trend)) or l5 <= 0:
        return UNKNOWN
    if trend >= F.ROLE_TREND_THRESHOLD:
        if event_now:
            return "RISE_TEAMMATE_OUT_NOW"
        if event_recent:
            return "RISE_AFTER_RECENT_TEAMMATE_ABSENCE"
        return "RISE_NO_TEAMMATE_EVENT"
    if trend <= -F.ROLE_TREND_THRESHOLD:
        return "FALL"
    return "STABLE"


def _event_type_for(position: str) -> str | None:
    group = F.pos_group(position)
    return "WR_ABSENCE" if group in ("WR", "TE") else "RB_ABSENCE" if group == "RB" else None


def build_teammate_events(player_weeks: list[dict], snap_rows: list[dict], injury_rows: list[dict]
                          ) -> list[dict]:
    """Reuse the #183 role-intelligence trigger events (WR/RB top-usage player OUT/DOUBTFUL)."""
    weekly = [{"season": r["season"], "week": r["week"], "team": r["team"],
               "opponent_team": r["opponent_team"], "player_id": r["player_id"],
               "player_display_name": r["player_name"], "position": r["position"],
               "targets": r["targets"], "carries": r["carries"]}
              for r in player_weeks if r["season_type"] == "REG" and r["position"] in prep.ROLE_POSITIONS]
    inj = [{"season": r["season"], "week": r["week"], "team": r["team"], "player_id": r["player_id"],
            "report_status": r["report_status"]} for r in injury_rows]
    usage = prep.build_player_game_usage_rows(weekly, snap_rows, [], inj, [], {})
    return rif.build_teammate_absence_trigger_events(usage, inj)


def _f9_team_week(season: int, week: int, team: str, index: F.InjuryIndex, player_hist: F.History,
                  team_hist: F.History, game_players: Mapping[tuple, set], feature_cache: dict) -> dict:
    """Pregame absent players (OUT) and recipients for one team-week."""
    def feats(pid: str) -> dict:
        key = (pid, team, season, week)
        if key not in feature_cache:
            feature_cache[key] = F.target_features(player_hist, team_hist, pid, team, season, week)
        return feature_cache[key]

    out_ids = index.team_listed(season, week, team, ("OUT",)) if index.is_final(season, week, team) else []
    unavailable = set(index.team_listed(season, week, team, ("OUT", "DOUBTFUL")))
    recent_games = [g["game_id"] for g in team_hist.prior(team, season, week, n=F.RECIPIENT_LOOKBACK_TEAM_GAMES)]
    pool = set()
    for gid in recent_games:
        pool |= game_players.get((gid, team), set())
    recipients = sorted(pool - unavailable)
    absent = []
    for pid in out_ids:
        f = feats(pid)
        last = player_hist.prior(pid, season, week, n=1)
        absent.append({"player_id": pid, "position": last[0]["position"] if last else UNKNOWN,
                       "target_share": f["target_share_last5"], "carry_share": f["carry_share_last5"]})
    rec_rows = []
    for pid in recipients:
        f = feats(pid)
        window = player_hist.prior(pid, season, week, n=5)
        window = [g for g in window if g["team"] == team]
        overlap = {}
        for a in absent:
            if window:
                overlap[a["player_id"]] = sum(a["player_id"] in game_players.get((g["game_id"], team), ())
                                              for g in window) / len(window)
        last = player_hist.prior(pid, season, week, n=1)
        rec_rows.append({"player_id": pid, "position": last[0]["position"] if last else UNKNOWN,
                         "target_share": f["target_share_last5"], "carry_share": f["carry_share_last5"],
                         "overlap": overlap})
    return {"absent": absent, "recipients": rec_rows, "final_report": index.is_final(season, week, team),
            "covered": (season, week, team) in index.team_weeks}


def f9_gains(tw: Mapping[str, Any], dimension: str, retention: float, same_position_weight: float) -> dict:
    share_key = "target_share" if dimension == "targets" else "carry_share"
    absent = [{"player_id": a["player_id"], "group": F.pos_group(a["position"]),
               "vacated_share": a[share_key]} for a in tw["absent"]]
    recipients = [{"player_id": r["player_id"], "group": F.pos_group(r["position"]),
                   "share": r[share_key], "overlap": r["overlap"]} for r in tw["recipients"]]
    return F.redistribute(absent, recipients, retention=retention, same_position_weight=same_position_weight)


def build_dataset(*, weekly_cache: Path, audit_manifest: Path, current_season_csv: Path | None,
                  snap_dir: Path, injury_dir: Path, players_csv: Path,
                  seasons: range = range(2016, 2027), first_season: int = 2015) -> Dataset:
    rows, provenance = harness.load_player_weeks(weekly_cache, audit_manifest, first_season=first_season,
                                                 current_season_csv=current_season_csv)
    scored = {m: harness.b0_rolling_mean(rows, m) for m in MARKETS}
    k = {m: harness.fit_scale_control(scored[m], m) for m in MARKETS}
    ds = Dataset(player_weeks=rows, provenance=provenance, scored=scored, k=k)

    crosswalk = F.load_crosswalk(players_csv)
    snap_rows = F.load_snap_rows(snap_dir, seasons, crosswalk)
    injury_rows = F.load_injury_rows(injury_dir, seasons)
    ds.diagnostics["snap_join"] = F.snap_join_diagnostics(snap_rows)
    ds.diagnostics["injury_rows"] = len(injury_rows)
    snap_hist = F.build_snap_history(snap_rows)
    player_hist, team_hist = F.build_usage_histories(rows)
    index = F.InjuryIndex(injury_rows)
    events = build_teammate_events(rows, snap_rows, injury_rows)
    ds.events, ds.injury_rows = events, injury_rows
    ds.diagnostics["teammate_absence_events"] = len(events)
    event_keys: dict[tuple, set] = defaultdict(set)
    for e in events:
        event_keys[(e["season"], e["team"], e["event_type"])].add((e["week"], e["removed_player_id"]))
    game_players: dict[tuple, set] = defaultdict(set)
    for r in rows:
        game_players[(r["game_id"], r["team"])].add(r["player_id"])

    feature_cache: dict = {}
    keyed: dict[tuple, dict] = {}
    for m in MARKETS:
        for r in scored[m]:
            keyed.setdefault(harness.row_key(r), r)
    for key, r in keyed.items():
        season, week, gid, pid = key
        team = r["team"]
        base = dict(season=season, week=week, game_id=gid, team=team, gsis_id=pid)
        snap = F.snap_features(snap_hist, pid, season, week)
        etype = _event_type_for(r["position"])
        now = recent = False
        if etype:
            for (w, removed) in event_keys.get((season, team, etype), ()):
                if removed == pid:
                    continue
                now |= w == week
                recent |= week - 3 <= w < week
        snap.update({"teammate_absence_event_this_week": now,
                     "teammate_absence_event_prior_3_weeks": recent,
                     "scenario": f2_scenario(snap, now, recent)})
        fkey = (pid, team, season, week)
        if fkey not in feature_cache:
            feature_cache[fkey] = F.target_features(player_hist, team_hist, pid, team, season, week)
        inj = F.injury_features(index, pid, team, season, week)
        inj["category"] = F.injury_category(inj)
        twk = (season, week, team)
        if twk not in ds.f9_team_weeks:
            ds.f9_team_weeks[twk] = _f9_team_week(season, week, team, index, player_hist, team_hist,
                                                  game_players, feature_cache)
        tw = ds.f9_team_weeks[twk]
        mine = next((x for x in tw["recipients"] if x["player_id"] == pid), None)
        f9 = {"n_teammates_listed_out": len(tw["absent"]),
              "vacated_target_share_sum": sum(a["target_share"] for a in tw["absent"] if _num(a["target_share"])),
              "vacated_carry_share_sum": sum(a["carry_share"] for a in tw["absent"] if _num(a["carry_share"])),
              "is_pregame_recipient": mine is not None,
              "max_overlap_with_absent": max(mine["overlap"].values()) if mine and mine["overlap"] else 0.0,
              "report_covered": tw["covered"]}
        ds.features[key] = {
            "F2_SNAP_SHARE_ROLE": contract.feature_row("F2_SNAP_SHARE_ROLE", **base, features=snap,
                                                       source_ids=SOURCE_IDS["F2_SNAP_SHARE_ROLE"]),
            "F3_TARGET_AIR_YARDS": contract.feature_row("F3_TARGET_AIR_YARDS", **base,
                                                        features=feature_cache[fkey],
                                                        source_ids=SOURCE_IDS["F3_TARGET_AIR_YARDS"]),
            "F8_INJURY_PRACTICE": contract.feature_row("F8_INJURY_PRACTICE", **base, features=inj,
                                                       source_ids=SOURCE_IDS["F8_INJURY_PRACTICE"]),
            "F9_ABSENCE_REDISTRIBUTION": contract.feature_row("F9_ABSENCE_REDISTRIBUTION", **base, features=f9,
                                                              source_ids=SOURCE_IDS["F9_ABSENCE_REDISTRIBUTION"]),
        }
    ds.diagnostics["feature_keys"] = len(ds.features)
    ds.diagnostics["f9_team_weeks"] = len(ds.f9_team_weeks)
    return ds


# ---------------------------------------------------------------------------
# Component adjustments (pure functions of features + params)
# ---------------------------------------------------------------------------

def f3_estimate(f: Mapping[str, Any], market: str, p: Mapping[str, float]) -> float | str:
    share, vol, tgt = f["target_share_last5"], f["team_targets_per_game_last5"], f["targets_last5"]
    if not (_num(share) and _num(vol)) or tgt <= 0 or not _num(f["adot_last5"]):
        return UNKNOWN
    e_targets = share * vol
    adot = f["adot_last5"]
    if market == "receptions":
        prior = min(max(p["c0"] + p["c1"] * adot, 0.05), 1.0)
        rate = (f["receptions_last5"] + SHRINK_TARGETS_M * prior) / (tgt + SHRINK_TARGETS_M)
    elif market == "receiving_yards":
        prior = max(p["y0"] + p["y1"] * adot, 0.0)
        rate = (f["receiving_yards_last5"] + SHRINK_TARGETS_M * prior) / (tgt + SHRINK_TARGETS_M)
    else:
        return UNKNOWN
    return e_targets * rate


def f2_multiplier(f: Mapping[str, Any], alphas: Mapping[str, float]) -> float:
    scenario = f["scenario"]
    alpha = alphas.get(scenario, 0.0)
    if scenario == UNKNOWN or alpha == 0.0:
        return 1.0
    ratio = min(max(f["snap_share_last3"] / f["snap_share_last5"], SNAP_RATIO_CLAMP[0]), SNAP_RATIO_CLAMP[1])
    return ratio ** alpha


def f2_broad_multiplier(f: Mapping[str, Any], alpha: float) -> float:
    l3, l5 = f["snap_share_last3"], f["snap_share_last5"]
    if not (_num(l3) and _num(l5)) or l5 <= 0 or alpha == 0.0:
        return 1.0
    return min(max(l3 / l5, SNAP_RATIO_CLAMP[0]), SNAP_RATIO_CLAMP[1]) ** alpha


def f8_multiplier(f: Mapping[str, Any], table: Mapping[str, float]) -> float:
    return table.get(f["category"], 1.0) if f["category"] != UNKNOWN else 1.0


def f9_multiplier_table(ds: Dataset, market: str, retention: float, same_w: float) -> dict[tuple, float]:
    """(season, week, team, player_id) -> multiplier, from pregame team-week redistribution."""
    dimension = "carries" if market == "rushing_yards" else "targets"
    share_key = "carry_share" if dimension == "carries" else "target_share"
    out: dict[tuple, float] = {}
    for (season, week, team), tw in ds.f9_team_weeks.items():
        if not tw["absent"]:
            continue
        gains = f9_gains(tw, dimension, retention, same_w)["gains"]
        for r in tw["recipients"]:
            g, s = gains.get(r["player_id"], 0.0), r[share_key]
            if g > 0 and _num(s) and s > 0:
                out[(season, week, team, r["player_id"])] = (s + g) / s
    return out


# ---------------------------------------------------------------------------
# DEV-only parameter fitting
# ---------------------------------------------------------------------------

def _mae(pairs: list[tuple[float, float]]) -> float:
    return statistics.fmean(abs(p - a) for p, a in pairs) if pairs else float("inf")


def _dev_rows(ds: Dataset, market: str) -> list[dict]:
    return [r for r in ds.scored[market] if r["b0"] is not None and _in_dev(r)]


def _wls(xs: list[float], ys: list[float], ws: list[float]) -> tuple[float, float]:
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys))
    slope = sxy / sxx if sxx > 0 else 0.0
    return my - slope * mx, slope


def fit_params(ds: Dataset) -> dict[str, Any]:
    """Fit every declared parameter on DEV_2016_2022 rows only."""
    params: dict[str, Any] = {"k": dict(ds.k), "fit_partition": "DEV_2016_2022",
                              "declared": {"SHRINK_TARGETS_M": SHRINK_TARGETS_M,
                                           "SNAP_RATIO_CLAMP": SNAP_RATIO_CLAMP,
                                           "ROLE_TREND_THRESHOLD": F.ROLE_TREND_THRESHOLD,
                                           "F8_MIN_DEV_ROWS": F8_MIN_DEV_ROWS,
                                           "REDISTRIBUTION_PER_PLAYER_CAP": F.REDISTRIBUTION_PER_PLAYER_CAP}}
    # F3 aDOT -> rate lines (target-weighted realized per-game rates; role ~= targets).
    f3: dict[str, Any] = {}
    for market, key in (("receptions", "c"), ("receiving_yards", "y")):
        xs, ys, ws = [], [], []
        for r in _dev_rows(ds, market):
            f = ds.features[harness.row_key(r)]["F3_TARGET_AIR_YARDS"]["features"]
            if _num(f["adot_last5"]) and r["role"] > 0:
                xs.append(f["adot_last5"]); ys.append(r["actual"] / r["role"]); ws.append(r["role"])
        f3[key + "0"], f3[key + "1"] = _wls(xs, ys, ws)
    for market in ("receptions", "receiving_yards"):
        rows = _dev_rows(ds, market)
        k = ds.k[market]
        est = [(f3_estimate(ds.features[harness.row_key(r)]["F3_TARGET_AIR_YARDS"]["features"], market, f3),
                k * r["b0"], r["actual"]) for r in rows]
        best = min(F3_W_GRID, key=lambda w: _mae([((w * e + (1 - w) * b) if _num(e) else b, a)
                                                  for e, b, a in est]))
        f3["w_" + market] = best
    params["F3"] = f3
    # F2 per-scenario alphas and the broad reference alpha.
    f2: dict[str, Any] = {}
    for market in MARKETS:
        rows = _dev_rows(ds, market)
        k = ds.k[market]
        feats = [(ds.features[harness.row_key(r)]["F2_SNAP_SHARE_ROLE"]["features"], k * r["b0"], r["actual"])
                 for r in rows]
        alphas = {"STABLE": 0.0}
        for s in F2_SCENARIOS:
            sub = [(f, b, a) for f, b, a in feats if f["scenario"] == s]
            alphas[s] = min(F2_ALPHA_GRID, key=lambda al: (_mae([(b * f2_multiplier(f, {s: al}), a)
                                                                 for f, b, a in sub]), abs(al))) if sub else 0.0
        broad = min(F2_ALPHA_GRID, key=lambda al: (_mae([(b * f2_broad_multiplier(f, al), a)
                                                         for f, b, a in feats]), abs(al)))
        f2[market] = {"alphas": alphas, "broad_alpha": broad,
                      "dev_rows_by_scenario": {s: sum(1 for f, _b, _a in feats if f["scenario"] == s)
                                               for s in F2_SCENARIOS + ("STABLE", UNKNOWN)}}
    params["F2"] = f2
    # F8 category multipliers.
    f8: dict[str, Any] = {}
    for market in MARKETS:
        rows = _dev_rows(ds, market)
        k = ds.k[market]
        by_cat: dict[str, list] = defaultdict(list)
        for r in rows:
            by_cat[ds.features[harness.row_key(r)]["F8_INJURY_PRACTICE"]["features"]["category"]].append(
                (k * r["b0"], r["actual"]))
        table, counts = {}, {c: len(v) for c, v in sorted(by_cat.items())}
        for cat, pairs in by_cat.items():
            if cat in (UNKNOWN, F.NOT_LISTED) or len(pairs) < F8_MIN_DEV_ROWS:
                continue
            m = min(F8_M_GRID, key=lambda mm: (_mae([(b * mm, a) for b, a in pairs]), abs(mm - 1.0)))
            if m != 1.0:
                table[cat] = m
        f8[market] = {"multipliers": table, "dev_rows_by_category": counts}
    params["F8"] = f8
    # F9 retention / same-position weight.
    f9: dict[str, Any] = {}
    for market in MARKETS:
        rows = _dev_rows(ds, market)
        k = ds.k[market]
        best, best_loss = (0.0, 0.5), None
        for ret in F9_RETENTION_GRID:
            for sw in F9_SAME_POSITION_GRID:
                table = f9_multiplier_table(ds, market, ret, sw)
                if not table and ret > 0:
                    continue
                loss = _mae([(k * r["b0"] * table.get((r["season"], r["week"], r["team"], r["player_id"]), 1.0),
                              r["actual"]) for r in rows])
                if best_loss is None or loss < best_loss - 1e-12:
                    best, best_loss = (ret, sw), loss
        f9[market] = {"retention": best[0], "same_position_weight": best[1]}
    params["F9"] = f9
    return params


# ---------------------------------------------------------------------------
# The consumer
# ---------------------------------------------------------------------------

ABLATIONS = ("F2_SNAP_SHARE_ROLE", "F3_TARGET_AIR_YARDS", "F8_INJURY_PRACTICE",
             "F9_ABSENCE_REDISTRIBUTION", "ALL")


def predict(ds: Dataset, market: str, params: Mapping[str, Any], ablation: str,
            *, f2_only_scenario: str | None = None, f2_broad: bool = False
            ) -> tuple[dict[tuple, float], dict[tuple, list[str]], dict[str, int]]:
    """Predictions for every scored row with a B0 value, plus per-row attribution.

    Returns (predictions, changed_by, fallback_counts).
    """
    use = set(FACTOR_MARKETS) if ablation == "ALL" else {ablation}
    use = {f for f in use if market in FACTOR_MARKETS[f]}
    k = params["k"][market]
    f9_table = (f9_multiplier_table(ds, market, params["F9"][market]["retention"],
                                    params["F9"][market]["same_position_weight"])
                if "F9_ABSENCE_REDISTRIBUTION" in use else {})
    alphas = dict(params["F2"][market]["alphas"])
    if f2_only_scenario is not None:
        alphas = {s: (a if s == f2_only_scenario else 0.0) for s, a in alphas.items()}
    preds: dict[tuple, float] = {}
    changed: dict[tuple, list[str]] = {}
    fallbacks: dict[str, int] = defaultdict(int)
    for r in ds.scored[market]:
        if r["b0"] is None:
            continue
        key = harness.row_key(r)
        fr = ds.features[key]
        pred = k * r["b0"]
        who: list[str] = []
        if "F3_TARGET_AIR_YARDS" in use:
            est = f3_estimate(fr["F3_TARGET_AIR_YARDS"]["features"], market, params["F3"])
            w = params["F3"]["w_" + market]
            if _num(est):
                new = w * est + (1 - w) * pred
                if abs(new - pred) > 1e-9:
                    who.append("F3_TARGET_AIR_YARDS")
                pred = new
            else:
                fallbacks["F3_UNKNOWN_TO_SCALE_BASE"] += 1
        if "F2_SNAP_SHARE_ROLE" in use:
            f2f = fr["F2_SNAP_SHARE_ROLE"]["features"]
            mult = (f2_broad_multiplier(f2f, params["F2"][market]["broad_alpha"]) if f2_broad
                    else f2_multiplier(f2f, alphas))
            if f2f["scenario"] == UNKNOWN:
                fallbacks["F2_UNKNOWN_TO_SCALE_BASE"] += 1
            if abs(mult - 1.0) > 1e-12:
                who.append("F2_SNAP_SHARE_ROLE")
            pred *= mult
        if "F8_INJURY_PRACTICE" in use:
            f8f = fr["F8_INJURY_PRACTICE"]["features"]
            mult = f8_multiplier(f8f, params["F8"][market]["multipliers"])
            if f8f["category"] == UNKNOWN:
                fallbacks["F8_UNKNOWN_TO_SCALE_BASE"] += 1
            if abs(mult - 1.0) > 1e-12:
                who.append("F8_INJURY_PRACTICE")
            pred *= mult
        if "F9_ABSENCE_REDISTRIBUTION" in use:
            mult = f9_table.get((r["season"], r["week"], r["team"], r["player_id"]), 1.0)
            if abs(mult - 1.0) > 1e-12:
                who.append("F9_ABSENCE_REDISTRIBUTION")
            pred *= mult
        preds[key] = pred
        changed[key] = who
    return preds, changed, dict(fallbacks)


__all__ = ["ABLATIONS", "CONSUMER", "Dataset", "FACTOR_MARKETS", "MARKETS", "build_dataset",
           "f2_multiplier", "f2_scenario", "f3_estimate", "f8_multiplier", "f9_gains",
           "f9_multiplier_table", "fit_params", "predict"]
