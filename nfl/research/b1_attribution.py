#!/usr/bin/env python3
"""Scientific attribution identities for NFL V0 B1.

Current B1 uses the same prior-player window as B0. That creates an important
algebraic identity which must be made explicit before further model stacking:

    B1 outcome
      = B0 rolling outcome
        * (B1 projected player opportunity / B0 rolling player opportunity)

for all six first-wave markets, provided the B1 efficiency term was constructed
from the same prior games via ratio-of-sums.

Therefore the present B1-vs-B0 difference is an OPPORTUNITY-FORECAST change.
The efficiency factor is not, by itself, a new source of predictive signal.

A second identity shows what the opportunity change actually is:

    B1 player opportunity
      = B0 rolling player opportunity
        * (current-team rolling volume / player-window team volume)

where player-window team volume is recovered exactly as:

    B0 rolling player opportunity / prior player share.

So if a player's prior-game window lines up with the current team's prior-game
window, current B1 collapses to B0. Differences arise when those windows differ
(e.g. missed games, team changes, or other role-history discontinuities).

This module is research-only. It fetches nothing, tunes nothing, and does not
select, publish, or promote picks.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from nfl.research import b1_evaluate as ev
from nfl.research import v0_baseline as b0


# market -> (B0 player-opportunity feature, B1 team-volume feature,
#            B1 player-share feature)
OPPORTUNITY_SPEC = {
    "pass_attempts": (
        "rolling_attempts",
        "projected_team_pass_attempts",
        "prior_player_pass_attempt_share",
    ),
    "passing_yards": (
        "rolling_attempts",
        "projected_team_pass_attempts",
        "prior_player_pass_attempt_share",
    ),
    "rush_attempts": (
        "rolling_carries",
        "projected_team_carries",
        "prior_player_carry_share",
    ),
    "rushing_yards": (
        "rolling_carries",
        "projected_team_carries",
        "prior_player_carry_share",
    ),
    "receptions": (
        "rolling_targets",
        "projected_team_targets",
        "prior_player_target_share",
    ),
    "receiving_yards": (
        "rolling_targets",
        "projected_team_targets",
        "prior_player_target_share",
    ),
}


def _finite(mapping: Mapping[str, Any], name: str, label: str) -> float:
    if name not in mapping or mapping[name] is None:
        raise ValueError(f"missing {label}: {name}")
    try:
        value = float(mapping[name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric {label}: {name}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite {label}: {name}")
    return value


def _market_spec(market: str) -> tuple[str, str, str]:
    if market not in OPPORTUNITY_SPEC or market not in ev.MARKETS:
        raise ValueError(f"unsupported B1 attribution market: {market}")
    return OPPORTUNITY_SPEC[market]


def opportunity_components(
    row: Mapping[str, Any],
    market: str,
) -> dict[str, float]:
    """Return the exact opportunity rescaling components for one B1 row.

    This function assumes a row on the B0/B1 comparable population, where B0
    prior opportunity is positive. It fails closed if the row contradicts that
    invariant instead of inventing a denominator.
    """
    b0_role_name, team_name, share_name = _market_spec(market)
    b0_features = row.get("b0_features") or {}
    features = row.get("features") or {}

    b0_player_opportunity = _finite(
        b0_features, b0_role_name, "B0 opportunity feature"
    )
    if b0_player_opportunity <= 0.0:
        raise ValueError("B0 player opportunity must be positive for attribution")

    projected_team_volume = _finite(
        features, team_name, "B1 team-volume feature"
    )
    if projected_team_volume < 0.0:
        raise ValueError("B1 projected team volume must be nonnegative")

    prior_player_share = _finite(
        features, share_name, "B1 player-share feature"
    )
    if prior_player_share < 0.0 or prior_player_share > 1.0:
        raise ValueError("B1 prior player share must be within [0, 1]")
    if prior_player_share == 0.0:
        raise ValueError(
            "inconsistent positive B0 opportunity with zero B1 player share"
        )

    # From share = sum(player opportunity) / sum(team opportunity), and
    # B0 rolling opportunity = sum(player opportunity) / n.
    player_window_team_volume = b0_player_opportunity / prior_player_share

    b1_player_opportunity = projected_team_volume * prior_player_share
    opportunity_scale = b1_player_opportunity / b0_player_opportunity
    team_window_scale = projected_team_volume / player_window_team_volume

    # These are the same algebraic quantity. Keep the cross-check local so a
    # future refactor cannot silently change the semantics.
    if not math.isclose(
        opportunity_scale,
        team_window_scale,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("B1 opportunity attribution identity violated")

    return {
        "b0_player_opportunity": b0_player_opportunity,
        "projected_team_volume": projected_team_volume,
        "prior_player_share": prior_player_share,
        "player_window_team_volume": player_window_team_volume,
        "b1_player_opportunity": b1_player_opportunity,
        "opportunity_scale": opportunity_scale,
        "team_window_scale": team_window_scale,
    }


def b0_prediction(row: Mapping[str, Any], market: str) -> float:
    """Return the frozen B0 rolling-outcome prediction for one market row."""
    _market_spec(market)
    stat, _ = ev.MARKETS[market]
    return _finite(
        row.get("b0_features") or {},
        f"rolling_{stat}",
        "B0 outcome feature",
    )


def b1_prediction(row: Mapping[str, Any], market: str) -> float:
    """Return the existing B1 prediction without changing B1 semantics."""
    _market_spec(market)
    _, predictor = ev.MARKETS[market]
    value = float(predictor(row.get("features") or {}))
    if not math.isfinite(value):
        raise ValueError(f"non-finite B1 prediction for {market}")
    return value


def reconstruct_b1_from_b0(
    row: Mapping[str, Any],
    market: str,
) -> float:
    """Reconstruct B1 solely from B0 outcome and opportunity rescaling."""
    base = b0_prediction(row, market)
    scale = opportunity_components(row, market)["opportunity_scale"]
    value = base * scale
    if not math.isfinite(value):
        raise ValueError(f"non-finite reconstructed B1 prediction for {market}")
    return value


def efficiency_identity_error(
    row: Mapping[str, Any],
    market: str,
) -> float:
    """Return B1 minus its opportunity-only reconstruction.

    On rows produced by the current B1 builder this should be numerically zero.
    A non-zero value means efficiency has become an independent model component
    (or the historical windows no longer align), which requires a new
    attribution design rather than silently reusing this conclusion.
    """
    return b1_prediction(row, market) - reconstruct_b1_from_b0(row, market)
