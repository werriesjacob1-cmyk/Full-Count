import glob, json, math, os, statistics as st
from collections import Counter, defaultdict
rows=[]
for f in sorted(glob.glob("/tmp/ev/results/grades_*.json")):
    d=json.load(open(f)); date=d.get("date") or os.path.basename(f)[7:17]
    for p in d.get("public_top_picks") or []:
        p["_date"]=date; rows.append(p)
def wilson(k,n,z=1.96):
    if not n: return (None,None)
    p=k/n; den=1+z*z/n; c=(p+z*z/(2*n))/den
    m=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den; return (round(c-m,3),round(c+m,3))
def summ(tag, sel):
    g=Counter((r.get("grade") or "ungraded") for r in sel)
    h,m=g.get("hit",0),g.get("miss",0); dec=h+m
    dp=[r["hit_probability"] for r in sel if r.get("grade") in ("hit","miss")
        and isinstance(r.get("hit_probability"),(int,float))]
    rate=h/dec if dec else None; mp=sum(dp)/len(dp) if dp else None
    lo,hi=wilson(h,dec)
    print(f"  {tag:<40} dec={dec:<4} rate={(f'{rate:.3f}' if rate is not None else 'n/a'):<6}"
          f" CI=[{lo},{hi}]  pred={(f'{mp:.3f}' if mp else 'n/a')}"
          f"  gap={(f'{rate-mp:+.3f}' if rate is not None and mp else 'n/a')}")

print("ROBUSTNESS OF THE -16pt CALIBRATION GAP\n")
print("1. Does restricting to a FAIR TEST close it?")
summ("all", rows)
summ("fair_test == True", [r for r in rows if r.get("fair_test") is True])
summ("fair_test not False", [r for r in rows if r.get("fair_test") is not False])
summ("lineup_assumed not True", [r for r in rows if r.get("lineup_assumed") is not True])
summ("eligibility eligible", [r for r in rows if (r.get("eligibility") or {}).get("eligibility") in (None,"eligible")])

print("\n2. Is it one bad stretch, or persistent by date?")
bd=defaultdict(list)
for r in rows: bd[r["_date"]].append(r)
for d in sorted(bd):
    g=Counter((x.get("grade") or "ungraded") for x in bd[d]); h,m=g.get("hit",0),g.get("miss",0)
    dp=[x["hit_probability"] for x in bd[d] if x.get("grade") in ("hit","miss")]
    print(f"  {d}  pub={len(bd[d]):<3} dec={h+m:<3} hit={h:<3}"
          f" rate={(h/(h+m)) if h+m else float('nan'):.3f}"
          f" pred={sum(dp)/len(dp) if dp else float('nan'):.3f}")

print("\n3. Calibration curve — is overconfidence uniform or concentrated?")
buckets=[(0.50,0.60),(0.60,0.65),(0.65,0.70),(0.70,0.80),(0.80,1.01)]
for lo,hi in buckets:
    sel=[r for r in rows if r.get("grade") in ("hit","miss")
         and isinstance(r.get("hit_probability"),(int,float))
         and lo<=r["hit_probability"]<hi]
    if not sel: continue
    h=sum(1 for r in sel if r["grade"]=="hit"); n=len(sel)
    mp=sum(r["hit_probability"] for r in sel)/n
    wl,wh=wilson(h,n)
    print(f"  predicted [{lo:.2f},{hi:.2f})  n={n:<4} realized={h/n:.3f} CI=[{wl},{wh}]"
          f"  mean_pred={mp:.3f}  gap={h/n-mp:+.3f}")

print("\n4. Beating the price? (realized minus market-implied, per market)")
def imp(o): return None if o is None else ((-o)/((-o)+100) if o<0 else 100/(o+100))
bym=defaultdict(list)
for r in rows: bym[r.get("stat")].append(r)
for m,sel in sorted(bym.items(), key=lambda kv:-len(kv[1])):
    dsel=[r for r in sel if r.get("grade") in ("hit","miss")]
    if not dsel: continue
    h=sum(1 for r in dsel if r["grade"]=="hit"); n=len(dsel)
    ii=[imp(r.get("market_odds")) for r in dsel if isinstance(r.get("market_odds"),(int,float))]
    mi=sum(ii)/len(ii) if ii else None
    print(f"  {m:<20} n={n:<4} realized={h/n:.3f}  implied={mi:.3f}  edge={h/n-mi:+.3f}" if mi else
          f"  {m:<20} n={n:<4} realized={h/n:.3f}  implied=n/a")
