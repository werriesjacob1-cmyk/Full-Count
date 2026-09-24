#!/usr/bin/env python3
"""Current-week (live research) feature rows for F2, F3, F8, F9.

    PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_live \
        --target-season 2026 --target-week 3 --out <dir>/live_2026_w3.json

Research only: no pick, no selector, no workflow change, and the official
inactives gate is not touched. Every row carries a timestamped
`information_cutoff` = the latest LOCAL RETRIEVAL time (file mtime, UTC) of
the sources that row used. The upstream publication time is not recorded by
nflverse, so retrieval time is an upper bound on availability: if it is
before kickoff, the information was pregame. Each row is validated with
`contract.validate_feature_row(row, prediction_cutoff=<that game's kickoff>)`.

Injury-dependent pieces (F8, F9 and F2's teammate-out scenario split) are
emitted only for team-weeks whose FINAL report is already filed (at least one
game-status designation in the file for that team-week); every other team's
game status is `UNKNOWN_FINAL_REPORT_NOT_YET_FILED`, and the consumer falls
back to the scale base for those pieces.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from nfl.research import role_intelligence_data_prep as prep
from nfl.research import role_intelligence_features as rif
from nfl.research.tier1 import contract, harness
from nfl.research.tier1 import player_opportunity_challenger as C
from nfl.research.tier1 import player_opportunity_evaluate as E
from nfl.research.tier1 import player_opportunity_features as F

UNKNOWN = contract.UNKNOWN
PENDING = "UNKNOWN_PENDING_FINAL_INJURY_REPORT"


def retrieved_at(path: Path) -> str:
    ts = datetime.fromtimestamp(os.stat(path).st_mtime, tz=timezone.utc).replace(microsecond=0)
    return ts.isoformat().replace("+00:00", "Z")


def final_report_team_weeks(injury_rows: list[dict], season: int, week: int) -> set[tuple[int, int, str]]:
    """A live team-week counts as FINAL only if some row already carries a game status."""
    return {(r["season"], r["week"], r["team"]) for r in injury_rows
            if r["season"] == season and r["week"] == week
            and r["report_status"] not in (F.NONE_DESIGNATED,)}


def live_events(rows: list[dict], snap_rows: list[dict], injury_rows: list[dict], season: int, week: int,
                teams: Iterable[str]) -> list[dict]:
    """#183 trigger events for the live week via placeholder usage rows (no realized numbers).

    `rif._top_usage_player_per_team_week` ranks only team-weeks that have a usage row, and a
    future week has none. A placeholder row with every count None never enters a running mean
    (its realized shares are UNKNOWN) and its id can never be a ranked candidate.
    """
    weekly = [{"season": r["season"], "week": r["week"], "team": r["team"],
               "opponent_team": r["opponent_team"], "player_id": r["player_id"],
               "player_display_name": r["player_name"], "position": r["position"],
               "targets": r["targets"], "carries": r["carries"]}
              for r in rows if r["season_type"] == "REG" and r["position"] in prep.ROLE_POSITIONS]
    inj = [{"season": r["season"], "week": r["week"], "team": r["team"], "player_id": r["player_id"],
            "report_status": r["report_status"]} for r in injury_rows]
    usage = prep.build_player_game_usage_rows(weekly, snap_rows, [], inj, [], {})
    for team in teams:
        for pos in ("WR", "RB"):
            usage.append({"season": season, "week": week, "team": team, "opponent_team": UNKNOWN,
                          "player_id": f"LIVE_PLACEHOLDER_{team}_{pos}", "player_display_name": "",
                          "position": pos, "targets": None, "carries": None, "team_targets": None,
                          "team_carries": None, "offense_snaps": None, "team_offense_snaps": None,
                          **{k: None for k in ("red_zone_targets", "red_zone_carries",
                                               "team_red_zone_opportunities", "goal_line_carries",
                                               "team_goal_line_carries", "third_down_targets",
                                               "third_down_carries", "team_third_down_opportunities",
                                               "two_minute_targets", "two_minute_carries",
                                               "team_two_minute_opportunities")},
                          "depth_team": None, "injury_report_status": None})
    return [e for e in rif.build_teammate_absence_trigger_events(usage, inj)
            if not e["removed_player_id"].startswith("LIVE_PLACEHOLDER_")]


def live_b0(rows: list[dict], market: str, season: int, week: int) -> dict[str, float]:
    """The harness B0 rule (mean of last 5 role appearances, min 3) as of (season, week)."""
    actual_fn, role_fn = harness.MARKETS[market]
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
    for r in rows:
        if (r["season"], r["week"]) >= (season, week):
            continue
        if role_fn(r) > 0:
            hist[r["player_id"]].append((actual_fn(r), role_fn(r)))
    return {pid: statistics.fmean(a for a, _ in d) for pid, d in hist.items()
            if len(d) >= 3 and statistics.fmean(ro for _, ro in d) > 0}


def build_live(*, target_season: int, target_week: int, params: dict, paths: dict) -> dict[str, Any]:
    rows, provenance = harness.load_player_weeks(paths["weekly_cache"], paths["audit_manifest"],
                                                 first_season=2015,
                                                 current_season_csv=paths["current_season_csv"])
    rows = [r for r in rows if (r["season"], r["week"]) < (target_season, target_week)]
    seasons = range(2016, target_season + 1)
    crosswalk = F.load_crosswalk(paths["players_csv"])
    snap_rows = [r for r in F.load_snap_rows(paths["snap_dir"], seasons, crosswalk)
                 if (r["season"], r["week"]) < (target_season, target_week)]
    injury_all = F.load_injury_rows(paths["injury_dir"], seasons)
    injury_rows = [r for r in injury_all if (r["season"], r["week"]) <= (target_season, target_week)]
    finals = final_report_team_weeks(injury_rows, target_season, target_week)
    history_final = {(r["season"], r["week"], r["team"]) for r in injury_rows
                     if (r["season"], r["week"]) < (target_season, target_week)}
    index = F.InjuryIndex(injury_rows, final_report_team_weeks=finals | history_final)
    snap_hist = F.build_snap_history(snap_rows)
    player_hist, team_hist = F.build_usage_histories(rows)
    game_players: dict[tuple, set] = defaultdict(set)
    for r in rows:
        game_players[(r["game_id"], r["team"])].add(r["player_id"])

    games = {}
    with paths["games_csv"].open(encoding="utf-8") as h:
        for g in csv.DictReader(h):
            if int(g["season"]) == target_season and int(g["week"]) == target_week:
                for team in (g["away_team"], g["home_team"]):
                    games[F.norm_team(team)] = g["game_id"]
    kick = {team: ko for (s, w, team), ko in E.kickoffs(paths["games_csv"]).items()
            if s == target_season and w == target_week}
    events = live_events(rows, snap_rows, injury_rows, target_season, target_week, sorted(games))
    event_now = defaultdict(set)
    event_recent = defaultdict(set)
    for e in events:
        if e["season"] != target_season:
            continue
        if e["week"] == target_week:
            event_now[(e["team"], e["event_type"])].add(e["removed_player_id"])
        elif target_week - 3 <= e["week"] < target_week:
            event_recent[(e["team"], e["event_type"])].add(e["removed_player_id"])

    t_stats = retrieved_at(paths["current_season_csv"])
    t_snap = max(retrieved_at(paths["snap_dir"] / f"snap_counts_{target_season}.csv"),
                 retrieved_at(paths["players_csv"]))
    t_inj = retrieved_at(paths["injury_dir"] / f"injuries_{target_season}.csv")
    cutoffs = {"F2_SNAP_SHARE_ROLE": max(t_snap, t_inj), "F3_TARGET_AIR_YARDS": t_stats,
               "F8_INJURY_PRACTICE": t_inj, "F9_ABSENCE_REDISTRIBUTION": max(t_inj, t_stats)}

    # Universe: players whose latest appearance is this season with any target or carry in
    # their last five appearances; team = team of that latest appearance.
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["player_id"]] = r
    b0 = {m: live_b0(rows, m, target_season, target_week) for m in C.MARKETS}
    feature_rows, predictions, rejected = [], [], []
    f9_cache: dict[tuple, dict] = {}
    tcache: dict = {}
    for pid, last in sorted(latest.items()):
        team = last["team"]
        if last["season"] != target_season or team not in games:
            continue
        tf = F.target_features(player_hist, team_hist, pid, team, target_season, target_week)
        if tf["targets_last5"] <= 0 and tf["carries_last5"] <= 0:
            continue
        base = dict(season=target_season, week=target_week, game_id=games[team], team=team, gsis_id=pid)
        final = (target_season, target_week, team) in finals
        snap = F.snap_features(snap_hist, pid, target_season, target_week)
        etype = C._event_type_for(last["position"])
        now = bool(etype) and bool(event_now[(team, etype)] - {pid})
        recent = bool(etype) and bool(event_recent[(team, etype)] - {pid})
        scenario = C.f2_scenario(snap, now, recent)
        if scenario.startswith("RISE") and not final:
            scenario = PENDING
        snap.update({"teammate_absence_event_this_week": now if final else PENDING,
                     "teammate_absence_event_prior_3_weeks": recent, "scenario": scenario})
        inj = F.injury_features(index, pid, team, target_season, target_week)
        inj["category"] = F.injury_category(inj)
        twk = (target_season, target_week, team)
        if twk not in f9_cache:
            f9_cache[twk] = C._f9_team_week(target_season, target_week, team, index, player_hist, team_hist,
                                            game_players, tcache)
        tw = f9_cache[twk]
        mine = next((x for x in tw["recipients"] if x["player_id"] == pid), None)
        f9 = {"final_report_filed": final,
              "n_teammates_listed_out": len(tw["absent"]) if final else PENDING,
              "n_out_with_prior_target_share": (sum(1 for a in tw["absent"] if C._num(a["target_share"])
                                                    and a["target_share"] > 0) if final else PENDING),
              "n_out_with_prior_carry_share": (sum(1 for a in tw["absent"] if C._num(a["carry_share"])
                                                   and a["carry_share"] > 0) if final else PENDING),
              "vacated_target_share_sum": (sum(a["target_share"] for a in tw["absent"]
                                               if C._num(a["target_share"])) if final else PENDING),
              "is_pregame_recipient": mine is not None}
        comp: dict[str, dict[str, Any]] = {}
        for m in C.MARKETS:
            p9 = params["F9"][m]
            table9 = {}
            if final and tw["absent"]:
                dim = "carries" if m == "rushing_yards" else "targets"
                gains = C.f9_gains(tw, dim, p9["retention"], p9["same_position_weight"])["gains"]
                share = mine["carry_share" if dim == "carries" else "target_share"] if mine else UNKNOWN
                g = gains.get(pid, 0.0)
                table9 = {"gain": g, "multiplier": (share + g) / share if C._num(share) and share > 0 else 1.0}
            m2 = C.f2_multiplier(snap, params["F2"][m]["alphas"]) if scenario != PENDING else 1.0
            m8 = C.f8_multiplier(inj, params["F8"][m]["multipliers"])
            m9 = table9.get("multiplier", 1.0)
            est3 = C.f3_estimate(tf, m, params["F3"]) if m != "rushing_yards" else UNKNOWN
            comp[m] = {"f2_multiplier": m2, "f8_multiplier": m8, "f9_multiplier": m9, "f3_estimate": est3}
            if pid in b0[m]:
                pred = params["k"][m] * b0[m][pid]
                if C._num(est3):
                    w = params["F3"]["w_" + m]
                    pred = w * est3 + (1 - w) * pred
                predictions.append({"gsis_id": pid, "team": team, "game_id": games[team], "market": m,
                                    "b0": b0[m][pid], "scale_control": params["k"][m] * b0[m][pid],
                                    "challenger_all": pred * m2 * m8 * m9,
                                    "research_only": True})
        f9.update({f"{m}_{k}": v for m in C.MARKETS for k, v in comp[m].items() if k == "f9_multiplier"})
        snap.update({f"{m}_f2_multiplier": comp[m]["f2_multiplier"] for m in C.MARKETS})
        inj.update({f"{m}_f8_multiplier": comp[m]["f8_multiplier"] for m in C.MARKETS})
        tf.update({f"{m}_f3_estimate": comp[m]["f3_estimate"] for m in ("receptions", "receiving_yards")})
        for fid, feats in (("F2_SNAP_SHARE_ROLE", snap), ("F3_TARGET_AIR_YARDS", tf),
                           ("F8_INJURY_PRACTICE", inj), ("F9_ABSENCE_REDISTRIBUTION", f9)):
            row = contract.feature_row(fid, **base, features=feats, source_ids=C.SOURCE_IDS[fid],
                                       information_cutoff=cutoffs[fid])
            prediction_cutoff = kick[team].isoformat().replace("+00:00", "Z")
            try:
                contract.validate_feature_row(row, prediction_cutoff=prediction_cutoff)
            except contract.FeatureContractError as exc:
                rejected.append({"gsis_id": pid, "factor_id": fid, "reason": str(exc)})
                continue
            row["prediction_cutoff_kickoff_utc"] = prediction_cutoff
            feature_rows.append(row)
    status = {
        "F2_SNAP_SHARE_ROLE": "BUILT (teammate-out split PENDING for teams without a final report)",
        "F3_TARGET_AIR_YARDS": "BUILT",
        "F8_INJURY_PRACTICE": ("PARTIAL: final report filed for " + ", ".join(sorted(t for _s, _w, t in finals))
                               + "; every other team is UNKNOWN_FINAL_REPORT_NOT_YET_FILED until the Friday file"),
        "F9_ABSENCE_REDISTRIBUTION": "PARTIAL: same final-report dependency as F8",
    }
    return {"target_season": target_season, "target_week": target_week,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "information_cutoffs": cutoffs,
            "information_cutoff_basis": "LOCAL_RETRIEVAL_TIME_UPPER_BOUND (file mtime, UTC)",
            "final_report_team_weeks": sorted(t for _s, _w, t in finals), "live_status": status,
            "n_feature_rows": len(feature_rows), "rejected_rows": rejected,
            "feature_rows": feature_rows, "research_predictions": predictions,
            "provenance": provenance, "source_sha256": E.source_hashes(paths),
            "params_file": "frozen_params.json (DEV_2016_2022 fit)"}


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-season", type=int, default=2026)
    ap.add_argument("--target-week", type=int, default=3)
    ap.add_argument("--params", type=Path, default=E.OUT_DIR / "frozen_params.json")
    ap.add_argument("--out", type=Path, default=E.OUT_DIR / "live_2026_w3_features.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    params = json.loads(args.params.read_text())
    out = build_live(target_season=args.target_season, target_week=args.target_week, params=params,
                     paths=dict(E.DEFAULTS))
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: out[k] for k in ("information_cutoffs", "final_report_team_weeks", "live_status",
                                          "n_feature_rows")}, indent=1))
    print("rejected", len(out["rejected_rows"]), "predictions", len(out["research_predictions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
