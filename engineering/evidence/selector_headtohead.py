#!/usr/bin/env python3
"""Does ANY number we already compute rank picks better than the model does?

Realized hit rate at fixed volume can only improve if the ORDERING puts more
winners in the top N. model_skill_audit.py established that the model's own
probability has no measurable within-market ranking skill (AUC 0.492
[0.461,0.521]). The obvious follow-up is cheap: we compute several other
numbers per pick, so does one of THEM order better?

Eight PRE-SPECIFIED scorers, no data mining. Every one is already on every
graded row, so there is nothing fitted and nothing to overfit in selection --
which is deliberate. An earlier attempt on home_runs mined the top-3 signals
per market and failed to transfer precisely because the mining was the
overfitting.

RESULT: NO. Nothing beats the incumbent out of sample.

Full sample (common subset, n=1783, 31 dates, 15 markets, pair-weighted
within-market AUC, date-clustered bootstrap):

    hit_probability (incumbent)  0.488 [0.456,0.520]   --
    market_implied (the price)   0.531 [0.486,0.577]   +0.043 [+0.001,+0.088]
    reliability grade            0.528 [0.506,0.551]   +0.040 [+0.008,+0.072]
    sample_n                     0.529 [0.486,0.578]   +0.042 [-0.001,+0.093]
    base_rate                    0.518 [0.483,0.552]   +0.030 [-0.002,+0.064]
    score (quality 0-100)        0.510 [0.467,0.553]   +0.022 [-0.029,+0.070]
    market_edge                  0.485 [0.440,0.532]   -0.002 [-0.061,+0.059]
    lift                         0.470 [0.440,0.501]   -0.018 [-0.031,-0.005]

Two appeared to beat the incumbent on the full sample. NEITHER survives a
chronological holdout, and a third joined them on train only:

    TRAIN 18 dates (08-07..08-25), n=856      TEST 13 dates (08-26..09-08), n=927
    reliability     +0.060 [+0.006,+0.120] BEATS    +0.028 [-0.010,+0.069] tie
    sample_n        +0.069 [+0.011,+0.132] BEATS    +0.028 [-0.033,+0.092] tie
    market_implied  +0.041 [-0.037,+0.122] tie      +0.044 [-0.006,+0.094] tie

On the held-out half EVERY scorer ties the incumbent. reliability's and
sample_n's full-sample significance was carried by the train half. That is the
same failure mode that killed the [0.60,0.62) vs-market finding last week,
caught here BEFORE it was reported as actionable.

market_implied is the interesting near-miss: it never reaches significance in
either half on its own, yet its point estimate is the most stable of the eight
(+0.041 train, +0.044 test). If anything here is real it is that one, and it
would need roughly double the dates to say so.

Note also eight comparisons at 95% expect about 0.4 false positives, and
reliability is a 4-level ordinal so its AUC has limited resolution regardless.

The incumbent is strikingly stable at no-skill: 0.489 train, 0.480 test, 0.488
full. Only `lift` is reliably worse than it, and only on the full sample and
train (test: -0.014 [-0.031,+0.005]).

WHAT THIS RULES OUT. Better USE of the numbers already computed is not the
lever. Reordering the board by price, evidence grade, sample depth, base rate,
quality score, market edge or lift does not beat what it does today. The
remaining candidates are new information, or a materially different model --
not a re-weighting of what is on hand.

EVIDENCE REGIME. Reads the `picks` array of results/grades_*.json -- the
mutable daily canonical file, NOT the immutable public Top Pick ledger. It can
support claims about model skill; it cannot support claims about deployed
product performance.

Read-only. Changes nothing, proposes nothing.

    python3 engineering/evidence/selector_headtohead.py [git-ref]
"""
import collections
import json
import random
import subprocess
import sys

REPS = 4000
SEED = 20260910
RELIABILITY_ORDER = {"A": 3, "B": 2, "C": 1, "D": 0}
REQUIRED = ("hit_probability", "market_edge", "lift", "score",
            "base_rate", "sample_n", "market_implied")

SCORERS = {
    "hit_probability (incumbent)": lambda r: r["hit_probability"],
    "market_implied (the price)": lambda r: r["market_implied"],
    "reliability grade": lambda r: r["reliability_rank"],
    "sample_n": lambda r: r["sample_n"],
    "base_rate": lambda r: r["base_rate"],
    "score (quality 0-100)": lambda r: r["score"],
    "market_edge": lambda r: r["market_edge"],
    "lift": lambda r: r["lift"],
}
INCUMBENT = "hit_probability (incumbent)"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load(ref):
    """Rows carrying EVERY scorer, so a head-to-head compares skill and not
    coverage. A scorer absent on different rows would otherwise be scored on a
    different population than its rivals."""
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "results/"],
                             capture_output=True, text=True).stdout.split()
    rows = []
    for path in sorted(p for p in listing if "/grades_" in p):
        blob = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True).stdout
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        day = path.split("grades_")[1][:10]
        for rec in (payload.get("picks") or []):
            if rec.get("grade") not in ("hit", "miss"):
                continue
            if any(not _num(rec.get(k)) for k in REQUIRED):
                continue
            rank = RELIABILITY_ORDER.get(rec.get("reliability"))
            if rank is None:
                continue
            rows.append(dict(day=day, hit=1 if rec["grade"] == "hit" else 0,
                             stat=(rec.get("projection") or {}).get("stat"),
                             reliability_rank=rank,
                             **{k: rec[k] for k in REQUIRED}))
    return rows


def auc(pairs):
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    marked = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    ranks, i = {}, 0
    while i < len(marked):
        j = i
        while j < len(marked) and marked[j][0] == marked[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        for k in range(i, j):
            ranks[k] = avg
        i = j
    s = sum(ranks[k] for k, (_, y) in enumerate(marked) if y == 1)
    return (s - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def within_market(rows, fn):
    """Pair-weighted mean of per-market AUC. Pooling ACROSS markets would
    mostly measure base-rate separation -- see model_skill_audit.py, where the
    pooled figure reads 0.748 and the within-market figure 0.492."""
    bym = collections.defaultdict(list)
    for r in rows:
        bym[r["stat"]].append(r)
    num = den = 0
    for group in bym.values():
        pos = sum(r["hit"] for r in group)
        neg = len(group) - pos
        if not pos or not neg:
            continue
        a = auc([(fn(r), r["hit"]) for r in group])
        if a is None:
            continue
        num += a * pos * neg
        den += pos * neg
    return num / den if den else None


def resample(rows, reps=REPS):
    bydate = collections.defaultdict(list)
    for r in rows:
        bydate[r["day"]].append(r)
    dates = sorted(bydate)
    for _ in range(reps):
        out = []
        for _ in range(len(dates)):
            out.extend(bydate[random.choice(dates)])
        yield out


def compare(rows, label):
    print(f"\n{label}: n={len(rows)}, {len({r['day'] for r in rows})} dates, "
          f"{len({r['stat'] for r in rows})} markets")
    draws = list(resample(rows))
    base_pt = within_market(rows, SCORERS[INCUMBENT])
    print(f"  {'scorer':30s} {'AUC':>6s} {'95% CI':>16s} {'vs incumbent':>22s}")
    for name, fn in SCORERS.items():
        pt = within_market(rows, fn)
        if pt is None:
            continue
        own, diff = [], []
        for s in draws:
            a = within_market(s, fn)
            b = within_market(s, SCORERS[INCUMBENT])
            if a is not None:
                own.append(a)
            if a is not None and b is not None:
                diff.append(a - b)
        own.sort()
        diff.sort()
        lo, hi = own[int(.025 * len(own))], own[int(.975 * len(own))]
        dl, dh = diff[int(.025 * len(diff))], diff[int(.975 * len(diff))]
        tag = "" if name == INCUMBENT else (
            " BEATS" if dl > 0 else (" worse" if dh < 0 else " tie"))
        print(f"  {name:30s} {pt:6.3f} [{lo:5.3f},{hi:5.3f}] "
              f"{pt - base_pt:+7.3f} [{dl:+6.3f},{dh:+6.3f}]{tag}")


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    random.seed(SEED)
    rows = load(ref)
    if not rows:
        print(f"no rows carrying every scorer at {ref}")
        return
    print(f"ref {ref}: {len(rows)} rows carrying all {len(SCORERS)} scorers")
    compare(rows, "FULL SAMPLE")
    # A chronological holdout, because a full-sample interval that excludes
    # zero is not a result. This is what a previously reported finding failed.
    dates = sorted({r["day"] for r in rows})
    cut = int(len(dates) * 0.6)
    train, test = set(dates[:cut]), set(dates[cut:])
    compare([r for r in rows if r["day"] in train],
            f"TRAIN ({dates[0]}..{dates[cut - 1]})")
    compare([r for r in rows if r["day"] in test],
            f"TEST, held out ({dates[cut]}..{dates[-1]})")


if __name__ == "__main__":
    main()
