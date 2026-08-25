#!/usr/bin/env python3
"""residual_opportunity_decomposition.py -- Priority 1+2 of the residual-
opportunity phase (2026-08-25): define a clean residual PA target (order
already explains some opportunity variance; this measures what's LEFT
after removing order's effect), then test which OTHER pregame-knowable
signals predict that residual, controlling for order and for the model's
own nominal probability.

WHY THIS EXISTS: pa_opportunity_model.py's equal-volume test showed an
order-only challenger nets only +0.22pp -- because batting order is
already partially priced into predicted_prob via score_batter's CONTEXT
component (generate_picks.py:1379/1386). The new question is narrower and
harder: after conditioning on order, is there ANY other pregame signal
that predicts a hitter will fall short of their order's typical PA? If
none exists, the opportunity thread should be closed, not forced.

TARGET DEFINITIONS (Priority 1's own ask -- report which is used and why):
  - residual_pa (continuous): actual_pa - E[PA | order], both computed
    directly on the population being described (this is a DESCRIPTIVE
    decomposition, not an out-of-sample model -- Priority 4 is where a
    fitted, holdout-evaluated model belongs).
  - is_shortfall (binary): actual_pa <= E[PA | order] - 1.0 -- the
    interpretable target used for every predictor breakdown below, since
    a categorical "did this order-slot hitter fall meaningfully short"
    is easier to reason about across predictors than a raw residual.

LEAKAGE: actual_pa is ONLY ever the target (fed into residual/shortfall
computation), never a feature. E[PA|order] is computed once from the
dedup'd player-game population and is itself derived only from a value
(order, via lineup_slot) that is pregame-knowable -- see
opportunity_decomposition.py's own docstring for the point-in-time-safety
argument this reuses verbatim. Every CANDIDATE predictor tested below
(days_rest, getaway_day, series_game, consecutive_games) is read directly
from `signals`, i.e. a value generate_picks.py's own scoring pass computed
before first pitch under the same verify_no_lookahead() guarantee the
whole backtest engine enforces -- nothing here invents a new pregame
source.

WHAT EACH CANDIDATE PREDICTOR ACTUALLY MEANS (checked against
generate_picks.py before use, not assumed from the name alone):
  - days_rest: `rs["days_since_last_game"]` -- literal days since this
    player's last game (generate_picks.py:1806).
  - getaway_day: 1 if today is the last game of a home/road stand before
    travel, else 0 (generate_picks.py:1891) -- a real, known scheduling
    reason teams sometimes rest regulars.
  - series_game: which game number (1-4+) within the current series
    (generate_picks.py:1893).
  - consecutive_games: only present when >= 10 (generate_picks.py:1808) --
    a sparse but meaningful everyday-fatigue flag.
  `platoon` and `bullpen_fatigue` were deliberately NOT tested here after
  checking their real meaning: `platoon` is matchup-quality (contact
  quality given batter/pitcher handedness, generate_picks.py:1650), not a
  role-instability signal, and `bullpen_fatigue` describes the OPPOSING
  bullpen's fatigue (generate_picks.py:1661), not this player's own
  playing-time risk -- neither is actually an opportunity-risk candidate,
  despite superficially plausible names.

    /tmp/mlbvenv/bin/python3 backtest/residual_opportunity_decomposition.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, load_rows
from opportunity_decomposition import derive_batting_order, HITTER_MARKETS
from pa_opportunity_model import dedupe_player_games

DEFAULT_PATH = "backtest/rows_canonical.jsonl"
SHORTFALL_MARGIN = 1.0


def order_mean_pa(player_game_rows):
    sums = defaultdict(float)
    counts = defaultdict(int)
    for r in player_game_rows:
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        pa = r.get("actual_pa")
        if order is None or pa is None:
            continue
        sums[order] += pa
        counts[order] += 1
    return {order: sums[order] / counts[order] for order in counts}


def residual_pa(row, order_means):
    order = derive_batting_order((row.get("signals") or {}).get("lineup_slot"))
    pa = row.get("actual_pa")
    if order is None or pa is None or order not in order_means:
        return None
    return pa - order_means[order]


def is_shortfall(row, order_means, margin=SHORTFALL_MARGIN):
    r = residual_pa(row, order_means)
    if r is None:
        return None
    return r <= -margin


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def _getaway_day_group(signals):
    v = signals.get("getaway_day")
    if v is None:
        return None
    # generate_picks.py:1891 stores the SCALED value: -2 when it IS a
    # getaway day, 0 otherwise -- not a 0/1 flag. A >=0.5 check here
    # silently matched nothing (verified live: 0 rows ever grouped as
    # "getaway_day" against a real ~32% getaway-day rate in the raw
    # signal). Fixed to match the real encoding.
    return "getaway_day" if v < 0 else "not_getaway_day"


def _days_rest_group(signals):
    v = signals.get("days_rest")
    if v is None:
        return None
    if v <= 0:
        return "0_days_rest"
    if v == 1:
        return "1_day_rest"
    if v <= 3:
        return "2-3_days_rest"
    return "4plus_days_rest"


def _series_game_group(signals):
    v = signals.get("series_game")
    if v is None:
        return None
    if v <= 1:
        return "series_game_1"
    if v <= 2:
        return "series_game_2"
    return "series_game_3plus"


def _consecutive_games_group(signals):
    # Only fires (per generate_picks.py:1808) when consecutive_games >= 10 --
    # absence is NOT "0 consecutive games", it means the signal never fired.
    v = signals.get("consecutive_games")
    return "10plus_consecutive_games" if v is not None else "no_fatigue_flag"


PREDICTORS = {
    "getaway_day": _getaway_day_group,
    "days_rest": _days_rest_group,
    "series_game": _series_game_group,
    "consecutive_games": _consecutive_games_group,
}


def shortfall_rate_by_predictor_same_order(player_game_rows, order_means, predictor_fn):
    """Controls for order: within EACH order slot separately, breaks down
    shortfall rate by the predictor's group value. This is the cleanest
    control -- comparing two order-1 hitters, not an order-1 hitter to an
    order-9 hitter."""
    by_order_group = defaultdict(lambda: defaultdict(lambda: {"n": 0, "shortfalls": 0}))
    for r in player_game_rows:
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        if order is None:
            continue
        shortfall = is_shortfall(r, order_means)
        if shortfall is None:
            continue
        group = predictor_fn(r.get("signals") or {})
        if group is None:
            continue
        cell = by_order_group[order][group]
        cell["n"] += 1
        cell["shortfalls"] += int(shortfall)
    return {
        order: {g: {"n": c["n"], "shortfall_rate": _rate(c["shortfalls"], c["n"])}
                for g, c in sorted(groups.items())}
        for order, groups in sorted(by_order_group.items())
    }


def shortfall_rate_by_predictor_same_probability_bucket(hitter_rows, order_means, predictor_fn):
    """Controls for the model's own nominal probability: within each 0.05
    predicted_prob bucket, breaks down shortfall rate by predictor group.
    Uses market rows (not deduped player-games) since predicted_prob is
    per-market -- a player-game can be in different buckets for different
    markets, and this asks "does the predictor matter for THIS market row's
    own nominal probability slice.\""""
    by_bucket_group = defaultdict(lambda: defaultdict(lambda: {"n": 0, "shortfalls": 0}))
    for r in hitter_rows:
        bucket = prob_bucket(r.get("predicted_prob"))
        if bucket is None:
            continue
        shortfall = is_shortfall(r, order_means)
        if shortfall is None:
            continue
        group = predictor_fn(r.get("signals") or {})
        if group is None:
            continue
        cell = by_bucket_group[bucket][group]
        cell["n"] += 1
        cell["shortfalls"] += int(shortfall)
    return {
        bucket: {g: {"n": c["n"], "shortfall_rate": _rate(c["shortfalls"], c["n"])}
                 for g, c in sorted(groups.items())}
        for bucket, groups in sorted(by_bucket_group.items())
    }


def year_stability_of_predictor(player_game_rows, order_means, predictor_fn, group_a, group_b):
    """Two-group year-by-year comparison for one predictor's most divergent
    pair of groups, pooled across order (order itself is not the axis of
    interest here -- this checks whether the predictor's effect is a
    one-year fluke)."""
    by_year_group = defaultdict(lambda: defaultdict(lambda: {"n": 0, "shortfalls": 0}))
    for r in player_game_rows:
        shortfall = is_shortfall(r, order_means)
        if shortfall is None:
            continue
        group = predictor_fn(r.get("signals") or {})
        if group not in (group_a, group_b):
            continue
        year = _year(r.get("date"))
        cell = by_year_group[year][group]
        cell["n"] += 1
        cell["shortfalls"] += int(shortfall)
    return {
        year: {g: {"n": c["n"], "shortfall_rate": _rate(c["shortfalls"], c["n"])}
               for g, c in sorted(groups.items())}
        for year, groups in sorted(by_year_group.items())
    }


def build_report(rows):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter_rows = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]
    player_games = dedupe_player_games(hitter_rows)
    means = order_mean_pa(player_games)

    overall_shortfalls = sum(1 for r in player_games if is_shortfall(r, means))
    overall_n = sum(1 for r in player_games if is_shortfall(r, means) is not None)

    report = {
        "target_definition": {
            "residual_pa": "actual_pa - E[actual_pa | batting_order], both computed on this population",
            "is_shortfall": f"residual_pa <= -{SHORTFALL_MARGIN}",
            "order_mean_pa": {o: round(v, 3) for o, v in sorted(means.items())},
        },
        "n_player_games": len(player_games),
        "overall_shortfall_rate": _rate(overall_shortfalls, overall_n),
        "predictors": {},
    }

    for name, fn in PREDICTORS.items():
        same_order = shortfall_rate_by_predictor_same_order(player_games, means, fn)
        same_bucket = shortfall_rate_by_predictor_same_probability_bucket(hitter_rows, means, fn)

        # Find the two groups with the largest pooled n for a year-stability check.
        pooled = defaultdict(lambda: {"n": 0, "shortfalls": 0})
        for r in player_games:
            shortfall = is_shortfall(r, means)
            if shortfall is None:
                continue
            g = fn(r.get("signals") or {})
            if g is None:
                continue
            pooled[g]["n"] += 1
            pooled[g]["shortfalls"] += int(shortfall)
        top_groups = sorted(pooled.items(), key=lambda kv: -kv[1]["n"])[:2]
        year_stability = None
        if len(top_groups) == 2:
            ga, gb = top_groups[0][0], top_groups[1][0]
            year_stability = year_stability_of_predictor(player_games, means, fn, ga, gb)

        report["predictors"][name] = {
            "pooled_by_group": {g: {"n": c["n"], "shortfall_rate": _rate(c["shortfalls"], c["n"])}
                                 for g, c in sorted(pooled.items())},
            "same_order_control": same_order,
            "same_probability_bucket_control": same_bucket,
            "year_stability_top_two_groups": year_stability,
        }

    return report


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows)
    report["_source_file"] = path
    report["_n_malformed_lines_skipped"] = n_malformed
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
