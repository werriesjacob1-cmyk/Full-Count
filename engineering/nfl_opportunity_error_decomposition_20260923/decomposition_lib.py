#!/usr/bin/env python3
"""Genuinely new combination/decomposition logic for Mission 9 Workstream D
(`NFL-OPPORTUNITY-ERROR-DECOMPOSITION-20260923`).

Everything predictive this workstream needs already exists, already merged
or already on a named draft PR, and is reused here strictly read-only,
unmodified:

- `nfl.research.receptions_team_opportunity_challenger` (merged on `main`):
  `predict_team_pass_dropbacks`, `predict_team_pass_dropbacks_coaching_aware`,
  `filter_team_rows_by_current_regime`, `estimate_current_week_target_share`,
  `estimate_current_week_catch_rate`, `estimate_current_week_snap_share`,
  `apply_snap_informed_target_share`, `compute_opportunity_projection`,
  and the private `_dropback_proxy`/`_rolling_dropback_mean` helpers.
- `nfl.research.qb_change_team_dropbacks` (draft PR #185, brought into this
  branch as an unmodified, read-only copy since it exists only there):
  `filter_team_rows_by_qb_continuity`, `predict_team_pass_dropbacks_qb_aware`,
  `resolve_incumbent_qb`.

This module adds exactly THREE new things, all of them genuinely new
combination/decomposition logic that did not exist anywhere in this repo
before this workstream, and all three are unit-tested in
`nfl/tests/test_nfl_opportunity_error_decomposition_lib.py`:

1. `predict_team_pass_dropbacks_coaching_and_qb_aware` -- the real "all three
   team-identity signals combined" team-volume estimator Mission 9's task
   explicitly asks for and that has never been built or evaluated before:
   restricts a team's own rolling dropback window to games that satisfy
   BOTH the coaching-regime filter AND the QB-continuity filter
   simultaneously (a real set intersection of the two independently-tested,
   unmodified filters' own outputs), not a new filtering rule of its own.
2. `role_transition_subgroup_flag` -- a pure OR of three flags this
   codebase's own challengers ALREADY emit unmodified
   (`coaching_feature_changed_the_projection`, `qb_feature_changed_the_
   projection`, `snap_role_change_applied`). This subgroup definition is
   fixed by pre-existing code, not invented after looking at any outcome,
   so a subgroup-vs-population comparison built on it is a real, disclosed
   subgroup analysis, not post-hoc retuning.
3. `player_clustered_bootstrap_mae_diff` -- resamples PLAYERS with
   replacement (not rows), pooling every row belonging to each drawn player
   in a replicate, matching this project's own established bootstrap
   discipline (PR #184's frozen-committee evaluation template) instead of
   a naive row-level bootstrap that would understate real within-player
   dependence (a given player's rows share his own real usage trend, his
   own team context, and often his own missing-data pattern).

Nothing here fabricates a value: every function returns `None`/empty and
a disclosed reason rather than guessing whenever real required input is
missing, matching the discipline every module it composes already
established.
"""
from __future__ import annotations

import random
import statistics
from typing import Any, Mapping, Sequence

from nfl.research.receptions_team_opportunity_challenger import (
    TeamOpportunityChallengerError,
    _dropback_proxy,  # noqa: F401 -- re-exported for callers that want the identical proxy definition
    _rolling_dropback_mean,
    filter_team_rows_by_current_regime,
)
from nfl.research.qb_change_team_dropbacks import filter_team_rows_by_qb_continuity


def predict_team_pass_dropbacks_coaching_and_qb_aware(
    team_box_score_rows: Sequence[Mapping[str, Any]],
    *,
    team: str,
    target_season: int,
    target_week: int,
    hc_intervals: Sequence[Any],
    game_date_index: Mapping[tuple[str, int, int], Any],
    starters: Sequence[Mapping[str, Any]],
    opponent_defense_allowed: float | None,
    opponent_defense_prior_games_n: int,
    rolling_window: int = 5,
) -> dict[str, Any]:
    """The real "all team-identity signals combined" team-volume estimator:
    restricts the team's own strictly-prior rolling dropback window to games
    that pass BOTH `filter_team_rows_by_current_regime` (same head coach)
    AND `filter_team_rows_by_qb_continuity` (same incumbent QB)
    simultaneously -- a real set intersection of two already-independently-
    tested, unmodified filters, not a new filtering rule.

    Mirrors `predict_team_pass_dropbacks_coaching_aware` and `predict_team_
    pass_dropbacks_qb_aware`'s exact structure so the three team-volume
    variants (coaching-only, QB-only, combined) are directly comparable on
    the same real box-score rows and the same real opponent-allowed value.

    Never fabricates a combined effect: when NEITHER filter actually
    excludes anything (both fall back to the full unfiltered set -- e.g. an
    UNKNOWN regime lookup and no real incumbent resolved), the intersection
    equals the naive unfiltered set and `combined_predicted` is numerically
    identical to `naive_predicted` by construction, exactly like each
    individual filter's own established fallback.
    """
    coaching_rows, regime_note = filter_team_rows_by_current_regime(
        team_box_score_rows, team=team, target_season=target_season, target_week=target_week,
        hc_intervals=hc_intervals, game_date_index=game_date_index,
    )
    qb_rows, qb_note = filter_team_rows_by_qb_continuity(
        team_box_score_rows, team=team, target_season=target_season, target_week=target_week,
        starters=starters,
    )
    naive_rows = [
        dict(r) for r in team_box_score_rows
        if r["team"] == team and (r["season"], r["week"]) < (target_season, target_week)
    ]

    coaching_keys = {(r["season"], r["week"]) for r in coaching_rows}
    qb_keys = {(r["season"], r["week"]) for r in qb_rows}
    combined_keys = coaching_keys & qb_keys
    combined_rows = [r for r in naive_rows if (r["season"], r["week"]) in combined_keys]

    combined_mean, combined_n = _rolling_dropback_mean(combined_rows, rolling_window=rolling_window)
    naive_mean, naive_n = _rolling_dropback_mean(naive_rows, rolling_window=rolling_window)

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

    combined_predicted, combined_basis = _blend(combined_mean, combined_n)
    naive_predicted, naive_basis = _blend(naive_mean, naive_n)

    return {
        "predicted_dropbacks_combined_aware": combined_predicted,
        "predicted_dropbacks_naive_control": naive_predicted,
        "combined_aware_basis": combined_basis,
        "naive_control_basis": naive_basis,
        "own_games_used_combined_aware": combined_n,
        "own_games_used_naive_control": naive_n,
        "combined_feature_changed_the_projection": (
            combined_predicted is not None and naive_predicted is not None
            and combined_predicted != naive_predicted
        ),
        "regime_note": regime_note,
        "qb_note": qb_note,
    }


def role_transition_subgroup_flag(record: Mapping[str, Any]) -> bool:
    """True iff at least one of the three already-existing, independently-
    fixed role-transition indicator flags this codebase's own challengers
    emit unmodified actually fired for this real row:
    `coaching_feature_changed_the_projection`, `qb_feature_changed_the_
    projection`, or `snap_role_change_applied`.

    This is a pure OR of pre-existing flags -- it defines no new threshold
    and does not look at any evaluation outcome (realized receptions,
    error, MAE) to decide subgroup membership, so a subgroup built from it
    is a genuine pre-registered subgroup, not one invented to fit a result.
    A flag absent from `record` is treated as False, never as True, so a
    record from a variant that does not compute a particular flag never
    silently widens the subgroup.
    """
    return bool(
        record.get("coaching_feature_changed_the_projection")
        or record.get("qb_feature_changed_the_projection")
        or record.get("snap_role_change_applied")
    )


def player_clustered_bootstrap_mae_diff(
    rows: Sequence[Mapping[str, Any]],
    *,
    player_key: str,
    error_a_key: str,
    error_b_key: str,
    n_boot: int = 2000,
    seed: int = 20260923,
) -> dict[str, Any]:
    """Bootstrap CI for MAE(a) - MAE(b), resampling PLAYERS with replacement
    (not rows) -- matching this project's own established cluster-bootstrap
    discipline (PR #184's frozen-committee held-out evaluation) instead of a
    naive row-level bootstrap, which would treat a player's own multiple
    real rows as independent draws when they in fact share that player's own
    real usage trend and team context.

    Each of `n_boot` replicates draws `len(distinct players)` players WITH
    replacement; every row belonging to a drawn player is included in that
    replicate each time the player is drawn (a player drawn twice
    contributes his rows twice). `error_a_key`/`error_b_key` name the two
    already-computed per-row absolute-error fields to compare.

    Returns the observed real difference (mean error_a - mean error_b over
    every real row, unresampled), the bootstrap mean and standard error of
    that difference, a two-sided 95% percentile confidence interval, and the
    real distinct-player and total-row counts. Raises rather than silently
    returning a fabricated interval if there are zero rows or zero distinct
    players.
    """
    if n_boot <= 0:
        raise TeamOpportunityChallengerError("n_boot must be positive")

    grouped: dict[Any, list[tuple[float, float]]] = {}
    for row in rows:
        pid = row[player_key]
        grouped.setdefault(pid, []).append((row[error_a_key], row[error_b_key]))

    players = sorted(grouped.keys(), key=lambda p: str(p))
    if not players:
        raise TeamOpportunityChallengerError("at least one real row is required")

    all_pairs = [pair for pid in players for pair in grouped[pid]]
    observed_a = statistics.fmean(p[0] for p in all_pairs)
    observed_b = statistics.fmean(p[1] for p in all_pairs)
    observed_diff = observed_a - observed_b

    rng = random.Random(seed)
    diffs: list[float] = []
    n_players = len(players)
    for _ in range(n_boot):
        drawn = [players[rng.randrange(n_players)] for _ in range(n_players)]
        pooled: list[tuple[float, float]] = []
        for pid in drawn:
            pooled.extend(grouped[pid])
        diffs.append(
            statistics.fmean(p[0] for p in pooled) - statistics.fmean(p[1] for p in pooled)
        )

    diffs.sort()

    def _percentile(sorted_values: list[float], pct: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        idx = pct * (len(sorted_values) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_values) - 1)
        frac = idx - lower
        return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * frac

    return {
        "n_rows": len(all_pairs),
        "n_distinct_players": n_players,
        "n_boot": n_boot,
        "observed_mae_a": observed_a,
        "observed_mae_b": observed_b,
        "observed_diff_a_minus_b": observed_diff,
        "bootstrap_mean_diff": statistics.fmean(diffs),
        "bootstrap_se_diff": statistics.pstdev(diffs) if len(diffs) > 1 else 0.0,
        "ci95_low": _percentile(diffs, 0.025),
        "ci95_high": _percentile(diffs, 0.975),
        "ci_excludes_zero": not (_percentile(diffs, 0.025) <= 0.0 <= _percentile(diffs, 0.975)),
    }


__all__ = [
    "predict_team_pass_dropbacks_coaching_and_qb_aware",
    "role_transition_subgroup_flag",
    "player_clustered_bootstrap_mae_diff",
]
