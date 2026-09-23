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
    own_rows = [dict(r) for r in team_box_score_rows if r["team"] == team]
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
    target_share_history: list[tuple[int, int, float]],
    catch_rate_game_log: Sequence[Mapping[str, Any]],
    target_season: int,
    target_week: int,
    line: float,
    over_odds: Any,
    under_odds: Any,
    residuals: Sequence[float],
    b0_projection: float | None = None,
    regime_note: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Assemble one real opportunity-engine challenger record for a single
    real receptions candidate at one real offered line, or None if any
    required real input is missing (never a fabricated record).

    REAL SOURCE (matchup row, target-share history, catch-rate game log,
    all caller-supplied from real nflverse-derived data) -> VERIFIED
    IDENTITY/TIMING (candidate_player_id/team, target_season/week) ->
    FEATURE (team dropback volume, current-season-aware share/rate) ->
    OPPORTUNITY PROJECTION (compute_opportunity_projection's absolute
    derivation, not a B0 rescale) -> OUTCOME DISTRIBUTION
    (score_shadow_candidate, reused unmodified) -> this frozen record.
    """
    team_dropbacks_info = predict_team_pass_dropbacks(matchup_row, side=side)
    target_share_info = estimate_current_week_target_share(
        player_id=candidate_player_id, target_share_history=target_share_history,
        target_season=target_season, target_week=target_week,
    )
    catch_rate_info = estimate_current_week_catch_rate(
        player_id=candidate_player_id, game_log=catch_rate_game_log,
        target_season=target_season, target_week=target_week,
    )
    projection_info = compute_opportunity_projection(
        predicted_team_dropbacks=team_dropbacks_info["predicted_dropbacks"],
        target_share=target_share_info["estimate"],
        catch_rate=catch_rate_info["estimate"],
    )
    projection = projection_info["projection"]
    if projection is None:
        return None

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
        "coaching_regime_note": regime_note,
        **score,
        "prediction_source": "B0_VS_TEAM_OPPORTUNITY_ENGINE_V1",
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
    }


__all__ = [
    "TeamOpportunityChallengerError",
    "predict_team_pass_dropbacks",
    "filter_team_rows_by_current_regime",
    "estimate_current_week_target_share",
    "estimate_current_week_catch_rate",
    "compute_opportunity_projection",
    "opportunity_side_probabilities_for_lines",
    "build_opportunity_challenger_record",
]
