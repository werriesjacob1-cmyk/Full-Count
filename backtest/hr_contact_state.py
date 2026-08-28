#!/usr/bin/env python3
"""hr_contact_state.py -- strictly-pregame bat-tracking state for HR research.

Scaffold only. Builds features; runs no experiment, fits no model, changes
no production behaviour. The experiment it serves is locked in
engineering/PREREG_HR_CONTACT_STATE_V1.md and must not be varied after
results are seen.

WHY THIS EXISTS. HR probability today comes from one number --
hr_rate = homeRuns/PA off the MLB StatsAPI season line
(mlb_sources.py:1069) -- fed through pp.pa_outcome_distribution() and
pp.p_at_least_home_runs(). No Statcast bat-tracking field reaches it.
bat_speed touches only `score` via bat_speed_bonus (generate_picks.py:1475).
The canonical run in flight is the first artifact to retain swing geometry,
so this is the first time the question is answerable.

THE ONE THING THAT MATTERS HERE IS THE DATE COMPARISON. A player's
bat-tracking on date D includes the swing that produced the home run being
predicted. Using `<=` instead of `<` would leak the outcome into its own
feature and manufacture a spectacular fake edge on exactly the rare event
under study. The comparison is `<`, in one place, asserted by test.
"""
from __future__ import annotations

BAT_TRACKING_FIELDS = ("bat_speed", "swing_length", "attack_angle",
                       "swing_path_tilt", "attack_direction")
DISTANCE_FIELD = "hit_distance_sc"

# Locked in the preregistration. Changing either invalidates the lock.
TRAILING_SWINGS = 100
MIN_SWINGS = 30

# arm_angle is deliberately absent: it is the PITCHER's release geometry.
# Including it would turn a batter contact-state test into a matchup test
# and make a null result uninterpretable. Reserved as a separate hypothesis.
EXCLUDED_BY_DESIGN = ("arm_angle",)


class LeakageError(Exception):
    """A feature was about to be built from the game being predicted."""


def _mean(xs):
    xs = [x for x in xs if x is not None and x == x]
    return sum(xs) / len(xs) if xs else None


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None and x == x)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def prior_swings(pitches, batter_id, as_of_date, *, limit=TRAILING_SWINGS):
    """Tracked swings for this batter STRICTLY BEFORE as_of_date, newest first.

    `pitches` is any iterable of dicts carrying game_date, batter and the
    bat-tracking fields -- the canonical Statcast projection's own shape.
    """
    out = []
    for p in pitches:
        if p.get("batter") != batter_id:
            continue
        d = p.get("game_date")
        if not d or d >= as_of_date:          # STRICT: same-day is excluded
            continue
        if p.get("bat_speed") is None:        # bat tracking is swing-only
            continue
        out.append(p)
    out.sort(key=lambda p: p["game_date"], reverse=True)
    return out[:limit]


def bat_speed_state(pitches, batter_id, as_of_date, *, min_swings=MIN_SWINGS):
    """Arm B features. None (never 0, never a league mean) when unsupported."""
    sw = prior_swings(pitches, batter_id, as_of_date)
    if len(sw) < min_swings:
        return {"bat_speed_mean": None, "bat_speed_p90": None,
                "n_swings": len(sw), "supported": False}
    vals = [p.get("bat_speed") for p in sw]
    return {"bat_speed_mean": _mean(vals), "bat_speed_p90": _pct(vals, 0.90),
            "n_swings": len(sw), "supported": True}


def swing_geometry_state(pitches, batter_id, as_of_date, *, min_swings=MIN_SWINGS):
    """Arm C features."""
    sw = prior_swings(pitches, batter_id, as_of_date)
    keys = [f for f in BAT_TRACKING_FIELDS if f != "bat_speed"]
    if len(sw) < min_swings:
        return dict({f"{k}_mean": None for k in keys},
                    n_swings=len(sw), supported=False)
    return dict({f"{k}_mean": _mean([p.get(k) for p in sw]) for k in keys},
                n_swings=len(sw), supported=True)


def distance_state(pitches, batter_id, as_of_date, *, min_swings=MIN_SWINGS):
    """Arm E feature. Only defined over batted balls, so it is sparser than
    the swing-based arms and is reported with its own n."""
    sw = prior_swings(pitches, batter_id, as_of_date)
    d = [p.get(DISTANCE_FIELD) for p in sw if p.get(DISTANCE_FIELD) is not None]
    if len(sw) < min_swings or not d:
        return {"hit_distance_mean": None, "hit_distance_p90": None,
                "n_batted": len(d), "supported": False}
    return {"hit_distance_mean": _mean(d), "hit_distance_p90": _pct(d, 0.90),
            "n_batted": len(d), "supported": True}


def assert_no_same_game_leakage(pitches, batter_id, as_of_date):
    """Belt and braces: prove no returned swing is from as_of_date or later."""
    bad = [p["game_date"] for p in prior_swings(pitches, batter_id, as_of_date)
           if p["game_date"] >= as_of_date]
    if bad:
        raise LeakageError(
            f"{len(bad)} swing(s) dated {sorted(set(bad))[:3]} would enter the "
            f"features for a prediction on {as_of_date}. A swing from the game "
            f"being predicted contains the outcome.")
    return True
