"""F15 research: observed personnel-conditioned field presence -> receptions.

Personnel is an on-field position composition, not a formation, route, target,
blocking assignment, or play call. This module is separate from authoritative B0.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from nfl.research.receptions_baseline_research import rolling_predictions

SOURCES = {
    "participation": {"sha256": "b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9", "bytes": 49688308, "published_at": "2025-09-04T10:24:49Z"},
    "ftn": {"sha256": "6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb", "bytes": 8254908, "published_at": "2025-09-01T01:29:37Z"},
    "stats_2023": {"sha256": "f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17"},
    "stats_2024": {"sha256": "3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759"},
    "stats_2025": {"sha256": "e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"},
}
MODEL_CUTOFF = "2025-09-11T00:00:00Z"  # Before regular-season week 2.
MIN_TEAM = 400
MIN_PLAYER = 100
RIDGE = 10.0
DEV = range(2, 9)
HELD = range(9, 19)
POSITION = {"WR", "TE", "RB", "FB"}


def verify(path: Path, meta: dict) -> None:
    if "bytes" in meta and path.stat().st_size != meta["bytes"]:
        raise ValueError("source byte count mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
        raise ValueError("source SHA-256 mismatch")
    if meta.get("published_at", "0000") >= MODEL_CUTOFF:
        raise ValueError("source published after model information cutoff")


def classify(ids: list[str], positions: list[str], label: str) -> tuple[str, dict[str, int]] | None:
    """Only a complete, internally consistent 11-man observation is usable."""
    if len(ids) != len(positions) or len(ids) != 11 or len(set(ids)) != 11:
        return None
    if any(not re.fullmatch(r"00-\d{7}", x) for x in ids):
        return None
    c = Counter(positions)
    if c["QB"] != 1 or sum(c[x] for x in ("C", "G", "T")) != 5:
        return None
    if set(c) - {"QB", "C", "G", "T", "RB", "FB", "TE", "WR"}:
        return None
    # The upstream explicit composition is an independent disagreement check.
    found = Counter({name: int(n) for n, name in re.findall(r"(\d+) ([A-Z]+)", label)})
    if found != c:
        return None
    backs = c["RB"] + c["FB"]
    tight = c["TE"]
    if backs + tight + c["WR"] != 5:
        return None
    return f"{backs}{tight}", {"WR": c["WR"], "TE": tight, "RB": backs, "FB": backs}


def load_profiles(participation: Path, ftn: Path) -> dict:
    verify(participation, SOURCES["participation"])
    verify(ftn, SOURCES["ftn"])
    chart = set()
    with ftn.open(encoding="utf-8-sig", newline="") as h:
        for r in csv.DictReader(h):
            key = (r["nflverse_game_id"], r["nflverse_play_id"])
            if key in chart:
                raise ValueError("duplicate FTN game/play")
            chart.add(key)
    team = defaultdict(Counter)
    player = defaultdict(Counter)
    positions_by_player = defaultdict(Counter)
    excluded = Counter()
    seen = set()
    with participation.open(encoding="utf-8-sig", newline="") as h:
        for r in csv.DictReader(h):
            key = (r["nflverse_game_id"], r["play_id"])
            if key in seen:
                raise ValueError("duplicate participation game/play")
            seen.add(key)
            if key not in chart:
                excluded["NO_EXACT_FTN_PLAY"] += 1
                continue
            game = key[0].split("_")
            offense = r["possession_team"]
            if len(game) != 4 or game[0] != "2024" or offense not in game[2:]:
                excluded["INVALID_GAME_OR_OFFENSE"] += 1
                continue
            ids = r["offense_players"].split(";")
            pos = r["offense_positions"].split(";")
            classified = classify(ids, pos, r["offense_personnel"])
            if classified is None:
                excluded["UNKNOWN_INCOMPLETE_OR_CONTRADICTORY_PACKAGE"] += 1
                continue
            group, slots = classified
            team[offense][group] += 1
            for pid, position in zip(ids, pos):
                if position in POSITION:
                    player[(pid, offense)][group] += 1
                    positions_by_player[(pid, offense)][position] += 1
    return {"team": team, "player": player, "positions": positions_by_player,
            "excluded": dict(excluded), "chart_plays": len(chart),
            "source_sha256": {k: SOURCES[k]["sha256"] for k in ("participation", "ftn")},
            "source_published_at": max(SOURCES[k]["published_at"] for k in ("participation", "ftn"))}


def feature(row: dict, profiles: dict) -> dict:
    if row["season"] != 2025 or row["week"] < 2 or row.get("b0") is None:
        return {"status": "NO_ADJUSTMENT", "reason": "OUTSIDE_PRIOR_B0_POPULATION"}
    game = row["game_id"].split("_")
    if len(game) != 4 or row["team"] not in game[2:]:
        raise ValueError("player team/game mismatch")
    if profiles["source_published_at"] >= MODEL_CUTOFF:
        raise ValueError("post-cutoff participation")
    t = profiles["team"].get(row["team"], Counter())
    pkey = (row["player_id"], row["team"])
    p = profiles["player"].get(pkey, Counter())
    nteam, nplayer = sum(t.values()), sum(p.values())
    if nteam < MIN_TEAM:
        return {"status": "NO_ADJUSTMENT", "reason": "INSUFFICIENT_PRIOR_TEAM_PLAYS"}
    if nplayer < MIN_PLAYER:
        return {"status": "NO_ADJUSTMENT", "reason": "INSUFFICIENT_PRIOR_SAME_TEAM_PLAYER_PLAYS"}
    pc = profiles["positions"][pkey]
    if len(pc) != 1:
        return {"status": "NO_ADJUSTMENT", "reason": "CONFLICTING_PLAYER_POSITION"}
    position = next(iter(pc))
    # Weighted share of available same-position skill slots. A presence in a
    # three-WR package is not equated with a one-WR package. This is field
    # opportunity, not receiving usage, and is calculated solely from 2024.
    opportunity = 0.0
    group_contributions = {}
    for group, plays in sorted(t.items()):
        backs, tight = int(group[0]), int(group[1])
        slots = {"WR": 5 - backs - tight, "TE": tight,
                 "RB": backs, "FB": backs}[position]
        if slots <= 0:
            if p[group]:
                raise ValueError("player position contradicts personnel slots")
            continue
        term = (plays / nteam) * (p[group] / plays) / slots
        group_contributions[group] = term
        opportunity += term
    return {"status": "ACTIVE", "opportunity": opportunity,
            "group_contributions": group_contributions, "position": position,
            "team_n": nteam, "player_n": nplayer,
            "team_groups": dict(t), "player_groups": dict(p),
            "source_sha256": profiles["source_sha256"],
            "source_published_at": profiles["source_published_at"],
            "claim": "prior personnel-conditioned field-slot share; not routes or targets"}


def load_baseline(stats_dir: Path) -> list[dict]:
    rows = []
    for year in (2023, 2024, 2025):
        path = stats_dir / f"stats_player_week_{year}.csv"
        verify(path, SOURCES[f"stats_{year}"])
        with path.open(encoding="utf-8-sig", newline="") as h:
            for r in csv.DictReader(h):
                targets, catches = float(r["targets"] or 0), float(r["receptions"] or 0)
                if max(targets, catches) <= 0:
                    continue
                rows.append({"player_id": r["player_id"], "player_name": r["player_display_name"],
                             "season": int(r["season"]), "week": int(r["week"]),
                             "season_type": r["season_type"], "game_id": r["game_id"],
                             "team": r["team"], "effective_targets": max(targets, catches),
                             "receptions": catches})
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"], r["player_id"]))
    return rolling_predictions(rows)


def fit(rows: list[dict]) -> dict:
    if not rows or any(r["week"] not in DEV for r in rows):
        raise ValueError("only 2025 weeks 2-8 may fit")
    b = [r["b0"] for r in rows]
    x = [r["b0"] * (r["f15"]["opportunity"] - 0.25) for r in rows]
    y = [r["actual"] for r in rows]
    bb, bx, xx = sum(v*v for v in b), sum(a*c for a,c in zip(b,x)), sum(v*v for v in x)
    by, xy = sum(a*c for a,c in zip(b,y)), sum(a*c for a,c in zip(x,y))
    scale = by / bb
    determinant = bb * (xx + RIDGE) - bx*bx
    return {"scale_only": scale,
            "coefficients": [(by*(xx+RIDGE)-bx*xy)/determinant, (bb*xy-bx*by)/determinant],
            "ridge": RIDGE, "development_n": len(rows)}


def predict(row: dict, fitted: dict) -> dict:
    if row["f15"]["status"] != "ACTIVE":
        return {"status": "NO_ADJUSTMENT", "reason": row["f15"]["reason"], "b0": row.get("b0")}
    b0 = row["b0"]
    term = fitted["coefficients"][1] * b0 * (row["f15"]["opportunity"] - 0.25)
    return {"status": "RESEARCH_CHALLENGER", "b0": b0,
            "scale_only": fitted["scale_only"]*b0,
            "challenger": max(0, fitted["coefficients"][0]*b0 + term),
            "personnel_term": term}


def metrics(rows: list[dict], key: str) -> dict:
    errors = [r[key]-r["actual"] for r in rows]
    return {"n": len(rows), "mae": sum(abs(e) for e in errors)/len(errors),
            "rmse": math.sqrt(sum(e*e for e in errors)/len(errors)),
            "bias": sum(errors)/len(errors)}


def evaluate(participation: Path, ftn: Path, stats_dir: Path, *, bootstrap: int = 1000) -> dict:
    profiles = load_profiles(participation, ftn)
    rows = []
    for r in load_baseline(stats_dir):
        if r["season"] != 2025 or r["season_type"] != "REG" or r["week"] not in range(2,19):
            continue
        r = dict(r)
        r["f15"] = feature(r, profiles)
        rows.append(r)
    dev = [r for r in rows if r["week"] in DEV and r["f15"]["status"] == "ACTIVE"]
    held_candidates = [r for r in rows if r["week"] in HELD]
    held = [r for r in held_candidates if r["f15"]["status"] == "ACTIVE"]
    fitted = fit(dev)
    scored = [{**r, **predict(r, fitted)} for r in held]
    clusters = defaultdict(list)
    for r in scored:
        clusters[r["player_id"]].append(r)
    rng = random.Random(15)
    keys = sorted(clusters)
    deltas = []
    for _ in range(bootstrap):
        sample = [r for k in rng.choices(keys,k=len(keys)) for r in clusters[k]]
        deltas.append(metrics(sample,"challenger")["mae"]-metrics(sample,"scale_only")["mae"])
    deltas.sort()
    team_plays = sum(sum(v.values()) for v in profiles["team"].values())
    return {"status": "EXPLORATORY_RESEARCH_ONLY_NOT_PROMOTED",
            "source": {"sha256": profiles["source_sha256"], "published_at": profiles["source_published_at"],
                       "attribution": "FTN Data via nflverse", "license": "CC BY-SA 4.0",
                       "chart_plays": profiles["chart_plays"], "complete_classified_plays": team_plays,
                       "excluded": profiles["excluded"]},
            "population": {"year": 2025, "dev_weeks": [2,8], "held_weeks": [9,18],
                           "development_active": len(dev), "held_candidates": len(held_candidates),
                           "held_active": len(scored), "players": len(clusters),
                           "teams": len({r["team"] for r in scored}),
                           "held_abstentions": dict(Counter(r["f15"]["reason"] for r in held_candidates if r["f15"]["status"] != "ACTIVE"))},
            "fit": fitted,
            "metrics": {k: metrics(scored,k) for k in ("b0","scale_only","challenger")},
            "delta_mae_challenger_minus_scale": metrics(scored,"challenger")["mae"]-metrics(scored,"scale_only")["mae"],
            "player_cluster_95_interval": [deltas[int(.025*bootstrap)],deltas[int(.975*bootstrap)]],
            "prediction_changes": {"count_vs_scale": sum(abs(r["challenger"]-r["scale_only"])>1e-9 for r in scored),
                                   "mean_absolute_vs_scale": sum(abs(r["challenger"]-r["scale_only"]) for r in scored)/len(scored)},
            "examples": [{k:r[k] for k in ("player_name","game_id","week","actual","b0","scale_only","challenger","personnel_term")} | {"feature": r["f15"]}
                         for r in sorted(scored,key=lambda r:-abs(r["personnel_term"]))[:5]],
            "matched_rows": [{k:r[k] for k in ("player_id","game_id","week","actual","b0","scale_only","challenger","personnel_term")} for r in scored]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--participation", type=Path, required=True)
    parser.add_argument("--ftn", type=Path, required=True)
    parser.add_argument("--stats-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.participation,args.ftn,args.stats_dir)
    raw = (json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("wb") as h:
        h.write(gzip.compress(raw,compresslevel=9,mtime=0))
    print(json.dumps({"report_sha256":hashlib.sha256(raw).hexdigest(),
                      "population":result["population"],"metrics":result["metrics"],
                      "delta":result["delta_mae_challenger_minus_scale"],
                      "interval":result["player_cluster_95_interval"]},sort_keys=True))


if __name__ == "__main__":
    main()

