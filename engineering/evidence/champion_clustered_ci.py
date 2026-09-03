import glob, json, os, random
from collections import Counter, defaultdict
rows=[]
for f in sorted(glob.glob("/tmp/ev/results/grades_*.json")):
    d=json.load(open(f)); date=d.get("date") or os.path.basename(f)[7:17]
    for p in d.get("public_top_picks") or []:
        p["_date"]=date; rows.append(p)
dec=[r for r in rows if r.get("grade") in ("hit","miss")]
by=defaultdict(list)
for r in dec: by[r["_date"]].append(r)
dates=sorted(by)

def rate(sel):
    h=sum(1 for r in sel if r["grade"]=="hit"); return h/len(sel) if sel else None

def boot(sel_filter=None, reps=20000, seed=20260903):
    rng=random.Random(seed); out=[]
    dd={d:[r for r in by[d] if (sel_filter is None or sel_filter(r))] for d in dates}
    keys=[d for d in dates if dd[d]]
    for _ in range(reps):
        draw=[]
        for _ in range(len(keys)): draw.extend(dd[rng.choice(keys)])
        v=rate(draw)
        if v is not None: out.append(v)
    out.sort()
    return out[int(0.025*len(out))], out[int(0.975*len(out))]

print("DATE-CLUSTERED BOOTSTRAP (20k reps, resampling DATES not picks)")
print("The Wilson interval assumes independent picks. Picks share a slate --")
print("same weather, same umpires, same day's lineups -- so it is too narrow.\n")
for tag, filt in (("all decided", None),
                  ("fair_test only", lambda r: r.get("fair_test") is True)):
    sel=[r for r in dec if (filt is None or filt(r))]
    n=len(sel); h=sum(1 for r in sel if r["grade"]=="hit")
    dp=[r["hit_probability"] for r in sel if isinstance(r.get("hit_probability"),(int,float))]
    lo,hi=boot(filt)
    mp=sum(dp)/len(dp)
    print(f"  {tag:<18} n={n:<4} dates={len(dates)}  realized={h/n:.4f}")
    print(f"  {'':<18} date-clustered 95% CI [{lo:.4f}, {hi:.4f}]")
    print(f"  {'':<18} mean predicted {mp:.4f}   -> gap {h/n-mp:+.4f}")
    print(f"  {'':<18} predicted 0.60 floor inside CI? {'YES' if lo<=0.60<=hi else 'NO'}")
    print()

print("SUPPLY QUESTION, given the calibration curve")
print("If gates were loosened, the added picks come from the LOWEST probability")
print("band admitted. Here is what each band actually returns today:\n")
bands=[(0.60,0.62),(0.62,0.65),(0.65,0.70),(0.70,1.01)]
for lo,hi in bands:
    sel=[r for r in dec if isinstance(r.get("hit_probability"),(int,float)) and lo<=r["hit_probability"]<hi]
    if not sel: continue
    h=sum(1 for r in sel if r["grade"]=="hit"); n=len(sel)
    mp=sum(r["hit_probability"] for r in sel)/n
    def imp(o): return None if o is None else ((-o)/((-o)+100) if o<0 else 100/(o+100))
    ii=[imp(r.get("market_odds")) for r in sel if isinstance(r.get("market_odds"),(int,float))]
    mi=sum(ii)/len(ii) if ii else float('nan')
    print(f"  predicted [{lo:.2f},{hi:.2f})  n={n:<4} realized={h/n:.3f}  pred={mp:.3f}"
          f"  implied={mi:.3f}  vs_market={h/n-mi:+.3f}")
