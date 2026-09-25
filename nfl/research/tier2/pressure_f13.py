"""F13 research: prior observed pressure/blitz tendencies -> passing-yards challenger.

FTN manually charted observations are not footage watched by FULL COUNT. The
2024 assets were published before the 2025 evaluation games. A rusher-positive
chart row is a *charted pass-rush opportunity*, not a claim of a legal pass
attempt; sacks, hits, pressure and blitz are distinct concepts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict, deque
from pathlib import Path

from nfl.research.passing_yards_baseline_research import rolling_predictions

SOURCES = {
    "participation": {"sha256":"b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9",
                      "bytes":49688308,"published_at":"2025-09-04T10:24:49Z",
                      "url":"https://github.com/nflverse/nflverse-data/releases/download/pbp_participation/pbp_participation_2024.csv"},
    "ftn": {"sha256":"6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb",
            "bytes":8254908,"published_at":"2025-09-01T01:29:37Z",
            "url":"https://github.com/nflverse/nflverse-data/releases/download/ftn_charting/ftn_charting_2024.csv"},
    "stats_2023":{"sha256":"f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17"},
    "stats_2024":{"sha256":"3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759"},
    "stats_2025":{"sha256":"e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"},
}
LICENSE = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = "FTN Data via nflverse"
MIN_QB = 100
MIN_DEFENSE = 100
RIDGE = 1000.0
DEV_WEEKS = (2, 8)
VALID_WEEKS = (9, 18)


def verify(path: Path, meta: dict) -> None:
    if "bytes" in meta and path.stat().st_size != meta["bytes"]:
        raise ValueError(f"source size mismatch: {path.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != meta["sha256"]:
        raise ValueError(f"source digest mismatch: {path.name}")


def _qb_identity(row: dict) -> str | None:
    players = row.get("offense_players", "").split(";")
    positions = row.get("offense_positions", "").split(";")
    if len(players) != len(positions):
        return None
    found = [player for player, pos in zip(players, positions) if pos == "QB"]
    return found[0] if len(found) == 1 and re.fullmatch(r"00-\d{7}", found[0]) else None


def _count(row: dict, key: str) -> int | None:
    raw = row.get(key, "")
    if not re.fullmatch(r"\d+", raw):
        return None
    return int(raw)


def load_profiles(participation: Path, ftn: Path) -> dict:
    """Join exact 2024 play keys and retain only charted pass-rush opportunities."""
    verify(participation, SOURCES["participation"])
    verify(ftn, SOURCES["ftn"])
    chart = {}
    with ftn.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["nflverse_game_id"], row["nflverse_play_id"])
            if key in chart:
                raise ValueError("duplicate FTN game/play identity")
            chart[key] = row
    qb, defense = defaultdict(Counter), defaultdict(Counter)
    refs = defaultdict(list)
    missing = Counter()
    with participation.open(encoding="utf-8-sig", newline="") as handle:
        seen = set()
        for row in csv.DictReader(handle):
            key = (row["nflverse_game_id"], row["play_id"])
            if key in seen:
                raise ValueError("duplicate participation game/play identity")
            seen.add(key)
            f = chart.get(key)
            if f is None:
                missing["NO_FTN_PLAY"] += 1
                continue
            if not re.fullmatch(r"2024_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}", key[0]):
                raise ValueError("wrong chart game identity")
            _, _, away, home = key[0].split("_")
            offense = row["possession_team"]
            if offense not in (away, home):
                missing["UNKNOWN_OFFENSE"] += 1
                continue
            rushers, blitzers = _count(f, "n_pass_rushers"), _count(f, "n_blitzers")
            pressure = row["was_pressure"]
            if rushers is None or rushers == 0:
                missing["NO_CHARTED_RUSH"] += 1
                continue
            if blitzers is None or blitzers > rushers or pressure not in ("TRUE", "FALSE"):
                missing["UNKNOWN_OR_INCONSISTENT_CHART"] += 1
                continue
            player = _qb_identity(row)
            if player is None:
                missing["AMBIGUOUS_QB"] += 1
                continue
            opponent = home if offense == away else away
            observation = {"game_id":key[0],"play_id":key[1],"qb_gsis_id":player,
                           "offense":offense,"defense":opponent,
                           "pressure":pressure == "TRUE","blitz":blitzers > 0,
                           "n_blitzers":blitzers,"n_pass_rushers":rushers}
            for group, name in ((qb,player),(defense,opponent)):
                group[name]["n"] += 1
                group[name]["pressure"] += observation["pressure"]
                group[name]["blitz"] += observation["blitz"]
                group[name]["pressure_and_blitz"] += observation["pressure"] and observation["blitz"]
            refs[("qb",player)].append((key[0],key[1]))
            refs[("defense",opponent)].append((key[0],key[1]))
    def finish(group, category):
        result = {}
        for identity, counts in group.items():
            play_ids = sorted(refs[(category,identity)])
            result[identity] = {**counts,
                "pressure_rate": counts["pressure"] / counts["n"],
                "blitz_rate": counts["blitz"] / counts["n"],
                "play_identity_sha256":hashlib.sha256(json.dumps(play_ids,separators=(",",":")).encode()).hexdigest(),
                "sample_play_ids":play_ids[:3]}
        return result
    return {"qb":finish(qb,"qb"),"defense":finish(defense,"defense"),
            "excluded":dict(missing),"source_digests":{k:SOURCES[k]["sha256"] for k in ("participation","ftn")},
            "observation_period":"2024 season; both exact assets published before 2025 week 2",
            "published_at":max(SOURCES["participation"]["published_at"],SOURCES["ftn"]["published_at"]),
            "license":LICENSE,"attribution":ATTRIBUTION}


def feature(row: dict, profiles: dict) -> dict:
    """Abstain if either prior player or opponent sample is too thin."""
    if row["season"] != 2025 or row["week"] < 2 or row.get("b0") is None:
        return {"status":"NO_ADJUSTMENT","reason":"OUTSIDE_SUPPORTED_2025_B0_POPULATION"}
    game = row["game_id"].split("_")
    team = row["team"]
    if len(game) != 4 or team not in game[2:]:
        raise ValueError("player team/game identity mismatch")
    opponent = game[3] if team == game[2] else game[2]
    if row.get("opponent_team") != opponent:
        raise ValueError("opponent identity mismatch")
    qb, defense = profiles["qb"].get(row["player_id"]), profiles["defense"].get(opponent)
    if qb is None or qb["n"] < MIN_QB:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_QB_CHARTING"}
    if defense is None or defense["n"] < MIN_DEFENSE:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_OPPONENT_CHARTING"}
    # Fixed before evaluation: mean of offense-experienced and defense-created
    # rates, centered on the eligible 2024 defense population's play-weighted mean.
    n = sum(d["n"] for d in profiles["defense"].values())
    pbar = sum(d["pressure"] for d in profiles["defense"].values()) / n
    bbar = sum(d["blitz"] for d in profiles["defense"].values()) / n
    p = (qb["pressure_rate"] + defense["pressure_rate"]) / 2 - pbar
    b = (qb["blitz_rate"] + defense["blitz_rate"]) / 2 - bbar
    return {"status":"ACTIVE","pressure_delta":p,"blitz_delta":b,
            "qb_n":qb["n"],"opponent_n":defense["n"],
            "qb_profile":qb,"opponent_profile":defense,
            "source_sha256":profiles["source_digests"],
            "source_published_at":profiles["published_at"],
            "feature_cutoff":"2025_WEEK_01_FINAL_BEFORE_WEEK_02",
            "claim":"2024 charted-rush pressure/blitz tendencies; not a QB under-pressure yardage split or cornerback matchup"}


def load_baseline(stats_dir: Path) -> list[dict]:
    rows = []
    for year in (2023,2024,2025):
        path = stats_dir / f"stats_player_week_{year}.csv"
        verify(path,SOURCES[f"stats_{year}"])
        with path.open(encoding="utf-8-sig",newline="") as handle:
            for r in csv.DictReader(handle):
                if r["position"] != "QB":
                    continue
                rows.append({"player_id":r["player_id"],"player_name":r["player_display_name"],
                             "season":int(r["season"]),"week":int(r["week"]),
                             "season_type":r["season_type"],"game_id":r["game_id"],
                             "team":r["team"],"opponent_team":r["opponent_team"],
                             "attempts":float(r["attempts"]),"passing_yards":float(r["passing_yards"])})
    rows.sort(key=lambda r:(r["season"],r["week"],r["game_id"],r["player_id"]))
    return rolling_predictions(rows)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    a = [list(row)+[rhs[i]] for i,row in enumerate(matrix)]
    for i in range(len(rhs)):
        pivot = max(range(i,len(rhs)),key=lambda j:abs(a[j][i]))
        if abs(a[pivot][i]) < 1e-12:
            raise ValueError("singular fitted model")
        a[i],a[pivot]=a[pivot],a[i]
        unit=a[i][i]
        a[i]=[v/unit for v in a[i]]
        for j in range(len(rhs)):
            if j != i:
                factor=a[j][i]
                a[j]=[x-factor*y for x,y in zip(a[j],a[i])]
    return [row[-1] for row in a]


def fit(rows: list[dict]) -> dict:
    """One prespecified ridge fit on development weeks only; scale control matched."""
    if not rows or any(not DEV_WEEKS[0] <= r["week"] <= DEV_WEEKS[1] for r in rows):
        raise ValueError("fit requires development weeks 2-8 only")
    xs = [[r["b0"],r["b0"]*r["f13"]["pressure_delta"],r["b0"]*r["f13"]["blitz_delta"]] for r in rows]
    ys = [r["actual"] for r in rows]
    scale = sum(x[0]*y for x,y in zip(xs,ys))/sum(x[0]**2 for x in xs)
    gram = [[sum(x[i]*x[j] for x in xs)+(RIDGE if i==j and i>0 else 0.0)
             for j in range(3)] for i in range(3)]
    rhs = [sum(x[i]*y for x,y in zip(xs,ys)) for i in range(3)]
    return {"scale_only":scale,"challenger":_solve(gram,rhs),"ridge":RIDGE,"development_n":len(rows)}


def predict(row: dict, fitted: dict) -> dict:
    """Preserve B0, return explicit no-adjustment if tactical evidence absent."""
    if row["f13"]["status"] != "ACTIVE":
        return {"baseline_b0":row.get("b0"),"status":"NO_ADJUSTMENT",
                "reason":row["f13"]["reason"],"challenger":None}
    b0 = row["b0"]
    p,b = row["f13"]["pressure_delta"],row["f13"]["blitz_delta"]
    c = fitted["challenger"]
    base_component=c[0]*b0
    pressure_component=c[1]*b0*p
    blitz_component=c[2]*b0*b
    projection=max(0.0,base_component+pressure_component+blitz_component)
    return {"baseline_b0":b0,"scale_only":fitted["scale_only"]*b0,
            "status":"RESEARCH_CHALLENGER","challenger":projection,
            "contributions":{"scaled_baseline":base_component,"pressure":pressure_component,
                             "blitz":blitz_component,"floor_effect":projection-(base_component+pressure_component+blitz_component)}}


def evaluate(participation: Path, ftn: Path, stats_dir: Path, *, bootstrap=1000) -> dict:
    profiles = load_profiles(participation,ftn)
    all_rows = load_baseline(stats_dir)
    candidates = []
    for row in all_rows:
        if row["season"] != 2025 or row["season_type"] != "REG" or not 2 <= row["week"] <= 18:
            continue
        x = dict(row)
        x["f13"] = feature(x,profiles)
        candidates.append(x)
    dev = [r for r in candidates if DEV_WEEKS[0] <= r["week"] <= DEV_WEEKS[1] and r["f13"]["status"] == "ACTIVE"]
    held = [r for r in candidates if VALID_WEEKS[0] <= r["week"] <= VALID_WEEKS[1] and r["f13"]["status"] == "ACTIVE"]
    fitted=fit(dev)
    scored=[]
    for r in held:
        z=predict(r,fitted)
        scored.append({"game_id":r["game_id"],"week":r["week"],"player_id":r["player_id"],
                       "player_name":r["player_name"],"team":r["team"],"opponent":r["opponent_team"],
                       "actual":r["actual"],"baseline_b0":r["b0"],"scale_only":z["scale_only"],
                       "challenger":z["challenger"],"contributions":z["contributions"],
                       "f13":r["f13"]})
    def mae(key, sample=scored):
        return sum(abs(r[key]-r["actual"]) for r in sample)/len(sample)
    by_player=defaultdict(list)
    for r in scored: by_player[r["player_id"]].append(r)
    rng=random.Random(20260925)
    players=sorted(by_player)
    deltas=[]
    for _ in range(bootstrap):
        sample=[r for _ in players for r in by_player[rng.choice(players)]]
        deltas.append(mae("challenger",sample)-mae("scale_only",sample))
    deltas.sort()
    report={"schema":"NFL_F13_PRESSURE_BLITZ_RESEARCH_V1","research_only":True,
            "evidence_class":"RETROSPECTIVE_EXPLORATORY_NOT_PROSPECTIVE",
            "source_manifest":SOURCES,"license":LICENSE,"attribution":ATTRIBUTION,
            "method":{"development":"2025 regular weeks 2-8, matched active rows only",
                      "validation":"2025 regular weeks 9-18, previously inspected historical period",
                      "denominator":"2024 game/play rows with FTN n_pass_rushers>0 and exactly one charted QB; not all legal pass attempts",
                      "fitted_scale_only":fitted["scale_only"],"fitted_challenger":fitted["challenger"],
                      "fixed_ridge":RIDGE,"min_qb":MIN_QB,"min_defense":MIN_DEFENSE,
                      "no_same_game_or_2025_tactical_features":True},
            "source_exclusions":profiles["excluded"],
            "development_n":len(dev),"validation_population_n":sum(VALID_WEEKS[0]<=r["week"]<=VALID_WEEKS[1] for r in candidates),
            "validation_activated_n":len(scored),
            "validation_activation_rate":len(scored)/sum(VALID_WEEKS[0]<=r["week"]<=VALID_WEEKS[1] for r in candidates),
            "metrics":{"baseline_b0_mae":mae("baseline_b0"),"scale_only_mae":mae("scale_only"),
                       "challenger_mae":mae("challenger"),
                       "delta_vs_scale":mae("challenger")-mae("scale_only")},
            "player_cluster_bootstrap":{"players":len(players),"iterations":bootstrap,"seed":20260925,
              "delta_vs_scale_ci95":[deltas[int(.025*bootstrap)],deltas[int(.975*bootstrap)]]},
            "examples":[r for r in scored if abs(r["contributions"]["pressure"])+abs(r["contributions"]["blitz"])>0][:3],
            "matched_rows":scored,
            "limitations":["Retrospective 2025 period previously inspected; no prospective accuracy claim",
                           "QB pressure/blitz exposure and opponent tendencies, not a direct under-pressure yardage split",
                           "2024 source profiles cannot verify 2025 coaching/personnel continuity",
                           "2023-25 weekly outcome files were fetched in 2026; their historical revision state is not authenticated"]}
    report["report_sha256"]=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return report


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--participation",type=Path,required=True)
    ap.add_argument("--ftn",type=Path,required=True)
    ap.add_argument("--stats-dir",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    result=evaluate(args.participation,args.ftn,args.stats_dir)
    with args.output.open("x",encoding="utf-8") as handle:
        json.dump(result,handle,sort_keys=True,allow_nan=False)
    print(json.dumps({"validation_activated_n":result["validation_activated_n"],
                      "metrics":result["metrics"],"seal":result["report_sha256"]}))


if __name__=="__main__": main()
