import json, random, numpy as np
from collections import defaultdict, Counter
T=json.load(open("oos_rows_indep.json"))
U=[r for r in T if r["s"] is not None and 0.40<=r["q"]<=0.70]   # BAND-RESTRICTED universe (exploratory)
print("OOS",len(T),"eligible universe (status!=None)",len(U),Counter(r["s"] for r in U))
def profit(o,y): return (o/100 if o>0 else 100/-o) if y else -1.0
for r in U: r["resid"]=r["p2"]-r["p1"]; r["edge"]=r["p"]-r["q"]
rules={"champion(top_pick)":None,"raw_p":"p","calibrated_pcal":"pcal","price_q(favorite)":"q","price_recal_p1":"p1","blend_p2":"p2","residual_p2-p1":"resid","edge_p-q":"edge"}
dates=sorted({r["d"] for r in U}); picks={k:[] for k in rules}; zero=0
for d in dates:
    day=[r for r in U if r["d"]==d]; champ=[r for r in day if r["s"]=="top_pick"]; n=len(champ)
    if n==0: zero+=1; continue
    for k,key in rules.items():
        sel=champ if key is None else sorted(day,key=lambda r:(-r[key],r["g"]))[:n]
        picks[k]+=[dict(r,_d=d) for r in sel]
print("slates",len(dates),"slates with 0 champion picks",zero)
def boot(rs,B=2000,seed=1):
    cl=defaultdict(list)
    for r in rs: cl[(r["_d"],r["g"])].append(r["y"])
    ks=list(cl); rng=random.Random(seed); out=[]
    for _ in range(B):
        s=[cl[ks[rng.randrange(len(ks))]] for _ in ks]; n=sum(len(x) for x in s); out.append(sum(sum(x) for x in s)/n)
    return np.percentile(out,[2.5,97.5])
champ_ids={(r["_d"],r["g"],r["p"],r["o"]) for r in picks["champion(top_pick)"]}
print(f"{'rule':24s} n  hits  hit%   meanQ  hit-Q  ROI   games maxPerGame overlapW/champ  CI")
for k,rs in picks.items():
    n=len(rs); h=sum(r["y"] for r in rs); mq=np.mean([r["q"] for r in rs]); roi=np.mean([profit(r["o"],r["y"]) for r in rs])
    g=Counter((r["_d"],r["g"]) for r in rs); ov=sum(1 for r in rs if (r["_d"],r["g"],r["p"],r["o"]) in champ_ids)
    lo,hi=boot(rs)
    print(f"{k:24s} {n:3d} {h:4d} {h/n:6.3f} {mq:6.3f} {h/n-mq:+6.3f} {roi:+6.3f} {len(g):4d} {max(g.values()):4d} {ov:6d}  [{lo:.3f},{hi:.3f}]")
print("champion family mix",Counter(r["f"] for r in picks["champion(top_pick)"]))
print("champion by family hit:",{f:(sum(1 for r in picks['champion(top_pick)'] if r['f']==f),round(np.mean([r['y'] for r in picks['champion(top_pick)'] if r['f']==f]),3)) for f in {r['f'] for r in picks['champion(top_pick)']}})
cp=picks["champion(top_pick)"]; print("champion stated vs real by family:",{f:(round(np.mean([r['p'] for r in cp if r['f']==f]),3),round(np.mean([r['q'] for r in cp if r['f']==f]),3),round(np.mean([r['y'] for r in cp if r['f']==f]),3)) for f in {r['f'] for r in cp}})
# portfolio: same-game top picks
g=Counter((r["_d"],r["g"]) for r in cp); print("champion picks per game dist",Counter(g.values()))
# by week stability of residual signal
import datetime as dt
wk=lambda d: (dt.date.fromisoformat(d)-dt.timedelta(days=dt.date.fromisoformat(d).weekday())).isoformat()
def LL(p,y): p=min(max(p,1e-4),1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p))
for w in sorted({wk(r["d"]) for r in T}):
    rs=[r for r in T if wk(r["d"])==w]
    print("week",w,len(rs),"p2-p1",round(np.mean([LL(r["p2"],r["y"])-LL(r["p1"],r["y"]) for r in rs]),4),"p-q",round(np.mean([LL(r["p"],r["y"])-LL(r["q"],r["y"]) for r in rs]),4))
for f in sorted({r["f"] for r in T}):
    rs=[r for r in T if r["f"]==f]
    print("fam",f,len(rs),"p2-p1",round(np.mean([LL(r["p2"],r["y"])-LL(r["p1"],r["y"]) for r in rs]),4),"pcal-p",round(np.mean([LL(r["pcal"],r["y"])-LL(r["p"],r["y"]) for r in rs]),4))
