#!/usr/bin/env python3
"""HC-regime x redistribution-baseline join, and a first hierarchical
committee-probability challenger, for the WR/RB role-intelligence substrate.

## What is genuinely new here

PR #143 already built 667 real WR/RB teammate-absence events (323
`WR_ABSENCE`, 344 `RB_ABSENCE`, 2012-2025) and four predeclared redistribution
baselines (`role_intelligence_baselines.py`). PR #142 already built a real,
point-in-time HC-regime registry (`coach_regime_registry.py`, 1999-2026, 255
dated intervals, sourced from nflverse/nfldata `data/games.csv`). Neither
workstream computed the *interaction* between them. This module:

1. Joins each of the 667 events to `coach_regime_registry.lookup_regime`'s
   real HC resolution for that event's own team/season/week -- i.e. that
   specific game's date, never a later one -- and reports the existing
   baseline MAE methodology bucketed by the resulting HC regime, to test
   whether redistribution pattern differs across real HC regimes.
2. Prototypes ONE hierarchical challenger,
   `HIERARCHICAL_COMMITTEE_PROBABILITY_V1`: a dependency-free (no
   numpy/sklearn -- NFL CI installs only `nfl/requirements-nfl.txt`, see that
   file's own docstring) multinomial/conditional-logit "committee" model
   estimating each remaining teammate's probability of absorbing the removed
   player's vacated opportunity share, conditioned on that teammate's own
   prior role (last-5 share, depth rank, recent-games count) and -- where
   real HC coverage exists -- how new the current HC regime is
   (`hc_regime_tenure_bucket`). Evaluated on a predeclared, disclosed
   train/held-out season split (2012-2021 train / 2022-2025 held-out) against
   the same four baselines, using the identical equal-volume MAE methodology
   and the identical `compute_mass_balance_diagnostics` mass-balance check.

## What this module reuses, never rebuilds

- `role_intelligence_features.build_teammate_absence_trigger_events`,
  `build_replacement_candidate_rows`, `build_role_state_rows`,
  `build_player_dimension_history`, `most_recent_prior_share`,
  `compute_dimension_shares`, `compute_mass_balance_diagnostics` -- the exact
  same event/candidate/role-state construction PR #143 already built and
  tested. This module does not re-derive the 667-event population; it
  reuses the function that builds it.
- `role_intelligence_baselines.BASELINE_PREDICTORS`,
  `DIMENSION_RELEVANT_EVENT_TYPES`, `predict_no_adjustment` -- the four
  predeclared baselines and their dimension-relevance rules, used as-is for
  every comparison in this module.
- `coach_regime_registry.lookup_regime`, `RegimeInterval`,
  `iter_team_coach_observations`, `build_hc_intervals_from_observations`,
  `stretch_intervals_to_continuous`, `build_game_date_index`,
  `verify_hc_games_source`, `HC_GAMES_SOURCE`, `ROLE_HC` -- the exact same
  real, point-in-time HC registry engine and source pin PR #142 already
  built and tested. This module does not re-implement interval construction
  or the fail-closed lookup semantics; it only calls them.

## Point-in-time correctness of the join (see `LeakageSafetyTests` in the
companion test file)

Each event is resolved via `lookup_regime(..., season=event["season"],
week=event["week"], game_date_index=...)`. `game_date_index` maps
`(team, season, week) -> that exact game's date`; `lookup_regime` only ever
reads the single entry for the event's own `(team, season, week)` key --
never a neighboring week's date, and never "today". Coverage from any
regime interval whose `start_date` falls AFTER that resolved date can
therefore structurally never influence the result: this is the identical
invariant `coach_regime_registry.py`'s own `LeakageSafetyTests` already
prove at the `lookup_regime` level; this module's own tests re-verify it
specifically through this join's own call path (season/week resolution,
not only a direct `target_date` call), because that resolution step is new
code this workstream adds.

## Disclosed limitations (see the draft PR / Issue #91 status for exact
numbers from the real 2012-2025 run)

- OC/DC/playcaller regimes carry zero real ingested intervals (PR #142's own
  disclosed gap) and are never looked up here -- this module resolves `HC`
  only. Every event's HC lookup can independently already be `UNKNOWN`
  (`NO_COVERAGE`) if the real registry genuinely has no HC interval covering
  that exact date; this module does not treat that as an error.
- `route_share` remains `UNKNOWN_NO_SOURCE_INGESTED` throughout (PR #143's
  own disclosed gap); this module never evaluates it.
- The hierarchical challenger is fit with a single predeclared, NOT
  cross-validated (iterations, learning rate, L2 penalty, regime-tenure
  threshold) configuration, trained once on a fixed train-season set and
  scored once on a fixed held-out season set -- a real but bounded first
  prototype, not a tuned or selected model. It is never promoted to any
  selector or public pick.
- The `NEW_REGIME_FIRST_30_DAYS` regime-tenure bucket is real but rare (a
  brand-new HC regime's first ~30 days of REG-season games), so its own
  trained weights are trained on very few examples in-sample; this is
  reported, not smoothed over, in `n_training_examples_by_bucket`.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Callable

from nfl.research.coach_regime_registry import (
    ROLE_HC,
    HC_GAMES_SOURCE,
    build_game_date_index,
    build_hc_intervals_from_observations,
    iter_team_coach_observations,
    lookup_regime,
    stretch_intervals_to_continuous,
    verify_hc_games_source,
)
from nfl.research.role_intelligence_baselines import (
    BASELINE_PREDICTORS,
    DIMENSION_RELEVANT_EVENT_TYPES,
    predict_no_adjustment,
)
from nfl.research.role_intelligence_features import (
    build_player_dimension_history,
    build_replacement_candidate_rows,
    build_role_state_rows,
    build_teammate_absence_trigger_events,
    compute_dimension_shares,
    compute_mass_balance_diagnostics,
    most_recent_prior_share,
)

# --------------------------------------------------------------------------
# HC-regime join
# --------------------------------------------------------------------------

# A brand-new HC regime's first N days of real, dated REG-season evidence
# (per `RegimeInterval.start_date`) is treated as "NEW"; everything else
# resolved is "ESTABLISHED". A predeclared, round number (roughly a team's
# first 2-3 games of a new hire), not fit to this analysis's own results.
NEW_REGIME_MAX_TENURE_DAYS = 30

UNKNOWN_REGIME_BUCKET = "UNKNOWN_REGIME"
NEW_REGIME_BUCKET = "NEW_REGIME_FIRST_30_DAYS"
ESTABLISHED_REGIME_BUCKET = "ESTABLISHED_REGIME"
REGIME_TENURE_BUCKETS = (NEW_REGIME_BUCKET, ESTABLISHED_REGIME_BUCKET, UNKNOWN_REGIME_BUCKET)

# Below this many real events sharing the exact same (team, HC persons,
# regime start date), the named regime is rolled up into a shared bucket
# rather than reported by name, to avoid presenting single-digit-N noise as
# a per-coach finding. Predeclared, round, not fit to this analysis's own
# results.
MIN_EVENTS_FOR_NAMED_REGIME = 20

# Predeclared, disclosed train/held-out season split for the challenger.
# Not tuned to any result computed in this module -- chosen before training
# as a simple two-thirds/one-third-of-seasons split of the substrate's own
# 2012-2025 window.
CHALLENGER_TRAIN_SEASONS = frozenset(range(2012, 2022))
CHALLENGER_HELD_OUT_SEASONS = frozenset(range(2022, 2026))

CHALLENGER_NAME = "HIERARCHICAL_COMMITTEE_PROBABILITY_V1"


def build_hc_registry(games_csv_bytes: bytes) -> tuple[list, dict]:
    """Real HC intervals + game-date index from the exact pinned games.csv bytes.

    Fails closed (raises) if `games_csv_bytes` drifts from
    `coach_regime_registry.HC_GAMES_SOURCE`'s recorded digest -- the same
    fail-closed check that module's own CLI performs, reused here rather
    than re-implemented.
    """
    import csv

    verify_hc_games_source(games_csv_bytes)
    rows = list(csv.DictReader(games_csv_bytes.decode("utf-8").splitlines()))
    observations = iter_team_coach_observations(rows)
    intervals = stretch_intervals_to_continuous(build_hc_intervals_from_observations(observations))
    game_date_index = build_game_date_index(rows)
    return intervals, game_date_index


def resolve_event_hc_regime(event: dict[str, Any], intervals, game_date_index) -> dict[str, Any]:
    """Point-in-time HC resolution for one event, via that event's own game date.

    Uses `lookup_regime(..., season=event["season"], week=event["week"],
    game_date_index=...)` -- never a directly-supplied `target_date` the
    caller could get wrong -- so the resolved date is structurally always
    this exact event's own game date (see module docstring's leakage-safety
    section).
    """
    result = lookup_regime(
        intervals, team=event["team"], role=ROLE_HC,
        season=event["season"], week=event["week"], game_date_index=game_date_index,
    )
    regime_key = None
    tenure_days = None
    bucket = UNKNOWN_REGIME_BUCKET
    if result.status == "RESOLVED":
        regime_key = f"{event['team']}:{'/'.join(result.persons)}:{result.interval.start_date.isoformat()}"
        tenure_days = (result.target_date - result.interval.start_date).days
        bucket = NEW_REGIME_BUCKET if tenure_days <= NEW_REGIME_MAX_TENURE_DAYS else ESTABLISHED_REGIME_BUCKET
    return {
        "hc_status": result.status,
        "hc_persons": result.persons,
        "hc_confidence": result.confidence,
        "hc_reason": result.reason,
        "hc_regime_key": regime_key,
        "hc_regime_tenure_days": tenure_days,
        "hc_regime_tenure_bucket": bucket,
    }


def attach_hc_regime_to_events(events: list[dict[str, Any]], intervals, game_date_index) -> list[dict[str, Any]]:
    """Return NEW event dicts (originals untouched) carrying the HC join fields."""
    return [{**event, **resolve_event_hc_regime(event, intervals, game_date_index)} for event in events]


# --------------------------------------------------------------------------
# Shared evaluation harness (same MAE methodology as
# `role_intelligence_baselines.evaluate_baselines`, generalized to accept an
# arbitrary predictor set and an arbitrary error-bucketing key, so both the
# regime-by-baseline report and the challenger-vs-baselines report can reuse
# one implementation instead of two divergent copies).
# --------------------------------------------------------------------------

def _realized_share(usage_index: dict, season: int, week: int, team: str, player_id: str, dimension: str):
    row = usage_index.get((season, week, team, player_id))
    if row is None:
        return None
    value = compute_dimension_shares(row)[dimension]
    return value if isinstance(value, (int, float)) else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def evaluate_predictors(
    events_with_regime: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
    predictors: dict[str, Callable],
    *,
    extra_bucket_key: Callable[[dict], str] | None = None,
    extra_bucket_name: str = "mae_by_extra_bucket",
) -> dict[str, Any]:
    """Score `predictors` against realized shares, same methodology as
    `role_intelligence_baselines.evaluate_baselines`, plus an optional extra
    error-bucketing dimension (used here for HC-regime buckets)."""
    relevant_event_types = DIMENSION_RELEVANT_EVENT_TYPES.get(dimension, frozenset())
    events = [e for e in events_with_regime if e["event_type"] in relevant_event_types]

    history = build_player_dimension_history(role_state_rows)
    usage_index = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in usage_rows}
    candidates_by_event: dict[tuple, list] = defaultdict(list)
    for candidate in candidates:
        key = (candidate["season"], candidate["week"], candidate["team"], candidate["removed_player_id"])
        candidates_by_event[key].append(candidate)

    results: dict[str, Any] = {}
    for name, predictor in predictors.items():
        errors = []
        predicted_by_event: dict[tuple, dict[str, float]] = {}
        for event in events:
            key = (event["season"], event["week"], event["team"], event["removed_player_id"])
            teammates = candidates_by_event.get(key, [])
            if not teammates:
                continue
            predictions = predictor(event, teammates, history, dimension)
            predicted_by_event[key] = predictions
            for player_id, predicted in predictions.items():
                realized = _realized_share(usage_index, event["season"], event["week"], event["team"], player_id, dimension)
                if realized is None:
                    continue
                errors.append({
                    "season": event["season"],
                    "event_type": event["event_type"],
                    "abs_error": abs(predicted - realized),
                    "extra_bucket": extra_bucket_key(event) if extra_bucket_key else None,
                })

        if not errors:
            results[name] = {"n": 0, "mae": None}
            continue

        mae = _mean([e["abs_error"] for e in errors])
        by_season: dict[int, list[float]] = defaultdict(list)
        by_extra: dict[str, list[float]] = defaultdict(list)
        for e in errors:
            by_season[e["season"]].append(e["abs_error"])
            if extra_bucket_key:
                by_extra[e["extra_bucket"]].append(e["abs_error"])

        mass_balance = compute_mass_balance_diagnostics(events, history, predicted_by_event, dimension)
        out = {
            "n": len(errors),
            "mae": mae,
            "mae_by_season": {season: _mean(v) for season, v in sorted(by_season.items())},
            "mass_balance": mass_balance,
        }
        if extra_bucket_key:
            out[extra_bucket_name] = {k: {"n": len(v), "mae": _mean(v)} for k, v in sorted(by_extra.items())}
        results[name] = out
    return results


def _named_regime_bucket_key(regime_counts: Counter) -> Callable[[dict], str]:
    def key(event: dict[str, Any]) -> str:
        regime_key = event["hc_regime_key"]
        if regime_key is None:
            return UNKNOWN_REGIME_BUCKET
        if regime_counts[regime_key] >= MIN_EVENTS_FOR_NAMED_REGIME:
            return regime_key
        return f"OTHER_NAMED_REGIMES_N_LT_{MIN_EVENTS_FOR_NAMED_REGIME}"
    return key


def evaluate_baselines_by_hc_regime(
    events_with_regime: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
) -> dict[str, Any]:
    """The existing four baselines' MAE, additionally bucketed by the joined
    real HC regime -- the genuinely new HC-regime x redistribution-baseline
    interaction this workstream exists to compute. Regimes with fewer than
    `MIN_EVENTS_FOR_NAMED_REGIME` events are rolled into a shared bucket
    rather than named, and events with no HC coverage are their own explicit
    `UNKNOWN_REGIME` bucket -- never silently dropped or merged into a named
    regime.
    """
    relevant_event_types = DIMENSION_RELEVANT_EVENT_TYPES.get(dimension, frozenset())
    regime_counts = Counter(
        e["hc_regime_key"] for e in events_with_regime
        if e["event_type"] in relevant_event_types and e["hc_regime_key"] is not None
    )
    return evaluate_predictors(
        events_with_regime, candidates, role_state_rows, usage_rows, dimension,
        BASELINE_PREDICTORS,
        extra_bucket_key=_named_regime_bucket_key(regime_counts),
        extra_bucket_name="mae_by_hc_regime",
    )


# --------------------------------------------------------------------------
# Hierarchical committee-probability challenger
# --------------------------------------------------------------------------

FEATURE_NAMES = ("bias", "prior_last5", "has_prior", "depth_inv", "has_depth", "games_n_norm")

# Predeclared training hyperparameters. Not cross-validated or tuned to any
# result this module computes -- a bounded first prototype, disclosed as such.
TRAIN_ITERATIONS = 200
TRAIN_LEARNING_RATE = 0.05
TRAIN_L2_PENALTY = 0.01

_DIMENSION_TO_CANDIDATE_FIELD = {
    "target_share": "candidate_prior_target_share_mean_last5",
    "carry_share": "candidate_prior_carry_share_mean_last5",
}


def _candidate_features(candidate: dict[str, Any], dimension: str) -> list[float]:
    field = _DIMENSION_TO_CANDIDATE_FIELD.get(dimension)
    prior = candidate.get(field) if field else None
    has_prior = 1.0 if isinstance(prior, (int, float)) else 0.0
    prior_value = float(prior) if has_prior else 0.0
    depth = candidate.get("candidate_depth_team")
    has_depth = 1.0 if isinstance(depth, int) else 0.0
    depth_inv = (1.0 / depth) if has_depth else 0.0
    games_n = candidate.get("candidate_prior_games_n") or 0
    games_n_norm = min(games_n, 16) / 16.0
    return [1.0, prior_value, has_prior, depth_inv, has_depth, games_n_norm]


def _softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def _score(weights: list[float], features: list[float]) -> float:
    return sum(w * f for w, f in zip(weights, features))


def _training_examples(
    events_with_regime: list[dict[str, Any]],
    candidates_by_event: dict[tuple, list],
    usage_index: dict,
    dimension: str,
    seasons: frozenset[int],
) -> list[tuple[str, list[list[float]], int]]:
    """(regime_tenure_bucket, per-candidate feature rows, index-of-true-absorber).

    The "true absorber" for one event is whichever eligible teammate's
    realized share minus his OWN prior-last5 share increased the most --
    i.e. who actually gained relative to his own baseline. Events with no
    teammate showing a positive gain (nobody plausibly "absorbed" anything
    real, e.g. a blowout where usage just didn't change) are skipped, since
    there is no real positive-absorption label to learn from -- a real,
    disclosed exclusion, not a fabricated label.
    """
    relevant_event_types = DIMENSION_RELEVANT_EVENT_TYPES.get(dimension, frozenset())
    examples: list[tuple[str, list[list[float]], int]] = []
    for event in events_with_regime:
        if event["event_type"] not in relevant_event_types or event["season"] not in seasons:
            continue
        key = (event["season"], event["week"], event["team"], event["removed_player_id"])
        teammates = candidates_by_event.get(key, [])
        if len(teammates) < 2:
            continue
        feats = [_candidate_features(t, dimension) for t in teammates]
        deltas: list[float | None] = []
        for teammate, f in zip(teammates, feats):
            realized = _realized_share(
                usage_index, event["season"], event["week"], event["team"],
                teammate["candidate_player_id"], dimension,
            )
            deltas.append((realized - f[1]) if realized is not None else None)
        known = [(i, d) for i, d in enumerate(deltas) if d is not None]
        if not known:
            continue
        best_i, best_delta = max(known, key=lambda pair: pair[1])
        if best_delta <= 0:
            continue
        examples.append((event["hc_regime_tenure_bucket"], feats, best_i))
    return examples


def train_committee_model(
    events_with_regime: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
    *,
    train_seasons: frozenset[int] = CHALLENGER_TRAIN_SEASONS,
    iterations: int = TRAIN_ITERATIONS,
    lr: float = TRAIN_LEARNING_RATE,
    l2: float = TRAIN_L2_PENALTY,
) -> dict[str, Any]:
    """Fit one conditional-logit weight vector per HC-regime-tenure bucket.

    A dependency-free (pure-Python) multinomial/conditional-logit fit --
    same "no numpy/sklearn in NFL CI" convention `game_market_c2_ridge.py`
    already established for this repo's other from-scratch model code.
    Standard conditional-logit gradient: for each training example, nudge
    the true absorber's features up and every candidate's expected
    (softmax-probability-weighted) features down, with a fixed L2 penalty.
    """
    usage_index = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in usage_rows}
    candidates_by_event: dict[tuple, list] = defaultdict(list)
    for candidate in candidates:
        key = (candidate["season"], candidate["week"], candidate["team"], candidate["removed_player_id"])
        candidates_by_event[key].append(candidate)

    examples = _training_examples(events_with_regime, candidates_by_event, usage_index, dimension, train_seasons)
    n_features = len(FEATURE_NAMES)
    weights: dict[str, list[float]] = {b: [0.0] * n_features for b in REGIME_TENURE_BUCKETS}
    counts = Counter(bucket for bucket, _, _ in examples)

    for _ in range(iterations):
        grads: dict[str, list[float]] = {b: [0.0] * n_features for b in REGIME_TENURE_BUCKETS}
        for bucket, feats, true_i in examples:
            scores = [_score(weights[bucket], f) for f in feats]
            probs = _softmax(scores)
            for j, f in enumerate(feats):
                coeff = (1.0 if j == true_i else 0.0) - probs[j]
                for k in range(n_features):
                    grads[bucket][k] += coeff * f[k]
        for bucket in REGIME_TENURE_BUCKETS:
            n = max(counts.get(bucket, 0), 1)
            for k in range(n_features):
                grad = grads[bucket][k] / n - l2 * weights[bucket][k]
                weights[bucket][k] += lr * grad

    return {
        "model_name": CHALLENGER_NAME,
        "dimension": dimension,
        "feature_names": FEATURE_NAMES,
        "weights": weights,
        "n_training_examples": len(examples),
        "n_training_examples_by_bucket": dict(counts),
        "train_seasons": sorted(train_seasons),
        "iterations": iterations,
        "lr": lr,
        "l2": l2,
    }


def predict_committee_model(
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict,
    dimension: str,
    model: dict[str, Any],
) -> dict[str, float]:
    """Each teammate keeps his own strictly-prior share (`predict_no_adjustment`,
    reused as-is), plus a learned, regime-bucket-conditioned probability share
    of the removed player's own most-recent-prior opportunity budget.

    Reuses `role_intelligence_baselines.predict_no_adjustment` and
    `role_intelligence_features.most_recent_prior_share` exactly as the four
    existing baselines do -- this challenger changes only how the vacated
    budget is split, never how a teammate's own baseline is read.
    """
    predictions = dict(predict_no_adjustment(event, teammates, history, dimension))
    removed_prior = most_recent_prior_share(
        history.get((event["removed_player_id"], dimension), []), event["season"], event["week"],
    )
    if removed_prior is None or not teammates:
        return predictions

    bucket = event.get("hc_regime_tenure_bucket", UNKNOWN_REGIME_BUCKET)
    weights = model["weights"].get(bucket) or model["weights"][ESTABLISHED_REGIME_BUCKET]
    feats = [_candidate_features(t, dimension) for t in teammates]
    scores = [_score(weights, f) for f in feats]
    probs = _softmax(scores)
    for teammate, probability in zip(teammates, probs):
        player_id = teammate["candidate_player_id"]
        predictions[player_id] = predictions.get(player_id, 0.0) + probability * removed_prior
    return predictions


def evaluate_challenger_vs_baselines(
    events_with_regime: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
    model: dict[str, Any],
    *,
    held_out_seasons: frozenset[int] = CHALLENGER_HELD_OUT_SEASONS,
) -> dict[str, Any]:
    """Score the challenger AND the four existing baselines on the SAME
    held-out-season events, so the comparison is apples-to-apples and the
    challenger is never scored on any event it (or its training season set)
    could have seen."""
    held_out_events = [e for e in events_with_regime if e["season"] in held_out_seasons]
    predictors = dict(BASELINE_PREDICTORS)
    predictors[CHALLENGER_NAME] = lambda event, teammates, history, dim: predict_committee_model(
        event, teammates, history, dim, model,
    )
    return evaluate_predictors(held_out_events, candidates, role_state_rows, usage_rows, dimension, predictors)


__all__ = [
    "NEW_REGIME_MAX_TENURE_DAYS",
    "UNKNOWN_REGIME_BUCKET",
    "NEW_REGIME_BUCKET",
    "ESTABLISHED_REGIME_BUCKET",
    "REGIME_TENURE_BUCKETS",
    "MIN_EVENTS_FOR_NAMED_REGIME",
    "CHALLENGER_TRAIN_SEASONS",
    "CHALLENGER_HELD_OUT_SEASONS",
    "CHALLENGER_NAME",
    "FEATURE_NAMES",
    "build_hc_registry",
    "resolve_event_hc_regime",
    "attach_hc_regime_to_events",
    "evaluate_predictors",
    "evaluate_baselines_by_hc_regime",
    "train_committee_model",
    "predict_committee_model",
    "evaluate_challenger_vs_baselines",
]
