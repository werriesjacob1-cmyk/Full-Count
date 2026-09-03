#!/usr/bin/env python3
"""Which champion-baseline findings survive date-clustered resampling?

CHAMPION_BASELINE.md reports per-band and per-family gaps against the market
price from the public Top Pick ledger. Those are point estimates. Picks share a
slate -- same weather, same umpires, same day's lineups -- so the unit of
independent evidence is the DATE, not the pick, and a per-cell point estimate
at n=9..40 across 10 dates says very little on its own.

This re-tests each segment by resampling the dates with replacement (20k reps)
and reporting the 95% interval of (realized - market_implied). It is
deliberately a NEGATIVE-RESULT tool: its job is to say which of the baseline's
findings are not yet distinguishable from noise.

Result on the 2026-08-18..09-02 estate (114 decided picks, 10 dates): exactly
ONE segment's interval excludes zero -- the [0.60,0.62) band, the picks
admitted just over MIN_LINE_PROB, at -0.179 [-0.332,-0.061]. It is not carried
by one slate: 10/10 dates contribute, 9/10 underperformed the price, and
leave-one-date-out holds between -0.137 and -0.234 without changing sign.

Two claims the baseline leans on do NOT survive:
    ALL decided     -0.098  [-0.243,+0.023]
    band [0.70,+)   +0.079  [-0.248,+0.323]
so "we lose ~11 points to the market" and "[0.70,+) is the only band beating
the price" should not be quoted as findings.

Read-only. Reads the public ledger estate at a git ref; changes nothing.

    python3 engineering/evidence/band_signal_clustered.py [git-ref]
"""
import collections
import json
import random
import subprocess
import sys

REPS = 20000
SEED = 20260903
BANDS = ("[.60,.62)", "[.62,.65)", "[.65,.70)", "[.70,+)")


def band(p):
    if p < 0.62:
        return BANDS[0]
    if p < 0.65:
        return BANDS[1]
    if p < 0.70:
        return BANDS[2]
    return BANDS[3]


def load(ref):
    """Decided public Top Picks at `ref`. Only hit/miss: ungraded is not a
    result, and void is not a wager, so neither belongs in a rate."""
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "results/"],
                             capture_output=True, text=True).stdout.split()
    rows = []
    for path in listing:
        if "/grades_" not in path:
            continue
        blob = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True).stdout
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        day = path.split("grades_")[1][:10]
        for rec in (payload.get("public_top_picks") or []):
            if rec.get("grade") not in ("hit", "miss"):
                continue
            if rec.get("hit_probability") is None or rec.get("market_implied") is None:
                continue
            rows.append(dict(day=day, stat=rec.get("stat"), p=rec["hit_probability"],
                             mi=rec["market_implied"], hit=1 if rec["grade"] == "hit" else 0))
    return rows


def edge(rows):
    return (sum(r["hit"] for r in rows) - sum(r["mi"] for r in rows)) / len(rows)


def bootstrap(rows, bydate, dates, pred):
    observed = [r for r in rows if pred(r)]
    if len(observed) < 3:
        return None
    draws = []
    for _ in range(REPS):
        sample = []
        for _ in range(len(dates)):
            sample.extend(bydate[random.choice(dates)])
        matched = [r for r in sample if pred(r)]
        if matched:
            draws.append(edge(matched))
    draws.sort()
    return len(observed), edge(observed), draws[int(.025 * len(draws))], draws[int(.975 * len(draws))]


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    random.seed(SEED)
    rows = load(ref)
    if not rows:
        print(f"no decided public Top Picks at {ref}")
        return
    bydate = collections.defaultdict(list)
    for r in rows:
        bydate[r["day"]].append(r)
    dates = sorted(bydate)

    print(f"ref {ref}: {len(rows)} decided public Top Picks across {len(dates)} dates")
    print(f"date-clustered bootstrap of (realized - market_implied), {REPS} reps\n")
    print(f"{'segment':34s} {'n':>4s} {'vsMkt':>7s} {'95% CI':>18s}  verdict")

    segments = [("ALL decided", lambda r: True)]
    segments += [(f"band {b}", lambda r, b=b: band(r["p"]) == b) for b in BANDS]
    segments += [(f"market {s}", lambda r, s=s: r["stat"] == s)
                 for s in sorted({r["stat"] for r in rows})]

    signals = []
    for name, pred in segments:
        res = bootstrap(rows, bydate, dates, pred)
        if not res:
            print(f"{name:34s}  (n<3, not measurable)")
            continue
        n, base, lo, hi = res
        if hi < 0 or lo > 0:
            verdict = "SIGNAL (CI excludes 0)"
            signals.append((name, pred))
        else:
            verdict = "not distinguishable from noise"
        print(f"{name:34s} {n:4d} {base:+7.3f} [{lo:+6.3f},{hi:+6.3f}]  {verdict}")

    # A segment carried by one or two slates is an artifact, not a finding.
    print("\nCluster support and leave-one-date-out for each SIGNAL:")
    if not signals:
        print("  (none)")
    for name, pred in signals:
        seg = [r for r in rows if pred(r)]
        byd = collections.defaultdict(list)
        for r in seg:
            byd[r["day"]].append(r)
        worse = sum(1 for rs in byd.values() if edge(rs) < 0)
        print(f"  {name}: n={len(seg)} across {len(byd)}/{len(dates)} dates; "
              f"{worse}/{len(byd)} slates underperformed the price")
        for d in sorted(byd):
            rest = [r for r in seg if r["day"] != d]
            if rest:
                print(f"    drop {d}: n={len(rest):3d}  vsMkt={edge(rest):+.3f}")


if __name__ == "__main__":
    main()
