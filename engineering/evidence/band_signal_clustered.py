#!/usr/bin/env python3
"""Which champion-baseline findings survive date-clustered resampling?

Picks share a slate -- same weather, same umpires, same day's lineups -- so the
unit of independent evidence is the DATE, not the pick. This resamples the
dates with replacement (20k reps) and reports 95% intervals for two different
questions that are easy to conflate:

    vs MARKET       realized - market_implied   "do we beat the price?"
    CALIBRATION     realized - predicted        "are our stated probabilities honest?"

It is deliberately a NEGATIVE-RESULT tool. Its job is to say which findings are
not yet distinguishable from noise.

HISTORY OF THIS FILE, because it is the point.

At 10 dates / 114 decided picks, exactly one segment's vs-market interval
excluded zero: the [0.60,0.62) band -- the picks admitted just over
MIN_LINE_PROB -- at -0.179 [-0.332,-0.061]. It looked robust: 10/10 dates
contributed, 9/10 underperformed the price, and leave-one-date-out held between
-0.137 and -0.234 without changing sign. It was reported as the only
statistically survivable finding on the project.

It did not replicate. Seven more slates arrived (17 dates / 247 decided, with
MIN_LINE_PROB, MIN_QUALITY_SCORE and MIN_POSITIVE_LIFT all byte-identical, so
the populations are comparable):

    [0.60,0.62) vs market, original 10 dates : n=42  -0.195
    [0.60,0.62) vs market, new 7 dates       : n=35  +0.022
    [0.60,0.62) vs market, combined 17       : n=77  -0.097  [-0.239,+0.027]

The sign reversed out of sample. On 17 dates NO segment's vs-market interval
excludes zero. The finding is RETRACTED: there is no evidence that any band or
market family beats or loses to the price. A within-sample interval that
excludes zero is not the same thing as a result, and 10 date-clusters could not
support the verdict that was drawn from them.

WHAT DID SURVIVE, and it is a different claim. Against PREDICTED rather than
against the market, the board is overconfident, and that replicated. Five
segments' intervals exclude zero (values as this tool prints them at 17 dates):

    ALL decided             n=247  -0.115  [-0.204,-0.035]
    band [0.60,0.62)        n= 77  -0.181  [-0.322,-0.058]
    band [0.65,0.70)        n= 76  -0.156  [-0.318,-0.001]
    market hits_runs_rbis   n=127  -0.144  [-0.257,-0.029]
    market strikeouts       n= 45  -0.167  [-0.329,-0.023]

Not distinguishable: band [0.62,0.65), band [0.70,+), market pitcher_outs, and
-- notably -- market hits at +0.031. The overconfidence is not uniform: it sits
in hits_runs_rbis (the largest family) and strikeouts, while plain hits is fine.

The overall gap replicated out of sample in direction, shrinking in magnitude:
original 10 dates -0.164, new 7 dates -0.062. 12/17 dates are negative and
leave-one-date-out holds between -0.093 and -0.140 without a sign flip. Every
segment above is likewise stable under leave-one-date-out.

Mean predicted barely moved between the two periods (0.6447 vs 0.6451); what
changed is realized (0.4803 -> 0.5833). The board advertises about 64% and has
delivered about 53% over 17 slates.

CAVEAT, stated because the retraction above exists precisely for want of one:
17 date-clusters is still not many, the interval on the headline gap is wide
(-0.204..-0.035), and the period-over-period magnitude nearly halved. Direction
is well supported; magnitude is not pinned down.

Read the two tables as answering different questions. "We are worse than the
market" is unsupported. "Our probabilities are too high" is supported, and is a
probability-model problem rather than a threshold one.

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
    """realized - market_implied: do we beat the price?"""
    return (sum(r["hit"] for r in rows) - sum(r["mi"] for r in rows)) / len(rows)


def calibration(rows):
    """realized - predicted: are our stated probabilities honest?"""
    return (sum(r["hit"] for r in rows) - sum(r["p"] for r in rows)) / len(rows)


def bootstrap(rows, bydate, dates, pred, metric=edge):
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
            draws.append(metric(matched))
    draws.sort()
    return (len(observed), metric(observed),
            draws[int(.025 * len(draws))], draws[int(.975 * len(draws))])


def table(rows, bydate, dates, segments, metric, label):
    """One bootstrap table. Returns the segments whose interval excludes zero."""
    print(f"\n{label}")
    print(f"{'segment':34s} {'n':>4s} {'value':>7s} {'95% CI':>18s}  verdict")
    signals = []
    for name, pred in segments:
        res = bootstrap(rows, bydate, dates, pred, metric)
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
    return signals


def support(rows, dates, signals, metric, name_of_metric):
    """A segment carried by one or two slates is an artifact, not a finding."""
    print(f"\nCluster support and leave-one-date-out ({name_of_metric}):")
    if not signals:
        print("  (none)")
    for name, pred in signals:
        seg = [r for r in rows if pred(r)]
        byd = collections.defaultdict(list)
        for r in seg:
            byd[r["day"]].append(r)
        worse = sum(1 for rs in byd.values() if metric(rs) < 0)
        print(f"  {name}: n={len(seg)} across {len(byd)}/{len(dates)} dates; "
              f"{worse}/{len(byd)} slates negative")
        vals = []
        for d in sorted(byd):
            rest = [r for r in seg if r["day"] != d]
            if rest:
                v = metric(rest)
                vals.append(v)
                print(f"    drop {d}: n={len(rest):3d}  {v:+.3f}")
        if vals:
            flips = "YES" if min(vals) * max(vals) < 0 else "NO"
            print(f"    range {min(vals):+.3f}..{max(vals):+.3f}  sign flips: {flips}")


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
    print(f"date-clustered bootstrap, {REPS} reps, resampling dates not picks")

    segments = [("ALL decided", lambda r: True)]
    segments += [(f"band {b}", lambda r, b=b: band(r["p"]) == b) for b in BANDS]
    segments += [(f"market {s}", lambda r, s=s: r["stat"] == s)
                 for s in sorted({r["stat"] for r in rows})]

    # Two different questions. Keep them apart: conflating them is how a
    # retracted vs-market finding got reported as the project's headline.
    mkt = table(rows, bydate, dates, segments, edge,
                "vs MARKET  (realized - market_implied): do we beat the price?")
    support(rows, dates, mkt, edge, "vs market")

    cal = table(rows, bydate, dates, segments, calibration,
                "CALIBRATION  (realized - predicted): are our probabilities honest?")
    support(rows, dates, cal, calibration, "calibration")

    print(f"\nmean predicted {sum(r['p'] for r in rows) / len(rows):.4f}  "
          f"realized {sum(r['hit'] for r in rows) / len(rows):.4f}")


if __name__ == "__main__":
    main()
