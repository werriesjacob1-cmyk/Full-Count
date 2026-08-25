#!/usr/bin/env python3
"""pa_opportunity_model.py -- Priority 2+3 of the opportunity-modeling
phase (2026-08-25): a point-in-time-safe empirical PA-distribution model,
conditioned on batting order, and a challenger prop probability built by
propagating that distribution -- P(prop hits) = sum_k P(PA=k|order) *
P(prop hits|PA=k) -- compared against the CURRENT predicted_prob.

WHY EMPIRICAL, NOT A PARAMETRIC MODEL: Priority 1
(backtest/priority1_opportunity_decomposition_2026-08-25.md) established
that batting order (from signals.lineup_slot, PREGAME-KNOWABLE per
generate_picks.py:1379) is a real, robust, year-stable predictor of both
actual_pa and realized hit rate, at matched nominal probability. Per the
standing instruction ("prefer simple and reproducible first"), the
simplest model that uses this relationship directly is an empirical
conditional distribution table, not a fitted parametric one -- nothing
here is a neural net or a regression with unexamined assumptions, it is
literally "how often did an order-4 hitter get exactly 3 PA, historically."

POINT-IN-TIME SAFETY: every input (`signals.lineup_slot` -> derived batting
order) is a value generate_picks.py's own scoring pass already computed
and that backtest/engine.py's PointInTime/verify_no_lookahead() machinery
already guarantees was knowable before first pitch -- see
backtest/opportunity_decomposition.py's own docstring for the same claim
(this reuses that exact derivation, not a new one). NOTHING here uses
actual_pa, outcome, final score, or any postgame field as a MODEL INPUT --
actual_pa/outcome are only ever the TARGET being predicted or the
ground truth being evaluated against, never a feature.

TRAIN/HOLDOUT DISCIPLINE: the PA distribution table and the P(hit|PA)
table are BOTH fit only on 2024+2025 rows. All evaluation (the model's own
calibration, and the challenger-vs-current comparison) runs against 2026
ONLY -- a year the tables never saw. This is not point-in-time safety in
the backtest-engine sense (that's already guaranteed upstream), it's
ordinary train/test discipline so this script's own conclusions aren't
circular.

SCOPE: batter markets only, and the challenger probability is built and
evaluated for `hits` specifically (the largest, most central hitter
market) -- not yet extended to every hitter market or any equal-volume
ranking replay. That is real, scoped remaining work, not silently skipped.

    /tmp/mlbvenv/bin/python3 backtest/pa_opportunity_model.py \
        backtest/rows_canonical.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from canonical_baseline_report import prob_bucket, load_rows
from opportunity_decomposition import derive_batting_order, pa_bucket_fine, HITTER_MARKETS

DEFAULT_PATH = "backtest/rows_canonical.jsonl"
TRAIN_YEARS = {"2024", "2025"}
HOLDOUT_YEARS = {"2026"}
PA_STATES = ["0", "1", "2", "3", "4", "5", "6+"]
MIN_LINE_PROB = 0.60  # generate_picks.py's own board-eligibility floor


def _year(date):
    return (date or "").split("-")[0] or "unknown"


def dedupe_player_games(rows):
    """actual_pa/order are identical across every prop_type row for the same
    (date, game_pk, player_id) -- verified directly against real canonical
    data (zero mismatches across 115,521 unique player-games checked). The
    PA distribution itself must be fit on one row per player-game, not once
    per market, or markets with more rows per player silently over-weight
    that player's single real PA outcome."""
    seen = {}
    for r in rows:
        key = (r.get("date"), r.get("game_pk"), r.get("player_id"))
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def fit_pa_distribution(player_game_rows):
    """P(PA=k | order), order in 1..9, fit on rows already restricted to the
    training years. Returns {order: {pa_state: probability}}."""
    counts = defaultdict(lambda: defaultdict(int))
    totals = defaultdict(int)
    for r in player_game_rows:
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        pa = r.get("actual_pa")
        if order is None or pa is None:
            continue
        state = pa_bucket_fine(pa)
        counts[order][state] += 1
        totals[order] += 1
    dist = {}
    for order, total in totals.items():
        dist[order] = {state: round(counts[order].get(state, 0) / total, 6) for state in PA_STATES}
        dist[order]["_n"] = total
    return dist


def fit_hit_rate_given_pa(rows, market):
    """P(market prop hits | actual_pa), fit on training-year rows for one
    market. Returns {pa_state: probability}."""
    counts = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in rows:
        if r.get("prop_type") != market:
            continue
        pa = r.get("actual_pa")
        if pa is None:
            continue
        c = counts[pa_bucket_fine(pa)]
        c["n"] += 1
        c["hits"] += r["outcome"]
    return {state: (round(c["hits"] / c["n"], 6) if c["n"] else None)
            for state, c in counts.items()}


def challenger_probability(order, pa_dist, hit_rate_given_pa):
    """P(prop hits) = sum_k P(PA=k|order) * P(prop hits|PA=k). Returns None
    if the order isn't in the fitted distribution (unseen in training) or
    every PA state it maps to is unpriced."""
    dist = pa_dist.get(order)
    if not dist:
        return None
    total_weight = 0.0
    total = 0.0
    for state in PA_STATES:
        p_pa = dist.get(state, 0.0)
        p_hit = hit_rate_given_pa.get(state)
        if p_hit is None or p_pa <= 0:
            continue
        total += p_pa * p_hit
        total_weight += p_pa
    if total_weight <= 0:
        return None
    return round(total / total_weight, 6)


def candidate_key(row):
    """Best-effort composite real-world identity for a comparisons row:
    (date, game_pk, player_id, prop_type, line, side) -- deliberately NOT
    player name alone, and deliberately including prop_type/market since
    this key is meant to be self-describing even though every current
    caller only ever populates `comparisons` for one fixed market per
    call. Returns None if the row doesn't carry every REQUIRED field (e.g.
    a hand-built test fixture) -- rows without a key are simply excluded
    from the duplicate check below.

    ALTERNATE-LINE SAFETY (audited 2026-08-25, explicit directive: "the
    candidate universe can contain alternate lines... candidate identity
    MUST distinguish" them): `line` is REQUIRED (every real canonical
    backtest row carries one) and included in the key specifically so two
    genuinely different lines for the same player/game/market (Over 0.5
    Total Bases vs Over 1.5 Total Bases) are never collapsed. Verified
    empirically against 143,237 real rows from the in-progress canonical
    rebuild: `line` is currently always 1:1 with (date, game_pk,
    player_id, prop_type) in this dataset -- the backtest engine only
    ever grades ONE line per player-game-market today, so this field is
    presently redundant with the other four for every real caller. It is
    included anyway as forward-looking correctness insurance: if a future
    alternate-line-aware backtest ever produces multiple lines per
    player-game-market (a known, separately-documented gap -- see
    backtest/candidate_dataset_feasibility_2026-08-25.md), this key
    continues to distinguish them correctly instead of silently colliding.

    `side` is included in the tuple (via `.get`, NOT required for a key
    to be built) for the same forward-looking reason, but is NOT required
    -- verified empirically that no real canonical backtest row carries a
    `side` field at all (`backtest/engine.py` never writes one; this
    product's backtest only ever grades Over bets today). Requiring it
    would silently disable this entire duplicate check for every real
    row. `.get("side")` returns None uniformly today, which changes
    nothing about current key values, but the field is ready the moment
    a real `side` value is ever written.

    Unlike shadow_policy_framework.compare_policies()'s snapshot guard
    (which fails closed by default on missing metadata, since a real
    run_policies() selection should always carry a snapshot_id), a
    missing key here is not itself suspicious: this function's own
    id()-based identity is already structurally correct with or without a
    key (see equal_volume_ranking_comparison()'s own docstring), so the
    key is pure additional duplicate-detection insurance, not the primary
    correctness mechanism -- there's no equivalent "this should always be
    populated" expectation to enforce."""
    if not all(k in row for k in ("date", "game_pk", "player_id", "prop_type", "line")):
        return None
    return (row.get("date"), row.get("game_pk"), row.get("player_id"), row.get("prop_type"),
            row.get("line"), row.get("side"))


def equal_volume_ranking_comparison(comparisons, min_line_prob=MIN_LINE_PROB):
    """Priority 4's actual promotion-relevant test: hold total selected
    VOLUME constant at what the CURRENT policy already selects
    (current_prob >= min_line_prob, generate_picks.py's own MIN_LINE_PROB
    floor), then ask what the CHALLENGER ranking would have selected at the
    SAME volume (its own top-N by challenger_prob). Reports overlap,
    added/removed picks, and each population's realized hit rate -- a
    challenger that only cuts volume or only wins by shrinking the pool
    does not show up as a win here, since N is fixed to match CURRENT.

    CANDIDATE IDENTITY, audited 2026-08-25 in response to an explicit
    "do not assume id() is the permanent solution" directive: this
    function takes exactly ONE `comparisons` list and derives
    current_selected/ranked_by_challenger/challenger_selected purely by
    filtering/sorting/slicing that SAME list -- there is no code path,
    for any caller, that could hand this function a "challenger" list
    built from separately-constructed objects representing the same
    candidates as "current". That makes `id()`-based identity a
    structural guarantee here (verified against every real caller:
    pa_opportunity_model.build_report, disagreement_challenger_model.
    build_report, residual_challenger_model.build_report,
    disagreement_experiment_runner.primary_challenger_result -- each
    builds ONE fresh comparisons list, appending exactly one dict per
    candidate, never merging two independently-built lists), not an
    accident of current call sites that a future caller could break.

    What `id()` alone does NOT catch: the same real-world candidate
    entering `comparisons` TWICE as two separate dict objects (e.g. a
    canonical-data duplication bug upstream). The duplicate-candidate_key
    check immediately below guards specifically against that, using a
    real composite identity when callers populate it."""
    keys_seen = {}
    for c in comparisons:
        key = c.get("_candidate_key")
        if key is None:
            continue
        if key in keys_seen:
            raise ValueError(
                f"equal_volume_ranking_comparison: duplicate candidate_key {key!r} "
                "(date, game_pk, player_id, prop_type) in comparisons -- two distinct "
                "dict objects represent the same real-world candidate. This would "
                "corrupt overlap/added/removed counting; fix the upstream row "
                "construction rather than proceeding."
            )
        keys_seen[key] = True

    current_selected = [c for c in comparisons if (c["current_prob"] or 0) >= min_line_prob]
    n = len(current_selected)
    if n == 0:
        return {"n_current_selected": 0}

    ranked_by_challenger = sorted(comparisons, key=lambda c: c["challenger_prob"], reverse=True)
    challenger_selected = ranked_by_challenger[:n]

    # Real bug found and fixed 2026-08-25 during a methodological review of
    # the (not-yet-run) decisive disagreement test: identity here used to be
    # a VALUE tuple (order, current_prob, challenger_prob, outcome). Ties on
    # that tuple are common by construction in the disagreement work
    # specifically -- challenger_prob is a shared empirical rate across an
    # entire (probability_bucket, conflict_tier) cell, so many genuinely
    # distinct candidates can carry the identical tuple. When a tie straddles
    # the top-N cutoff, the OLD code treated "does this exact tuple exist
    # ANYWHERE in challenger_selected" as true for every tied candidate, not
    # just the ones actually cut in -- silently undercounting `removed` and
    # overcounting `overlap`. `id(c)` (Python object identity) is exact and
    # free: every element of current_selected/challenger_selected/ranked_by_
    # challenger is the SAME dict object as in `comparisons` (sorting/slicing
    # never copies), so this has zero risk of a false match and zero cost.
    # See this function's own docstring above for the full audit of WHY
    # id() is structurally safe here, not merely observed-safe.
    current_ids = {id(c) for c in current_selected}
    challenger_ids = {id(c) for c in challenger_selected}

    overlap = [c for c in current_selected if id(c) in challenger_ids]
    removed = [c for c in current_selected if id(c) not in challenger_ids]  # in current, not challenger
    added = [c for c in challenger_selected if id(c) not in current_ids]    # in challenger, not current

    def _hit_rate(items):
        return _rate(sum(c["outcome"] for c in items), len(items))

    return {
        "n_current_selected": n,
        "n_challenger_selected": len(challenger_selected),
        "current_hit_rate": _hit_rate(current_selected),
        "challenger_hit_rate": _hit_rate(challenger_selected),
        "n_overlap": len(overlap),
        "overlap_hit_rate": _hit_rate(overlap),
        "n_removed_by_challenger": len(removed),
        "removed_hit_rate": _hit_rate(removed),
        "n_added_by_challenger": len(added),
        "added_hit_rate": _hit_rate(added),
    }


def _rate(hits, n):
    return round(hits / n, 4) if n else None


def build_report(rows, market="hits"):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter_rows = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]

    train_rows = [r for r in hitter_rows if _year(r.get("date")) in TRAIN_YEARS]
    holdout_rows = [r for r in hitter_rows if _year(r.get("date")) in HOLDOUT_YEARS]

    train_player_games = dedupe_player_games(train_rows)
    pa_dist = fit_pa_distribution(train_player_games)
    hit_rate_given_pa = fit_hit_rate_given_pa(train_rows, market)

    # ---- Model's own calibration: does the FITTED pa_dist (2024-2025)
    # predict the HOLDOUT year's (2026) actual PA distribution per order? ----
    holdout_player_games = dedupe_player_games(holdout_rows)
    holdout_pa_dist = fit_pa_distribution(holdout_player_games)
    pa_dist_calibration = {}
    for order in sorted(set(pa_dist) | set(holdout_pa_dist)):
        fitted = pa_dist.get(order, {})
        actual = holdout_pa_dist.get(order, {})
        fitted_mean = sum(_pa_state_to_number(s) * fitted.get(s, 0) for s in PA_STATES)
        actual_mean = sum(_pa_state_to_number(s) * actual.get(s, 0) for s in PA_STATES)
        pa_dist_calibration[order] = {
            "train_n": fitted.get("_n"), "holdout_n": actual.get("_n"),
            "fitted_mean_pa": round(fitted_mean, 3) if fitted else None,
            "holdout_actual_mean_pa": round(actual_mean, 3) if actual else None,
        }

    # ---- Challenger vs current, evaluated on HOLDOUT market rows only ----
    market_holdout_rows = [r for r in holdout_rows if r.get("prop_type") == market]
    comparisons = []
    for r in market_holdout_rows:
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        if order is None:
            continue
        challenger = challenger_probability(order, pa_dist, hit_rate_given_pa)
        if challenger is None:
            continue
        comparisons.append({
            "current_prob": r.get("predicted_prob"),
            "challenger_prob": challenger,
            "_candidate_key": candidate_key(r),
            "order": order,
            "outcome": r["outcome"],
        })

    # ---- Core test: within matched CURRENT probability buckets, does the
    # challenger probability's own ranking separate realized hit rate? ----
    by_current_bucket = defaultdict(list)
    for c in comparisons:
        b = prob_bucket(c["current_prob"])
        if b is not None:
            by_current_bucket[b].append(c)

    discrimination_within_bucket = {}
    for bucket, items in sorted(by_current_bucket.items()):
        if len(items) < 40:
            continue
        items_sorted = sorted(items, key=lambda c: c["challenger_prob"])
        half = len(items_sorted) // 2
        low_half, high_half = items_sorted[:half], items_sorted[half:]
        low_rate = round(sum(c["outcome"] for c in low_half) / len(low_half), 4) if low_half else None
        high_rate = round(sum(c["outcome"] for c in high_half) / len(high_half), 4) if high_half else None
        discrimination_within_bucket[bucket] = {
            "n": len(items),
            "challenger_low_half_hit_rate": low_rate,
            "challenger_high_half_hit_rate": high_rate,
            "delta": round(high_rate - low_rate, 4) if (low_rate is not None and high_rate is not None) else None,
        }

    return {
        "market": market,
        "train_years": sorted(TRAIN_YEARS),
        "holdout_years": sorted(HOLDOUT_YEARS),
        "n_train_player_games": len(train_player_games),
        "n_holdout_player_games": len(holdout_player_games),
        "pa_distribution_fitted_on_train": pa_dist,
        "pa_distribution_calibration_train_vs_holdout": pa_dist_calibration,
        "hit_rate_given_pa_fitted_on_train": hit_rate_given_pa,
        "n_holdout_market_rows_with_challenger_prob": len(comparisons),
        "discrimination_within_current_probability_bucket": discrimination_within_bucket,
        "equal_volume_ranking_comparison_holdout": equal_volume_ranking_comparison(comparisons),
    }


def _pa_state_to_number(state):
    return 6 if state == "6+" else int(state)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    market = sys.argv[2] if len(sys.argv) > 2 else "hits"
    rows, n_malformed = load_rows(path)
    if not rows:
        print(f"No rows read from {path} -- nothing to report.", file=sys.stderr)
        return 1
    report = build_report(rows, market=market)
    report["_source_file"] = path
    report["_n_malformed_lines_skipped"] = n_malformed
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
