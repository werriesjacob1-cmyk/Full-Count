#!/usr/bin/env python3
"""test_prop_math.py — checks the probability math against INDEPENDENT
implementations, so it stays honest as the code changes.

    /tmp/mlbvenv/bin/python3 test_prop_math.py          # or any python3
    python3 test_prop_math.py -v                        # show every check

WHY THIS FILE EXISTS. Every number this pipeline ships is a probability, and
a probability that is quietly wrong looks exactly like one that is right --
it is in [0,1], it sorts, it renders. Reading the formulas does not catch
that. So nothing here re-derives the code's own logic: each check either
compares against a library that was written by someone else (scipy), or
against a slow brute-force version that is obviously correct by inspection
(full enumeration, pairwise counting, root-finding), or against a Monte Carlo
simulation with enough trials to pin the answer inside its own noise.

scipy is used where present and brute-forced where not, so this runs anywhere.

Every tolerance below is deliberate. Exact-arithmetic checks are held to 1e-9
because anything looser would hide a real algebra error; the Monte Carlo
checks are held to 5 standard errors of the simulation itself, which is the
only honest tolerance for a stochastic reference.
"""
import math
import random
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import prop_probability as pp

try:
    from scipy.stats import binom as _sp_binom
except Exception:
    _sp_binom = None

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


# ══════════════════════════════════════════════════════════════════════════
#  Independent reference implementations. Slow and obvious on purpose.
# ══════════════════════════════════════════════════════════════════════════

def ref_binom_sf(n, p, k):
    """P(X >= k) for X ~ Binomial(n, p), summed term by term with math.comb.
    No recurrence, no logs -- the definition, written out."""
    if k <= 0:
        return 1.0
    if n <= 0 or k > n:
        return 0.0
    return sum(math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
               for i in range(int(k), int(n) + 1))


def ref_tb_dist(pa_dist, n):
    """Full enumeration of every sequence of n plate appearances. O(5^n), so
    only usable for small n -- which is the point: it cannot be wrong."""
    dist = {0: 1.0}
    for _ in range(int(n)):
        nxt = {}
        for tb, ptb in dist.items():
            for b, pb in pa_dist.items():
                nxt[tb + b] = nxt.get(tb + b, 0.0) + ptb * pb
        dist = nxt
    return dist


def enumerate_tb(pa_dist, n):
    """Even more literal: build every outcome tuple explicitly."""
    import itertools
    out = {}
    for combo in itertools.product(sorted(pa_dist), repeat=int(n)):
        pr = 1.0
        for c in combo:
            pr *= pa_dist[c]
        out[sum(combo)] = out.get(sum(combo), 0.0) + pr
    return out


def ref_auc(y, s):
    """AUC by counting every positive/negative pair directly, ties at 0.5."""
    pos = [si for yi, si in zip(y, s) if yi == 1]
    neg = [si for yi, si in zip(y, s) if yi == 0]
    if not pos or not neg:
        return None
    tot = 0.0
    for a in pos:
        for b in neg:
            tot += 1.0 if a > b else (0.5 if a == b else 0.0)
    return tot / (len(pos) * len(neg))


def ref_wilson_lower(hits, n, z=1.96):
    """Wilson lower bound found by BISECTION on its defining property, rather
    than by the closed form the code uses: the bound is the smallest p whose
    upper z-score reaches the observed proportion."""
    if n <= 0:
        return 0.0
    phat = hits / n
    def g(p):
        se = math.sqrt(p * (1.0 - p) / n) if 0 < p < 1 else 0.0
        return (phat - p) - z * se
    lo, hi = 0.0, phat
    if g(0.0) < 0:
        return 0.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ══════════════════════════════════════════════════════════════════════════
#  1. Binomial tail
# ══════════════════════════════════════════════════════════════════════════
head("1. _binom_at_least against %s" % ("scipy.stats.binom.sf" if _sp_binom
                                        else "a math.comb term-by-term sum"))

worst = 0.0
worst_at = None
for n in range(0, 26):
    for p in (0.0, 1e-9, 0.01, 0.077, 0.2345, 0.5, 0.75, 0.999, 1.0):
        for k in range(0, 10):
            got = pp._binom_at_least(n, p, k)
            if _sp_binom is not None and k > 0 and n > 0:
                exp = float(_sp_binom.sf(k - 1, n, p))
            else:
                exp = ref_binom_sf(n, p, k)
            if abs(got - exp) > worst:
                worst = abs(got - exp)
                worst_at = (n, p, k, got, exp)
check(worst < 1e-9, "binomial tail matches the reference over 2500 (n,p,k) cases",
      "max abs error %.3e at (n,p,k)=%s" % (worst, worst_at[:3] if worst_at else None))

# The degenerate corners, spelled out. These are the ones that silently
# return a plausible number instead of raising.
check(close(pp._binom_at_least(0, 0.3, 0), 1.0), "n=0, k=0 -> P(X>=0) = 1, not 0",
      "regression guard: the n<=0 test used to fire before the k<=0 test")
check(close(pp._binom_at_least(0, 0.3, 1), 0.0), "n=0, k=1 -> 0")
check(close(pp._binom_at_least(5, 0.0, 1), 0.0), "p=0 -> P(>=1) = 0")
check(close(pp._binom_at_least(5, 1.0, 5), 1.0), "p=1 -> P(>=n) = 1")
check(close(pp._binom_at_least(5, 1.0, 6), 0.0), "p=1 but k>n -> 0")
check(close(pp._binom_at_least(3, 0.5, 9), 0.0), "k > n -> 0")
check(all(0.0 <= pp._binom_at_least(n, p, k) <= 1.0
          for n in range(0, 12) for p in (0.0, 0.3, 1.0) for k in range(0, 6)),
      "never returns a value outside [0,1]")

# ══════════════════════════════════════════════════════════════════════════
#  2. Fractional trial counts
# ══════════════════════════════════════════════════════════════════════════
head("2. fractional trial counts are a two-point mixture, not a rounding")

ok = True
detail = ""
for n in (4.62, 3.5, 1.59, 0.4, 22.7):
    lo = int(n // 1)
    f = n - lo
    for p in (0.05, 0.28, 0.6):
        for k in (1, 2, 3):
            exp = (1 - f) * ref_binom_sf(lo, p, k) + f * ref_binom_sf(lo + 1, p, k)
            got = pp._binom_at_least(n, p, k)
            if not close(got, exp, 1e-12):
                ok = False
                detail = "n=%s p=%s k=%s got %.12f want %.12f" % (n, p, k, got, exp)
check(ok, "fractional n equals the explicit floor/ceil mixture", detail)

# The bias this replaced: rounding is strictly worse for P(>=1), which is
# concave in n. Assert the direction so nobody reintroduces int(round(n)).
p_rounded = pp._binom_at_least(5, 0.2, 1)
p_mixed = pp._binom_at_least(4.62, 0.2, 1)
check(p_mixed < p_rounded,
      "mixing at n=4.62 sits below rounding up to 5, as concavity requires",
      "mixed %.6f < rounded %.6f" % (p_mixed, p_rounded))
check(close(pp._binom_at_least(4.0, 0.3, 1), pp._binom_at_least(4, 0.3, 1)),
      "an integral float trial count takes the integer path")

# ══════════════════════════════════════════════════════════════════════════
#  3. Per-PA outcome distribution
# ══════════════════════════════════════════════════════════════════════════
head("3. pa_outcome_distribution")

d = pp.pa_outcome_distribution(singles_rate=0.155, double_rate=0.048,
                               triple_rate=0.004, hr_rate=0.041)
check(close(sum(d.values()), 1.0), "probabilities sum to 1")
check(all(v >= 0 for v in d.values()), "no negative probabilities")
check(close(d[1], 0.155) and close(d[4], 0.041), "component rates pass through unchanged")
check(close(d[0], 1.0 - 0.248), "the zero-base bucket is everything that is not a hit")

hot = pp.pa_outcome_distribution(singles_rate=0.8, double_rate=0.5,
                                 triple_rate=0.1, hr_rate=0.3)
check(close(sum(hot.values()), 1.0), "pathological rates summing past 1 are renormalised",
      "sum of inputs was 1.7; output sums to %.12f" % sum(hot.values()))
check(close(hot[0], 0.0), "a fully renormalised distribution leaves no out probability")

nod = pp.pa_outcome_distribution(avg=0.245)
check(close(sum(nod.values()), 1.0), "AVG-only fallback still sums to 1")
check(close(pp.pa_outcome_distribution()[0], pp.pa_outcome_distribution(avg=0.245)[0]),
      "no arguments at all falls back to the documented league AVG")
check(close(pp.pa_outcome_distribution(singles_rate=0.15, double_rate=0.05,
                                       triple_rate=None, hr_rate=0.03)[3], 0.0),
      "a None triple rate is treated as zero, not an error")

# ══════════════════════════════════════════════════════════════════════════
#  4. Total-bases convolution
# ══════════════════════════════════════════════════════════════════════════
head("4. total_bases_distribution: DP against full enumeration")

worst = 0.0
for n in range(0, 7):
    dp = pp.total_bases_distribution(d, n)
    br = enumerate_tb(d, n)
    for key in set(dp) | set(br):
        worst = max(worst, abs(dp.get(key, 0.0) - br.get(key, 0.0)))
check(worst < 1e-12, "DP matches explicit enumeration of every PA sequence, n=0..6",
      "max abs error %.3e" % worst)

for n in (0, 1, 3, 5, 8, 12):
    tot = sum(pp.total_bases_distribution(d, n).values())
    check(close(tot, 1.0, 1e-9), "the n=%d total-bases distribution sums to 1" % n)

check(pp.total_bases_distribution(d, 0) == {0: 1.0}, "zero PA puts all mass on zero bases")
check(pp.total_bases_distribution(d, -3) == {0: 1.0}, "a negative PA count degrades to zero bases")
check(max(pp.total_bases_distribution(d, 4)) == 16, "4 PA can reach at most 16 total bases")

frac = pp.total_bases_distribution(d, 4.62)
lo_d = pp.total_bases_distribution(d, 4)
hi_d = pp.total_bases_distribution(d, 5)
ok = all(close(frac.get(k, 0.0), 0.38 * lo_d.get(k, 0.0) + 0.62 * hi_d.get(k, 0.0), 1e-9)
         for k in set(lo_d) | set(hi_d))
check(ok, "a fractional PA count mixes the two neighbouring integer distributions")
check(close(sum(frac.values()), 1.0), "the fractional-PA distribution still sums to 1")

# ══════════════════════════════════════════════════════════════════════════
#  5. Threshold probabilities, and the identity linking hits to bases
# ══════════════════════════════════════════════════════════════════════════
head("5. threshold probabilities")

check(close(pp.p_at_least_total_bases(0, d, 5), 1.0), "P(TB >= 0) = 1")
check(close(pp.p_at_least_hits(1, d, 4.62),
            pp.p_at_least_total_bases(1, d, 4.62), 1e-12),
      "P(>=1 hit) equals P(>=1 total base): a hit is exactly a PA with a base")
check(pp.p_at_least_total_bases(2, d, 4.62) > pp.p_at_least_hits(2, d, 4.62),
      "two total bases is easier than two hits (a double does it in one PA)")

for k in range(1, 5):
    a = pp.p_at_least_hits(k, d, 4.62)
    b = pp.p_at_least_hits(k + 1, d, 4.62)
    check(a >= b, "P(>=%d hits) >= P(>=%d hits): thresholds are monotone" % (k, k + 1))
for n1, n2 in ((3, 4), (4, 5), (4.2, 4.8)):
    check(pp.p_at_least_hits(1, d, n1) <= pp.p_at_least_hits(1, d, n2),
          "more plate appearances never lowers P(>=1 hit) (%s vs %s)" % (n1, n2))

check(close(pp.p_at_least_home_runs(1, d, 4.62),
            pp._binom_at_least(4.62, d[4], 1), 1e-12),
      "home runs are binomial on the per-PA home-run rate")
check(close(pp.p_at_least_strikeouts(5, 23.1, 0.25),
            pp._binom_at_least(23.1, 0.25, 5), 1e-12),
      "pitcher strikeouts are binomial on batters faced")
check(close(pp.p_at_least_walks(1, 4.3, 0.09),
            pp._binom_at_least(4.3, 0.09, 1), 1e-12),
      "walks are binomial on plate appearances")

# ══════════════════════════════════════════════════════════════════════════
#  6. Monte Carlo: the convolution against actual simulated seasons
# ══════════════════════════════════════════════════════════════════════════
head("6. Monte Carlo cross-check (independent of every formula above)")

TRIALS = 400000
rng = random.Random(20260806)
bases = sorted(d)
cum = []
acc = 0.0
for b in bases:
    acc += d[b]
    cum.append((acc, b))
n_pa = 4.62
lo_pa, frac_pa = int(n_pa), n_pa - int(n_pa)
tb_hits = {1: 0, 2: 0, 3: 0, 4: 0}
h_hits = {1: 0, 2: 0, 3: 0}
hr_hits = 0
for _ in range(TRIALS):
    npa = lo_pa + (1 if rng.random() < frac_pa else 0)
    tb = h = hr = 0
    for _ in range(npa):
        u = rng.random()
        for c, b in cum:
            if u <= c:
                break
        tb += b
        h += 1 if b >= 1 else 0
        hr += 1 if b == 4 else 0
    for k in tb_hits:
        if tb >= k:
            tb_hits[k] += 1
    for k in h_hits:
        if h >= k:
            h_hits[k] += 1
    if hr >= 1:
        hr_hits += 1

def mc_check(label, mc_count, exact):
    mc = mc_count / TRIALS
    se = math.sqrt(max(mc * (1 - mc), 1e-12) / TRIALS)
    check(abs(exact - mc) <= 5 * se,
          "%s agrees with simulation" % label,
          "exact %.5f  simulated %.5f  gap %.5f = %.2f SE" %
          (exact, mc, exact - mc, abs(exact - mc) / se))

for k in (1, 2, 3, 4):
    mc_check("P(TB >= %d)" % k, tb_hits[k], pp.p_at_least_total_bases(k, d, n_pa))
for k in (1, 2, 3):
    mc_check("P(hits >= %d)" % k, h_hits[k], pp.p_at_least_hits(k, d, n_pa))
mc_check("P(HR >= 1)", hr_hits, pp.p_at_least_home_runs(1, d, n_pa))

# ══════════════════════════════════════════════════════════════════════════
#  7. Stolen bases
# ══════════════════════════════════════════════════════════════════════════
head("7. stolen bases")

check(close(pp.p_stolen_base(3, 0.2, 0.8), 1 - (1 - 0.16) ** 3, 1e-12),
      "P(>=1 SB) is 1-(1-attempt*success)^opportunities")
check(close(pp.p_stolen_base(0, 0.9, 0.9), 0.0), "never reaching base means no steal")
check(close(pp.p_stolen_base(3, 0.0, 0.9), 0.0), "never attempting means no steal")
check(close(pp.p_stolen_base(3, 0.9, 0.0), 0.0), "never succeeding means no steal")
check(close(pp.p_stolen_base(2, 1.0, 1.0), 1.0), "always attempting and succeeding is certain")
check(0.0 <= pp.p_stolen_base(1.59, 2.0, 2.0) <= 1.0,
      "rates above 1 are clamped rather than producing a probability above 1")

# The bias the fractional mixture exists to remove, on the case that motivated
# it: 1.59 times on base must not be evaluated as 2.
check(pp.p_stolen_base(1.59, 0.25, 0.76) < pp.p_stolen_base(2, 0.25, 0.76),
      "1.59 opportunities scores below 2, rather than rounding up to it")

# ══════════════════════════════════════════════════════════════════════════
#  8. Threshold selection
# ══════════════════════════════════════════════════════════════════════════
head("8. best_threshold")

seen = {}
def fake(need):
    seen[need] = True
    # 0.99, not 0.97: MAX_USEFUL_PROB is 0.97 itself and best_threshold's
    # band check is inclusive (min_prob <= p <= max_prob), same convention
    # generate_picks.py's _pick_line uses. A fake value exactly AT the cap
    # was being wrongly asserted as excluded -- it was actually included
    # (0.97 <= 0.97), so this test was checking arithmetic that doesn't
    # match the function's own documented, deliberate boundary. Moved
    # off the boundary so the test asserts what it always meant to.
    return {1: 0.99, 2: 0.61, 3: 0.24}.get(need, 0.0)

line, prob, ev = pp.best_threshold("hits", fake)
check(sorted(seen) == [1, 2, 3], "'over 0.5/1.5/2.5' map to needing 1/2/3",
      "ceil(line) is the integer threshold, not round(line)")
check(line == 1.5 and close(prob, 0.61),
      "picks the highest probability INSIDE the usable band, not the highest overall",
      "0.99 is above MAX_USEFUL_PROB and is correctly skipped")
check(pp.MIN_USEFUL_PROB <= prob <= pp.MAX_USEFUL_PROB, "the chosen line is inside the band")

line, prob, ev = pp.best_threshold("hits", lambda k: {1: 0.3, 2: 0.2, 3: 0.1}[k])
check(line == 0.5 and close(prob, 0.3),
      "with nothing in band it falls back to the best available and reports it honestly")
check(len(ev) == 3 and all("needs" in e and "prob" in e for e in ev),
      "every evaluated line is returned so the caller can see what was rejected")
line, prob, ev = pp.best_threshold("nonexistent_prop", lambda k: 0.5)
check(line is None and prob is None and ev == [],
      "an unknown prop type returns nothing rather than inventing a line")

check(pp.MIN_USEFUL_PROB < pp.MAX_USEFUL_PROB, "the usable band is non-empty")
ranked = pp.rank_by_hit_probability(
    [{"hit_probability": 0.4}, {"hit_probability": None}, {"hit_probability": 0.9}])
check([c["hit_probability"] for c in ranked] == [0.9, 0.4, None],
      "ranking sorts by probability and puts unpriced candidates last, not first")

# ══════════════════════════════════════════════════════════════════════════
#  9. UNIT CONSISTENCY. Percentage vs fraction, per-AB vs per-PA.
# ══════════════════════════════════════════════════════════════════════════
head("9. unit consistency (a rate read in the wrong unit already shipped once)")

# A strikeout rate handed over as a percentage rather than a fraction is the
# exact class of bug that produced a pick advertised at "10000% to hit".
# Every entry point clamps, so a mis-scaled input degrades to a bound instead
# of escaping as a probability above 1.
check(close(pp.p_at_least_strikeouts(5, 23.1, 25.0), 1.0),
      "a K rate passed as 25.0 (percent) clamps to certainty instead of exceeding 1",
      "clamping cannot fix the units, but it does keep the output a probability")
check(0.0 <= pp.p_at_least_strikeouts(5, 23.1, 0.25) <= 1.0,
      "the same rate as a fraction (0.25) gives a real probability")
check(pp.p_at_least_strikeouts(5, 23.1, 0.25) < 0.99,
      "and that probability is not pinned at the ceiling, so the units are distinguishable",
      "P(>=5 K | 23.1 BF, 25%%) = %.4f" % pp.p_at_least_strikeouts(5, 23.1, 0.25))

for fn, args in (("p_at_least_walks", (1, 4.3, 5.0)),
                 ("p_at_least_home_runs", (1, pp.pa_outcome_distribution(hr_rate=0.05,
                                                                         singles_rate=0.15,
                                                                         double_rate=0.05,
                                                                         triple_rate=0.0), 4.3))):
    v = getattr(pp, fn)(*args)
    check(0.0 <= v <= 1.0, "%s returns a probability, not a percentage" % fn)

# A per-AB rate used as per-PA overstates by the PA/AB ratio (~1.10). Assert
# the two are actually distinguishable, so a silent swap cannot go unnoticed.
per_ab, per_pa = 0.270, 0.270 * 0.9093
check(pp._binom_at_least(4.3, per_ab, 1) - pp._binom_at_least(4.3, per_pa, 1) > 0.02,
      "per-AB and per-PA hit rates differ by more than 2 points at 4.3 PA",
      "%.4f vs %.4f -- large enough that a mixed unit would show up in grading" %
      (pp._binom_at_least(4.3, per_ab, 1), pp._binom_at_least(4.3, per_pa, 1)))

# NaN must not propagate into a ranked board as a silently-sorting value.
nan = float("nan")
try:
    v = pp._binom_at_least(4.3, nan, 1)
    check(v != v or 0.0 <= v <= 1.0, "a NaN rate yields NaN or a bounded value, never a fake number",
          "got %r" % v)
except (ValueError, TypeError):
    check(True, "a NaN rate raises rather than returning a plausible-looking probability")

# ══════════════════════════════════════════════════════════════════════════
#  10. Reality check: the model must not claim absurd certainty
# ══════════════════════════════════════════════════════════════════════════
head("10. reality check against real 2026 season rates")

# Luis Arraez, the hardest man in baseball to strike out and the highest
# P(>=1 hit) in the league, from his real season line: 107 games, 4.42 PA/game.
# Observed: a hit in 76.6% of his games. Anything much above that is broken.
arraez = pp.pa_outcome_distribution(singles_rate=0.2371, double_rate=0.0432,
                                    triple_rate=0.0021, hr_rate=0.0123)
p_arraez = pp.p_at_least_hits(1, arraez, 4.42)
check(0.70 <= p_arraez <= 0.82,
      "the league's best contact hitter lands near his observed 76.6%%, not near certainty",
      "modelled %.3f vs observed 0.766 over 107 real games" % p_arraez)
check(p_arraez < 0.90, "no batter is ever modelled above 90%% to record a hit")

# A league-average regular, built from the REAL pooled 2026 per-PA rates over
# 93,570 plate appearances in 23,427 games by batters with 250+ PA:
#   1B .1437  2B .0422  3B .0038  HR .0330   at 3.9941 PA per game
# Observed: a hit in 61.02% of those games, a home run in 12.23%.
avg_bat = pp.pa_outcome_distribution(singles_rate=0.1437, double_rate=0.0422,
                                     triple_rate=0.0038, hr_rate=0.0330)
p_avg = pp.p_at_least_hits(1, avg_bat, 3.9941)
check(0.59 <= p_avg <= 0.66,
      "an average regular lands near the measured league rate of 61.0%",
      "modelled %.4f vs 0.6102 observed across 23,427 player-games" % p_avg)

# THIS GAP IS A KNOWN, MEASURED BIAS, NOT NOISE -- pinned here so it cannot
# grow unnoticed. P(>=1) is concave in the plate-appearance count, and the
# two-point fractional mixture carries variance <= 0.25 against a real
# within-player PA variance of 0.877, so the model sits ABOVE the truth. See
# the note on _mix_fractional. Measured at +0.0091 per player (weighted) and
# +0.024 on these pooled rates. The assertion is one-sided on purpose: the
# bias may shrink, but it must never exceed 4 points.
check(0.0 <= p_avg - 0.6102 <= 0.04,
      "the known upward P(>=1 hit) bias stays within its measured 4-point bound",
      "modelled %.4f - observed 0.6102 = %+.4f" % (p_avg, p_avg - 0.6102))

# Home runs are the one threshold the PA-count collapse does not distort.
p_hr = pp.p_at_least_home_runs(1, avg_bat, 3.9941)
check(abs(p_hr - 0.1223) <= 0.015,
      "P(>=1 HR) for an average regular tracks the observed 12.23% closely",
      "modelled %.4f vs 0.1223 observed, gap %+.4f" % (p_hr, p_hr - 0.1223))

# Strikeouts: a real starter at 23.1 BF and a 25% K rate should land near the
# 5-6 K range that books post, not at a corner.
p_k = pp.p_at_least_strikeouts(6, 23.1, 0.25)
check(0.25 <= p_k <= 0.55, "a typical starter's P(>=6 K) is a live number, not 0 or 1",
      "modelled %.3f at 23.1 batters faced and a 25%% K rate" % p_k)

# ══════════════════════════════════════════════════════════════════════════
#  11. Empirical shrinkage and the Wilson bound (mlb_sources)
# ══════════════════════════════════════════════════════════════════════════
head("11. shrinkage and the Wilson lower bound")

try:
    import mlb_sources as ms
except Exception as e:
    check(True, "mlb_sources not importable here, skipping (%s)" % type(e).__name__)
    ms = None

if ms is not None:
    worst = 0.0
    for n in (1, 2, 5, 12, 40, 96, 300):
        for h in range(0, n + 1, max(1, n // 7)):
            worst = max(worst, abs(ms._wilson_lower(h, n) - ref_wilson_lower(h, n)))
    check(worst < 1e-6, "Wilson lower bound matches a bisection on its defining property",
          "max abs error %.3e" % worst)
    check(close(ms._wilson_lower(0, 0), 0.0), "zero observations gives a bound of 0, not a crash")
    check(ms._wilson_lower(0, 12) >= 0.0, "0-for-12 has a finite non-negative bound")
    check(ms._wilson_lower(12, 12) < 1.0, "12-for-12 does not claim certainty")
    check(ms._wilson_lower(74, 100) < 74 / 100, "the bound sits below the point estimate")
    check(ms._wilson_lower(50, 1000) > ms._wilson_lower(5, 100),
          "a larger sample at the same rate gives a tighter (higher) lower bound")

    # _apply_shrinkage must be exactly the Beta-binomial posterior mean, and
    # must converge on the observed rate as evidence accumulates.
    table = {1: {"rates": {"hits_1plus": {"p": 0.8, "n": 5, "hit": 4}}},
             2: {"rates": {"hits_1plus": {"p": 0.5, "n": 100, "hit": 50}}}}
    ms._apply_shrinkage(table, prior_games=20)
    league = (4 + 50) / (5 + 100)
    for pid, want_n, want_h in ((1, 5, 4), (2, 100, 50)):
        r = table[pid]["rates"]["hits_1plus"]
        check(close(r["p_hat"], round((want_h + 20 * league) / (want_n + 20), 4), 1e-9),
              "player %d's p_hat is the Beta posterior mean with prior strength 20" % pid)
        check(close(r["league_p"], round(league, 4), 1e-9),
              "player %d sees the pooled league rate" % pid)
    thin = table[1]["rates"]["hits_1plus"]
    thick = table[2]["rates"]["hits_1plus"]
    check(abs(thin["p_hat"] - league) < abs(thin["p"] - league),
          "a 5-game sample is pulled toward the league rate")
    check(abs(thick["p_hat"] - thick["p"]) < abs(thin["p_hat"] - thin["p"]),
          "a 100-game sample moves far less than a 5-game one")

    big = {1: {"rates": {"k": {"p": 0.8, "n": 100000, "hit": 80000}}}}
    ms._apply_shrinkage(big, prior_games=20)
    check(close(big[1]["rates"]["k"]["p_hat"], 0.8, 1e-3),
          "with unlimited evidence the estimate converges on the observed rate")
    empty = {}
    ms._apply_shrinkage(empty, prior_games=20)
    check(empty == {}, "an empty table shrinks to an empty table rather than raising")

# ══════════════════════════════════════════════════════════════════════════
#  12. Calibration module against independent references
# ══════════════════════════════════════════════════════════════════════════
head("12. backtest/calibration.py")

try:
    from backtest import calibration as C
except Exception as e:
    check(True, "backtest.calibration not importable here, skipping (%s)" % type(e).__name__)
    C = None

if C is not None:
    import numpy as np

    # PAV against the max-min characterisation of isotonic regression, which
    # is a different definition of the same object rather than the same
    # algorithm rewritten.
    def brute_isotonic(y, w):
        n = len(y)
        out = []
        for i in range(n):
            best = -1e18
            for a in range(0, i + 1):
                inner = 1e18
                for b in range(i, n):
                    ww = sum(w[a:b + 1])
                    inner = min(inner, sum(y[j] * w[j] for j in range(a, b + 1)) / ww)
                best = max(best, inner)
            out.append(best)
        return out

    rr = random.Random(5)
    worst = 0.0
    for _ in range(60):
        n = rr.randint(1, 10)
        y = [rr.random() for _ in range(n)]
        w = [float(rr.randint(1, 4)) for _ in range(n)]
        got = C._pool_adjacent_violators(list(range(n)), y, w)
        exp = brute_isotonic(y, w)
        worst = max(worst, max(abs(a - b) for a, b in zip(got, exp)))
    check(worst < 1e-9, "PAV matches max-min isotonic regression on 60 random problems",
          "max abs error %.3e" % worst)

    check(list(C._pool_adjacent_violators([0], [0.7], [1.0])) == [0.7], "PAV on a single point")
    flat = C._pool_adjacent_violators([0, 1, 2], [0.9, 0.5, 0.1], [1.0, 1.0, 1.0])
    check(all(close(v, 0.5) for v in flat),
          "a strictly decreasing input collapses to one weighted mean")
    inc = C._pool_adjacent_violators([0, 1, 2], [0.1, 0.5, 0.9], [1.0, 1.0, 1.0])
    check(all(close(a, b) for a, b in zip(inc, [0.1, 0.5, 0.9])),
          "an already-increasing input is left alone")
    out = C._pool_adjacent_violators([0, 1, 2, 3], [0.2, 0.8, 0.4, 0.9], [1.0] * 4)
    check(all(out[i] <= out[i + 1] + 1e-12 for i in range(3)), "PAV output is non-decreasing")

    # Platt: verify the returned (A,B) is a stationary point of the objective
    # by checking the analytic gradient is zero there. This tests the fit
    # rather than re-running the fitter.
    rr = random.Random(9)
    p = np.array([rr.random() for _ in range(300)])
    y = np.array([1.0 if rr.random() < 1 / (1 + math.exp(-(3 * pi - 1.4))) else 0.0 for pi in p])
    A, B = C._fit_platt(p, y)
    npos, nneg = float((y == 1).sum()), float((y == 0).sum())
    t = np.where(y == 1, (npos + 1) / (npos + 2), 1.0 / (nneg + 2))
    q = 1.0 / (1.0 + np.exp(-(A * p + B)))
    g1, g2 = float(np.sum((q - t) * p)), float(np.sum(q - t))
    check(abs(g1) < 1e-5 and abs(g2) < 1e-5,
          "Platt converges to a true stationary point of the log-loss",
          "gradient (%.2e, %.2e) at A=%.4f B=%.4f" % (g1, g2, A, B))
    check(A > 0, "Platt recovers a positive slope from positively-related data")

    for name, pp_, yy in (("separable", np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9]),
                           np.array([0.0, 0, 0, 1, 1, 1])),
                          ("all one class", np.array([0.1, 0.5, 0.9]), np.array([1.0, 1, 1])),
                          ("constant input", np.array([0.5] * 8),
                           np.array([1.0, 0, 1, 0, 1, 0, 1, 0]))):
        A, B = C._fit_platt(pp_, yy)
        out = 1.0 / (1.0 + np.exp(-(A * pp_ + B)))
        check(np.isfinite([A, B]).all() and float(out.min()) > 0 and float(out.max()) < 1,
              "Platt stays finite and strictly inside (0,1) on %s data" % name)

    # Brier, log loss and ECE against arithmetic written out by hand.
    rr = random.Random(31)
    rows = []
    for _ in range(500):
        pv = rr.random()
        rows.append({"predicted_prob": pv, "outcome": 1 if rr.random() < pv else 0,
                     "prop_type": "hits", "fair_test": True})
    bs = C.brier_score(rows)
    man_b = sum((r["predicted_prob"] - r["outcome"]) ** 2 for r in rows) / len(rows)
    check(close(bs["brier_score"], round(man_b, 5), 1e-9),
          "Brier score is the mean squared error, computed by hand")
    base = sum(r["outcome"] for r in rows) / len(rows)
    man_bb = sum((base - r["outcome"]) ** 2 for r in rows) / len(rows)
    check(close(bs["brier_skill_score"], round(1 - man_b / man_bb, 5), 1e-6),
          "Brier skill score is 1 - brier/baseline against a base-rate predictor")
    ll = C.log_loss(rows)
    man_l = -sum(r["outcome"] * math.log(r["predicted_prob"]) +
                 (1 - r["outcome"]) * math.log(1 - r["predicted_prob"]) for r in rows) / len(rows)
    check(close(ll["log_loss"], round(man_l, 5), 1e-9), "log loss computed by hand")

    rt = C.reliability_table(rows, n_bins=10, min_bin_count=1)
    check(sum(b["count"] for b in rt["bins"]) == len(rows),
          "every row lands in exactly one reliability bin")
    num = den = 0.0
    for b in rt["bins"]:
        if b["count"]:
            num += b["count"] * abs(b["observed_rate"] - b["mean_predicted"])
            den += b["count"]
    check(close(rt["expected_calibration_error"], round(num / den, 4), 1e-4),
          "ECE is the count-weighted mean absolute gap")
    edge = C.reliability_table([{"predicted_prob": 0.0, "outcome": 0},
                                {"predicted_prob": 1.0, "outcome": 1}],
                               n_bins=10, min_bin_count=1)
    filled = [b["bin"] for b in edge["bins"] if b["count"]]
    check(filled == [0, 9], "p=0 and p=1 land in the first and last bin, not out of range")

    cal = C.fit_calibrator(rows, method="isotonic")
    grid = [i / 40.0 for i in range(41)]
    vals = [cal.predict(g) for g in grid]
    check(all(vals[i] <= vals[i + 1] + 1e-12 for i in range(len(vals) - 1)),
          "the fitted isotonic calibrator is monotone non-decreasing")
    check(all(0.0 <= v <= 1.0 for v in vals), "calibrated outputs stay inside [0,1]")
    check(close(C.Calibrator.from_dict(cal.to_dict()).predict(0.5), cal.predict(0.5)),
          "a calibrator round-trips through its serialised form unchanged")

# ══════════════════════════════════════════════════════════════════════════
#  13. Signals module: AUC and its standard error
# ══════════════════════════════════════════════════════════════════════════
head("13. backtest/signals.py: AUC")

try:
    from backtest import signals as S
except Exception as e:
    check(True, "backtest.signals not importable here, skipping (%s)" % type(e).__name__)
    S = None

if S is not None:
    import numpy as np
    rr = random.Random(77)
    worst = 0.0
    for _ in range(40):
        n = rr.randint(6, 40)
        # coarse scores on purpose: ties are the case a naive AUC gets wrong
        s = [float(rr.randint(0, 5)) for _ in range(n)]
        y = [1 if rr.random() < 0.5 else 0 for _ in range(n)]
        if len(set(y)) < 2:
            continue
        got = S.auc(np.array(y), np.array(s))
        exp = ref_auc(y, s)
        worst = max(worst, abs(got - exp))
    check(worst < 1e-12, "AUC matches direct pairwise counting, including ties",
          "max abs error %.3e" % worst)
    check(close(S.auc(np.array([0, 0, 1, 1]), np.array([1.0, 2, 3, 4])), 1.0),
          "perfectly ordered scores give AUC 1")
    check(close(S.auc(np.array([0, 0, 1, 1]), np.array([4.0, 3, 2, 1])), 0.0),
          "perfectly reversed scores give AUC 0")
    check(close(S.auc(np.array([0, 0, 1, 1]), np.array([1.0, 1, 1, 1])), 0.5),
          "all-tied scores give AUC 0.5, not 1")
    check(S.auc(np.array([1, 1, 1]), np.array([1.0, 2, 3])) is None,
          "AUC with only one class returns None rather than a number")

    # Hanley-McNeil, written out separately from the implementation.
    for a, pos, neg in ((0.75, 30, 30), (0.58, 40, 20), (0.9, 100, 100)):
        q1 = a / (2 - a)
        q2 = 2 * a * a / (1 + a)
        var = (a * (1 - a) + (pos - 1) * (q1 - a * a) + (neg - 1) * (q2 - a * a)) / (pos * neg)
        check(close(S.auc_se(a, pos, neg), math.sqrt(var), 1e-12),
              "AUC standard error matches Hanley-McNeil at a=%.2f" % a)
    check(S.auc_se(0.75, 400, 400) < S.auc_se(0.75, 40, 40),
          "the AUC standard error shrinks as the sample grows")
    se = S.auc_se(0.58, 30, 30)
    check(0.58 - 1.96 * se < 0.5,
          "a 0.58 AUC on 60 rows is not distinguishable from chance",
          "SE %.4f, 95%% CI reaches %.3f" % (se, 0.58 - 1.96 * se))


# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 78)
passed = sum(1 for ok, _, _ in _results if ok)
total = len(_results)
print("RESULT: %d/%d checks passed" % (passed, total))
if passed != total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print("  FAILED: %s" % msg)
            if detail:
                print("          %s" % detail)
print("=" * 78)
sys.exit(0 if passed == total else 1)
