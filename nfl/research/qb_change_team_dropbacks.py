#!/usr/bin/env python3
"""QB-change-aware team pass-dropback volume: the missing personnel-change
consumer named in the permanent factor register's P10 ("account for QB
change effects on ALL teammates, not storytelling").

## What already existed, unconsumed (confirmed by reading it before writing
a line of this file)

`nfl/research/qb_continuity_features.py` (`infer_team_week_starters`,
`build_prior_qb_continuity_features`) already builds a real, strictly-prior,
no-lookahead QB-starter-identity/tenure substrate from nflverse weekly
player-stat rows. Its own module docstring is explicit: "This module does
not evaluate that hypothesis, correlate it with anything, or wire it into
any model or selector." Grepping every other module in this repo confirms
that remains true -- no caller of `build_prior_qb_continuity_features`
exists anywhere in `nfl/` outside its own tests. This is a real instance of
the "tested but unconsumed" failure mode this project has already found and
fixed twice for the coaching-regime feature (PR #179 -> #181) and separately
flagged as a permanent epistemic rule for PR #177.

## What this module adds

`filter_team_rows_by_qb_continuity` and `predict_team_pass_dropbacks_qb_
aware` are structurally the QB-identity analogue of `receptions_team_
opportunity_challenger.filter_team_rows_by_current_regime` / `predict_team_
pass_dropbacks_coaching_aware`: restrict a team's own prior-game rolling
dropback-volume window to only games actually started (by real recorded
pass-attempt volume, `qb_continuity_features.infer_team_week_starters`,
reused unmodified) by the SAME quarterback who enters the target week as
incumbent, rather than blending in games a since-departed starter played.
This directly changes `team_dropbacks` -- which feeds `receptions_team_
opportunity_challenger.compute_opportunity_projection` (reused unmodified)
for EVERY receiving-corps teammate on that team, not just one flagged
player -- exactly the "ALL teammates" requirement P10 names, and exactly
the mechanism this repo already validated works for the coaching-regime
case.

## What this module reuses, never rebuilds

- `qb_continuity_features.infer_team_week_starters` -- the exact real
  starter-identity aggregation, called here read-only.
- `receptions_team_opportunity_challenger._dropback_proxy`,
  `_rolling_dropback_mean` -- the identical dropback-proxy definition and
  rolling-window arithmetic already used by the coaching-aware and naive
  team-volume predictions, imported directly (not reimplemented) so a
  QB-aware number is directly comparable to those existing numbers on the
  same real box-score rows.
- `receptions_team_opportunity_challenger.compute_opportunity_projection`,
  `estimate_current_week_target_share`, `estimate_current_week_catch_rate`,
  `predict_team_pass_dropbacks_coaching_aware` -- reused unmodified as the
  "otherwise-identical simpler control" for the comparison record below.
- `receptions_shadow.score_shadow_candidate` -- reused unmodified for the
  real probability-side comparison.

This module makes ZERO changes to `receptions_team_opportunity_challenger.
py`, `qb_continuity_features.py`, or any other existing file -- it is a new,
additive, read-only composition layer, following the same non-invasive
pattern the coaching-aware and snap-share-aware consumers already
established.

## Fail-closed / NO_ADJUSTMENT discipline

`resolve_incumbent_qb` never fabricates an incumbent: a team with no real
prior-week starter observation returns `incumbent_player_id: None` and the
caller falls back to the fully unfiltered ("no QB feature") row set, exactly
like the coaching-aware filter's own `UNKNOWN` regime fallback. Separately,
even when a real incumbent IS resolved, if that incumbent has made zero
prior starts under the current identity that this repo's data can see (a
true first start -- e.g. a real in-season QB change) the QB-restricted row
set is legitimately empty; `predict_team_pass_dropbacks_qb_aware` reports
this explicitly via `own_games_used_qb_aware == 0` and
`qb_aware_basis` naming whichever real fallback applied (opponent-only or
no-real-prior-history), never a guessed number standing in for the missing
QB-specific history. The composition record below (`build_qb_change_aware_
record`) additionally sets `status: "NO_ADJUSTMENT_INSUFFICIENT_QB_TENURE_
HISTORY"` for exactly this real, disclosed insufficient-evidence case,
rather than silently returning a number with no caveat attached.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from nfl.research.qb_continuity_features import infer_team_week_starters
from nfl.research.receptions_shadow import score_shadow_candidate
from nfl.research.receptions_team_opportunity_challenger import (
    TeamOpportunityChallengerError,
    _dropback_proxy,
    _rolling_dropback_mean,
    compute_opportunity_projection,
    estimate_current_week_catch_rate,
    estimate_current_week_target_share,
    predict_team_pass_dropbacks_coaching_aware,
)


def resolve_incumbent_qb(
    starters: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
) -> dict[str, Any]:
    """Real, strictly-prior incumbent-QB identity + consecutive-start tenure
    for one team entering (target_season, target_week), from real recorded
    starter rows (`infer_team_week_starters` output, reused unmodified).

    Uses only team-weeks strictly BEFORE the target week -- the same
    no-lookahead cutoff `filter_team_rows_by_current_regime` enforces for
    HC identity. Returns `incumbent_player_id: None` (never a guess) when
    the team has no real prior starter observation at all (Week 1 of the
    earliest season this repo's substrate covers for that team).
    """
    team_starters = sorted(
        (s for s in starters if s["team"] == team and (s["season"], s["week"]) < (target_season, target_week)),
        key=lambda s: (s["season"], s["week"]),
    )
    if not team_starters:
        return {
            "incumbent_player_id": None,
            "qb_tenure_starts": None,
            "reason": "NO_REAL_PRIOR_STARTER_HISTORY",
        }
    incumbent_id = team_starters[-1]["starter_player_id"]
    tenure = 1
    for prior in reversed(team_starters[:-1]):
        if prior["starter_player_id"] == incumbent_id:
            tenure += 1
        else:
            break
    return {"incumbent_player_id": incumbent_id, "qb_tenure_starts": tenure, "reason": None}


def filter_team_rows_by_qb_continuity(
    team_box_score_rows: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
    starters: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Real QB-identity-aware filter for one team's own prior box-score
    rows: restrict the rolling window to games real recorded pass-attempt
    volume shows were actually started by the SAME quarterback who is the
    real incumbent entering the target week.

    No-lookahead is enforced unconditionally here, exactly like `filter_
    team_rows_by_current_regime`: a game at or after the target week can
    never enter either the filtered set or the unfiltered fallback.

    A box-score row whose (team, season, week) has no matching real starter
    observation (a genuine source gap, not expected in the normal case) is
    conservatively EXCLUDED from the QB-filtered set rather than assumed to
    match -- fail-closed, never a guessed identity match.
    """
    own_rows = [
        dict(r) for r in team_box_score_rows
        if r["team"] == team and (r["season"], r["week"]) < (target_season, target_week)
    ]
    incumbent_info = resolve_incumbent_qb(
        starters, team=team, target_season=target_season, target_week=target_week,
    )
    incumbent_id = incumbent_info["incumbent_player_id"]
    if incumbent_id is None:
        return own_rows, {**incumbent_info, "qb_filter_applied": False}

    starter_by_team_week = {
        (s["team"], s["season"], s["week"]): s["starter_player_id"] for s in starters
    }
    kept = [
        row for row in own_rows
        if starter_by_team_week.get((team, row["season"], row["week"])) == incumbent_id
    ]
    return kept, {
        **incumbent_info,
        "qb_filter_applied": len(kept) != len(own_rows),
        "rows_excluded_by_qb_filter": len(own_rows) - len(kept),
        "rows_with_unresolved_starter": sum(
            1 for row in own_rows
            if (team, row["season"], row["week"]) not in starter_by_team_week
        ),
    }


def predict_team_pass_dropbacks_qb_aware(
    team_box_score_rows: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
    starters: Sequence[Mapping[str, Any]],
    opponent_defense_allowed: float | None,
    opponent_defense_prior_games_n: int,
    rolling_window: int = 5,
) -> dict[str, Any]:
    """The real QB-change consumer: computes the team's own strictly-prior
    dropback rolling mean TWICE from the same raw real box-score rows --
    once restricted to games started by the real current incumbent QB
    (`filter_team_rows_by_qb_continuity`), once with no QB restriction at
    all (the "otherwise-identical model without the QB-continuity feature"
    control) -- then blends EACH with the same real opponent dropbacks-
    allowed value, mirroring `predict_team_pass_dropbacks_coaching_aware`'s
    structure exactly so the two features are independently ablatable and
    directly comparable on the same real data.

    Never fabricates a QB-change effect: when no real incumbent can be
    resolved, `qb_aware` and `naive_control` are numerically IDENTICAL by
    construction (the filter returns the same unfiltered rows to both).
    """
    filtered_rows, qb_note = filter_team_rows_by_qb_continuity(
        team_box_score_rows, team=team, target_season=target_season, target_week=target_week,
        starters=starters,
    )
    naive_rows = [
        dict(r) for r in team_box_score_rows
        if r["team"] == team and (r["season"], r["week"]) < (target_season, target_week)
    ]

    qb_own_mean, qb_own_n = _rolling_dropback_mean(filtered_rows, rolling_window=rolling_window)
    naive_own_mean, naive_own_n = _rolling_dropback_mean(naive_rows, rolling_window=rolling_window)

    have_opp = opponent_defense_allowed is not None and opponent_defense_prior_games_n > 0

    def _blend(own_mean: float | None, own_n: int) -> tuple[float | None, str]:
        have_own = own_mean is not None and own_n > 0
        if have_own and have_opp:
            return (own_mean + opponent_defense_allowed) / 2.0, "BLENDED_OFFENSE_AND_DEFENSE"
        if have_own:
            return own_mean, "OFFENSE_ONLY_NO_REAL_OPPONENT_PRIOR"
        if have_opp:
            return opponent_defense_allowed, "DEFENSE_ONLY_NO_REAL_OWN_PRIOR"
        return None, "NO_REAL_PRIOR_HISTORY"

    qb_predicted, qb_basis = _blend(qb_own_mean, qb_own_n)
    naive_predicted, naive_basis = _blend(naive_own_mean, naive_own_n)

    return {
        "predicted_dropbacks_qb_aware": qb_predicted,
        "predicted_dropbacks_naive_control": naive_predicted,
        "qb_aware_basis": qb_basis,
        "naive_control_basis": naive_basis,
        "own_games_used_qb_aware": qb_own_n,
        "own_games_used_naive_control": naive_own_n,
        "qb_feature_changed_the_projection": (
            qb_predicted is not None and naive_predicted is not None and qb_predicted != naive_predicted
        ),
        "qb_note": qb_note,
    }


def build_qb_change_aware_record(
    *,
    candidate_player_id: str,
    candidate_team: str,
    team_box_score_rows: Sequence[Mapping[str, Any]],
    starters: Sequence[Mapping[str, Any]],
    hc_intervals: Sequence[Any],
    game_date_index: Mapping[tuple[str, int, int], date],
    opponent_defense_allowed: float | None,
    opponent_defense_prior_games_n: int,
    target_share_history: list[tuple[int, int, float]],
    catch_rate_game_log: Sequence[Mapping[str, Any]],
    target_season: int,
    target_week: int,
    line: float,
    over_odds: Any,
    under_odds: Any,
    residuals: Sequence[float],
    rolling_window: int = 5,
) -> dict[str, Any] | None:
    """One real, comparable receptions-projection record for a single
    candidate: BASELINE = the already-merged coaching-aware opportunity
    engine (`predict_team_pass_dropbacks_coaching_aware`, reused
    unmodified, itself already the real production-research team-volume
    feature) vs. QB-AWARE = the same chain with `predict_team_pass_
    dropbacks_qb_aware`'s QB-restricted team volume substituted in --
    everything else (target share, catch rate, odds, residuals) held
    identical between the two, isolating exactly what the QB-continuity
    feature changed.

    Returns None only if the QB-aware projection itself cannot be computed
    from real data (never a fabricated record) -- the SAME fail-closed rule
    `compute_opportunity_projection` already enforces, reused unmodified.
    """
    baseline_dropbacks_info = predict_team_pass_dropbacks_coaching_aware(
        team_box_score_rows, team=candidate_team, target_season=target_season, target_week=target_week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=opponent_defense_allowed,
        opponent_defense_prior_games_n=opponent_defense_prior_games_n,
        rolling_window=rolling_window,
    )
    qb_dropbacks_info = predict_team_pass_dropbacks_qb_aware(
        team_box_score_rows, team=candidate_team, target_season=target_season, target_week=target_week,
        starters=starters, opponent_defense_allowed=opponent_defense_allowed,
        opponent_defense_prior_games_n=opponent_defense_prior_games_n,
        rolling_window=rolling_window,
    )
    target_share_info = estimate_current_week_target_share(
        player_id=candidate_player_id, target_share_history=target_share_history,
        target_season=target_season, target_week=target_week,
    )
    catch_rate_info = estimate_current_week_catch_rate(
        player_id=candidate_player_id, game_log=catch_rate_game_log,
        target_season=target_season, target_week=target_week,
    )

    baseline_projection_info = compute_opportunity_projection(
        predicted_team_dropbacks=baseline_dropbacks_info["predicted_dropbacks_coaching_aware"],
        target_share=target_share_info["estimate"], catch_rate=catch_rate_info["estimate"],
    )
    qb_projection_info = compute_opportunity_projection(
        predicted_team_dropbacks=qb_dropbacks_info["predicted_dropbacks_qb_aware"],
        target_share=target_share_info["estimate"], catch_rate=catch_rate_info["estimate"],
    )
    if qb_projection_info["projection"] is None:
        return None

    baseline_score = None
    if baseline_projection_info["projection"] is not None:
        baseline_score = score_shadow_candidate(
            projection=baseline_projection_info["projection"], line=line,
            over_odds=over_odds, under_odds=under_odds, residuals=residuals,
        )
    qb_score = score_shadow_candidate(
        projection=qb_projection_info["projection"], line=line,
        over_odds=over_odds, under_odds=under_odds, residuals=residuals,
    )

    insufficient_evidence = qb_dropbacks_info["own_games_used_qb_aware"] == 0
    status = (
        "NO_ADJUSTMENT_INSUFFICIENT_QB_TENURE_HISTORY" if insufficient_evidence
        else "RESEARCH_ONLY_NOT_PROMOTED"
    )

    return {
        "candidate_player_id": candidate_player_id,
        "candidate_team": candidate_team,
        "target_season": target_season,
        "target_week": target_week,
        "baseline_projection": baseline_projection_info["projection"],
        "baseline_probabilities": baseline_score,
        "qb_aware_projection": qb_projection_info["projection"],
        "qb_aware_probabilities": qb_score,
        "team_dropbacks_baseline": baseline_dropbacks_info,
        "team_dropbacks_qb_aware": qb_dropbacks_info,
        "target_share": target_share_info,
        "catch_rate": catch_rate_info,
        "qb_feature_changed_the_projection": (
            baseline_projection_info["projection"] is not None
            and qb_projection_info["projection"] != baseline_projection_info["projection"]
        ),
        "status": status,
        "prediction_source": "QB_CHANGE_AWARE_TEAM_OPPORTUNITY_V1",
    }


__all__ = [
    "resolve_incumbent_qb",
    "filter_team_rows_by_qb_continuity",
    "predict_team_pass_dropbacks_qb_aware",
    "build_qb_change_aware_record",
    "TeamOpportunityChallengerError",
    "infer_team_week_starters",
]
