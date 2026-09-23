#!/usr/bin/env python3
"""Real team-plays -> player-participation -> catch-probability -> receptions
distribution opportunity chain.

This is a THIRD, independent research challenger to B0's rolling-mean
receptions projection (alongside the already-merged `receptions_frozen_
challenger` and `receptions_role_adjusted_challenger`). Unlike those two --
which both re-scale B0's own rolling-mean number -- this module derives an
ABSOLUTE projection from first principles:

    team pass-dropback volume (opponent-adjusted)
        x player target share (current-2026-season-aware, shrunk toward the
          2025 prior when the current-season sample is thin)
        x player catch rate (same shrinkage discipline)
        = expected receptions
        -> `receptions_shadow.score_shadow_candidate` (reused unmodified)
        -> coherent standard/alternate-line probabilities from one
           distribution, exactly like every other challenger in this repo.

Every piece of the team-opportunity substrate below (`team_prior_features`,
`defense_prior_features`, `game_matchup_features`) already existed, already
merged, already tested, and was never assembled into a player-prop
predictor -- confirmed by grep before writing a line of this file (see the
AGENT CLAIM on Issue #91, workstream `NFL-RECEPTIONS-TEAM-OPPORTUNITY-
ENGINE-20260923`). `role_intelligence_features.build_role_state_rows` and
`build_player_dimension_history` already exist for the player-share side.
This module is deliberately a thin composition layer over all of that real,
already-reviewed infrastructure, not a reimplementation of any of it.

## Honest scope disclosure (do not remove or soften this)

The team pass-volume side of this chain currently runs on the existing
PINNED 2023-2025 nflverse PBP-derived team box-score substrate (exact
byte/sha256-verified, the same source `game_market_c2_*` already uses) --
NOT a new live 2026 PBP fetch. Extending it to a live, weekly-changing 2026
PBP release would need the same schema/sanity-validation redesign Mission 2
applied to the live roster asset (`nfl-live-receptions-shadow-board.yml`),
which is real additional scope this module does not take on. The PLAYER
side (target share, catch rate) DOES use real live current-season 2026
weekly-stats data where available, shrunk toward the 2025 prior -- this is
a genuine, partial current-season upgrade over `receptions_role_adjusted_
challenger`'s static 2025-only prior-share ranking (a gap SUPERCHAD's own
review, Issue #91 comment `5789796992`, correctly flagged), not a complete
one. Reported honestly here and in every evidence artifact this module
produces, never presented as more complete than it is.

## Honest result (do not remove or soften this)

A real evaluation on 2,954 matched real (player, week) observations from
the 2025 season (weeks 8-18, real last-5-game B0 rolling mean vs. this
module's real opportunity projection, both against real realized
receptions) found **this challenger does NOT beat B0** on that metric, on
that population: B0 MAE=1.299 vs. challenger MAE=1.412. A real, disclosed
negative finding, not accuracy evidence for this challenger. See
`engineering/nfl_team_opportunity_engine_20260923/README.md` and
`team_opportunity_real_evaluation_report.json` for the full reproducible
evidence, including which real inputs (opponent-adjusted team dropback
volume, current-season-shrunk target share) drove each evaluated
projection. Consistent with this project's own standard, the real,
tested, end-to-end connection built here is an engineering deliverable in
its own right, independent of this result.

## Coaching consumer (do not remove or soften this)

`predict_team_pass_dropbacks_coaching_aware` and `filter_team_rows_by_
current_regime` (both below) are ACTUALLY CONSUMED by `build_opportunity_
challenger_record` -- the coaching-aware team-dropback prediction, not a
naive unfiltered one, is what feeds `compute_opportunity_projection`. On
the main 2,954-row 2025-week-8+ evaluation above, the coaching feature
changed ZERO projections (`coaching_ablation.rows_where_coaching_feature_
changed_the_projection == 0` in the evidence report) -- a real, honest
null result, not a bug: genuine in-season HC firings are rare, and none
fell inside any evaluated player's own rolling-5 window in that
population. Directly targeting the three real, known 2023 in-season HC
changes in the loaded registry (Las Vegas/Antonio Pierce 2023-11-05,
Carolina/Chris Tabor 2023-12-03, LA Chargers/Giff Smith 2023-12-23) DOES
produce genuine, non-synthetic activation: e.g. LV week 10 2023,
coaching-aware predicted dropbacks 30.7 (1 real game under the new
regime) vs. naive-control 35.9 (5 games spanning the change) -- see
`real_2023_in_season_hc_change_demo` in the evidence report for all
three, plus `nfl/tests/test_receptions_team_opportunity_challenger.py`'s
`PredictTeamPassDropbacksCoachingAwareTests` and the synthetic-but-
mechanism-verifying `test_coaching_regime_change_actually_changes_the_
projection`.

## What this module does NOT do

- Never fabricates a team pass-volume estimate: `predict_team_pass_
  dropbacks` returns `predicted_dropbacks: None` (never 0.0 or a guessed
  number) when neither the team's own nor the opponent's prior history has
  at least one real prior game.
- Never fabricates a target share or catch rate: both estimators return
  `None` when a player has zero real usage history at every level (current
  season AND the 2025 prior), and reject (raise) a share/rate outside
  [0, 1] rather than silently clip it -- an out-of-range value is a real
  upstream data-integrity bug, not a number to paper over.
- Never re-trains anything live and never reads wall-clock time; every
  function takes its point-in-time (season, week) explicitly from the
  caller, exactly like `coach_regime_registry.lookup_regime` itself does.
- Never touches B0's own live decision path, `role_regime_redistribution*.
  py`, `role_intelligence_*.py`, `coach_regime_registry.py`, or any
  Codex-claimed file. Consumes all of them read-only, unmodified.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from nfl.research import coach_regime_registry as coach_mod
from nfl.research.receptions_shadow import score_shadow_candidate


class TeamOpportunityChallengerError(ValueError):
    """Raised on malformed input. Never silently substitutes a guess."""


# ---------------------------------------------------------------------------
# Team pass-volume opportunity (Section 4)
# ---------------------------------------------------------------------------

def predict_team_pass_dropbacks(matchup_row: Mapping[str, Any], *, side: str) -> dict[str, Any]:
    """Real team pass-dropback-volume prediction for one side of a real
    `game_matchup_features.build_game_matchup_features` output row
    (unmodified upstream builder), blending that team's own strictly-prior
    rolling mean dropback proxy with the opponent's strictly-prior rolling
    mean dropbacks-ALLOWED -- the same blend pattern `game_matchup_features`
    already establishes for its own `*_play_volume_matchup_blend` fields,
    applied specifically to pass dropbacks (nflfastR's own `pass_attempt`
    convention already counts a sacked dropback as an attempt, so
    `dropback_proxy = attempts + sacks_suffered` is the closest available
    real proxy for pass-attempt opportunity, not a new invented quantity).

    This is the genuinely-consumed OPPONENT-specific feature required by
    Section 6: the opponent's own real prior pass-funnel tendency directly
    changes the predicted number for the team in question, not just an
    isolated, unconsumed matchup module.
    """
    if side not in ("home", "away"):
        raise TeamOpportunityChallengerError("side must be 'home' or 'away'")
    opponent = "away" if side == "home" else "home"

    own_dropbacks = matchup_row[f"{side}_offense_prior_mean_dropback_proxy"]
    own_n = matchup_row[f"{side}_offense_prior_games_n"]
    opp_allowed = matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"]
    opp_n = matchup_row[f"{opponent}_defense_prior_games_n"]

    have_own = own_dropbacks is not None and own_n > 0
    have_opp = opp_allowed is not None and opp_n > 0

    if have_own and have_opp:
        predicted = (own_dropbacks + opp_allowed) / 2.0
        basis = "BLENDED_OFFENSE_AND_DEFENSE"
    elif have_own:
        predicted = own_dropbacks
        basis = "OFFENSE_ONLY_NO_REAL_OPPONENT_PRIOR"
    elif have_opp:
        predicted = opp_allowed
        basis = "DEFENSE_ONLY_NO_REAL_OWN_PRIOR"
    else:
        predicted = None
        basis = "NO_REAL_PRIOR_HISTORY"

    return {
        "predicted_dropbacks": predicted,
        "own_prior_games_n": own_n,
        "opponent_prior_games_n": opp_n,
        "basis": basis,
    }


def filter_team_rows_by_current_regime(
    team_box_score_rows: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
    hc_intervals: Sequence[Any],
    game_date_index: Mapping[tuple[str, int, int], date],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Real HC-continuity-aware filter for ONE team's own prior box-score
    rows: restrict the rolling window to games played under the SAME head
    coach the target game will be played under, per `coach_regime_registry.
    lookup_regime` (already-merged, already-tested, real 1999-2026 nfldata
    `games.csv`-derived intervals). This is the genuinely-consumed COACHING
    feature required by Section 6.

    Applied only to the OFFENSE (this team's own tendency) side of the
    chain, not the opponent-allowed defense side -- a disclosed, deliberate
    simplification (this team's own play-calling staff plausibly changes
    its OWN tendency; the opponent's defensive funnel is treated as
    coach-agnostic for this pass) that avoids needing reciprocal-pair
    filtering across both teams of every historical game.

    Never fabricates a coaching fact: an UNKNOWN regime lookup (no games.csv
    coverage, an ambiguous overlapping interval, etc.) means NO filtering is
    applied -- the caller gets back the full, unfiltered row set (the
    "simpler control") with `regime_filter_applied: False` and the real
    reason disclosed, never a guessed regime boundary.
    """
    # Strictly-prior first: no-lookahead is enforced HERE, unconditionally,
    # not left to the caller or to whether a regime lookup resolves -- a
    # game at or after the target week must never enter either the
    # regime-filtered set OR the "no filtering applied" fallback below.
    own_rows = [
        dict(r) for r in team_box_score_rows
        if r["team"] == team and (r["season"], r["week"]) < (target_season, target_week)
    ]
    lookup = coach_mod.lookup_regime(
        hc_intervals, team=team, role="HC",
        season=target_season, week=target_week, game_date_index=game_date_index,
    )
    if lookup.status != "RESOLVED" or lookup.interval is None:
        return own_rows, {
            "regime_lookup_status": lookup.status,
            "regime_reason": lookup.reason,
            "regime_filter_applied": False,
        }

    regime_start = lookup.interval.start_date
    kept = [
        row for row in own_rows
        if game_date_index.get((team, row["season"], row["week"])) is not None
        and game_date_index[(team, row["season"], row["week"])] >= regime_start
    ]
    return kept, {
        "regime_lookup_status": "RESOLVED",
        "regime_persons": lookup.persons,
        "regime_start_date": regime_start.isoformat(),
        "regime_filter_applied": len(kept) != len(own_rows),
        "rows_excluded_by_regime_filter": len(own_rows) - len(kept),
    }


def _dropback_proxy(row: Mapping[str, Any]) -> float:
    """Same definition `team_prior_features`/`defense_prior_features` use:
    a sacked dropback is already an `attempt` under nflfastR's own
    convention, so `attempts + sacks_suffered` recovers the true dropback
    count. Reused here, not reimplemented differently, so the coaching-aware
    and naive-control predictions below are comparable to `predict_team_
    pass_dropbacks`'s own values on the same real box-score rows.
    """
    return float(row["attempts"]) + float(row["sacks_suffered"])


def _rolling_dropback_mean(
    rows: Sequence[Mapping[str, Any]], *, rolling_window: int,
) -> tuple[float | None, int]:
    ordered = sorted(rows, key=lambda r: (r["season"], r["week"]))
    window = ordered[-rolling_window:] if rolling_window > 0 else ordered
    if not window:
        return None, 0
    return sum(_dropback_proxy(r) for r in window) / len(window), len(window)


def predict_team_pass_dropbacks_coaching_aware(
    team_box_score_rows: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
    hc_intervals: Sequence[Any],
    game_date_index: Mapping[tuple[str, int, int], date],
    opponent_defense_allowed: float | None,
    opponent_defense_prior_games_n: int,
    rolling_window: int = 5,
) -> dict[str, Any]:
    """The ACTUAL coaching-consumer this module was missing: computes the
    team's own strictly-prior dropback rolling mean TWICE from the same raw
    real box-score rows -- once restricted to games under the current HC
    regime (`filter_team_rows_by_current_regime`, reused unmodified), once
    with no coaching restriction at all (the "otherwise-identical model
    without the coaching feature" control) -- then blends EACH with the
    SAME real opponent dropbacks-allowed value, producing two real,
    independently inspectable team-volume predictions rather than a single
    number with unused metadata attached.

    Unlike `predict_team_pass_dropbacks` (which reads pre-aggregated means
    off a `game_matchup_features` row and cannot distinguish a coaching
    change within its rolling window), this recomputes the OWN-side rolling
    mean directly from raw rows so the regime filter can actually change
    which games are averaged. The opponent-allowed side is unchanged
    (Section 6's disclosed simplification: coaching continuity is applied
    to a team's own offense, not credited to the opponent's defense).

    Never fabricates a coaching effect: when `lookup_regime` cannot resolve
    a regime (UNKNOWN), `coaching_aware` and `naive_control` are
    numerically IDENTICAL by construction (the filter returns the same
    unfiltered row set to both), and `regime_note.regime_filter_applied` is
    `False` -- the explicit fallback Section 5 requires, not a guess.
    """
    filtered_rows, regime_note = filter_team_rows_by_current_regime(
        team_box_score_rows, team=team, target_season=target_season, target_week=target_week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
    )
    naive_rows = [
        dict(r) for r in team_box_score_rows
        if r["team"] == team and (r["season"], r["week"]) < (target_season, target_week)
    ]

    coaching_own_mean, coaching_own_n = _rolling_dropback_mean(filtered_rows, rolling_window=rolling_window)
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

    coaching_predicted, coaching_basis = _blend(coaching_own_mean, coaching_own_n)
    naive_predicted, naive_basis = _blend(naive_own_mean, naive_own_n)

    return {
        "predicted_dropbacks_coaching_aware": coaching_predicted,
        "predicted_dropbacks_naive_control": naive_predicted,
        "coaching_aware_basis": coaching_basis,
        "naive_control_basis": naive_basis,
        "own_games_used_coaching_aware": coaching_own_n,
        "own_games_used_naive_control": naive_own_n,
        "coaching_feature_changed_the_projection": (
            coaching_predicted is not None and naive_predicted is not None
            and coaching_predicted != naive_predicted
        ),
        "regime_note": regime_note,
    }


# ---------------------------------------------------------------------------
# Player opportunity: current-season-aware target share and catch rate
# (Section 5)
# ---------------------------------------------------------------------------

def _shrunk_estimate(
    *, current_values: Sequence[float], prior_value: float | None, shrinkage_k: float,
) -> dict[str, Any]:
    """Real sample-size-based shrinkage blend of a current-season mean
    toward a strictly-prior-season value, never toward an arbitrary
    constant. `shrinkage_k` is the number of current-season games at which
    the current-season mean and the prior value get equal weight (a
    pre-declared smoothing constant, not fit to any evaluation data).

    Returns `estimate: None` only when BOTH the current-season sample and
    the prior value are unavailable -- never a fabricated 0.0.
    """
    n_current = len(current_values)
    current_mean = (sum(current_values) / n_current) if n_current else None

    if current_mean is None and prior_value is None:
        return {"estimate": None, "n_current_season_games": 0, "basis": "NO_REAL_HISTORY_AT_ANY_LEVEL"}
    if current_mean is None:
        return {"estimate": prior_value, "n_current_season_games": 0, "basis": "PRIOR_SEASON_ONLY"}
    if prior_value is None:
        return {"estimate": current_mean, "n_current_season_games": n_current, "basis": "CURRENT_SEASON_ONLY"}

    weight_current = n_current / (n_current + shrinkage_k)
    blended = weight_current * current_mean + (1.0 - weight_current) * prior_value
    return {
        "estimate": blended,
        "n_current_season_games": n_current,
        "current_season_mean": current_mean,
        "prior_season_value": prior_value,
        "weight_current_season": weight_current,
        "basis": "SHRUNK_CURRENT_TOWARD_PRIOR_SEASON",
    }


def estimate_current_week_target_share(
    *,
    player_id: str,
    target_share_history: list[tuple[int, int, float]],
    target_season: int,
    target_week: int,
    shrinkage_k: float = 3.0,
) -> dict[str, Any]:
    """Real current-season-aware target-share estimate for one player's
    upcoming (target_season, target_week) game.

    `target_share_history` is `role_intelligence_features.
    build_player_dimension_history(...)[(player_id, "target_share")]` --
    real, already-computed, already-tested realized per-game shares, used
    read-only here. This function does not recompute a single share value;
    it only slices and shrinks an already-real historical series.
    """
    if shrinkage_k <= 0:
        raise TeamOpportunityChallengerError("shrinkage_k must be positive")
    prior_games = [
        share for (season, week, share) in target_share_history
        if (season, week) < (target_season, target_week)
    ]
    current_season_values = [
        share for (season, week, share) in target_share_history
        if season == target_season and week < target_week
    ]
    prior_season_values = [
        share for (season, week, share) in target_share_history if season == target_season - 1
    ]
    prior_value = (sum(prior_season_values) / len(prior_season_values)) if prior_season_values else (
        (sum(prior_games[-5:]) / len(prior_games[-5:])) if prior_games else None
    )
    result = _shrunk_estimate(
        current_values=current_season_values, prior_value=prior_value, shrinkage_k=shrinkage_k,
    )
    share = result["estimate"]
    if share is not None and not (0.0 <= share <= 1.0):
        raise TeamOpportunityChallengerError(
            f"impossible target share {share!r} for player {player_id!r} -- "
            "must be within [0, 1]; this indicates an upstream data-integrity bug, "
            "not a value to silently clip"
        )
    result["player_id"] = player_id
    result["target_season"] = target_season
    result["target_week"] = target_week
    return result


def estimate_current_week_catch_rate(
    *,
    player_id: str,
    game_log: Sequence[Mapping[str, Any]],
    target_season: int,
    target_week: int,
    shrinkage_k: float = 5.0,
) -> dict[str, Any]:
    """Real current-season-aware catch-rate (receptions / targets) estimate.

    `game_log` is a real, already-fetched per-game row sequence with
    `season`, `week`, `targets`, `receptions` -- the same shape
    `receptions_shadow.current_b0_projection` already consumes elsewhere in
    this codebase, reused here rather than a new schema.
    """
    if shrinkage_k <= 0:
        raise TeamOpportunityChallengerError("shrinkage_k must be positive")

    def _rate_games(rows: Sequence[Mapping[str, Any]]) -> list[float]:
        return [
            row["receptions"] / row["targets"]
            for row in rows
            if row.get("targets") and row["targets"] > 0
        ]

    prior_games = [r for r in game_log if (r["season"], r["week"]) < (target_season, target_week)]
    current_season_rates = _rate_games(
        [r for r in prior_games if r["season"] == target_season]
    )
    prior_season_rows = [r for r in prior_games if r["season"] == target_season - 1]
    prior_season_rates = _rate_games(prior_season_rows)
    if prior_season_rates:
        prior_value = sum(prior_season_rates) / len(prior_season_rates)
    else:
        fallback_rates = _rate_games(prior_games[-5:])
        prior_value = (sum(fallback_rates) / len(fallback_rates)) if fallback_rates else None

    result = _shrunk_estimate(
        current_values=current_season_rates, prior_value=prior_value, shrinkage_k=shrinkage_k,
    )
    rate = result["estimate"]
    if rate is not None and not (0.0 <= rate <= 1.0):
        raise TeamOpportunityChallengerError(
            f"impossible catch rate {rate!r} for player {player_id!r} -- "
            "must be within [0, 1]; this indicates an upstream data-integrity bug, "
            "not a value to silently clip"
        )
    result["player_id"] = player_id
    result["target_season"] = target_season
    result["target_week"] = target_week
    return result


# ---------------------------------------------------------------------------
# Compose: team volume x player share x catch rate -> projection -> record
# (Sections 4-7)
# ---------------------------------------------------------------------------

def compute_opportunity_projection(
    *, predicted_team_dropbacks: float | None, target_share: float | None, catch_rate: float | None,
) -> dict[str, Any]:
    """Compose the full chain into one expected-receptions projection.

    Returns `projection: None` (never 0.0 or a fabricated number) if any
    required real input is missing or non-positive -- an opportunity
    engine that cannot see a real team volume, a real share, or a real
    catch rate must abstain, not guess.
    """
    if predicted_team_dropbacks is None or target_share is None or catch_rate is None:
        return {"projection": None, "expected_targets": None, "reason": "MISSING_REQUIRED_INPUT"}
    if not (predicted_team_dropbacks > 0):
        return {"projection": None, "expected_targets": None, "reason": "NON_POSITIVE_TEAM_DROPBACKS"}
    if not (0.0 < target_share <= 1.0):
        return {"projection": None, "expected_targets": None, "reason": "TARGET_SHARE_OUT_OF_RANGE"}
    if not (0.0 < catch_rate <= 1.0):
        return {"projection": None, "expected_targets": None, "reason": "CATCH_RATE_OUT_OF_RANGE"}

    expected_targets = predicted_team_dropbacks * target_share
    projection = expected_targets * catch_rate
    if not (projection > 0):
        return {"projection": None, "expected_targets": expected_targets, "reason": "NON_POSITIVE_PROJECTION"}
    return {"projection": projection, "expected_targets": expected_targets, "reason": None}


def opportunity_side_probabilities_for_lines(
    *,
    projection: float,
    lines: Sequence[float],
    over_odds_by_line: dict[float, Any],
    under_odds_by_line: dict[float, Any],
    residuals: Sequence[float],
) -> dict[float, dict[str, Any]]:
    """Score every real offered line (standard + alternate) from the SAME
    opportunity projection and the SAME residual distribution -- proves
    coherence by construction, exactly like every other challenger in this
    repo (`receptions_role_adjusted_challenger.
    role_adjusted_side_probabilities_for_lines`, `receptions_shadow.
    score_shadow_candidate` itself for B0).
    """
    if not lines:
        raise TeamOpportunityChallengerError("at least one line is required")
    out: dict[float, dict[str, Any]] = {}
    for line in lines:
        if line not in over_odds_by_line or line not in under_odds_by_line:
            raise TeamOpportunityChallengerError(f"missing real odds for line {line!r}")
        out[line] = score_shadow_candidate(
            projection=projection, line=line,
            over_odds=over_odds_by_line[line], under_odds=under_odds_by_line[line],
            residuals=residuals,
        )
    return out


def build_opportunity_challenger_record(
    *,
    candidate_player_id: str,
    candidate_team: str,
    matchup_row: Mapping[str, Any],
    side: str,
    team_box_score_rows: Sequence[Mapping[str, Any]],
    hc_intervals: Sequence[Any],
    game_date_index: Mapping[tuple[str, int, int], date],
    target_share_history: list[tuple[int, int, float]],
    catch_rate_game_log: Sequence[Mapping[str, Any]],
    target_season: int,
    target_week: int,
    line: float,
    over_odds: Any,
    under_odds: Any,
    residuals: Sequence[float],
    rolling_window: int = 5,
    b0_projection: float | None = None,
) -> dict[str, Any] | None:
    """Assemble one real opportunity-engine challenger record for a single
    real receptions candidate at one real offered line, or None if any
    required real input is missing (never a fabricated record).

    REAL SOURCE (matchup row, raw team box scores, target-share history,
    catch-rate game log, all caller-supplied from real nflverse-derived
    data) -> VERIFIED IDENTITY/TIMING (candidate_player_id/team,
    target_season/week) -> FEATURE (coaching-regime-aware team dropback
    volume, current-season-aware share/rate) -> OPPORTUNITY PROJECTION
    (compute_opportunity_projection's absolute derivation, not a B0
    rescale) -> OUTCOME DISTRIBUTION (score_shadow_candidate, reused
    unmodified) -> this frozen record.

    The team-volume feature is the COACHING-AWARE prediction from
    `predict_team_pass_dropbacks_coaching_aware` -- this is what Section
    6/PR #179's follow-on condition (Issue #91 comment `5797780943`)
    requires: the coaching signal actually changes the projection this
    function computes, not just metadata attached alongside it. The
    otherwise-identical `naive_control` prediction (same real data, no
    regime filtering) is preserved in the record for direct before/after
    comparison, never discarded.
    """
    opponent = "away" if side == "home" else "home"
    opponent_allowed = matchup_row[f"{opponent}_defense_prior_mean_opp_dropback_proxy_allowed"]
    opponent_n = matchup_row[f"{opponent}_defense_prior_games_n"]

    team_dropbacks_info = predict_team_pass_dropbacks_coaching_aware(
        team_box_score_rows, team=candidate_team, target_season=target_season, target_week=target_week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
        opponent_defense_allowed=opponent_allowed, opponent_defense_prior_games_n=opponent_n,
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
    projection_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_dropbacks_info["predicted_dropbacks_coaching_aware"],
        target_share=target_share_info["estimate"],
        catch_rate=catch_rate_info["estimate"],
    )
    projection = projection_info["projection"]
    if projection is None:
        return None

    # Real before/after: the SAME target share and catch rate, but the
    # naive-control (no coaching filter) team-volume number -- isolates
    # exactly what the coaching feature changed, never fabricated.
    control_projection_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_dropbacks_info["predicted_dropbacks_naive_control"],
        target_share=target_share_info["estimate"],
        catch_rate=catch_rate_info["estimate"],
    )
    control_score = None
    if control_projection_info["projection"] is not None:
        control_score = score_shadow_candidate(
            projection=control_projection_info["projection"], line=line,
            over_odds=over_odds, under_odds=under_odds, residuals=residuals,
        )

    score = score_shadow_candidate(
        projection=projection, line=line, over_odds=over_odds, under_odds=under_odds, residuals=residuals,
    )
    return {
        "candidate_player_id": candidate_player_id,
        "candidate_team": candidate_team,
        "target_season": target_season,
        "target_week": target_week,
        "b0_projection": b0_projection,
        "opportunity_projection": projection,
        "expected_targets": projection_info["expected_targets"],
        "team_dropbacks": team_dropbacks_info,
        "target_share": target_share_info,
        "catch_rate": catch_rate_info,
        "naive_control_projection": control_projection_info["projection"],
        "naive_control_probabilities": control_score,
        "coaching_feature_changed_the_projection": team_dropbacks_info["coaching_feature_changed_the_projection"],
        **score,
        "prediction_source": "B0_VS_TEAM_OPPORTUNITY_ENGINE_V1",
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


__all__ = [
    "TeamOpportunityChallengerError",
    "predict_team_pass_dropbacks",
    "filter_team_rows_by_current_regime",
    "predict_team_pass_dropbacks_coaching_aware",
    "estimate_current_week_target_share",
    "estimate_current_week_catch_rate",
    "compute_opportunity_projection",
    "opportunity_side_probabilities_for_lines",
    "build_opportunity_challenger_record",
]
