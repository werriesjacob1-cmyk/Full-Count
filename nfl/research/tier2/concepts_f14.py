"""F14 research: prior FTN offensive-concept exposure -> receptions challenger.

On-field participation in a concept play is not a route, target, screen
recipient, blocking assignment, or QB read. All four concept flags come
directly from FTN charting, never inferred from pass length or formation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from nfl.research.receptions_baseline_research import rolling_predictions

FLAGS=("is_play_action","is_motion","is_rpo","is_screen_pass")
SOURCES={
 "participation":{"sha256":"b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9","bytes":49688308,"published_at":"2025-09-04T10:24:49Z","url":"https://github.com/nflverse/nflverse-data/releases/download/pbp_participation/pbp_participation_2024.csv"},
 "ftn":{"sha256":"6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb","bytes":8254908,"published_at":"2025-09-01T01:29:37Z","url":"https://github.com/nflverse/nflverse-data/releases/download/ftn_charting/ftn_charting_2024.csv"},
 "stats_2023":{"sha256":"f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17"},
 "stats_2024":{"sha256":"3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759"},
 "stats_2025":{"sha256":"e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"},
}
LICENSE="https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION="FTN Data via nflverse"
MIN_TEAM=400
MIN_PLAYER=100
RIDGE=10.0
DEV_WEEKS=(2,8)
VALID_WEEKS=(9,18)


def verify(path:Path, meta:dict)->None:
    if "bytes" in meta and path.stat().st_size!=meta["bytes"]:
        raise ValueError("source size mismatch: "+path.name)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=meta["sha256"]:
        raise ValueError("source digest mismatch: "+path.name)


def load_profiles(participation:Path,ftn:Path)->dict:
    """Bind charted flags to exact team/play and documented on-field GSIS IDs."""
    verify(participation,SOURCES["participation"])
    verify(ftn,SOURCES["ftn"])
    chart={}
    with ftn.open(encoding="utf-8-sig",newline="") as h:
        for r in csv.DictReader(h):
            key=(r["nflverse_game_id"],r["nflverse_play_id"])
            if key in chart: raise ValueError("duplicate FTN game/play")
            chart[key]=r
    team=defaultdict(Counter)
    player=defaultdict(Counter)
    refs=defaultdict(list)
    exclusions=Counter()
    seen=set()
    with participation.open(encoding="utf-8-sig",newline="") as h:
        for p in csv.DictReader(h):
            key=(p["nflverse_game_id"],p["play_id"])
            if key in seen: raise ValueError("duplicate participation game/play")
            seen.add(key)
            f=chart.get(key)
            if f is None:
                exclusions["NO_FTN_PLAY"]+=1; continue
            if not re.fullmatch(r"2024_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}",key[0]):
                raise ValueError("wrong game identity")
            offense=p["possession_team"]
            if offense not in key[0].split("_")[2:]:
                exclusions["UNKNOWN_OFFENSE"]+=1; continue
            ids=p["offense_players"].split(";")
            positions=p["offense_positions"].split(";")
            if len(ids)!=len(positions) or positions.count("QB")!=1:
                exclusions["NO_UNIQUE_QB_SCRIMMAGE_PLAY"]+=1; continue
            if len(ids)!=len(set(ids)) or any(not re.fullmatch(r"00-\d{7}",pid) for pid in ids):
                exclusions["INVALID_ON_FIELD_IDENTITY"]+=1; continue
            values={name:f[name] for name in FLAGS}
            if any(v not in ("TRUE","FALSE") for v in values.values()):
                exclusions["UNKNOWN_CONCEPT_FLAG"]+=1; continue
            team[offense]["n"]+=1
            refs[("team",offense)].append(key)
            for name,value in values.items():
                team[offense][name]+=value=="TRUE"
            for pid in ids:
                player[(pid,offense)]["n"]+=1
                refs[("player",pid,offense)].append(key)
                for name,value in values.items():
                    player[(pid,offense)][name]+=value=="TRUE"
    def finish(counter,ref_key):
        result={}
        for identity,counts in counter.items():
            key=ref_key(identity)
            plays=sorted(refs[key])
            result[identity]={**counts,**{name+"_rate":counts[name]/counts["n"] for name in FLAGS},
                "play_identity_sha256":hashlib.sha256(json.dumps(plays,separators=(",",":")).encode()).hexdigest(),
                "sample_play_ids":plays[:3]}
        return result
    return {"team":finish(team,lambda x:("team",x)),
            "player":finish(player,lambda x:("player",x[0],x[1])),
            "excluded":dict(exclusions),"source_sha256":{k:SOURCES[k]["sha256"] for k in ("participation","ftn")},
            "published_at":max(SOURCES[k]["published_at"] for k in ("participation","ftn")),
            "attribution":ATTRIBUTION,"license":LICENSE,
            "identity_scope":"player on field in flagged team play; not targeted involvement"}


def feature(row:dict,profiles:dict)->dict:
    if row["season"]!=2025 or row["week"]<2 or row.get("b0") is None:
        return {"status":"NO_ADJUSTMENT","reason":"OUTSIDE_SUPPORTED_2025_B0_POPULATION"}
    game=row["game_id"].split("_")
    if len(game)!=4 or row["team"] not in game[2:]:
        raise ValueError("player team/game identity mismatch")
    t=profiles["team"].get(row["team"])
    p=profiles["player"].get((row["player_id"],row["team"]))
    if t is None or t["n"]<MIN_TEAM:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_TEAM_CONCEPT_PLAYS"}
    if p is None or p["n"]<MIN_PLAYER:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_SAME_TEAM_PLAYER_PARTICIPATION"}
    n=sum(v["n"] for v in profiles["team"].values())
    league={name:sum(v[name] for v in profiles["team"].values())/n for name in FLAGS}
    deltas={name:(t[name+"_rate"]+p[name+"_rate"])/2-league[name] for name in FLAGS}
    return {"status":"ACTIVE","deltas":deltas,"team_n":t["n"],"player_n":p["n"],
            "team_profile":t,"player_profile":p,
            "source_sha256":profiles["source_sha256"],"source_published_at":profiles["published_at"],
            "feature_cutoff":"2025_WEEK_01_FINAL_BEFORE_WEEK_02",
            "claim":"2024 team concept use and player on-field exposure, not player routes/targets/reads"}


def load_baseline(stats_dir:Path)->list[dict]:
    rows=[]
    for year in (2023,2024,2025):
        path=stats_dir/f"stats_player_week_{year}.csv"
        verify(path,SOURCES[f"stats_{year}"])
        with path.open(encoding="utf-8-sig",newline="") as h:
            for r in csv.DictReader(h):
                targets=float(r["targets"] or 0)
                catches=float(r["receptions"] or 0)
                if max(targets,catches)<=0: continue
                rows.append({"player_id":r["player_id"],"player_name":r["player_display_name"],
                    "position":r["position"],"season":int(r["season"]),"week":int(r["week"]),
                    "season_type":r["season_type"],"game_id":r["game_id"],"team":r["team"],
                    "effective_targets":max(targets,catches),"receptions":catches})
    rows.sort(key=lambda r:(r["season"],r["week"],r["game_id"],r["player_id"]))
    return rolling_predictions(rows)


def _solve(matrix:list[list[float]],rhs:list[float])->list[float]:
    a=[list(row)+[rhs[i]] for i,row in enumerate(matrix)]
    for i in range(len(rhs)):
        pivot=max(range(i,len(rhs)),key=lambda j:abs(a[j][i]))
        if abs(a[pivot][i])<1e-12: raise ValueError("singular fit")
        a[i],a[pivot]=a[pivot],a[i]
        unit=a[i][i]; a[i]=[v/unit for v in a[i]]
        for j in range(len(rhs)):
            if j!=i:
                factor=a[j][i]
                a[j]=[x-factor*y for x,y in zip(a[j],a[i])]
    return [r[-1] for r in a]


def fit(rows:list[dict])->dict:
    if not rows or any(not DEV_WEEKS[0]<=r["week"]<=DEV_WEEKS[1] for r in rows):
        raise ValueError("development weeks 2-8 required")
    xs=[[r["b0"]]+[r["b0"]*r["f14"]["deltas"][name] for name in FLAGS] for r in rows]
    ys=[r["actual"] for r in rows]
    scale=sum(x[0]*y for x,y in zip(xs,ys))/sum(x[0]**2 for x in xs)
    size=len(FLAGS)+1
    gram=[[sum(x[i]*x[j] for x in xs)+(RIDGE if i==j and i>0 else 0)
           for j in range(size)] for i in range(size)]
    rhs=[sum(x[i]*y for x,y in zip(xs,ys)) for i in range(size)]
    return {"scale_only":scale,"challenger":_solve(gram,rhs),"ridge":RIDGE,"development_n":len(rows)}


def predict(row:dict,fitted:dict)->dict:
    if row["f14"]["status"]!="ACTIVE":
        return {"status":"NO_ADJUSTMENT","reason":row["f14"]["reason"],
                "baseline_b0":row.get("b0"),"challenger":None}
    b0=row["b0"]; c=fitted["challenger"]
    terms={name:c[i+1]*b0*row["f14"]["deltas"][name] for i,name in enumerate(FLAGS)}
    terms["scaled_baseline"]=c[0]*b0
    value=max(0.0,sum(terms.values()))
    terms["floor_effect"]=value-sum(terms.values())
    return {"status":"RESEARCH_CHALLENGER","baseline_b0":b0,
            "scale_only":fitted["scale_only"]*b0,"challenger":value,"contributions":terms}


def evaluate(participation:Path,ftn:Path,stats_dir:Path,*,bootstrap=1000)->dict:
    profiles=load_profiles(participation,ftn)
    rows=[]
    for original in load_baseline(stats_dir):
        if original["season"]!=2025 or original["season_type"]!="REG" or not 2<=original["week"]<=18:
            continue
        r=dict(original); r["f14"]=feature(r,profiles); rows.append(r)
    dev=[r for r in rows if DEV_WEEKS[0]<=r["week"]<=DEV_WEEKS[1] and r["f14"]["status"]=="ACTIVE"]
    held=[r for r in rows if VALID_WEEKS[0]<=r["week"]<=VALID_WEEKS[1] and r["f14"]["status"]=="ACTIVE"]
    fitted=fit(dev)
    scored=[]
    for r in held:
        z=predict(r,fitted)
        scored.append({"game_id":r["game_id"],"week":r["week"],"player_id":r["player_id"],
            "player_name":r["player_name"],"team":r["team"],"actual":r["actual"],
            "baseline_b0":r["b0"],"scale_only":z["scale_only"],"challenger":z["challenger"],
            "contributions":z["contributions"],"f14":r["f14"]})
    def mae(key,sample=scored): return sum(abs(r[key]-r["actual"]) for r in sample)/len(sample)
    by_player=defaultdict(list)
    for r in scored: by_player[r["player_id"]].append(r)
    rng=random.Random(20260925); ids=sorted(by_player); deltas=[]
    for _ in range(bootstrap):
        sample=[r for _ in ids for r in by_player[rng.choice(ids)]]
        deltas.append(mae("challenger",sample)-mae("scale_only",sample))
    deltas.sort()
    held_total=sum(VALID_WEEKS[0]<=r["week"]<=VALID_WEEKS[1] for r in rows)
    report={"schema":"NFL_F14_OFFENSIVE_CONCEPT_RESEARCH_V1","research_only":True,
        "evidence_class":"RETROSPECTIVE_EXPLORATORY_NOT_PROSPECTIVE",
        "source_manifest":SOURCES,"attribution":ATTRIBUTION,"license":LICENSE,
        "method":{"development":"2025 regular weeks 2-8, matched active rows",
                  "validation":"2025 regular weeks 9-18, historically inspected",
                  "baseline":"existing last-five role-positive appearance B0",
                  "factors":list(FLAGS),"factor_meaning":"player on-field and team concept rates, not player-specific targets",
                  "fitted_scale_only":fitted["scale_only"],"fitted_challenger":fitted["challenger"],
                  "ridge":RIDGE,"min_team":MIN_TEAM,"min_player":MIN_PLAYER},
        "source_exclusions":profiles["excluded"],"development_n":len(dev),
        "validation_population_n":held_total,"validation_activated_n":len(scored),
        "validation_activation_rate":len(scored)/held_total,
        "metrics":{"baseline_b0_mae":mae("baseline_b0"),"scale_only_mae":mae("scale_only"),
                   "challenger_mae":mae("challenger"),"delta_vs_scale":mae("challenger")-mae("scale_only")},
        "player_cluster_bootstrap":{"players":len(ids),"iterations":bootstrap,"seed":20260925,
                                    "delta_vs_scale_ci95":[deltas[int(.025*bootstrap)],deltas[int(.975*bootstrap)]]},
        "examples":[r for r in scored if sum(abs(v) for k,v in r["contributions"].items() if k in FLAGS)>0][:3],
        "matched_rows":scored,
        "limitations":["Previously inspected 2025 period, no prospective accuracy claim",
                       "2024 participation and FTN source available pre-2025 week 2, but exact weekly-file historical revision state unverified",
                       "On-field concept exposure is not player routes, targets, screen reception, or QB reads",
                       "Current-game role and coaching continuity not verified; no selection/price claims"]}
    report["report_sha256"]=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return report


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--participation",type=Path,required=True)
    ap.add_argument("--ftn",type=Path,required=True)
    ap.add_argument("--stats-dir",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    r=evaluate(args.participation,args.ftn,args.stats_dir)
    with args.output.open("x",encoding="utf-8") as h: json.dump(r,h,sort_keys=True,allow_nan=False)
    print(json.dumps({"activated":r["validation_activated_n"],"metrics":r["metrics"],"seal":r["report_sha256"]}))


if __name__=="__main__": main()
