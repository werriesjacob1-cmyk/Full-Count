#!/usr/bin/env python3
"""
correlation.py — classifies whether two picks belong in the same parlay.

WHY THIS EXISTS.

Every prop in this pipeline is scored independently: score_batter never looks
at what score_pitcher said about the guy on the mound, and nothing anywhere
computes a JOINT probability for two legs together. That is the right design
for a single-leg board -- ranking by chance of cashing does not need to know
about any other pick. It is the wrong design the moment two picks get
multiplied together into a parlay, because independent-probability
multiplication is only correct when the legs actually ARE independent, and
game props usually are not.

This module does not compute a parlay's true joint probability -- that needs
real correlation coefficients estimated from outcomes, which needs the same
kind of backtest validation everything else here gets before being trusted.
What it does is classify the RELATIONSHIP between two legs from the game
structure itself: same team, same game, pitcher-versus-the-team-he-is-
facing. That structural classification is exactly what this project's own
hard rules already encode by hand ("no negative correlation in SGPs", "two
hitters same team vs bad pitcher OK", "K prop + opposing hitter = never") --
this makes that reasoning a checkable function instead of something applied
inconsistently by memory.

FOUR LABELS, DELIBERATELY NOT A NUMBER.

A correlation COEFFICIENT implies a level of precision nothing here has
earned yet. The labels are the honest version of what is actually known:

  - "redundant": same player, overlapping outcome (e.g. his own 1+ Hits and
    2+ Total Bases). Not two bets -- stacking them adds stake without adding
    real diversification, since one mostly implies the other.
  - "positive": legs whose outcomes tend to move together (same team, same
    game; a pitcher and his own team's total).
  - "negative": legs that trade off against each other (a pitcher's
    strikeout/scoreless prop against a hitter on the team he is facing).
  - "independent": no structural relationship identified. Different games
    are the clean case; this is also the default when nothing else matches,
    which is a statement of "not established" rather than "proven zero".

WHAT THIS IS NOT. It does not (yet) rank how strong a positive or negative
relationship is, does not handle three-way interactions, and "independent"
here means "no rule fired", not "verified uncorrelated". Treat it as a
structural filter for parlay legality (this project's own rules: no negative
correlation, no redundant stacking, same-game cap), not as an input to a
probability calculation.
"""
from collections import namedtuple

Verdict = namedtuple("Verdict", ["label", "reason"])

# Stat families whose outcomes overlap enough with each other, for the SAME
# player, to count as redundant rather than merely correlated. Verified
# against pp.pa_outcome_distribution's own event model: a single at-bat can
# only be one outcome, so "1+ hits" and "1+ total bases" are answered by the
# same at-bat, and "1+ HR" already implies both.
_OVERLAPPING_BATTER_FAMILIES = {
    "hits", "total_bases", "home_runs", "singles", "doubles", "triples",
    "hits_runs_rbis",
    # Found out of sync during a bug sweep: hits_runs_rbis (h+r+rbi, literally
    # the sum of these two) was already in this set, but its own component
    # stats were not -- a player's Runs pick and RBIs pick were being scored
    # "positive" (merely correlated) instead of "redundant" (largely the same
    # outcome), the exact distinction this set exists to draw.
    "runs", "rbis",
}

# Pitcher stat families that work AGAINST the hitters he is facing tonight --
# a strikeout is an out for the batter, and NRFI/a scoreless inning for his
# team means the opposing lineup did not score, generated no runs/hits/RBIs
# for its own hitters in that half-inning. Same reasoning covers outs
# recorded: every extra out he gets is an at-bat the opposing lineup did not
# turn into a hit.
_PITCHER_STATS_OPPOSE_HITTERS = {"strikeouts", "first_inning_run", "pitcher_outs"}


def _teams_in_matchup(matchup):
    """'Away Team @ Home Team' -> (away, home). Returns (None, None) if the
    string doesn't have the expected shape rather than guessing."""
    if not matchup or " @ " not in matchup:
        return None, None
    away, home = matchup.split(" @ ", 1)
    return away.strip(), home.strip()


def _opponent_team(pick):
    """The team a pitcher (or batter) is facing tonight, from matchup + his
    own team -- not from `side` alone, since `side` says which dugout he's
    in, not who he's playing."""
    away, home = _teams_in_matchup(pick.get("matchup"))
    team = pick.get("team")
    if not away or not home or not team:
        return None
    if team == away:
        return home
    if team == home:
        return away
    return None


def classify(pick_a, pick_b):
    """Classify the relationship between two picks for parlay purposes.

    Each pick is a candidate dict in the shape generate_picks.py already
    produces (name, team, matchup, game_pk, type, projection.stat, side).
    Returns a Verdict(label, reason).
    """
    stat_a = (pick_a.get("projection") or {}).get("stat")
    stat_b = (pick_b.get("projection") or {}).get("stat")
    same_game = (pick_a.get("game_pk") is not None
                and pick_a.get("game_pk") == pick_b.get("game_pk"))

    if not same_game:
        return Verdict("independent",
                       "different games -- no shared game environment")

    same_player = (pick_a.get("player_id") is not None
                  and pick_a.get("player_id") == pick_b.get("player_id"))
    if same_player:
        if stat_a in _OVERLAPPING_BATTER_FAMILIES and stat_b in _OVERLAPPING_BATTER_FAMILIES:
            return Verdict("redundant",
                           f"same player, overlapping outcomes ({stat_a} and {stat_b} "
                           f"are largely the same at-bats)")
        return Verdict("positive",
                       "same player, same game -- a good night pushes every "
                       "prop on him the same direction")

    same_team = pick_a.get("team") is not None and pick_a.get("team") == pick_b.get("team")

    # Pitcher vs. a hitter on the team he's actually facing.
    a_pitcher_opposing_b = (pick_a.get("type") == "pitcher"
                            and stat_a in _PITCHER_STATS_OPPOSE_HITTERS
                            and pick_b.get("type") == "batter"
                            and _opponent_team(pick_a) == pick_b.get("team"))
    b_pitcher_opposing_a = (pick_b.get("type") == "pitcher"
                            and stat_b in _PITCHER_STATS_OPPOSE_HITTERS
                            and pick_a.get("type") == "batter"
                            and _opponent_team(pick_b) == pick_a.get("team"))
    if a_pitcher_opposing_b or b_pitcher_opposing_a:
        pitcher, hitter = (pick_a, pick_b) if a_pitcher_opposing_b else (pick_b, pick_a)
        return Verdict("negative",
                       f"{pitcher['name']}'s prop works against the lineup "
                       f"{hitter['name']} bats in")

    if same_team:
        return Verdict("positive",
                       "same team, same game -- shared run environment, "
                       "shared opposing pitcher/bullpen")

    # Same game, opposing teams, no pitcher-vs-his-own-hitters relationship
    # identified (e.g. two position players on opposite sides, or a pitcher
    # and a hitter on HIS OWN team facing the other pitcher). Not claiming a
    # relationship that hasn't been checked.
    return Verdict("independent",
                   "same game, opposing sides, no specific structural "
                   "relationship identified")


def screen_parlay(picks):
    """Check a proposed set of legs against this project's own parlay rules
    (see references/hard-rules-log.md): no redundant same-player stacking,
    no negative correlation, no duplicate player across legs beyond the
    redundant case already caught above. Returns (ok, violations) where
    violations is a list of (pick_a, pick_b, Verdict) for every disqualifying
    pair -- every pair is checked, not just the first failure, so a caller
    can show the whole picture rather than one violation at a time."""
    violations = []
    for i in range(len(picks)):
        for j in range(i + 1, len(picks)):
            v = classify(picks[i], picks[j])
            if v.label in ("negative", "redundant"):
                violations.append((picks[i], picks[j], v))
    return not violations, violations
