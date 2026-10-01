"""Independent re-implementation of #219's core numbers (not importing forward_chain.py)."""
import json, math, subprocess, sys, random, datetime as dt
from collections import defaultdict, Counter
import numpy as np
REPO="/tmp/claude-0/full-count-worktrees/mlbacc"; SHA="58abe9f2e5a2b69d0f8bc98c22c8bafe7b00a224"
sys.path.insert(0,REPO)
from dashboard.live_state import canonical_prop_id
sh=lambda *a: subprocess.check_output(["git","-C",REPO,*a])
ls=lambda d: sh("ls-tree","--name-only",SHA,d+"/").decode().split()
js=lambda p: json.loads(sh("show",f"{SHA}:{p}"))
FAM={"hits","hits_runs_rbis","strikeouts","pitcher_outs"}
def q_of(o): return -o/(-o+100) if o<0 else 100/(o+100)
def lg(p): p=min(max(p,1e-4),1-1e-4); return math.log(p/(1-p))
def y_of(g): return {"hit":1,"miss":0}.get(g)
rows={}
db={}
for p in ls("results"):
    b=p.split("/")[-1]
    if not b.startswith("grades_"): continue
    d=b[7:-5]
    if not "2026-08-04"<=d<="2026-09-27": continue
    for x in js(p).get("picks") or []:
        if x.get("recommendation_status") is None: continue
        try: cid=canonical_prop_id(x)
        except Exception: cid=None
        st=(x.get("projection") or {}).get("stat"); y=y_of(x.get("grade"))
        if None in (cid,st,y,x.get("hit_probability"),x.get("market_odds")): continue
        db[cid]=dict(d=d,g=str(x.get("game_pk")),f=st if st in FAM else "other",p=x["hit_probability"],o=x["market_odds"],y=y,s=x["recommendation_status"])
fb={}
for p in ls("output"):
    b=p.split("/")[-1]
    if not b.startswith("board_freeze_graded_"): continue
    d=b[20:-5]
    if not "2026-08-04"<=d<="2026-09-27": continue
    G=js(p); B=js(f"output/board_freeze_{d}.json")
    if G.get("source_board_sha256")!=B.get("board_sha256"): continue
    for x in G["records"]:
        st=x.get("stat"); y=y_of(x.get("grade")); pp=(x.get("prediction") or {}).get("hit_probability"); o=(x.get("market") or {}).get("market_odds")
        if None in (x.get("candidate_id"),st,y,pp,o): continue
        fb[x["candidate_id"]]=dict(d=d,g=str(x.get("game_pk")),f=st if st in FAM else "other",p=pp,o=o,y=y,s=(x.get("selector") or {}).get("recommendation_status"))
rows=dict(db); rows.update(fb)
R=sorted(rows.values(),key=lambda r:r["d"])
print("fb",len(fb),"db",len(db),"overlap",len(set(fb)&set(db)),"merged",len(R))
for r in R: r["lp"],r["lq"],r["q"]=lg(r["p"]),lg(q_of(r["o"])),q_of(r["o"])
mon=lambda d: dt.date.fromisoformat(d)-dt.timedelta(days=dt.date.fromisoformat(d).weekday())
def fit(rs,cols):
    X=np.column_stack([np.ones(len(rs))]+[[r[c] for r in rs] for c in cols]); y=np.array([r["y"] for r in rs],float)
    b=np.zeros(X.shape[1]); pen=np.full(X.shape[1],1.0); pen[0]=0
    for _ in range(100):
        mu=1/(1+np.exp(-X@b)); st=np.linalg.solve((X*(mu*(1-mu))[:,None]).T@X+np.diag(pen),X.T@(y-mu)-pen*b); b+=st
        if np.max(np.abs(st))<1e-10: break
    return b
ARMS={"p1":["lq"],"p2":["lp","lq"],"pcal":["lp"]}
weeks=sorted({mon(r["d"]) for r in R}); T=[]
for w in weeks[2:]:
    tr=[r for r in R if mon(r["d"])<w]; te=[r for r in R if mon(r["d"])==w]
    co={}
    for a,c in ARMS.items():
        co[a]={"_":fit(tr,c)}
        for f in {r["f"] for r in tr}:
            fr=[r for r in tr if r["f"]==f]
            if len(fr)>=50 and 0<sum(r["y"] for r in fr)<len(fr): co[a][f]=fit(fr,c)
    for r in te:
        r=dict(r)
        for a,c in ARMS.items():
            b=co[a].get(r["f"],co[a]["_"]); r[a]=1/(1+math.exp(-(b[0]+sum(bi*r[ci] for bi,ci in zip(b[1:],c)))))
        T.append(r)
def LL(p,y): p=min(max(p,1e-4),1-1e-4); return -(y*math.log(p)+(1-y)*math.log(1-p))
print("OOS",len(T),"games",len({(r['d'],r['g']) for r in T}))
for a in ("p","q","p1","p2","pcal"): print(a, round(np.mean([LL(r[a],r["y"]) for r in T]),6))
print("p1-p0",np.mean([LL(r["p1"],r["y"])-LL(r["p"],r["y"]) for r in T]),"p2-p1",np.mean([LL(r["p2"],r["y"])-LL(r["p1"],r["y"]) for r in T]))
tp=[r for r in T if r["s"]=="top_pick"]; print("top picks",len(tp),"stated",np.mean([r["p"] for r in tp]),"real",np.mean([r["y"] for r in tp]))
for f in ("pitcher_outs","strikeouts"):
    fr=[r for r in T if r["f"]==f]; print(f,len(fr),{a:round(np.mean([LL(r[a],r["y"]) for r in fr]),4) for a in ("p","q","p2")})
json.dump(T,open("oos_rows_indep.json","w"))
