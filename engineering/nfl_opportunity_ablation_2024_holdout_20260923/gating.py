"""Gating helper for the snap-share role-change hypothesis under test in
this workstream (`NFL-OPPORTUNITY-ABLATION-2024-HOLDOUT-20260923`).

The existing `receptions_team_opportunity_challenger.apply_snap_informed_
target_share` (already merged, read-only reuse only, never modified here)
applies its snap-share role-change rescale to essentially every eligible
row -- it is not gated behind any "large real change only" threshold. Per
Issue #91 comment `5799901415` (SUPERCHAD's own suggestion, echoed in the
`NFL-MISSION7-PARALLEL-20260923` AGENT CLAIM, comment `5800036689`), the
open question is whether role signals have value specifically under
EVIDENCED role-change situations rather than being forced onto the entire
population.

`gated_snap_informed_target_share` below is the one genuinely new piece of
predictive logic this workstream introduces: it decides, from the real,
UNCLAMPED role-change ratio (`current_season_mean / prior_season_value`,
read directly off `estimate_current_week_snap_share`'s own return dict
before any clamping), whether a row's real role-change signal is "large
enough" to apply the adjustment at all. When it is, the actual rescale
math is delegated unchanged to `apply_snap_informed_target_share` -- this
function never reimplements that arithmetic, only adds a gate in front of
it. When the ratio falls inside the gate band (or no real snap signal is
available at all), the row is left at its unadjusted target-share estimate,
exactly like `apply_snap_informed_target_share`'s own fallback behavior for
a missing signal.

Never fabricates a ratio: a row with no real current-season snap game or no
real positive prior-season baseline is treated the same way
`apply_snap_informed_target_share` treats it -- left unadjusted, with the
reason disclosed -- never coerced into "inside the gate" or "outside the
gate" from a value that doesn't exist.
"""
from __future__ import annotations

from typing import Any

from nfl.research.receptions_team_opportunity_challenger import (
    MIN_CURRENT_SEASON_SNAP_GAMES_FOR_ROLE_CHANGE_SIGNAL,
    TeamOpportunityChallengerError,
    apply_snap_informed_target_share,
)


def gated_snap_informed_target_share(
    *,
    target_share_info: dict[str, Any],
    snap_share_info: dict[str, Any],
    gate_low: float,
    gate_high: float,
) -> dict[str, Any]:
    """Apply the real snap-informed target-share adjustment ONLY when the
    real, unclamped role-change ratio is more extreme than `[gate_low,
    gate_high]`. Otherwise returns `target_share_info` unchanged (the
    "otherwise-identical simpler control": leave the row at its unadjusted,
    shrinkage-blended target share).

    `gate_low`/`gate_high` are the caller's pre-declared, real threshold
    band (e.g. `(0.7, 1.4)` or `(0.5, 2.0)`) -- this function does not pick
    or fit a threshold itself.

    Always adds `gate_triggered` (bool) and `gate_raw_ratio` (the real
    unclamped ratio actually observed, or `None` when no real signal
    existed to gate on) to the returned dict, so a caller can tell a
    genuine "ratio observed but inside the band" row apart from a "no real
    signal at all" row even though both leave the target share unadjusted.
    """
    if gate_low > gate_high:
        raise TeamOpportunityChallengerError("gate_low must be <= gate_high")

    target_share = target_share_info.get("estimate")
    if target_share is None:
        return dict(
            target_share_info,
            snap_role_change_applied=False,
            snap_role_change_ratio=None,
            gate_triggered=False,
            gate_raw_ratio=None,
            gate_reason="NO_TARGET_SHARE_ESTIMATE",
        )

    snap_current_n = snap_share_info.get("n_current_season_games", 0)
    snap_prior_value = snap_share_info.get("prior_season_value")
    snap_current_mean = snap_share_info.get("current_season_mean")
    have_real_signal = (
        snap_current_n >= MIN_CURRENT_SEASON_SNAP_GAMES_FOR_ROLE_CHANGE_SIGNAL
        and snap_prior_value is not None and snap_prior_value > 0
        and snap_current_mean is not None
    )
    if not have_real_signal:
        return dict(
            target_share_info,
            snap_role_change_applied=False,
            snap_role_change_ratio=None,
            gate_triggered=False,
            gate_raw_ratio=None,
            gate_reason="NO_REAL_SNAP_SIGNAL",
        )

    raw_ratio = snap_current_mean / snap_prior_value
    if gate_low <= raw_ratio <= gate_high:
        return dict(
            target_share_info,
            snap_role_change_applied=False,
            snap_role_change_ratio=None,
            gate_triggered=False,
            gate_raw_ratio=raw_ratio,
            gate_reason="WITHIN_GATE_BAND",
        )

    adjusted = apply_snap_informed_target_share(
        target_share_info=target_share_info, snap_share_info=snap_share_info,
    )
    adjusted["gate_triggered"] = True
    adjusted["gate_raw_ratio"] = raw_ratio
    adjusted["gate_reason"] = "OUTSIDE_GATE_BAND"
    return adjusted


__all__ = ["gated_snap_informed_target_share"]
