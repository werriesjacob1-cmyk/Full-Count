#!/usr/bin/env python3
"""Frozen role/opponent-intelligence-adjusted receptions challenger.

Connects `role_regime_redistribution.HIERARCHICAL_COMMITTEE_PROBABILITY_V1`
-- a real conditional-logit model that predicts how a removed WR/RB's
vacated opportunity share is absorbed by his teammates, trained on the
real 2012-2021 nflverse teammate-absence corpus and held-out evaluated on
2022-2025 (already merged, already tested, and until this module,
completely unwired from any live prediction) -- to `receptions_shadow.py`'s
B0 rolling-mean projection. The result is a genuine opportunity-delta-
adjusted receptions projection which, via the SAME empirical-residual
scoring B0 itself already uses, produces a coherent probability
distribution for standard AND alternate receptions lines from one shared
player distribution.

## Honest scientific disclosure (do not remove or soften this)

`FROZEN_COMMITTEE_MODEL` below is the exact, reproducible result of
running the already-merged, unmodified `role_regime_redistribution.
train_committee_model` against the real 2012-2021 nflverse substrate
(`role_intelligence_data_prep`/`role_intelligence_features`, unmodified),
then evaluating it against the SAME module's own predeclared held-out
seasons (2022-2025) via `evaluate_challenger_vs_baselines` -- reproduced
end-to-end on 2026-09-23 (668 real WR/RB absence events, 217 real training
examples, 199 ESTABLISHED_REGIME / 18 NEW_REGIME_FIRST_30_DAYS).

On that held-out set, `HIERARCHICAL_COMMITTEE_PROBABILITY_V1` scored
MAE=0.06196 (n=449) against `NO_ADJUSTMENT`'s MAE=0.06050 (n=441) on
target-share prediction -- i.e. **this real trained model does NOT show an
accuracy improvement over doing nothing, on this metric, on this held-out
set.** (The n=449 vs n=441 samples are not perfectly identical populations
-- see `evaluate_predictors`'s own per-predictor accumulation in
`role_regime_redistribution.py`; both are the full real held-out
population for their own predictor, but this is disclosed here rather
than presented as a strictly matched-volume comparison.) The committee
model DOES beat the other three real baselines on this same held-out set
(PROPORTIONAL_TEAMMATE_REDISTRIBUTION 0.06396, DEPTH_CHART_NEXT_MAN
0.07740, RECENT_USAGE_NEXT_MAN 0.08254) -- it is the best real
*adjustment* model tested, just not better than making no adjustment at
all. Both framings are true; neither is omitted. This is disclosed here, not suppressed or hidden downstream: this
module still wires the real mechanism end-to-end (source -> verified
identity/timing -> feature -> opportunity delta -> outcome distribution ->
frozen prediction) because building and testing that connection is itself
a real engineering deliverable -- "a correct end-to-end prediction with
insufficient settled volume is an engineering success, not scientific
proof of predictive superiority" is this project's own standard -- but
this module makes NO accuracy claim, and nothing here is promoted to any
selector, eligibility gate, or public pick. Full held-out comparison
against all four `role_intelligence_baselines` predictors is preserved in
`FROZEN_COMMITTEE_MODEL["held_out_report"]` below, not cherry-picked.

## What this module does NOT do

- Never re-trains the committee model live. `FROZEN_COMMITTEE_MODEL` is a
  fixed constant, exactly the discipline `receptions_frozen_challenger.
  FROZEN_NB_FIT` already established for this repo's other frozen
  challenger.
- Never fabricates a teammate-absence event. An `event` is only ever
  supplied by the caller from real official-inactive/injury evidence
  (mirroring `role_intelligence_features.build_teammate_absence_trigger_
  events`'s own real, pregame-only trigger source) -- this module performs
  no source fetching, parsing, or event detection of its own.
- Never independently re-derives a second opportunity budget. The
  adjusted projection scales the SAME player's own B0 rolling-mean
  projection by the ratio of his committee-predicted post-redistribution
  target share to his own strictly-prior target share -- it does not
  invent an unrelated adjustment or double-count team target volume.
- Returns `None` -- never a fabricated adjustment -- whenever any required
  real input is missing or unusable: the candidate is not among the
  model's predicted teammates, the candidate has no real prior target
  share, that prior share is not strictly positive, or the resulting
  adjusted projection is not strictly positive.
- Performs no date/clock logic and reads no wall-clock time anywhere in
  this module. Point-in-time safety is the caller's responsibility
  (exactly as it already is for `receptions_shadow.current_b0_projection`
  and `role_intelligence_features`'s own strictly-prior invariant) -- this
  module is a pure, stateless transform of caller-supplied real numbers.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nfl.research.receptions_shadow import score_shadow_candidate
from nfl.research.role_regime_redistribution import predict_committee_model

FROZEN_COMMITTEE_MODEL: dict[str, Any] = {
    "model_name": "HIERARCHICAL_COMMITTEE_PROBABILITY_V1",
    "dimension": "target_share",
    "feature_names": ("bias", "prior_last5", "has_prior", "depth_inv", "has_depth", "games_n_norm"),
    "weights": {
        "ESTABLISHED_REGIME": [
            -3.0316321514928766e-18,
            0.08100139679398129,
            -0.07132764729673349,
            0.22785189738658024,
            0.20329452991045843,
            0.018117697234998568,
        ],
        "NEW_REGIME_FIRST_30_DAYS": [
            2.10292539029361e-17,
            0.13424636023195094,
            -0.4074911795367009,
            0.541281225201379,
            0.38738773891021194,
            -0.4350491190923772,
        ],
        "UNKNOWN_REGIME": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    },
    "n_training_examples": 217,
    "n_training_examples_by_bucket": {"ESTABLISHED_REGIME": 199, "NEW_REGIME_FIRST_30_DAYS": 18},
    "train_seasons": list(range(2012, 2022)),
    "iterations": 200,
    "lr": 0.05,
    "l2": 0.01,
    "fit_reproduced_at": "2026-09-23",
    "n_events_total": 668,
    "held_out_seasons": [2022, 2023, 2024, 2025],
    "held_out_report": {
        "NO_ADJUSTMENT": {"n": 441, "mae": 0.06050387800295093},
        "PROPORTIONAL_TEAMMATE_REDISTRIBUTION": {"n": 441, "mae": 0.06396060775856668},
        "DEPTH_CHART_NEXT_MAN": {"n": 441, "mae": 0.07739678825309278},
        "RECENT_USAGE_NEXT_MAN": {"n": 441, "mae": 0.08254290234546448},
        "HIERARCHICAL_COMMITTEE_PROBABILITY_V1": {"n": 449, "mae": 0.06196222908360095},
    },
    "held_out_finding": (
        "HIERARCHICAL_COMMITTEE_PROBABILITY_V1 did NOT beat NO_ADJUSTMENT's "
        "MAE on this held-out set (0.06196, n=449 vs 0.06050, n=441) -- a "
        "real, disclosed negative finding, not accuracy evidence for this "
        "challenger. It DOES beat the other three real baselines tested "
        "(PROPORTIONAL_TEAMMATE_REDISTRIBUTION 0.06396, "
        "DEPTH_CHART_NEXT_MAN 0.07740, RECENT_USAGE_NEXT_MAN 0.08254)."
    ),
    "players_crosswalk_digest_bytes": 7234131,
    "games_csv_digest_sha256": "26332ae5d8d8d0481f0670cf5e3849497a415351d4026ae5bee15a5aab96d188",
    "status": "RESEARCH_ONLY_NOT_PROMOTED",
}


class RoleAdjustedChallengerError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


def predicted_post_redistribution_target_share(
    *,
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict[tuple[str, str], list[tuple[int, int, float]]],
    candidate_player_id: str,
    model: dict[str, Any] = FROZEN_COMMITTEE_MODEL,
) -> float | None:
    """Real committee-model target-share prediction for one specific
    teammate. Returns None if `candidate_player_id` is not among the
    teammates the model actually predicted for (e.g. not a real teammate
    of the removed player, or the event carried no teammates at all) --
    never a guessed or interpolated value.
    """
    if not teammates:
        return None
    predictions = predict_committee_model(event, teammates, history, "target_share", model)
    return predictions.get(candidate_player_id)


def compute_role_adjusted_projection(
    *,
    b0_projection: float,
    candidate_own_prior_target_share: float | None,
    predicted_post_redistribution_target_share: float | None,
) -> float | None:
    """Scale B0's own rolling-mean receptions projection by the ratio of
    the committee-predicted post-redistribution target share to the
    candidate's own strictly-prior target share.

    Preserves opportunity accounting rather than inventing a second
    budget: this is a multiplicative re-scaling of B0's own real number,
    never an independently-derived projection. Returns None (never 0.0 or
    a fabricated number) whenever either share is missing or the prior
    share is not strictly positive -- dividing by a zero or negative prior
    share would produce a meaningless ratio, not a real signal.
    """
    if candidate_own_prior_target_share is None or predicted_post_redistribution_target_share is None:
        return None
    if not (candidate_own_prior_target_share > 0):
        return None
    if not (b0_projection > 0):
        return None
    ratio = predicted_post_redistribution_target_share / candidate_own_prior_target_share
    adjusted = b0_projection * ratio
    return adjusted if adjusted > 0 else None


def role_adjusted_side_probabilities_for_lines(
    *,
    adjusted_projection: float,
    lines: Sequence[float],
    over_odds_by_line: dict[float, Any],
    under_odds_by_line: dict[float, Any],
    residuals: Sequence[float],
) -> dict[float, dict[str, Any]]:
    """Score every real offered line (standard + alternate) from the SAME
    adjusted projection and the SAME residual distribution -- proves
    coherence by construction: no line gets its own independently-modeled
    probability, every line is a different threshold read off one shared
    player outcome distribution, exactly like `receptions_shadow.
    score_shadow_candidate` itself already guarantees for B0.
    """
    if not lines:
        raise RoleAdjustedChallengerError("at least one line is required")
    out: dict[float, dict[str, Any]] = {}
    for line in lines:
        if line not in over_odds_by_line or line not in under_odds_by_line:
            raise RoleAdjustedChallengerError(f"missing real odds for line {line!r}")
        out[line] = score_shadow_candidate(
            projection=adjusted_projection,
            line=line,
            over_odds=over_odds_by_line[line],
            under_odds=under_odds_by_line[line],
            residuals=residuals,
        )
    return out


def build_role_adjusted_challenger_record(
    *,
    b0_projection: float,
    line: float,
    over_odds: Any,
    under_odds: Any,
    residuals: Sequence[float],
    event: dict[str, Any],
    teammates: list[dict[str, Any]],
    history: dict[tuple[str, str], list[tuple[int, int, float]]],
    candidate_player_id: str,
    candidate_own_prior_target_share: float | None,
    model: dict[str, Any] = FROZEN_COMMITTEE_MODEL,
) -> dict[str, Any] | None:
    """Assemble one real role-adjusted challenger record for a single real
    receptions candidate at one real offered line, or None if any required
    real input is missing (never a fabricated record).

    REAL SOURCE (event/teammates/history, all caller-supplied from real
    official-inactive and prior-usage data) -> VERIFIED IDENTITY/TIMING
    (candidate_player_id, event's own season/week/team) -> FEATURE
    (predicted post-redistribution target share) -> OPPORTUNITY DELTA
    (compute_role_adjusted_projection's ratio) -> OUTCOME DISTRIBUTION
    (score_shadow_candidate, reused unmodified) -> this frozen record.
    """
    predicted_share = predicted_post_redistribution_target_share(
        event=event, teammates=teammates, history=history,
        candidate_player_id=candidate_player_id, model=model,
    )
    adjusted_projection = compute_role_adjusted_projection(
        b0_projection=b0_projection,
        candidate_own_prior_target_share=candidate_own_prior_target_share,
        predicted_post_redistribution_target_share=predicted_share,
    )
    if adjusted_projection is None:
        return None

    score = score_shadow_candidate(
        projection=adjusted_projection, line=line,
        over_odds=over_odds, under_odds=under_odds, residuals=residuals,
    )
    return {
        "candidate_player_id": candidate_player_id,
        "b0_projection": b0_projection,
        "adjusted_projection": adjusted_projection,
        "candidate_own_prior_target_share": candidate_own_prior_target_share,
        "predicted_post_redistribution_target_share": predicted_share,
        "removed_player_id": event.get("removed_player_id"),
        "event_type": event.get("event_type"),
        "hc_regime_tenure_bucket": event.get("hc_regime_tenure_bucket"),
        "model_name": model["model_name"],
        "model_held_out_finding": model["held_out_finding"],
        **score,
        "prediction_source": "B0_VS_ROLE_ADJUSTED_HIERARCHICAL_COMMITTEE_V1",
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


__all__ = [
    "FROZEN_COMMITTEE_MODEL",
    "RoleAdjustedChallengerError",
    "predicted_post_redistribution_target_share",
    "compute_role_adjusted_projection",
    "role_adjusted_side_probabilities_for_lines",
    "build_role_adjusted_challenger_record",
]
