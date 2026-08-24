#!/usr/bin/env python3
"""
recommendation.py — the layer between "the model computed a number" and
"the website tells a bettor what to do with it."

WHY THIS EXISTS.

A 2026-08-15 audit of this pipeline found that "confidence" (High/Medium/Low)
was computed in every score_* function purely from `score >= 70` and a
sample-size gate -- NEVER from `hit_probability`. Nothing anywhere enforced a
real probability floor before a pick could be labelled a recommendation. The
result, verified against the live board the day of the audit: a Triple prop
at 2.2% probability (+8000) and another at 1.2% (+10000) were sitting in the
"Locks" tab labelled High Confidence, and 7 of that day's 10 "Top Picks" were
below the board's own advertised 60% floor with no visual flag anywhere.

This module is the fix, and it is deliberately a SEPARATE layer from
`generate_picks.py`'s scoring functions, not a patch inside them. Scoring
answers "how good is this player's matchup, form, and skill read." That is a
real and useful question, and score/confidence/reliability keep meaning
exactly what they already meant. This module answers a DIFFERENT question:
"does this specific bet, at this specific price, on this specific line,
right now, deserve to be called a recommendation" -- and that answer must be
allowed to come out empty. A candidate can score 95/100 (an excellent read on
the player) and still not be a Top Pick, if what's actually being bet is a
17% chance of a triple. Those are not in conflict; they are two different,
both-true facts about the same pick.

FOUR RECOMMENDATION STATES, not the old binary "is it in the board's top
category or not":

  TOP PICK  — Full Count is willing to be judged on this bet. Every one of a
              short, real list of hard requirements must hold: probability
              floor, evidence quality, a confirmed (not projected) lineup, a
              real price that clears the model's own uncertainty, and fresh
              data. No fallback exists that pads this list back up when too
              few candidates qualify. Zero qualifying candidates means zero
              Top Picks, and that is the CORRECT output on a night with no
              real bets available, not a bug to work around.
  LEAN      — the model has a real, computed opinion (a genuine positive lift
              over the market's own base rate), but the bet doesn't clear
              every Top Pick requirement -- thin evidence, an unconfirmed
              lineup, stale data, or a probability below the floor with no
              standout price. Still worth surfacing, never worth calling a
              Lock.
  VALUE     — a probability below the Top Pick floor that still clears a real
              ROI+robustness test against the market price (the same
              "22% at +500 can be a good bet and still probably lose" case
              the audit asked this module to make legible, not conflate with
              High Confidence).
  NEUTRAL   — no real, defensible read either direction. Saying nothing is a
              real, honest answer this module is allowed to give, on
              purpose -- it must not manufacture an opinion on every market
              just to keep every tab full.

WHAT THIS DOES NOT DO. It does not replace `confidence`/`reliability`/
`score` anywhere in generate_picks.py -- those remain real, precisely-defined
scoring diagnostics. It does not touch how a candidate's probability is
computed. It only decides, after everything else is final, whether what got
computed earns one of the four labels above -- and it is the ONLY place in
this codebase that is allowed to say "Top Pick."
"""
import os
import subprocess
from datetime import datetime, timezone

import prop_probability as pp

# ══════════════════════════════════════════════════════════════════════════
#  VERSIONING — every prediction must be traceable to the exact system that
#  produced it. Bumped by hand whenever the corresponding layer changes in a
#  way that would make old and new predictions non-comparable; NOT bumped on
#  every commit (that would make the version number noise, not signal).
# ══════════════════════════════════════════════════════════════════════════

# The scoring formulas themselves (score_batter/score_pitcher/etc. in
# generate_picks.py) -- bump when a weight, threshold, or formula changes.
MODEL_VERSION = "2026.08.15"

# This module: what counts as Top Pick/Lean/Value/Neutral, and the hard
# floors below. Bump whenever TOP_PICK_MIN_PROB or the classification logic
# itself changes -- this is the version a "why did last month's Top Pick
# hit-rate differ from this month's" question should look at first.
SELECTION_POLICY_VERSION = "1.0.0"

# attach_hit_probabilities' shrinkage/blending constants (EMPIRICAL_WEIGHT,
# MODEL_SHRINK_K, STRIKEOUT_SHRINK_K) -- bump when the calibration math
# itself is refit or replaced.
CALIBRATION_VERSION = "1.0.0"

# The set of signals actually feeding score_batter/score_pitcher's weighted
# formula -- bump when a signal is added, removed, or re-weighted.
FEATURE_VERSION = "1.0.0"


def git_sha(short=True):
    """The exact commit that produced this prediction, when available.
    Returns None (not a fabricated placeholder) outside a git checkout or
    when git itself is unavailable -- an absent SHA is honest; a fake one
    would defeat the entire point of this field."""
    try:
        args = ["git", "rev-parse", "--short", "HEAD"] if short else ["git", "rev-parse", "HEAD"]
        out = subprocess.run(args, capture_output=True, text=True, timeout=5,
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else None
    except Exception:
        return None


def build_metadata(*, odds_fetched_at=None, board_generated_at=None):
    """The version/provenance block every board should carry once, at the
    top level -- not per-row (the same run produces every row under the same
    versions, so per-row repetition would be the same field written
    thousands of times for no benefit)."""
    return {
        "model_version": MODEL_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "feature_version": FEATURE_VERSION,
        "git_sha": git_sha(),
        "prediction_timestamp": datetime.now(timezone.utc).isoformat(),
        "odds_fetched_at": odds_fetched_at,
        "board_generated_at": board_generated_at,
    }


# ══════════════════════════════════════════════════════════════════════════
#  THE HARD FLOOR
# ══════════════════════════════════════════════════════════════════════════
#
# Deliberately reuses generate_picks.MIN_LINE_PROB's value (0.60) rather than
# inventing a fresh number. That threshold already carries its own real
# justification and history in this codebase (the -350-equivalent price band
# documented in prop_probability.py, and the "must actually be likely"
# design intent stated on MIN_LINE_PROB itself) -- picking a different number
# here with no data behind it would be arbitrary in exactly the way this
# rebuild exists to eliminate. If TOP_PICK_MIN_PROB and MIN_LINE_PROB are
# ever meant to diverge, that should be a measured decision, not a silent
# copy-drift -- hence the explicit constant and the comment, not a bare
# import of the other module's number.
TOP_PICK_MIN_PROB = 0.60

# A Top Pick's evidence must rest on a real sample. Reuses attach_reliability's
# own grade scale (already validated, already the thing MIN_RELIABILITY_TO_LEAD
# in generate_picks.py gates on) rather than a new one.
TOP_PICK_MIN_RELIABILITY = ("A", "B")

# The bet must still be positive-EV at the PESSIMISTIC end of its own real,
# correctly-scoped interval (see prop_probability.value_verdict) -- reuses
# the value screen's own already-validated ROI floor.
TOP_PICK_MIN_ROI = pp.MIN_ROI

# A real, if lower, bar for a Lean: some genuine positive read, not zero.
# Reuses generate_picks.MIN_POSITIVE_LIFT (already the threshold that
# separates "a real read" from "arithmetically positive but meaningless" for
# the rest of this codebase).
LEAN_MIN_LIFT = 0.02

# How stale the board's own price/generation data is allowed to be before a
# Top Pick fails closed. Prices refresh every 5 minutes on the live
# dashboard (dashboard-prices.yml) and the full board rebuilds at least
# every 2 hours -- 45 minutes is generous slack above the price-refresh
# cadence (catches that job silently failing) while still being tight
# relative to a full rebuild, so a genuinely stale board can't keep minting
# Top Picks on old prices for hours.
MAX_PRICE_AGE_SECONDS = 45 * 60

# The board itself (lineups, game state, everything not covered by the
# 5-minute price refresh) going this stale fails Top Pick status closed --
# roughly double dashboard-refresh.yml's own 2-hour cadence, so one missed
# scheduled run doesn't immediately blank the board, but two does.
MAX_BOARD_AGE_SECONDS = 4 * 60 * 60


def _parse_iso(ts):
    """Real bug, caught by test_refresh_prices.py before shipping: a naive
    timestamp (no offset) parses to a naive datetime, and subtracting that
    from `now` (always UTC-aware here) raises TypeError rather than
    computing a real age -- which would have taken the whole recommendation
    layer down the first time any caller passed a bare ISO string. Assumed
    UTC for a naive input, matching the convention every real timestamp
    in this codebase already uses (datetime.now(timezone.utc).isoformat())."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def freshness_check(*, now=None, odds_fetched_at=None, board_generated_at=None,
                    max_price_age_s=MAX_PRICE_AGE_SECONDS,
                    max_board_age_s=MAX_BOARD_AGE_SECONDS):
    """Is the data behind this board fresh enough to publish an official Top
    Pick right now? Returns (is_fresh: bool, reasons: [str]).

    Deliberately board-level, not per-candidate: every pick on a given board
    build was scored against the same fetch of prices and lineups, so there
    is one real freshness state per run, not one per row. A missing
    timestamp is NOT treated as fresh -- an unknown age is exactly the
    "uncertain" case this function exists to fail closed on, not to assume
    the best of."""
    reasons = []
    ok = True
    board_dt = _parse_iso(board_generated_at)
    if board_dt is None:
        ok = False
        reasons.append("board generation time unknown")
    else:
        age = (now - board_dt).total_seconds() if now else 0
        if age > max_board_age_s:
            ok = False
            reasons.append(f"board is {age/3600:.1f}h old (limit {max_board_age_s/3600:.1f}h)")
    # Deliberately NOT "or board_dt". A board can be freshly generated while
    # a specific price was never actually re-verified this run (or a caller
    # simply forgot to pass its own fetch time) -- borrowing board_dt here
    # would let that unverified price pass as fresh on the board's own
    # unrelated timestamp, exactly the "assume the best of an unknown age"
    # this function's own docstring says it exists not to do.
    price_dt = _parse_iso(odds_fetched_at)
    if price_dt is None:
        ok = False
        reasons.append("price fetch time unknown")
    elif now:
        page = (now - price_dt).total_seconds()
        if page > max_price_age_s:
            ok = False
            reasons.append(f"prices are {page/60:.0f}m old (limit {max_price_age_s/60:.0f}m)")
    return ok, reasons


# ══════════════════════════════════════════════════════════════════════════
#  CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════

def _result(status, reasons, **extra):
    return {"status": status, "status_reasons": reasons, **extra}


def classify_recommendation(candidate, *, now=None, data_fresh=True, fresh_reasons=None):
    """The single function allowed to decide Top Pick / Lean / Value /
    Neutral. Pure -- reads only fields already on the candidate plus the
    board-level freshness verdict computed once by freshness_check() and
    passed in, never re-derived per-row.

    Every branch is reachable and every branch states WHY, so a candidate
    that just misses Top Pick status can always say by how much and on what
    axis, instead of a bare status flip a reader has to reverse-engineer.

    Returns {"status": "top_pick"|"lean"|"value"|"neutral", "status_reasons": [...]}."""
    prob = candidate.get("hit_probability")
    if prob is None:
        return _result("neutral", ["no real probability computed for this line"])

    # 2026-08-24 accuracy investigation, real live case: Jose Urquidy,
    # recalled from the minors 10 days prior, zero real starts on record
    # this stint (sample_n=0) -- graded reliability "D" ("very thin sample
    # -- the number is barely more than a base rate"), yet the board showed
    # an 82.6% strikeout probability and a +13.4pt "lift" as a Lean, built
    # entirely from a league-average workload GUESS ("no real start found
    # for him in the L14 window") stacked on a 35-PA relief sample. D-tier's
    # own stated meaning is "barely more than a base rate" -- sample_n==0 is
    # the literal, most extreme case of that description (not thin, ZERO),
    # and RELIABILITY_TIERS/PITCHER_STARTS_RELIABILITY_TIERS both silently
    # folded it into the same bucket as "a handful of real starts," letting
    # a candidate built on pure fallback assumptions still surface as an
    # actionable Lean with a headline number that looked like a real edge.
    # A genuinely zero-evidence read is a real 'no opinion' -- exactly what
    # NEUTRAL already exists to say -- never a Lean, regardless of how large
    # its raw probability or lift happens to compute.
    if candidate.get("sample_n") == 0:
        return _result("neutral", ["no real MLB track record behind this line at all -- "
                                   "the read leans entirely on league-average/fallback "
                                   "assumptions rather than this player's own data, so it "
                                   "cannot stand as even a Lean yet"])

    reliability = candidate.get("reliability")
    lineup_assumed = bool(candidate.get("lineup_assumed"))
    lift = candidate.get("lift")
    odds = candidate.get("market_odds")
    # ci must already be scoped to this EXACT line (see attach_reliability's
    # per-line fix) -- this function trusts whatever it's handed rather than
    # re-deriving it, which is exactly the trust that was misplaced before
    # this rebuild. It does not know or care where the ci came from; it is
    # the caller's job to never pass one computed for a different stat.
    ci = candidate.get("prob_ci")

    evidence_ok = reliability in TOP_PICK_MIN_RELIABILITY
    lineup_ok = not lineup_assumed
    has_real_lean = lift is not None and lift >= LEAN_MIN_LIFT

    if odds is None:
        # No real price exists to test value or the pessimistic-CI floor
        # against at all -- this can never be a Top Pick or a graded Value
        # bet, only, at best, a directional Lean.
        if has_real_lean and evidence_ok:
            return _result("lean", ["a real read, but no market price is posted yet to "
                                    "grade a Top Pick's price/value requirement against"])
        return _result("neutral", ["no market price posted, and no strong enough read to "
                                   "lean on regardless"])

    # require_robust=True: this policy's Top Pick/Value requirements include
    # the pessimistic-end robustness test (see this module's own docstring
    # on "positive-EV at the PESSIMISTIC end of its own real, correctly-
    # scoped interval"). An honestly-absent interval (modelled_shrunk/
    # league_only lines never get one -- see generate_picks.py's
    # attach_hit_probabilities) is not evidence the bet is robust; it means
    # this exact question cannot be answered for this line, which is a
    # required-test failure, not a skipped one.
    verdict = pp.value_verdict(prob, odds, prob_lo=(ci[0] if ci else None),
                               min_roi=TOP_PICK_MIN_ROI, require_robust=True)
    agreement = pp.market_agreement(prob, odds)
    suspect = agreement["agreement"] == "SUSPECT"
    clears_value = verdict["verdict"] == "BET"

    # SUSPECT is NOT a blanket block on every recommendation state -- verified
    # by direct calculation before shipping this: for a 65% favorite, given
    # this codebase's own ASSUMED_PROP_HOLD (8%) and MIN_ROI (5%) constants,
    # NO price simultaneously clears both "not SUSPECT" (a <= 7 points vs the
    # devigged market) and the ROI floor -- the vig itself accounts for more
    # than the SUSPECT band allows, so requiring both would make Top Pick
    # mathematically impossible for any real favorite. value_board.py's own
    # screen() already established the right precedent: SUSPECT only
    # distinguishes tier A ("market agrees too") from tier B ("model likes it,
    # market doesn't -- size down"), it never blocks a bet outright there.
    # Followed here for Top Pick/Lean. It DOES remain a hard gate for the
    # Value/Longshot bucket specifically, because that is the exact failure
    # mode market_agreement's ratio test was built to catch (the CJ Abrams
    # case: a longshot's rate overstated 2x against a sharper market read) --
    # a low-probability bet whose only case IS "the market disagrees with us"
    # deserves more scrutiny on that disagreement than a favorite does.
    if prob >= TOP_PICK_MIN_PROB and evidence_ok and lineup_ok and clears_value and data_fresh:
        # ci-conditional wording is defense-in-depth, not the primary fix --
        # require_robust=True above already makes clears_value structurally
        # impossible to reach with ci is None (see value_verdict). Kept
        # explicit anyway so this rationale can never claim a test that
        # did not actually run, even if that coupling changes later.
        price_test_clause = (
            "the price/value test at the pessimistic end of its own interval"
            if ci else
            # Structurally unreachable post-fix (require_robust=True forces
            # clears_value False whenever ci is None -- see above), but
            # worded honestly rather than claiming a test that ci's absence
            # means never actually ran, in case that coupling ever changes.
            "the price/value test (no defensible per-line interval exists for "
            "this line, so the pessimistic-end check could not run)"
        )
        reasons = ["clears the real probability floor (>= "
                   f"{TOP_PICK_MIN_PROB*100:.0f}%), a real evidence grade ({reliability}), "
                   f"a confirmed lineup, live pricing, and {price_test_clause}"]
        if suspect:
            reasons.append(f"note: the market itself disagrees with this read "
                           f"({agreement['note']}) — still a Top Pick on the model's own "
                           f"probability and price test, but size with that in mind")
        return _result("top_pick", reasons)

    if not data_fresh:
        return _result("lean" if (has_real_lean or prob >= TOP_PICK_MIN_PROB) else "neutral",
                       ["would otherwise qualify, but the board's own data is stale: "
                        + "; ".join(fresh_reasons or ["freshness unknown"])],
                       stale=True)

    if not lineup_ok:
        status = "value" if (clears_value and prob < TOP_PICK_MIN_PROB and not suspect) else (
            "lean" if (has_real_lean or prob >= TOP_PICK_MIN_PROB) else "neutral")
        return _result(status, ["lineup slot is still a projection (Rotowire/last-known), "
                                "not a confirmed lineup — cannot be an official Top Pick "
                                "until it is"])

    if clears_value and prob < TOP_PICK_MIN_PROB:
        if suspect:
            # This is the one place SUSPECT is a hard gate: a low-probability
            # bet whose apparent value comes entirely from disagreeing sharply
            # with a sharper market is far more likely a model error than a
            # real find (market_agreement's own documented rationale).
            return _result("neutral", [f"positive-EV on the model's own number, but the "
                                       f"model disagrees sharply with the market on this "
                                       f"low-probability read ({agreement['note']}) — treated "
                                       f"as unreliable rather than genuine value"])
        return _result("value", [verdict["why"]])

    if not evidence_ok and (has_real_lean or prob >= TOP_PICK_MIN_PROB):
        return _result("lean", [f"reliability grade {reliability} is too thin a sample to "
                                f"stand behind as a Top Pick yet, even though the read itself "
                                f"is real"])

    if has_real_lean or prob >= TOP_PICK_MIN_PROB:
        return _result("lean", ["a real, positive read that doesn't clear every Top Pick "
                                "requirement"])

    return _result("neutral", ["no meaningful evidence either direction — this is a real "
                               "'no opinion,' not a gap in coverage"])


def attach_recommendations(candidates, *, now=None, odds_fetched_at=None,
                           board_generated_at=None):
    """Batch entry point: computes the board-level freshness verdict ONCE,
    then classifies every candidate against it. Mutates and returns the same
    list, matching apply_signal_weights'/attach_reliability's own in-place
    convention."""
    now = now or datetime.now(timezone.utc)
    fresh, fresh_reasons = freshness_check(now=now, odds_fetched_at=odds_fetched_at,
                                           board_generated_at=board_generated_at)
    for c in candidates:
        c.update(classify_recommendation(c, now=now, data_fresh=fresh,
                                         fresh_reasons=fresh_reasons))
    return candidates
