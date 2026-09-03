#!/usr/bin/env python3
"""Authoritative description of what FULL COUNT is actually trying to beat.

PUBLIC LEDGER ESTATE ONLY. Reads `public_top_picks` out of results/grades_*.json
-- the immutable record of what was actually published to users, carrying its
own publication provenance and settlement state. It never touches the legacy
static-board rows (`picks`), never touches the prospective shadow, and never
pools the three. Mixing them would answer a different question than "how do the
picks Jacob actually shows people perform".

DECIDED = hit + miss. Voids and ungraded are reported separately and are NOT in
the denominator, matching real wager settlement.
"""
import glob, json, math, os, statistics as st, sys
from collections import Counter, defaultdict

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ev/results"

def wilson(k, n, z=1.96):
    if not n: return (None, None)
    p = k / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    m = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (round(c-m, 4), round(c+m, 4))

def american_to_implied(o):
    if o is None: return None
    return (-o)/((-o)+100) if o < 0 else 100/(o+100)

rows, dates = [], set()
for f in sorted(glob.glob(os.path.join(ROOT, "grades_*.json"))):
    d = json.load(open(f))
    date = d.get("date") or os.path.basename(f)[7:17]
    for p in d.get("public_top_picks") or []:
        p["_date"] = date; rows.append(p); dates.add(date)

def report(label, sel):
    n = len(sel)
    g = Counter((r.get("grade") or "ungraded") for r in sel)
    hits, miss = g.get("hit", 0), g.get("miss", 0)
    dec = hits + miss
    rate = hits/dec if dec else None
    lo, hi = wilson(hits, dec)
    probs = [r["hit_probability"] for r in sel if isinstance(r.get("hit_probability"), (int, float))]
    dprobs = [r["hit_probability"] for r in sel
              if r.get("grade") in ("hit", "miss") and isinstance(r.get("hit_probability"), (int, float))]
    odds = [r["market_odds"] for r in sel if isinstance(r.get("market_odds"), (int, float))]
    imp = [american_to_implied(o) for o in odds]
    print(f"\n### {label}")
    print(f"  published N           {n}")
    print(f"  decided N             {dec}   (hit {hits} / miss {miss})")
    print(f"  void / ungraded       {g.get('void',0)} / {g.get('ungraded',0)}")
    if rate is not None:
        print(f"  REALIZED HIT RATE     {rate:.4f}   95% CI [{lo}, {hi}]")
    if dprobs:
        mp = sum(dprobs)/len(dprobs)
        print(f"  mean predicted prob   {mp:.4f}   (median {st.median(dprobs):.4f}) over decided")
        if rate is not None:
            print(f"  CALIBRATION GAP       {rate-mp:+.4f}   (realized minus predicted)")
    if odds:
        print(f"  price  mean {sum(odds)/len(odds):+.1f}  median {st.median(odds):+.0f}"
              f"  min {min(odds):+.0f}  max {max(odds):+.0f}")
        print(f"  mean market-implied   {sum(imp)/len(imp):.4f}")
        if rate is not None:
            print(f"  vs MARKET             realized {rate:.4f} - implied {sum(imp)/len(imp):.4f}"
                  f" = {rate-sum(imp)/len(imp):+.4f}")
    if sel:
        ds = sorted({r["_date"] for r in sel})
        print(f"  dates represented     {len(ds)}  ({ds[0]} -> {ds[-1]})   picks/day {n/len(ds):.2f}")
        pl = Counter(r.get("player_id") for r in sel); gm = Counter(r.get("game_pk") for r in sel)
        print(f"  concentration         {len(pl)} players (top {pl.most_common(1)[0][1]}),"
              f" {len(gm)} games (top {gm.most_common(1)[0][1]})")
        vv = Counter(json.dumps(r.get("versions"), sort_keys=True) for r in sel)
        print(f"  version strata        {len(vv)}")

print("=" * 74)
print("FULL COUNT — CHAMPION BASELINE (public Top Pick ledger estate only)")
print(f"source: {ROOT}   files: {len(glob.glob(os.path.join(ROOT,'grades_*.json')))}"
      f"   ledger rows: {len(rows)}   dates: {len(dates)}")
print("=" * 74)

report("ALL PUBLIC TOP PICKS", rows)
report("HITS market only", [r for r in rows if r.get("stat") == "hits"])

print("\n### BY MARKET (decided N >= 5 reported; smaller shown for completeness)")
bym = defaultdict(list)
for r in rows: bym[r.get("stat") or "?"].append(r)
print(f"  {'market':<26} {'pub':>4} {'dec':>4} {'hit':>4} {'rate':>7} {'95% CI':>18} {'predicted':>10} {'gap':>8}")
for m, sel in sorted(bym.items(), key=lambda kv: -len(kv[1])):
    g = Counter((r.get("grade") or "ungraded") for r in sel)
    h, ms = g.get("hit", 0), g.get("miss", 0); dec = h+ms
    dp = [r["hit_probability"] for r in sel if r.get("grade") in ("hit","miss")
          and isinstance(r.get("hit_probability"), (int,float))]
    rate = h/dec if dec else None
    lo, hi = wilson(h, dec)
    mp = sum(dp)/len(dp) if dp else None
    print(f"  {m:<26} {len(sel):>4} {dec:>4} {h:>4} "
          f"{(f'{rate:.4f}' if rate is not None else '  n/a'):>7} "
          f"{(f'[{lo}, {hi}]' if dec else ''):>18} "
          f"{(f'{mp:.4f}' if mp else ''):>10} "
          f"{(f'{rate-mp:+.4f}' if (rate is not None and mp) else ''):>8}")
