#!/usr/bin/env python3
"""Downstream training-sensitivity certification for the 667->668 WR/RB
teammate-absence event correction (PR #150 root-caused a hash-randomized
tie-break bug in `role_intelligence_features._top_usage_player_per_team_week`;
PR #154 fixed it; Issue #91 comment `5744022823` flagged one open sub-claim
of PR #154 as *not personally re-verified*).

## The one open sub-claim this module exists to close

PR #154 reported that the paired baseline/challenger MAE table for
`HIERARCHICAL_COMMITTEE_PROBABILITY_V1` (`role_regime_redistribution.py`,
draft PR #147) on the corrected 668-event population is numerically
identical (same 5 decimal places) to PR #150's original 667-event table,
reasoning that the single flipping event (`WR_ABSENCE`, season 2012, week 2,
team `GB`, `removed_player_id` `00-0024267` -- Greg Jennings, tied with
Randall Cobb `00-0028002` on week-1 `target_share`) falls inside the
2012-2021 TRAINING window, not the reported 2022-2025 HELD-OUT window. That
reasoning is correct about *row membership* in the held-out evaluation set,
but it does not by itself prove the challenger's FITTED PARAMETERS are
unaffected -- a training population change can silently change a fitted
model's held-out predictions even when the held-out rows themselves never
move. This module provides a reusable, tested function that actually fits
the challenger on two different training populations and scores both on the
identical held-out population, so that claim is checked rather than assumed.

## Design: dependency-injected, not a re-implementation

`role_regime_redistribution.py` (PR #147, branch
`claude/nfl-role-redistribution-experiment-20260919`) and
`role_regime_redistribution_audit.py` (PR #150, branch
`claude/nfl-role-redistribution-audit-20260919`, which owns
`compute_paired_evaluation`) are BOTH still draft and unmerged as of this
module's own base commit -- they do not exist on `main`, and this module
must not edit or depend on either draft branch's files. `compute_training_sensitivity`
below therefore takes the challenger's train/predict/paired-evaluation
functions as explicit parameters (`train_committee_model_fn`,
`build_challenger_predictor_fn`, `compute_paired_evaluation_fn`) rather than
importing `role_regime_redistribution`/`role_regime_redistribution_audit`
directly. This is not a re-derivation of PR #147's model or PR #150's
paired-evaluation methodology -- callers pass in those EXACT functions
unmodified (see `load_production_adapters` below, which does the real,
unmodified import once those modules are reachable) -- it only changes how
this module obtains them, so the comparison stays real, tested, and
importable on `main` today. The bundled test file
(`nfl/tests/test_role_regime_redistribution_training_sensitivity_audit.py`)
injects a tiny, hand-computable synthetic model-fitting/predicting/
evaluation stand-in instead, to prove `compute_training_sensitivity` itself
correctly detects a known parameter difference -- it does not and cannot
test PR #147's actual challenger math, which is out of this module's scope
by design (see the workstream's explicit exclusions).

## Real result (this module's own scratch-harness run against real,
digest-verified 2012-2025 nflverse data, `PYTHONHASHSEED=0` for the pre-fix
builder -- confirmed to deterministically reproduce the pre-fix 667-event
population; the fixed builder is hashseed-independent, confirmed separately
across seeds 0/1/42; not committed here -- see
`engineering/ENGINEERING_HANDOFF.md`'s entry for this workstream for the
full run log)

Fetched fresh copies of `stats_player_week_<season>.csv`/`injuries_<season>.csv`
2012-2025 and `depth_charts_<season>.csv` 2012-2024 via this repo's own
already-merged `role_intelligence_data_prep.py` (fail-closed digest
verification built into that module, unmodified) -- 53,110 WR/RB usage rows,
matching PR #143/#147/#150/#154's own reported count exactly. Built the
pre-fix event population from this worktree's own current
`role_intelligence_features.py` (still pre-fix / hash-order-dependent as of
this module's base commit -- confirmed byte-identical to `main`) and the
fixed population from PR #154's branch file (fetched read-only, not copied
into this repository), running both in one process under
`PYTHONHASHSEED=0`.

- Pre-fix population: 667 events (323 `WR_ABSENCE` / 344 `RB_ABSENCE`),
  digest `5df6fab3...4807e4999`. Fixed population: 668 events (324/344),
  digest `a7e322de...c1b0a6f` -- matching PR #154's independently reported
  digest exactly. The two populations differ by EXACTLY one event
  (`WR_ABSENCE`/2012/wk2/`GB`/`00-0024267`), confirmed by an explicit
  symmetric-difference check, not merely by count.
- The 2022-2025 held-out event population is BYTE-IDENTICAL between the two
  runs (verified by an explicit digest comparison), as PR #154 assumed.
- `carry_share` (relevant only to `RB_ABSENCE` events, see
  `role_intelligence_baselines.DIMENSION_RELEVANT_EVENT_TYPES`): the flip
  event is a `WR_ABSENCE` event, so it NEVER enters `carry_share` training at
  all. Training example counts are identical (232 total, 223
  `ESTABLISHED_REGIME` / 9 `NEW_REGIME_FIRST_30_DAYS` in both runs), the
  fitted weight vectors are bit-for-bit identical, and the held-out paired
  MAE table (`paired_n=250`) is bit-for-bit identical for the challenger AND
  all four baselines. PR #154's "identical" claim is exactly correct here,
  not merely close.
- `target_share` (relevant to `WR_ABSENCE`): training example counts differ
  by exactly one, in the `ESTABLISHED_REGIME` bucket only (216 -> 217 total;
  198 -> 199 `ESTABLISHED_REGIME`; 18 `NEW_REGIME_FIRST_30_DAYS` unchanged in
  both). The `ESTABLISHED_REGIME` fitted weight vector genuinely changes --
  e.g. the `has_prior` coefficient moves from -0.082909 (pre-fix) to
  -0.071328 (fixed), a ~14% relative shift, and `depth_inv` from 0.233243 to
  0.227852 (~2.3%) -- while `NEW_REGIME_FIRST_30_DAYS`'s weights are
  bit-for-bit identical (the added example was never in that bucket). On the
  held-out 2022-2025 population (`paired_n=441`, identical row set both
  runs), the four baselines' MAE are bit-for-bit identical (they are not
  fitted), but `HIERARCHICAL_COMMITTEE_PROBABILITY_V1`'s MAE moves from
  0.06241770851649521 (pre-fix) to 0.0624156172516236 (fixed) -- a real,
  non-zero difference of ~2.09e-6 (~0.003% relative). Both values round to
  0.06242 at 5 decimal places, so PR #154's literal "same 5 decimal places"
  claim holds at that precision, but the two runs are NOT bit-identical --
  they diverge starting at the 6th decimal place. An event-clustered
  bootstrap 95% CI (2000 resamples, same methodology as PR #150's
  `bootstrap_mae_ci_by_event`) is approximately [0.0573, 0.0675] for both
  runs -- a ~0.01 half-width dwarfing the ~2e-6 point-estimate shift by
  roughly four orders of magnitude, so the difference is real but far below
  this population's own sampling noise.

**Verdict**: PR #154's headline claim ("numerically identical") is TRUE for
`carry_share` in the strongest, bit-for-bit sense, and is an OVERCLAIM for
`target_share` in the strict sense -- the training correction DOES change
the challenger's fitted parameters and DOES change its held-out predictions
for `target_share`, just by a magnitude that rounds away at the 5-decimal
precision PR #154 reported and that is negligible next to the bootstrap CI.
This is disclosed precisely rather than rounded into either "identical" or
"different" so the record does not silently smooth over a real, if tiny,
discrepancy (Issue #91 comment `5743926733`).

No model/selector/public-pick promotion. This module makes no claim about
whether `HIERARCHICAL_COMMITTEE_PROBABILITY_V1` should ever be promoted --
only about whether the 667/668 correction changes its fitted output, which
it does, marginally, for `target_share` only.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# --------------------------------------------------------------------------
# Inputs / outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingSensitivityInputs:
    """One training-population variant's full pipeline state.

    `events_with_regime` is expected to span BOTH the training and held-out
    seasons (mirroring how `role_regime_redistribution.train_committee_model`
    and `role_regime_redistribution_audit.compute_paired_evaluation` are
    actually called in production -- each filters by season internally
    rather than being handed a pre-sliced population), so that this
    function can also verify the held-out slice is identical between the
    two variants rather than merely assuming it.
    """

    name: str
    events_with_regime: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    role_state_rows: list[dict[str, Any]]
    usage_rows: list[dict[str, Any]]


def _default_event_identity(event: dict[str, Any]) -> tuple:
    return (
        event.get("event_type"),
        event.get("season"),
        event.get("week"),
        event.get("team"),
        event.get("removed_player_id"),
    )


def event_population_digest(
    events: Iterable[dict[str, Any]],
    *,
    event_identity_fn: Callable[[dict[str, Any]], tuple] = _default_event_identity,
) -> str:
    """Stable content digest over an event population's identity keys,
    independent of list order. Same construction PR #150's own
    `event_set_digest` uses (sorted identity-key JSON, SHA-256) -- a small,
    generic utility reimplemented here (not imported) only because PR #150's
    module is itself unmerged and out of scope to depend on; it derives no
    part of the paired-evaluation or model-fitting methodology.
    """
    keys = sorted(event_identity_fn(e) for e in events)
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class TrainingSensitivityResult:
    dimension: str
    variant_a_name: str
    variant_b_name: str

    held_out_event_digest: dict[str, str]
    held_out_populations_identical: bool

    n_training_examples: dict[str, int | None]
    n_training_examples_by_bucket: dict[str, dict[str, int]]

    fitted_weights: dict[str, dict[str, list[float]]]
    weight_delta_by_bucket: dict[str, list[float]]
    max_abs_weight_delta: float

    held_out_paired_n: dict[str, int]
    mae_by_predictor: dict[str, dict[str, float | None]]
    mae_abs_delta_by_predictor: dict[str, float | None]
    challenger_name: str
    mae_abs_delta_challenger: float | None
    max_abs_mae_delta_any_predictor: float
    bit_identical_predictions: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "variant_a_name": self.variant_a_name,
            "variant_b_name": self.variant_b_name,
            "held_out_event_digest": self.held_out_event_digest,
            "held_out_populations_identical": self.held_out_populations_identical,
            "n_training_examples": self.n_training_examples,
            "n_training_examples_by_bucket": self.n_training_examples_by_bucket,
            "fitted_weights": self.fitted_weights,
            "weight_delta_by_bucket": self.weight_delta_by_bucket,
            "max_abs_weight_delta": self.max_abs_weight_delta,
            "held_out_paired_n": self.held_out_paired_n,
            "mae_by_predictor": self.mae_by_predictor,
            "mae_abs_delta_by_predictor": self.mae_abs_delta_by_predictor,
            "challenger_name": self.challenger_name,
            "mae_abs_delta_challenger": self.mae_abs_delta_challenger,
            "max_abs_mae_delta_any_predictor": self.max_abs_mae_delta_any_predictor,
            "bit_identical_predictions": self.bit_identical_predictions,
        }


# --------------------------------------------------------------------------
# The comparison itself
# --------------------------------------------------------------------------


def compute_training_sensitivity(
    dimension: str,
    variant_a: TrainingSensitivityInputs,
    variant_b: TrainingSensitivityInputs,
    *,
    train_committee_model_fn: Callable[..., dict[str, Any]],
    build_challenger_predictor_fn: Callable[[dict[str, Any]], Callable],
    compute_paired_evaluation_fn: Callable[..., dict[str, Any]],
    baseline_predictors: dict[str, Callable],
    challenger_name: str,
    train_seasons: frozenset[int],
    held_out_seasons: frozenset[int],
    event_identity_fn: Callable[[dict[str, Any]], tuple] = _default_event_identity,
) -> TrainingSensitivityResult:
    """Fit `challenger_name` on `variant_a` and `variant_b`'s own training
    populations (`train_committee_model_fn(events_with_regime, candidates,
    usage_rows, dimension, train_seasons=train_seasons)`, the exact call
    shape `role_regime_redistribution.train_committee_model` uses), then
    score BOTH fitted models plus the (unfitted, so trivially identical)
    `baseline_predictors` on the identical held-out population via
    `compute_paired_evaluation_fn(events_with_regime, candidates,
    role_state_rows, usage_rows, dimension, predictors, seasons=
    held_out_seasons)` -- the exact call shape
    `role_regime_redistribution_audit.compute_paired_evaluation` uses.

    Does not assume the two variants' held-out slices match: it digests
    both and reports `held_out_populations_identical` explicitly, so a
    caller who accidentally passes non-comparable populations gets a loud
    signal rather than a silently misleading MAE delta.

    Returns a `TrainingSensitivityResult` with per-bucket fitted-weight
    deltas AND the held-out paired MAE deltas, so "did training-population
    sensitivity change the fitted parameters" and "did it change the
    held-out predictions enough to matter" are both directly answerable
    (a model can have a real parameter delta with a negligible prediction
    delta, or vice versa -- this function never collapses that distinction
    into a single boolean).
    """
    held_out_digest_a = event_population_digest(
        (e for e in variant_a.events_with_regime if e.get("season") in held_out_seasons),
        event_identity_fn=event_identity_fn,
    )
    held_out_digest_b = event_population_digest(
        (e for e in variant_b.events_with_regime if e.get("season") in held_out_seasons),
        event_identity_fn=event_identity_fn,
    )
    held_out_populations_identical = held_out_digest_a == held_out_digest_b

    model_a = train_committee_model_fn(
        variant_a.events_with_regime, variant_a.candidates, variant_a.usage_rows,
        dimension, train_seasons=train_seasons,
    )
    model_b = train_committee_model_fn(
        variant_b.events_with_regime, variant_b.candidates, variant_b.usage_rows,
        dimension, train_seasons=train_seasons,
    )

    weights_a: dict[str, list[float]] = dict(model_a.get("weights") or {})
    weights_b: dict[str, list[float]] = dict(model_b.get("weights") or {})
    all_buckets = sorted(set(weights_a) | set(weights_b))
    weight_delta_by_bucket: dict[str, list[float]] = {}
    max_abs_weight_delta = 0.0
    for bucket in all_buckets:
        wa = weights_a.get(bucket) or []
        wb = weights_b.get(bucket) or []
        n = max(len(wa), len(wb))
        wa = list(wa) + [0.0] * (n - len(wa))
        wb = list(wb) + [0.0] * (n - len(wb))
        deltas = [abs(x - y) for x, y in zip(wa, wb)]
        weight_delta_by_bucket[bucket] = deltas
        if deltas:
            max_abs_weight_delta = max(max_abs_weight_delta, max(deltas))

    predictors_a = dict(baseline_predictors)
    predictors_a[challenger_name] = build_challenger_predictor_fn(model_a)
    predictors_b = dict(baseline_predictors)
    predictors_b[challenger_name] = build_challenger_predictor_fn(model_b)

    paired_a = compute_paired_evaluation_fn(
        variant_a.events_with_regime, variant_a.candidates, variant_a.role_state_rows,
        variant_a.usage_rows, dimension, predictors_a, seasons=held_out_seasons,
    )
    paired_b = compute_paired_evaluation_fn(
        variant_b.events_with_regime, variant_b.candidates, variant_b.role_state_rows,
        variant_b.usage_rows, dimension, predictors_b, seasons=held_out_seasons,
    )

    mae_a: dict[str, float | None] = dict(paired_a.get("mae_by_predictor") or {})
    mae_b: dict[str, float | None] = dict(paired_b.get("mae_by_predictor") or {})
    all_predictor_names = sorted(set(mae_a) | set(mae_b))
    mae_abs_delta_by_predictor: dict[str, float | None] = {}
    max_abs_mae_delta_any_predictor = 0.0
    for name in all_predictor_names:
        va, vb = mae_a.get(name), mae_b.get(name)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = abs(va - vb)
            mae_abs_delta_by_predictor[name] = delta
            max_abs_mae_delta_any_predictor = max(max_abs_mae_delta_any_predictor, delta)
        else:
            mae_abs_delta_by_predictor[name] = None

    return TrainingSensitivityResult(
        dimension=dimension,
        variant_a_name=variant_a.name,
        variant_b_name=variant_b.name,
        held_out_event_digest={variant_a.name: held_out_digest_a, variant_b.name: held_out_digest_b},
        held_out_populations_identical=held_out_populations_identical,
        n_training_examples={
            variant_a.name: model_a.get("n_training_examples"),
            variant_b.name: model_b.get("n_training_examples"),
        },
        n_training_examples_by_bucket={
            variant_a.name: dict(model_a.get("n_training_examples_by_bucket") or {}),
            variant_b.name: dict(model_b.get("n_training_examples_by_bucket") or {}),
        },
        fitted_weights={variant_a.name: weights_a, variant_b.name: weights_b},
        weight_delta_by_bucket=weight_delta_by_bucket,
        max_abs_weight_delta=max_abs_weight_delta,
        held_out_paired_n={
            variant_a.name: paired_a.get("paired_n", 0),
            variant_b.name: paired_b.get("paired_n", 0),
        },
        mae_by_predictor={variant_a.name: mae_a, variant_b.name: mae_b},
        mae_abs_delta_by_predictor=mae_abs_delta_by_predictor,
        challenger_name=challenger_name,
        mae_abs_delta_challenger=mae_abs_delta_by_predictor.get(challenger_name),
        max_abs_mae_delta_any_predictor=max_abs_mae_delta_any_predictor,
        bit_identical_predictions=(max_abs_mae_delta_any_predictor == 0.0),
    )


# --------------------------------------------------------------------------
# Optional real-production adapter (lazy import -- PR #147/#150 are draft
# and unmerged as of this module's base commit; importing them eagerly at
# module load time would make this file fail to import on `main`).
# --------------------------------------------------------------------------


def load_production_adapters() -> dict[str, Any]:
    """Import PR #147's `role_regime_redistribution` and PR #150's
    `role_regime_redistribution_audit` UNMODIFIED and return the exact
    callables/constants `compute_training_sensitivity` needs, so a caller
    can re-run this module's comparison against the real challenger once
    those modules are reachable (merged, or made importable another way) --
    without this file depending on them at import time.

    Raises `ModuleNotFoundError` with a clear message if they are not
    reachable, rather than silently falling back to anything synthetic.
    """
    try:
        from nfl.research.role_regime_redistribution import (  # type: ignore[import-not-found]
            CHALLENGER_HELD_OUT_SEASONS,
            CHALLENGER_NAME,
            CHALLENGER_TRAIN_SEASONS,
            train_committee_model,
        )
        from nfl.research.role_regime_redistribution_audit import (  # type: ignore[import-not-found]
            build_challenger_predictor,
            compute_paired_evaluation,
        )
        from nfl.research.role_intelligence_baselines import BASELINE_PREDICTORS
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only once PR #147/#150 land
        raise ModuleNotFoundError(
            "load_production_adapters() requires PR #147's role_regime_redistribution.py "
            "and PR #150's role_regime_redistribution_audit.py to be importable as "
            "nfl.research.role_regime_redistribution(_audit) -- both remain draft/unmerged "
            "as of this module's base commit. Pass the equivalent functions directly to "
            "compute_training_sensitivity() instead, or re-run once those PRs merge."
        ) from exc

    return {
        "train_committee_model_fn": train_committee_model,
        "build_challenger_predictor_fn": build_challenger_predictor,
        "compute_paired_evaluation_fn": compute_paired_evaluation,
        "baseline_predictors": dict(BASELINE_PREDICTORS),
        "challenger_name": CHALLENGER_NAME,
        "train_seasons": CHALLENGER_TRAIN_SEASONS,
        "held_out_seasons": CHALLENGER_HELD_OUT_SEASONS,
    }


__all__ = [
    "TrainingSensitivityInputs",
    "TrainingSensitivityResult",
    "event_population_digest",
    "compute_training_sensitivity",
    "load_production_adapters",
]
