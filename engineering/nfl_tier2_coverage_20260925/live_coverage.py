#!/usr/bin/env python3
"""Live F11/F12 research predictions for upcoming games (EXPLORATORY; never B0).

Uses only frozen parameters (coverage_params.json, coverage_family_params.json)
and strictly prior data: coverage features from completed seasons S-1/S-2,
B0 = the harness rule over weekly rows before the target week. Output is for
prospective sealing alongside the Tier 1 protocol seal; F11/F12 are REJECTED
historically, so these rows are exploratory evidence, not a hypothesis test.

    PYTHONPATH=<repo> python3 live_coverage.py --season 2026 --week 3 \
        --games g1,g2 --out <dir>/coverage_exploratory.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import evaluate_coverage as EC
from nfl.research.tier1 import harness as H
from nfl.research.tier2 import coverage_challenger as C
from nfl.research.tier2 import coverage_data as D
from nfl.research.tier2 import coverage_features as F

HERE = Path(__file__).resolve().parent


def next_game_rows(rows, season, week, games, role_fn):
    """One synthetic REG target-game row per player whose latest appearance is this
    season with a target team, templated on his last ROLE appearance for the market
    (so the harness scores it); b0_rolling_mean then uses only his prior role
    appearances. Team/opponent come from the latest appearance."""
    team_game = {}
    for gid in games:
        _s, _w, away, home = gid.split("_")
        team_game[away], team_game[home] = (gid, home), (gid, away)
    latest, last_role = {}, {}
    for r in rows:
        latest[r["player_id"]] = r
        if role_fn(r) > 0:
            last_role[r["player_id"]] = r
    return [{**last_role[pid], "season": season, "week": week, "season_type": "REG", "team": cur["team"],
             "game_id": team_game[cur["team"]][0], "opponent_team": team_game[cur["team"]][1]}
            for pid, cur in latest.items()
            if pid in last_role and cur["season"] == season and cur["team"] in team_game]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    games = sorted(a.games.split(","))
    rows, positions, tables, hc, prov = EC.load()
    rows = [r for r in rows if (r["season"], r["week"]) < (a.season, a.week)]
    mz = json.loads((HERE / "coverage_params.json").read_text())["params"]
    fam = json.loads((HERE / "coverage_family_params.json").read_text())["params"]
    avail = set(D.COVERAGE_SEASONS)
    win = F._window(a.season, avail)
    league_man, league_mix = F.league_man_share(tables, win), F.league_family_mix(tables, win)
    out = []
    for market in EC.MARKETS:
        _act, role_fn = H.MARKETS[market]
        fakes = next_game_rows(rows, a.season, a.week, games, role_fn)
        scored = [s for s in H.b0_rolling_mean(rows + fakes, market)
                  if (s["season"], s["week"]) == (a.season, a.week) and s["b0"] is not None]
        k_mz, k_fam = mz[market]["k"], fam[market]["k"]
        for s in scored:
            pid, pos, opp = s["player_id"], positions.get(s["player_id"], "OTHER"), D.team(s["opponent_team"])
            rec = F.receiver_profile(tables, pid, pos, a.season, avail)
            dfn = F.defense_profile(tables, opp, a.season, avail, hc.get((a.season, opp)))
            posp = F.position_profile(tables, pos, a.season, avail)
            recf = F.receiver_family_profile(tables, pid, rec, a.season, avail)
            dfam = F.defense_family_mix(tables, opp, a.season, avail, hc.get((a.season, opp)))
            posf = F.position_family_profile(tables, pos, a.season, avail)
            modes = {}
            for m in C.MODES:
                q, why = C.matchup_ratio(m, market, rec, posp, dfn, league_man)
                modes[m] = {"ratio": q, "reason": why, "alpha_frozen": mz[market]["modes"][m]["alpha"],
                            "prediction_frozen": C.predict(s["b0"], k_mz, q, mz[market]["modes"][m]["alpha"]),
                            "prediction_alpha1_sensitivity": C.predict(s["b0"], k_mz, q, 1.0)}
            for m in C.FAMILY_MODES:
                q, why = C.family_ratio(m, market, recf, posf, dfam, league_mix)
                modes[m] = {"ratio": q, "reason": why, "alpha_frozen": fam[market]["modes"][m]["alpha"],
                            "prediction_frozen": C.predict(s["b0"], k_fam, q, fam[market]["modes"][m]["alpha"]),
                            "prediction_alpha1_sensitivity": C.predict(s["b0"], k_fam, q, 1.0)}
            out.append({"game_id": s["game_id"], "gsis_id": pid, "team": s["team"], "opponent": opp,
                        "position": pos, "market": market, "b0": s["b0"], "scale_control": k_mz * s["b0"],
                        "receiver_man_zone": ({b: {kk: rec["bins"][b][kk] for kk in ("target_rate", "n_onfield")}
                                               for b in F.BINS} | {"faced_man_share": rec["exposure_man_share"]})
                        if rec.get("status") == "OK" else rec.get("status"),
                        "opponent_man_share": dfn.get("man_share") if dfn.get("status") == "OK" else dfn.get("status"),
                        "opponent_family_mix": dfam.get("mix") if dfam.get("status") == "OK" else dfam.get("status"),
                        "opponent_regime": dfn.get("regime"), "dc_identity": D.UNKNOWN, "modes": modes})
    body = {"label": "EXPLORATORY_RESEARCH_ONLY_NOT_A_PICK", "season": a.season, "week": a.week, "games": games,
            "feature_window": win, "information_cutoff_basis":
            "coverage: completed seasons only (participation released after postseason); "
            "B0: weekly rows before the target week (pinned stats file hash in provenance)",
            "params_sha256": {p: hashlib.sha256((HERE / p).read_bytes()).hexdigest()
                              for p in ("coverage_params.json", "coverage_family_params.json")},
            "provenance": prov, "rows": out}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n")
    print("rows", len(out))


if __name__ == "__main__":
    main()
