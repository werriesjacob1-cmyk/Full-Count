#!/usr/bin/env python3
"""Does the model rank picks better than chance WITHIN a market?

That question, not calibration, is the one tied to the North Star. Realized hit
rate at a fixed pick volume can only improve if the ordering puts more winners
in the top N. Recalibrating probabilities downward does not reorder anything --
it makes the advertised number honest and, through MIN_LINE_PROB, cuts volume.

FINDINGS as of 2026-09-10 (2,134 graded predictions, 32 dates, 15 markets;
date-clustered bootstrap, 4k reps, resampling dates because picks share a slate)

  1. NO measurable within-market ranking skill.

        within-market pooled AUC   n=2134   0.492  [0.461, 0.521]
          main board only          n= 213   0.477  [0.374, 0.578]
          best_of_category         n=1742   0.514  [0.484, 0.542]

     This is a well-powered null, not an underpowered shrug: the interval is
     about +/-0.03 wide and sits on 0.500.

  2. THE TRAP. Pooling across markets gives AUC 0.748 [0.721, 0.777], which
     looks like strong skill and is not. It counts cross-market pairs -- a 5%
     home-run prop against a 65% hits prop -- so it mostly proves the model
     knows base rates differ. Pair-weighting within market collapses it to
     0.492. Never quote the pooled figure as ranking skill.

  3. PER-MARKET, the pooled null is not uniform. Four of fifteen markets have
     intervals excluding 0.500:

        home_runs        n=276  0.371 [0.280, 0.461]   INVERTED
        pitcher_outs     n=132  0.649 [0.541, 0.767]   real skill
        rbis             n=126  0.608 [0.507, 0.700]   real skill
        singles          n=127  0.598 [0.510, 0.677]   real skill

     MULTIPLE COMPARISONS, stated rather than buried: fifteen markets tested at
     95% would throw about one false positive by chance, so four is suggestive
     of real structure but no single market above is settled. Treat each as a
     hypothesis. home_runs is the strongest candidate -- largest n, most
     extreme, and it drives the whole moonshot category (AUC 0.362, n=155) --
     so a higher predicted home-run probability went with FEWER home runs.

     The pooled 0.492 and these four coexist because the skilled and inverted
     markets roughly cancel, and eleven markets contribute nothing.

  3b. HOME_RUNS DEEP DIVE, and it ends in a negative result worth keeping.

     The inversion REPLICATES out of sample. Splitting the 31 HR dates 60/40
     (train 2026-08-07..08-25, test 08-26..09-08), the model's AUC on the 13
     held-out dates is 0.298 -- more inverted than the 0.371 full-sample figure.
     So this is not one bad stretch.

     Its recorded signals look damning at first glance. The model's dominant
     input, hard_hit_105_rate, correlates +0.767 with its own output; three
     signals that DO track outcome are essentially unweighted:

        signal              model weight   corr(signal, outcome)  95% CI
        season_barrel_pct        +0.573    -0.220  [-0.387,-0.049]
        series_game              -0.293    +0.166  [+0.036,+0.298]
        pull_park_synergy        +0.007    +0.204  [+0.061,+0.351]
        park_hand_index          -0.011    +0.184  [+0.017,+0.353]
        lineup_slot              +0.001    +0.137  [+0.010,+0.255]

     Do NOT act on that table. Three reasons, and the third is decisive:

       - hard_hit_105_rate, the actual dominant driver, is NOT established as
         backwards: -0.142 [-0.300,+0.018] spans zero. The headline reading
         ("the model uses contact quality backwards") is not supported.
       - 29 signals were tested; at 95% several of the above are expected to be
         false positives, and these are a SELECTED population, so collider bias
         from selecting on the composite score cannot be excluded. The negative
         sign does hold in both selection strata (moonshot and
         best_of_category), which is evidence against a pure artifact, but not
         proof.
       - It does not transfer. Selecting the top three signals on TRAIN dates
         only, z-scored on TRAIN statistics, and scoring the held-out TEST
         dates gives composite AUC 0.447 against the model's 0.298 -- a
         difference of +0.149 with 95% CI [-0.127,+0.364]. Not distinguishable,
         and the composite does not beat chance either. Per-signal correlations
         at 18 dates / 147 rows simply do not carry to new dates.

     CONCLUSION. The HR ranking is reliably anti-informative, and no better
     ranking is recoverable from the recorded signals at this sample size.
     That argues against rebuilding the HR scorer on this evidence, and for a
     presentational fix instead: the moonshot CATEGORY delivers its advertised
     range (realized 0.196 against a 15-25% design target), so the product is
     not broken in aggregate -- only its internal ordering is, and the board
     should stop implying the top moonshot is a better bet than the fifth.

  4. Calibration across the full 0.01-0.95 range is decent -- decile gaps run
     -0.079..+0.041, mildly overconfident above the middle. That is much better
     than the -0.115 gap measured on the PUBLIC LEDGER alone, and the
     difference is a selection effect: the published board is the top slice of
     a distribution with no real within-market ordering, so it preferentially
     selects overstated probabilities and then regresses toward the base rate.

EVIDENCE REGIME. This reads the `picks` array of results/grades_*.json -- the
mutable daily canonical file, NOT the immutable public Top Pick ledger. It can
support a claim about model skill. It cannot support a claim about deployed
product performance; only public_top_picks can. best_of_category is 1,742 of
the 2,134 rows and by design sits below the main board's floor, which is what
gives the probability range needed to measure ranking at all.

Read-only. Reads git objects, changes nothing, proposes nothing.

    python3 engineering/evidence/model_skill_audit.py [git-ref]
"""
import collections
import json
import random
import subprocess
import sys

REPS = 4000
SEED = 20260910
MIN_MARKET_N = 40


def load(ref):
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
            grade, prob = rec.get("grade"), rec.get("hit_probability")
            if grade not in ("hit", "miss") or prob is None:
                continue
            rows.append(dict(day=day, p=prob, hit=1 if grade == "hit" else 0,
                             cat=rec.get("category") or "main",
                             stat=(rec.get("projection") or {}).get("stat")))
    return rows


def auc(rows):
    """Rank AUC with tie correction. None when one class is absent."""
    pos = [r["p"] for r in rows if r["hit"]]
    neg = [r["p"] for r in rows if not r["hit"]]
    if not pos or not neg:
        return None
    marked = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
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


def within_market_auc(rows):
    """Pair-weighted mean of per-market AUC: skill with base rates removed."""
    bym = collections.defaultdict(list)
    for r in rows:
        bym[r["stat"]].append(r)
    num = den = 0
    for group in bym.values():
        pos = sum(r["hit"] for r in group)
        neg = len(group) - pos
        if not pos or not neg:
            continue
        a = auc(group)
        if a is None:
            continue
        num += a * pos * neg
        den += pos * neg
    return num / den if den else None


def bootstrap(rows, bydate, dates, metric, pred):
    observed = [r for r in rows if pred(r)]
    base = metric(observed)
    if base is None:
        return None
    draws = []
    for _ in range(REPS):
        sample = []
        for _ in range(len(dates)):
            sample.extend(bydate[random.choice(dates)])
        v = metric([r for r in sample if pred(r)])
        if v is not None:
            draws.append(v)
    draws.sort()
    return len(observed), base, draws[int(.025 * len(draws))], draws[int(.975 * len(draws))]


def verdict(lo, hi):
    if lo > 0.5:
        return "REAL SKILL"
    if hi < 0.5:
        return "INVERTED"
    return "no measurable skill (CI spans 0.50)"


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    random.seed(SEED)
    rows = load(ref)
    if not rows:
        print(f"no graded predictions at {ref}")
        return
    bydate = collections.defaultdict(list)
    for r in rows:
        bydate[r["day"]].append(r)
    dates = sorted(bydate)
    print(f"ref {ref}: {len(rows)} graded predictions, {len(dates)} dates, "
          f"{len({r['stat'] for r in rows})} markets")
    print("by category:", dict(collections.Counter(r["cat"] for r in rows)))

    print("\nCALIBRATION across the full predicted range (deciles)")
    ordered = sorted(rows, key=lambda r: r["p"])
    size = len(ordered) // 10
    print(f"  {'bucket':16s} {'n':>4s} {'pred':>6s} {'real':>6s} {'gap':>7s}")
    for i in range(10):
        g = ordered[i * size:(i + 1) * size] if i < 9 else ordered[i * size:]
        if not g:
            continue
        pred = sum(x["p"] for x in g) / len(g)
        real = sum(x["hit"] for x in g) / len(g)
        print(f"  {g[0]['p']:.3f}-{g[-1]['p']:.3f}  {len(g):4d} "
              f"{pred:6.3f} {real:6.3f} {real - pred:+7.3f}")

    print("\nRANKING SKILL (AUC, date-clustered 95% CI). 0.500 = no information.")
    segments = [
        ("WITHIN-MARKET pooled", within_market_auc, lambda r: True),
        ("  main board only", within_market_auc, lambda r: r["cat"] == "main"),
        ("  best_of_category", within_market_auc, lambda r: r["cat"] == "best_of_category"),
        ("POOLED across markets", auc, lambda r: True),
    ]
    for label, metric, pred in segments:
        res = bootstrap(rows, bydate, dates, metric, pred)
        if not res:
            continue
        n, base, lo, hi = res
        note = "  <- base-rate separation, NOT skill" if label.startswith("POOLED") else ""
        print(f"  {label:24s} n={n:5d} AUC={base:.3f} [{lo:.3f},{hi:.3f}]  "
              f"{verdict(lo, hi)}{note}")

    print("\nPER-MARKET (n >= %d)" % MIN_MARKET_N)
    bym = collections.defaultdict(list)
    for r in rows:
        bym[r["stat"]].append(r)
    print(f"  {'market':22s} {'n':>4s} {'base':>6s} {'AUC':>6s} {'95% CI':>16s}  verdict")
    for m, group in sorted(bym.items(), key=lambda kv: -len(kv[1])):
        if len(group) < MIN_MARKET_N:
            continue
        res = bootstrap(rows, bydate, dates, auc, lambda r, m=m: r["stat"] == m)
        if not res:
            continue
        n, base, lo, hi = res
        rate = sum(r["hit"] for r in group) / len(group)
        print(f"  {m:22s} {n:4d} {rate:6.3f} {base:6.3f} [{lo:5.3f},{hi:5.3f}]  "
              f"{verdict(lo, hi)}")


if __name__ == "__main__":
    main()
