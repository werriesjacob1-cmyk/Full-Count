"""F16 research: prior verified defensive-box response -> rushing-yards challenger.

This is a box-tendency formulation, not a rushing-scheme classifier. The
available pre-2025 FTN/participation files do not bind a runner or run concept
to each charted play. Box frequencies cover all verified scrimmage formations,
not specifically run calls. They are a matchup proxy, never a current plan.
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
from pathlib import Path

from nfl.research.rushing_yards_baseline_research import rolling_predictions

SOURCES = {
    "participation": {"sha256":"b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9","bytes":49688308,"published_at":"2025-09-04T10:24:49Z"},
    "ftn": {"sha256":"6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb","bytes":8254908,"published_at":"2025-09-01T01:29:37Z"},
    "stats_2023": {"sha256":"f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17"},
    "stats_2024": {"sha256":"3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759"},
    "stats_2025": {"sha256":"e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"},
}
CUTOFF="2025-09-11T00:00:00Z"
MIN_OFFENSE=300
MIN_DEFENSE_11=100
MIN_DEFENSE_OTHER=100
RIDGE=100.0
DEV=range(2,9)
HELD=range(9,19)
ROLE={"RB","FB"}


def verify(path:Path, meta:dict)->None:
    if "bytes" in meta and path.stat().st_size!=meta["bytes"]:
        raise ValueError("source byte count mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=meta["sha256"]:
        raise ValueError("source SHA-256 mismatch")
    if meta.get("published_at","0000")>=CUTOFF:
        raise ValueError("source published after feature cutoff")


def classify_box_play(p:dict,f:dict)->tuple[str,int,str,str]|None:
    """Return directly observed box and complete offensive grouping only."""
    game=p["nflverse_game_id"].split("_")
    offense=p["possession_team"]
    if len(game)!=4 or game[0]!="2024" or offense not in game[2:]:
        return None
    defense=game[2] if offense==game[3] else game[3]
    ids=p["offense_players"].split(";")
    pos=p["offense_positions"].split(";")
    if len(ids)!=len(pos) or len(ids)!=11 or len(set(ids))!=11:
        return None
    if any(not re.fullmatch(r"00-\d{7}",pid) for pid in ids):
        return None
    c=Counter(pos)
    if c["QB"]!=1 or sum(c[x] for x in ("C","G","T"))!=5:
        return None
    if set(c)-{"QB","C","G","T","RB","FB","TE","WR"}:
        return None
    labels=Counter({name:int(n) for n,name in re.findall(r"(\d+) ([A-Z]+)",p["offense_personnel"])})
    if labels!=c:
        return None
    backs=c["RB"]+c["FB"]
    if backs+c["TE"]+c["WR"]!=5:
        return None
    # Zero can mean absent data; it is never treated as a real zero-man box.
    a,b=f["n_defense_box"].strip(),p["defenders_in_box"].strip()
    if not (a.isdigit() and b.isdigit() and a==b and 1<=int(a)<=11):
        return None
    return ("11" if backs==1 and c["TE"]==1 and c["WR"]==3 else "OTHER",int(a),offense,defense)


def load_profiles(participation:Path,ftn:Path)->dict:
    verify(participation,SOURCES["participation"])
    verify(ftn,SOURCES["ftn"])
    chart={}
    with ftn.open(encoding="utf-8-sig",newline="") as h:
        for r in csv.DictReader(h):
            k=(r["nflverse_game_id"],r["nflverse_play_id"])
            if k in chart: raise ValueError("duplicate FTN game/play")
            chart[k]=r
    seen=set();offense=defaultdict(Counter);defense=defaultdict(Counter)
    refs=[];ex=Counter();box=Counter()
    with participation.open(encoding="utf-8-sig",newline="") as h:
        for p in csv.DictReader(h):
            k=(p["nflverse_game_id"],p["play_id"])
            if k in seen: raise ValueError("duplicate participation game/play")
            seen.add(k)
            f=chart.get(k)
            if f is None:
                ex["NO_EXACT_FTN_PLAY"]+=1;continue
            z=classify_box_play(p,f)
            if z is None:
                ex["UNKNOWN_OR_CONTRADICTORY_BOX_OR_GROUP"]+=1;continue
            group,n,o,d=z
            offense[o][group]+=1
            defense[d][(group,"total")]+=1
            defense[d][(group,"heavy")]+=n>=7
            box[str(n)]+=1
            refs.append(k)
    refs.sort()
    return {"offense":offense,"defense":defense,"exclusions":dict(ex),
            "box_counts":dict(sorted(box.items())),"chart_rows":len(chart),
            "verified_plays":len(refs),"verified_play_ids_sha256":hashlib.sha256(json.dumps(refs,separators=(",",":")).encode()).hexdigest(),
            "sample_plays":refs[:5],"source_sha256":{k:SOURCES[k]["sha256"] for k in ("participation","ftn")},
            "source_published_at":max(SOURCES[k]["published_at"] for k in ("participation","ftn"))}


def feature(row:dict,profiles:dict)->dict:
    if row["season"]!=2025 or row["week"]<2 or row["position"] not in ROLE or row.get("b0") is None:
        return {"status":"NO_ADJUSTMENT","reason":"OUTSIDE_PRIOR_RB_FB_B0_POPULATION"}
    g=row["game_id"].split("_")
    if len(g)!=4 or row["team"] not in g[2:]:
        raise ValueError("player team/game mismatch")
    if profiles["source_published_at"]>=CUTOFF:
        raise ValueError("post-cutoff box source")
    opponent=g[2] if row["team"]==g[3] else g[3]
    o=profiles["offense"].get(row["team"],Counter())
    d=profiles["defense"].get(opponent,Counter())
    no=sum(o.values())
    if no<MIN_OFFENSE:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_OFFENSE_GROUP_PLAYS"}
    if d[("11","total")]<MIN_DEFENSE_11 or d[("OTHER","total")]<MIN_DEFENSE_OTHER:
        return {"status":"NO_ADJUSTMENT","reason":"INSUFFICIENT_PRIOR_OPPONENT_BOX_PLAYS"}
    p11=o["11"]/no
    heavy11=d[("11","heavy")]/d[("11","total")]
    heavyother=d[("OTHER","heavy")]/d[("OTHER","total")]
    expected=p11*heavy11+(1-p11)*heavyother
    return {"status":"ACTIVE","expected_heavy_box_rate":expected,
            "offense_11_share":p11,"defense_heavy_given_11":heavy11,
            "defense_heavy_given_other":heavyother,
            "offense_n":no,"defense_n_11":d[("11","total")],
            "defense_n_other":d[("OTHER","total")],
            "opponent":opponent,"source_sha256":profiles["source_sha256"],
            "source_published_at":profiles["source_published_at"],
            "claim":"2024 all-charted-play box response to verified personnel; not run-only or current plan"}


def load_baseline(stats_dir:Path)->list[dict]:
    rows=[]
    for year in (2023,2024,2025):
        path=stats_dir/f"stats_player_week_{year}.csv"
        verify(path,SOURCES[f"stats_{year}"])
        with path.open(encoding="utf-8-sig",newline="") as h:
            for r in csv.DictReader(h):
                carries=float(r["carries"] or 0)
                if carries<=0:continue
                rows.append({"player_id":r["player_id"],"player_name":r["player_display_name"],
                             "position":r["position"],"season":int(r["season"]),
                             "week":int(r["week"]),"season_type":r["season_type"],
                             "game_id":r["game_id"],"team":r["team"],
                             "carries":carries,"rushing_yards":float(r["rushing_yards"] or 0)})
    rows.sort(key=lambda r:(r["season"],r["week"],r["game_id"],r["player_id"]))
    # Reuse PR #186's exact B0 and rejected volume-efficiency challenger.
    return rolling_predictions(rows)


def fit(rows:list[dict])->dict:
    if not rows or any(r["season"]!=2025 or r["week"] not in DEV for r in rows):
        raise ValueError("only 2025 weeks 2-8 may fit")
    b=[r["b0"] for r in rows]
    x=[r["b0"]*(r["f16"]["expected_heavy_box_rate"]-0.25) for r in rows]
    y=[r["actual"] for r in rows]
    bb,bx,xx=sum(v*v for v in b),sum(a*c for a,c in zip(b,x)),sum(v*v for v in x)
    by,xy=sum(a*c for a,c in zip(b,y)),sum(a*c for a,c in zip(x,y))
    det=bb*(xx+RIDGE)-bx*bx
    return {"scale_only":by/bb,"coefficients":[(by*(xx+RIDGE)-bx*xy)/det,(bb*xy-bx*by)/det],
            "ridge":RIDGE,"development_n":len(rows)}


def predict(row:dict,fitted:dict)->dict:
    if row["f16"]["status"]!="ACTIVE":
        return {"status":"NO_ADJUSTMENT","reason":row["f16"]["reason"],"b0":row.get("b0")}
    b0=row["b0"]
    term=fitted["coefficients"][1]*b0*(row["f16"]["expected_heavy_box_rate"]-0.25)
    return {"status":"RESEARCH_CHALLENGER","b0":b0,
            "scale_only":fitted["scale_only"]*b0,
            "challenger":max(0,fitted["coefficients"][0]*b0+term),"box_term":term}


def metrics(rows:list[dict],key:str)->dict:
    e=[r[key]-r["actual"] for r in rows]
    return {"n":len(e),"mae":sum(abs(v) for v in e)/len(e),
            "rmse":math.sqrt(sum(v*v for v in e)/len(e)),"bias":sum(e)/len(e)}


def evaluate(participation:Path,ftn:Path,stats_dir:Path,*,bootstrap:int=1000)->dict:
    profiles=load_profiles(participation,ftn)
    candidates=[]
    for row in load_baseline(stats_dir):
        if row["season"]!=2025 or row["season_type"]!="REG" or row["week"] not in range(2,19) or row["position"] not in ROLE:
            continue
        r=dict(row);r["f16"]=feature(r,profiles);candidates.append(r)
    dev=[r for r in candidates if r["week"] in DEV and r["f16"]["status"]=="ACTIVE"]
    held_all=[r for r in candidates if r["week"] in HELD]
    held=[r for r in held_all if r["f16"]["status"]=="ACTIVE"]
    fitted=fit(dev)
    scored=[{**r,**predict(r,fitted)} for r in held]
    clusters={name:defaultdict(list) for name in ("player_id","game_id")}
    for r in scored:
        for name in clusters:clusters[name][r[name]].append(r)
    intervals={}
    for name,seed in (("player_id",16),("game_id",17)):
        mapping=clusters[name];keys=sorted(mapping);rng=random.Random(seed);delta=[]
        for _ in range(bootstrap):
            sample=[r for k in rng.choices(keys,k=len(keys)) for r in mapping[k]]
            delta.append(metrics(sample,"challenger")["mae"]-metrics(sample,"scale_only")["mae"])
        delta.sort();intervals[name]=[delta[int(.025*bootstrap)],delta[int(.975*bootstrap)]]
    return {"status":"EXPLORATORY_RESEARCH_ONLY_NOT_PROMOTED",
            "source":{k:profiles[k] for k in ("exclusions","box_counts","chart_rows","verified_plays","verified_play_ids_sha256","sample_plays","source_sha256","source_published_at")},
            "population":{"season":2025,"dev_weeks":[2,8],"held_weeks":[9,18],
                          "development_active":len(dev),"held_candidates":len(held_all),
                          "held_active":len(scored),"players":len(clusters["player_id"]),
                          "games":len(clusters["game_id"]),"teams":len({r["team"] for r in scored}),
                          "held_abstentions":dict(Counter(r["f16"]["reason"] for r in held_all if r["f16"]["status"]!="ACTIVE"))},
            "fit":fitted,"metrics":{k:metrics(scored,k) for k in ("b0","scale_only","challenger","c1_carries3_times_ypc8")},
            "delta_mae_challenger_minus_b0":metrics(scored,"challenger")["mae"]-metrics(scored,"b0")["mae"],
            "delta_mae_challenger_minus_scale":metrics(scored,"challenger")["mae"]-metrics(scored,"scale_only")["mae"],
            "cluster_95_intervals_vs_scale":intervals,
            "prediction_changes":{"count_vs_scale":sum(abs(r["challenger"]-r["scale_only"])>1e-9 for r in scored),
                                  "mean_absolute_vs_scale":sum(abs(r["challenger"]-r["scale_only"]) for r in scored)/len(scored)},
            "examples":[{k:r[k] for k in ("player_name","position","game_id","week","actual","b0","scale_only","challenger","box_term")} | {"feature":r["f16"]}
                        for r in sorted(scored,key=lambda r:-abs(r["box_term"]))[:5]],
            "matched_rows":[{k:r[k] for k in ("player_id","game_id","week","actual","b0","scale_only","challenger","box_term","c1_carries3_times_ypc8")} for r in scored]}


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--participation",type=Path,required=True)
    p.add_argument("--ftn",type=Path,required=True)
    p.add_argument("--stats-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    result=evaluate(a.participation,a.ftn,a.stats_dir)
    raw=(json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_bytes(gzip.compress(raw,compresslevel=9,mtime=0))
    print(json.dumps({"report_sha256":hashlib.sha256(raw).hexdigest(),"population":result["population"],
                      "metrics":result["metrics"],"intervals":result["cluster_95_intervals_vs_scale"]},sort_keys=True))


if __name__=="__main__":main()

