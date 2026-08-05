#!/usr/bin/env python3
"""
prop_probability.py — converts projections into actual probabilities of a
prop hitting, against the standard lines sportsbooks post.

WHY THIS EXISTS:

The stated goal for this system is picks with the best CHANCE OF HITTING,
not the best theoretical edge. Those are different objectives and they
require different math. Edge needs the book's line and price. Hit
probability needs neither — it needs a distribution over outcomes and a
threshold.

No free source for MLB player prop lines exists (verified: Action Network
exposes only game markets — moneyline/spread/total/team-runs — and returns
404 on every props endpoint tried; The Odds API charges for player props).
But that turns out not to block this goal, because prop lines are heavily
standardized. A book will post "over 0.5 hits" and "over 1.5 hits", not
"over 1.13 hits". So the real probabilities can be computed against the
standard thresholds without knowing the exact posted line.

THE PROBLEM THIS FIXES:

The scorer currently picks a prop TYPE from threshold rules -- e.g. a batter
with season K% <= 18 gets "Over 1.5 Hits". That rule says nothing about how
likely it is to hit. "Over 1.5 Hits" requires TWO hits, which is roughly a
25-30% proposition for most hitters, while "Over 0.5 Hits" requires one and
runs 65-70% for the same player. Optimizing for chance-of-hitting means
choosing the threshold by its computed probability, not by an unrelated rule.

METHOD:

Exact, not simulated. Per-plate-appearance outcome probabilities are derived
from a player's real rate stats, then convolved over the expected number of
PAs via dynamic programming to get the full distribution of total bases and
hits. Strikeout and stolen-base props use binomial models over the relevant
trial count. Exact convolution is both more accurate and cheaper than Monte
Carlo at this size, and it's deterministic -- the same inputs always produce
the same number, which matters for a pipeline whose picks get graded later.

CALIBRATION CAVEAT, STATED UP FRONT: these are model probabilities, not
observed frequencies. They assume PAs are independent and that a player's
rate stats are the true rates. Both are approximations -- they ignore
opposing-pitcher quality within the game, park, and in-game leverage. The
right way to know whether a "68%" really hits 68% of the time is to grade
these against results over weeks, which grade_results.py already collects
the data for. Until that sample exists, treat these as well-founded
estimates and comparative rankings, not validated frequencies.
"""

# The lines sportsbooks actually post for MLB player props. Sourced from
# standard book offerings; these are the thresholds a bettor can realistically
# find, which is what makes computing against them useful without a live feed.
STANDARD_LINES = {
    "hits":         [0.5, 1.5, 2.5],
    "total_bases":  [1.5, 2.5, 3.5],
    "home_runs":    [0.5],
    "strikeouts":   [3.5, 4.5, 5.5, 6.5, 7.5, 8.5],
    "stolen_bases": [0.5],
    "walks":        [0.5],
    "runs":         [0.5, 1.5],
    "rbis":         [0.5, 1.5],
}


def _binom_at_least(n, p, k):
    """P(X >= k) for X ~ Binomial(n, p). Exact, iterative pmf (no scipy)."""
    if n <= 0: return 0.0
    p = min(max(p, 0.0), 1.0)
    if k <= 0: return 1.0
    if k > n: return 0.0
    # pmf(0), then step up multiplicatively; avoids factorial overflow
    pmf = (1.0 - p) ** n
    cdf_below = 0.0
    for i in range(int(k)):
        cdf_below += pmf
        if p >= 1.0:
            pmf = 0.0
        else:
            pmf = pmf * (n - i) / (i + 1) * (p / (1.0 - p))
    return max(0.0, min(1.0, 1.0 - cdf_below))


def pa_outcome_distribution(avg=None, singles_rate=None, double_rate=None,
                            triple_rate=None, hr_rate=None):
    """Per-PA probabilities of {0,1,2,3,4} total bases.

    Falls back to league-typical extra-base composition when only AVG is
    known. Those shares (roughly 63% singles / 22% doubles / 2% triples /
    13% homers of all hits) are approximations of a normal MLB hit mix --
    they exist so a thin-data player still yields a usable distribution
    rather than nothing, and any player with real component rates should
    pass them in instead."""
    if singles_rate is None or double_rate is None or hr_rate is None:
        h = avg if avg is not None else 0.245
        singles_rate = h * 0.63
        double_rate = h * 0.22
        triple_rate = h * 0.02
        hr_rate = h * 0.13
    triple_rate = triple_rate or 0.0
    hit_total = singles_rate + double_rate + triple_rate + hr_rate
    if hit_total > 1.0:  # normalize pathological inputs rather than emit garbage
        scale = 1.0 / hit_total
        singles_rate *= scale; double_rate *= scale
        triple_rate *= scale; hr_rate *= scale
        hit_total = 1.0
    return {0: 1.0 - hit_total, 1: singles_rate, 2: double_rate,
            3: triple_rate, 4: hr_rate}


def total_bases_distribution(pa_dist, n_pa):
    """Exact distribution of total bases over n_pa plate appearances, by
    convolving the per-PA distribution n_pa times (DP, not simulation)."""
    n = int(round(n_pa))
    if n <= 0: return {0: 1.0}
    dist = {0: 1.0}
    for _ in range(n):
        nxt = {}
        for tb, p_tb in dist.items():
            for bases, p_b in pa_dist.items():
                if p_b <= 0: continue
                nxt[tb + bases] = nxt.get(tb + bases, 0.0) + p_tb * p_b
        dist = nxt
    return dist


def p_at_least_total_bases(threshold, pa_dist, n_pa):
    dist = total_bases_distribution(pa_dist, n_pa)
    return sum(p for tb, p in dist.items() if tb >= threshold)


def p_at_least_hits(threshold, pa_dist, n_pa):
    """Hits (not bases): any PA with >=1 base is a hit, so this is binomial
    on the per-PA hit probability."""
    p_hit = sum(p for bases, p in pa_dist.items() if bases >= 1)
    return _binom_at_least(int(round(n_pa)), p_hit, threshold)


def p_at_least_home_runs(threshold, pa_dist, n_pa):
    return _binom_at_least(int(round(n_pa)), pa_dist.get(4, 0.0), threshold)


def p_at_least_strikeouts(threshold, batters_faced, k_rate):
    """Pitcher Ks. k_rate is per batter faced (K% / 100)."""
    return _binom_at_least(int(round(batters_faced)), k_rate, threshold)


def p_at_least_walks(threshold, n_pa, bb_rate):
    return _binom_at_least(int(round(n_pa)), bb_rate, threshold)


def p_stolen_base(times_on_base, attempt_rate, success_rate):
    """P(>=1 SB). Gates on actually reaching base first -- elite speed with a
    low on-base ability is far fewer opportunities than raw speed suggests,
    which a speed-only model systematically overrates."""
    p_per_time_on = max(0.0, min(1.0, attempt_rate * success_rate))
    return _binom_at_least(int(round(times_on_base)), p_per_time_on, 1)


# ══════════════════════════════════════════════════════════════════════════
#  BEST-BET SELECTION — the piece that serves "highest chance of hitting"
# ══════════════════════════════════════════════════════════════════════════

# Below this, a prop isn't a realistic recommendation regardless of how the
# model ranks it. Above the ceiling, the line is so easy the book prices it
# at heavy juice and it stops being worth betting -- so an "always hits"
# result is not automatically the best recommendation.
MIN_USEFUL_PROB = 0.50
MAX_USEFUL_PROB = 0.92


def best_threshold(prop_type, prob_fn, lines=None,
                   min_prob=MIN_USEFUL_PROB, max_prob=MAX_USEFUL_PROB):
    """Pick the standard line with the highest hit probability that's still a
    real bet. Returns (line, probability, all_evaluated) or (None, None, all).

    Prefers the HIGHEST probability inside the usable band rather than the
    highest line -- the objective here is chance of hitting, not payout."""
    lines = lines if lines is not None else STANDARD_LINES.get(prop_type, [])
    evaluated = []
    for ln in lines:
        # "over 1.5" needs 2+, so the integer threshold is ceil(line)
        need = int(ln) + 1 if float(ln).is_integer() else int(ln + 0.5)
        p = prob_fn(need)
        evaluated.append({"line": ln, "needs": need, "prob": round(p, 4)})
    usable = [e for e in evaluated if min_prob <= e["prob"] <= max_prob]
    if usable:
        best = max(usable, key=lambda e: e["prob"])
        return best["line"], best["prob"], evaluated
    # Nothing in-band: fall back to the closest thing to a real bet we have,
    # flagged by its probability so the caller can decline it.
    if evaluated:
        best = max(evaluated, key=lambda e: e["prob"] if e["prob"] <= max_prob else -1)
        return best["line"], best["prob"], evaluated
    return None, None, evaluated


def rank_by_hit_probability(candidates):
    """Sort scored candidates by computed hit probability, descending.

    This is the ordering that matches the stated goal. The existing 0-100
    score is a relative quality ranking across dissimilar prop types; it is
    NOT a probability and shouldn't be read as one. A 78-scored stolen base
    and a 78-scored total-bases pick can have very different odds of
    actually cashing."""
    return sorted(candidates,
                  key=lambda c: (c.get("hit_probability") or 0.0), reverse=True)
