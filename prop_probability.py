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


def _mix_fractional(n, fn):
    """Evaluate fn at a FRACTIONAL trial count by mixing the two neighbouring
    integer counts, weighted by the fractional part.

    A hitter does not get 4.62 plate appearances. He gets 4 or 5, and 4.62 is
    the expected value across the ways the game can unfold -- so the honest
    reading is "62% chance of 5 PA, 38% chance of 4", and the probability is
    the weighted average of those two worlds.

    THIS IS NOT A REFINEMENT, IT WAS A REAL BUG. The previous code rounded the
    trial count with int(round(n)), which is systematically biased upward for
    any prop needing at least one success, because P(>=1) is concave in n:
    rounding 4.62 up to 5 adds more probability than rounding 4.38 down to 4
    removes. Measured on the stolen-base model, where the trial count is small
    enough that a single rounded trial is a large relative change: an expected
    1.59 times on base rounded to 2, turning a true 30.2% into a reported
    36.3%. Six points of overstatement on the pick that was ranked #1 on the
    board, from a rounding call -- and every batter prop carried the same bias,
    since a projected 4.62 PA was silently computed as 5."

    AUDIT, 2026-08-06: THIS FIXED THE BIAS BUT NOT ALL OF IT. Two integer
    points carry at most variance f(1-f) <= 0.25, and the real spread of
    plate appearances per game is far wider than that. Measured over 23,427
    real 2026 player-games (batters with 250+ PA): PA/game has within-player
    variance 0.877, which is 3.5x the most the two-point mixture can ever
    represent. The real distribution is 1 PA 4.3%, 2 PA 4.1%, 3 PA 10.4%,
    4 PA 52.8%, 5 PA 26.4%, 6 PA 2.0%, 7 PA 0.1% -- and the 8.4% of games
    ending at 2 PA or fewer, which are near-automatic losses for "over 0.5
    hits", cannot be represented here at all.

    Since P(>=1) is concave in n, under-dispersing n biases it UP. Measured
    against each player's own real PA distribution: P(>=1 hit) is overstated
    by +0.0091 on average (max +0.0355 for one player), while the higher
    thresholds go slightly the other way (P(>=2 hits) -0.0030, P(>=3 TB)
    -0.0018) because P(>=k) turns convex in n for larger k. Home runs are
    unaffected (+0.0004).

    RECOMMENDED, not applied: replace the two-point mixture with a mixture
    over a PA distribution matching both the mean AND the measured within-
    player variance of 0.877. Left alone here because it shifts every batter
    number on the board and the aggregate effect is under one point."""
    lo = int(n // 1)
    frac = n - lo
    if frac <= 1e-9:
        return fn(lo)
    return (1.0 - frac) * fn(lo) + frac * fn(lo + 1)


def _binom_at_least(n, p, k):
    """P(X >= k) for X ~ Binomial(n, p). Exact, iterative pmf (no scipy).

    Accepts a fractional n and handles it as a mixture (see _mix_fractional)
    rather than rounding it to an integer."""
    # ORDER MATTERS. The k<=0 guard has to come FIRST. Checked against
    # scipy.stats.binom.sf over n in 0..30 x p in {0, 1e-9, .01, .077, .2345,
    # .5, .75, .999, 1} x k in 0..11 (3240 cases): every case agreed to 0.0
    # except exactly one -- n=0, k=0 returned 0.0 where P(X >= 0) = 1.0, the
    # full 1.0 of error, because the n<=0 guard fired before the k<=0 one.
    # Unreachable from today's callers (every threshold is >= 1) but a
    # trap for the next one, e.g. any "did he record at least 0" floor.
    if k <= 0: return 1.0
    if n <= 0: return 0.0
    p = min(max(p, 0.0), 1.0)
    if n != int(n):
        return _mix_fractional(n, lambda m: _binom_at_least(m, p, k))
    n = int(n)
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
    convolving the per-PA distribution n_pa times (DP, not simulation).

    A fractional n_pa is handled as a mixture of the two neighbouring integer
    counts, for the same reason _mix_fractional documents -- rounding it here
    biased every total-bases and hits probability upward."""
    if n_pa != int(n_pa):
        lo = int(n_pa // 1)
        frac = n_pa - lo
        d_lo = total_bases_distribution(pa_dist, lo)
        d_hi = total_bases_distribution(pa_dist, lo + 1)
        out = {}
        for tb, pv in d_lo.items():
            out[tb] = out.get(tb, 0.0) + (1.0 - frac) * pv
        for tb, pv in d_hi.items():
            out[tb] = out.get(tb, 0.0) + frac * pv
        return out
    n = int(n_pa)
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
    return _binom_at_least(n_pa, p_hit, threshold)


def p_at_least_home_runs(threshold, pa_dist, n_pa):
    return _binom_at_least(n_pa, pa_dist.get(4, 0.0), threshold)


def p_at_least_strikeouts(threshold, batters_faced, k_rate):
    """Pitcher Ks. k_rate is per batter faced (K% / 100)."""
    return _binom_at_least(batters_faced, k_rate, threshold)


def p_at_least_walks(threshold, n_pa, bb_rate):
    return _binom_at_least(n_pa, bb_rate, threshold)


def p_stolen_base(times_on_base, attempt_rate, success_rate):
    """P(>=1 SB). Gates on actually reaching base first -- elite speed with a
    low on-base ability is far fewer opportunities than raw speed suggests,
    which a speed-only model systematically overrates.

    AUDIT, 2026-08-06: the OBP GATE IS SELF-CONSISTENT AND COSTS ALMOST
    NOTHING. The obvious objection is that the caller derives times_on_base
    from OBP, and OBP counts home runs and extra-base hits, which are not
    chances to steal second. That is true of the numerator and it is a real
    32% inflation -- measured over 181 base-stealers, (H+BB+HBP) is 1.316x
    (median 1.315, max 1.681) the times they actually reached FIRST.

    But it does not propagate, because batter_pa_composition builds
    attempt_rate over the SAME inflated denominator: attempt_rate =
    (SB+CS)/(H+BB+HBP). The product attempt_rate * times_on_base is therefore
    the right expected number of attempts either way, and the inflation
    cancels exactly at the mean. Only the shape of 1-(1-p)^n is affected --
    more trials at a lower rate. Measured against real 2026 game logs
    (17,624 player-games), replacing both halves with the first-base-only
    definition moves P(>=1 SB) by a mean of just -0.0010, max -0.0132.

    WHAT DOES COST SOMETHING, measured on the same data, is collapsing the
    opportunity count to its mean before feeding it here. Versus mixing over
    each player's REAL per-game distribution of times-reached-first, the
    shipped call is biased high by +0.0062 on average and mean |error| 0.0071
    against observed per-game steal frequencies; the exact mixture cuts those
    to +0.0023 and 0.0053. The worst cases are the ones the board actually
    recommends: David Hamilton +7.2 points (obs 19.2%, model 26.4%), Jazz
    Chisholm +4.7, Oneil Cruz +3.8, Jose Ramirez +3.8. Same root cause as the
    PA-count issue documented on _mix_fractional -- see the note there."""
    p_per_time_on = max(0.0, min(1.0, attempt_rate * success_rate))
    return _binom_at_least(times_on_base, p_per_time_on, 1)


# ══════════════════════════════════════════════════════════════════════════
#  BEST-BET SELECTION — the piece that serves "highest chance of hitting"
# ══════════════════════════════════════════════════════════════════════════

# Below this, a prop isn't a realistic recommendation regardless of how the
# model ranks it. Above the ceiling, the line is so easy the book prices it
# at heavy juice and it stops being worth betting -- so an "always hits"
# result is not automatically the best recommendation.
# ── The price band, expressed as probability ──────────────────────────────
#
# No free source of player-prop PRICES exists (verified: Action Network
# exposes only game markets, The Odds API charges for props). That turns out
# not to block a price constraint, because probability and price are the same
# statement. A book posts a prop near its fair value plus hold, so a model
# probability implies a price whether or not anyone looks it up.
#
#     American odds     implied prob    approx true prob after prop vig
#        +100 (even)        50.0%                 ~52%
#        -150               60.0%                 ~57%
#        -250               71.4%                 ~68%
#        -350               77.8%                 ~74%
#        -700               87.5%                 ~84%
#
# The ceiling is therefore a PRICE ceiling in disguise. At 0.92 the model was
# recommending props priced around -700 to -900: bets that win nine times in
# ten and still lose money, because -700 needs 87.5% just to break even. The
# ceiling is now the -350 equivalent, which is the worst price worth taking.
#
# The vig adjustment is an assumption and is stated as one. Two-way prop
# markets typically hold 5-8%, so roughly half of that sits on each side, and
# the true probability behind a posted price runs a few points below its
# implied probability. Longshot props hold considerably more -- Bobby Witt Jr.
# to steal a base priced at +320 implies 23.8% against a measured 28.1% from
# his own game log, which is a much wider gap than a favourite carries. Treat
# the estimated prices below as a band, not a quote.
MIN_USEFUL_PROB = 0.50

# NOT A PRICE CAP. This was briefly set to 0.75 (the -350 equivalent) to keep
# the board off unbettable favourites, and that was a design error: it solved
# a price problem by discarding a probability estimate. A 0.90 calibrated
# probability is a fact about the player; whether it is bettable is a fact
# about the book, and the book may well post a derivative line near even
# money. Throwing away the estimate loses information that no price lookup
# can give back.
#
# The replacement is max_acceptable_price() below: keep the full estimate,
# publish the worst price at which the bet still makes sense, and reject at
# the sportsbook against the real number.
#
# A ceiling still exists, but only to exclude the genuinely degenerate -- a
# prop the model puts above 97% is almost always a data error rather than a
# free bet.
MAX_USEFUL_PROB = 0.97


# The user's own hard floor on price: never bet worse than this regardless of
# how strong the read is.
USER_MAX_PRICE = -350


def max_acceptable_price(prob, user_limit=USER_MAX_PRICE, margin=0.0):
    """The worst American price at which this bet is still worth taking.

    Two constraints, and the tighter one wins:

    1. FAIR VALUE. Betting a price worse than the model's own fair odds is
       negative expectation by construction, however likely the bet is. A 68%
       shot is fair at -213; taking it at -300 loses money over time.
    2. THE USER'S LIMIT. An independent preference for near-even prices,
       applied regardless of what the model thinks.

    Returns the American price to compare against the book: if the posted
    price is this number or better, the bet clears; if worse, skip it.

    `margin` demands a cushion below fair value (0.05 requires the book to be
    5 points of probability better than fair) for those who want more than a
    break-even edge."""
    p = float(prob)
    if p <= 0.0 or p >= 1.0:
        return None
    target = min(0.999, p - margin) if margin else p
    if target <= 0.0:
        return None
    fair = american_odds(target, include_vig=False)
    if fair is None:
        return None
    # "Better" means further toward the underdog side, so the tighter of the
    # two limits is simply the larger number on the American scale.
    return max(fair, user_limit)


def price_is_acceptable(posted_odds, prob, user_limit=USER_MAX_PRICE, margin=0.0):
    """Does a real posted price clear the bar for this probability?"""
    limit = max_acceptable_price(prob, user_limit, margin)
    if limit is None or posted_odds is None:
        return None
    return float(posted_odds) >= float(limit)

# Half of a typical 7% two-way prop hold, added to a true probability to
# approximate what the book will post.
ASSUMED_HALF_VIG = 0.035


def implied_probability(american_odds):
    """Book-implied probability (vig included) for an American price."""
    o = float(american_odds)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_odds(prob, include_vig=True):
    """Approximate American price for a true probability.

    Returns the price a book would plausibly POST (vig included) by default,
    or the fair no-vig price with include_vig=False. Approximate by
    construction -- see the note on hold above."""
    p = float(prob)
    if include_vig:
        p = min(0.995, p + ASSUMED_HALF_VIG)
    if p <= 0.0 or p >= 1.0:
        return None
    return round(-100.0 * p / (1.0 - p)) if p >= 0.5 else round(100.0 * (1.0 - p) / p)


def format_odds(prob):
    """Human-readable estimated price, e.g. '-215' or '+140'."""
    o = american_odds(prob)
    if o is None:
        return "n/a"
    return f"{'+' if o > 0 else ''}{int(o)}"


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


# ══════════════════════════════════════════════════════════════════════════
#  VALUE SCREEN — where a good read and a fair price actually meet
# ══════════════════════════════════════════════════════════════════════════
#
# THE INTUITION THIS CORRECTS. It feels as though a near-certain prop earns
# some flexibility on price: if it is going to hit anyway, who cares about
# paying up? The arithmetic says the reverse, and not by a little.
#
# Return on a bet is p*d - 1, where d is the decimal price. At -300 (d=1.333)
# a bet must clear 75% just to break even, so a 78% read returns +4% while a
# 74% read LOSES 1.3%. Four points of estimation error flips the sign. At
# +200 (d=3.0) break-even is 33.3%, and the same four-point error moves a 40%
# read from +20% to +8% -- still comfortably profitable.
#
# Short prices amplify estimation error because the stake is large relative to
# the win. So the edge required to justify a bet GROWS as the price shortens,
# and "it is a lock" is an argument for more discipline on price, not less.
#
#     price    break-even    p needed for +5% ROI    edge required
#     +200        33.3%            35.0%                +1.7 pts
#     -150        60.0%            63.0%                +3.0 pts
#     -300        75.0%            78.8%                +3.8 pts
#     -500        83.3%            87.5%                +4.2 pts
#
# A FIXED EDGE THRESHOLD IN PROBABILITY POINTS IS THEREFORE THE WRONG TOOL.
# "Accept anything with +5 points of edge" is far too loose at -500 and
# needlessly strict at +200. Screening on ROI applies the right standard at
# every price automatically.
#
# THE SECOND TEST, which matters more than the first. This model's own
# uncertainty is not small: a season-long empirical rate carries a 95%
# interval roughly +/-8 points wide. An edge of +5 points against a number
# that uncertain is not an edge, it is noise wearing an edge's clothing. So a
# bet must ALSO still be positive expectation when evaluated at the
# PESSIMISTIC end of its own confidence interval. That is what separates a
# real disagreement with the market from a rounding error.

# Minimum return on stake for a bet to be worth making. 5% is deliberately
# modest: prop markets hold 10-15%, so clearing the hold at all is the hard
# part, and demanding a large ROI on top mostly selects for estimation error.
MIN_ROI = 0.05


def decimal_odds(american):
    a = float(american)
    return 1.0 + (100.0 / -a if a < 0 else a / 100.0)


def expected_roi(prob, american):
    """Return per unit staked. Positive means the bet makes money long-run."""
    return float(prob) * decimal_odds(american) - 1.0


def kelly_fraction(prob, american):
    """Fraction of bankroll Kelly would stake. Returned for sizing context,
    NOT as a recommendation to bet full Kelly -- full Kelly assumes the
    probability is exactly right, which is precisely the assumption this
    model cannot make. Most practitioners use a quarter to a half of it."""
    d = decimal_odds(american)
    if d <= 1.0:
        return 0.0
    f = (float(prob) * d - 1.0) / (d - 1.0)
    return max(0.0, f)


def value_verdict(prob, american, prob_lo=None, min_roi=MIN_ROI):
    """Does a good read and a fair price meet on this prop?

    prob     — the calibrated model probability
    american — the real posted price
    prob_lo  — pessimistic end of the model's interval, if known

    Returns a dict with the verdict and the arithmetic behind it. Both tests
    must pass: the bet must clear min_roi at the model's estimate, AND still
    be positive expectation at the bottom of its own confidence interval."""
    d = decimal_odds(american)
    implied = implied_probability(american)
    roi = expected_roi(prob, american)
    breakeven = 1.0 / d
    out = {
        "american": int(american), "decimal": round(d, 3),
        "implied": round(implied, 4), "model": round(float(prob), 4),
        "edge_pts": round((float(prob) - implied) * 100, 1),
        "roi": round(roi, 4), "breakeven_prob": round(breakeven, 4),
        "kelly": round(kelly_fraction(prob, american), 4),
        "prob_needed_for_min_roi": round((1.0 + min_roi) / d, 4),
    }
    robust = None
    if prob_lo is not None:
        robust = expected_roi(prob_lo, american) > 0
        out["roi_at_worst_case"] = round(expected_roi(prob_lo, american), 4)
        out["robust_to_uncertainty"] = bool(robust)
    if roi < min_roi:
        out["verdict"] = "NO BET"
        out["why"] = (f"{roi*100:+.1f}% return is under the {min_roi*100:.0f}% floor — "
                      f"needs {out['prob_needed_for_min_roi']*100:.1f}% to clear it, "
                      f"model says {float(prob)*100:.1f}%")
    elif robust is False:
        out["verdict"] = "NO BET"
        out["why"] = ("edge disappears at the pessimistic end of the model's own "
                      "confidence interval — the disagreement with the market is "
                      "inside our margin of error")
    else:
        out["verdict"] = "BET"
        out["why"] = (f"{roi*100:+.1f}% expected return; break-even is "
                      f"{breakeven*100:.1f}% and the model says {float(prob)*100:.1f}%")
    return out


# ══════════════════════════════════════════════════════════════════════════
#  DE-VIGGING AND MARKET AGREEMENT
# ══════════════════════════════════════════════════════════════════════════
#
# A posted price is not the book's estimate of the probability. It is that
# estimate plus hold. Comparing a model number directly against implied
# probability therefore compares apples to apples plus a tax, and makes every
# bet look worse than it is -- which is why the whole board appeared to be
# 7-14 points of "negative edge" when much of that was simply the vig.
#
# Removing it needs both sides of the market. Two-way props (over/under, yes/
# no) sum to more than 100% by exactly the hold, and dividing each side by
# that sum recovers the book's actual view. FanDuel's feed carries only the
# YES side for most player props, so the hold is estimated from the
# league-wide relationship instead and stated as an estimate.
#
# WHY THIS MATTERS BEYOND ARITHMETIC. The de-vigged price is the sharpest
# free probability estimate in existence for these events -- it is what a
# firm with far more data and far more at stake believes. Treating it as a
# SECOND OPINION rather than an obstacle turns it into immediate validation:
# where this model and the market agree, both are probably close; where they
# diverge wildly, the base rate strongly favours the market being right and
# the model being broken. That is a confidence signal available tonight,
# without waiting for bets to settle.

# Typical two-way hold on MLB player props, as a fraction. Props are held far
# harder than game lines (which run 4-5%); 8% is a conservative middle for
# the popular batter markets.
ASSUMED_PROP_HOLD = 0.08


def devig(implied, hold=ASSUMED_PROP_HOLD):
    """The book's own probability estimate, with hold removed.

    Proportional method: the posted implied probability is scaled down by the
    overround. Exact when both sides are known; an approximation here, and
    labelled as such, because the feed exposes only one side."""
    return max(0.0, min(1.0, float(implied) / (1.0 + hold)))


def market_agreement(model_prob, american, hold=ASSUMED_PROP_HOLD):
    """Compare the model against the de-vigged market. A confidence signal.

    Returns the two probabilities, the gap, and a verdict on what the gap
    means. Large disagreement is treated as evidence against the MODEL, not
    as an opportunity -- a firm pricing thousands of these with money at risk
    is the stronger prior, and this project has already seen what happens
    when that assumption is skipped (a 2x disagreement on CJ Abrams' home-run
    rate that was almost certainly the model missing tonight's context)."""
    implied = implied_probability(american)
    fair = devig(implied, hold)
    gap = float(model_prob) - fair
    a = abs(gap)
    # ABSOLUTE POINTS ARE NOT ENOUGH ON RARE EVENTS. A model saying 8.9%
    # against a de-vigged 4.3% is only 4.6 points apart and passes any
    # sensible absolute threshold -- while being a 2x disagreement. That
    # loophole put three CJ Abrams longshots at +1300 to +2200 on the board
    # with "edges" of +78% to +104%, which is not what real edge looks like.
    # The ratio catches on rare events what the difference catches on common
    # ones, and a prop must survive both.
    ratio = (float(model_prob) / fair) if fair > 0.005 else float("inf")
    if a <= 0.03 and 0.8 <= ratio <= 1.25:
        verdict, note = "AGREE", "model and market are within 3 points — both are probably close"
    elif a <= 0.07 and 0.65 <= ratio <= 1.5:
        verdict, note = ("LEAN", "a real but modest disagreement — the kind that is "
                                 "occasionally right and worth tracking")
    else:
        why = (f"{ratio:.1f}x the market's estimate" if (ratio > 1.5 or ratio < 0.65)
               else f"{a*100:.1f} points from the market")
        verdict, note = ("SUSPECT", f"{why} — a large disagreement with a sharper "
                                    "estimator is far more often a gap in the model "
                                    "than an edge in the market")
    return {"implied": round(implied, 4), "market_fair": round(fair, 4),
            "model": round(float(model_prob), 4), "gap": round(gap, 4),
            "ratio": round(ratio, 3) if ratio != float("inf") else None,
            "agreement": verdict, "note": note}
