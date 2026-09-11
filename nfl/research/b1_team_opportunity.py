#!/usr/bin/env python3
"""B1 causal opportunity decomposition for NFL V0 research.

B1 improves on the player rolling-average baseline by decomposing outcomes into:
- projected TEAM opportunity
- PRIOR player share of that opportunity
- PRIOR player efficiency

This module is deliberately pure. It accepts already-built features and returns
predictions. It does not fetch, join, fit, select, rank, publish, or use prices.
"""
from __future__ import annotations

import math
from typing import Mapping, Any


def _feature(features: Mapping[str, Any], name: str) -> float:
    if name not in features or features[name] is None:
        raise ValueError(f"missing B1 feature: {name}")
    try:
        value = float(features[name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric B1 feature: {name}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite B1 feature: {name}")
    return value


def _nonnegative(features: Mapping[str, Any], name: str) -> float:
    value = _feature(features, name)
    if value < 0:
        raise ValueError(f"negative B1 feature: {name}")
    return value


def _share(features: Mapping[str, Any], name: str) -> float:
    value = _feature(features, name)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"share outside [0, 1]: {name}")
    return value


def predict_pass_attempts(features: Mapping[str, Any]) -> float:
    team_attempts = _nonnegative(features, "projected_team_pass_attempts")
    share = _share(features, "prior_player_pass_attempt_share")
    return team_attempts * share


def predict_rush_attempts(features: Mapping[str, Any]) -> float:
    team_carries = _nonnegative(features, "projected_team_carries")
    share = _share(features, "prior_player_carry_share")
    return team_carries * share


def predict_receptions(features: Mapping[str, Any]) -> float:
    team_targets = _nonnegative(features, "projected_team_targets")
    target_share = _share(features, "prior_player_target_share")
    catch_rate = _share(features, "prior_player_catch_rate")
    return team_targets * target_share * catch_rate


def predict_receiving_yards(features: Mapping[str, Any]) -> float:
    team_targets = _nonnegative(features, "projected_team_targets")
    target_share = _share(features, "prior_player_target_share")
    ypt = _nonnegative(features, "prior_player_yards_per_target")
    return team_targets * target_share * ypt


def predict_passing_yards(features: Mapping[str, Any]) -> float:
    team_attempts = _nonnegative(features, "projected_team_pass_attempts")
    attempt_share = _share(features, "prior_player_pass_attempt_share")
    ypa = _nonnegative(features, "prior_player_pass_yards_per_attempt")
    return team_attempts * attempt_share * ypa


def predict_rushing_yards(features: Mapping[str, Any]) -> float:
    team_carries = _nonnegative(features, "projected_team_carries")
    carry_share = _share(features, "prior_player_carry_share")
    ypc = _nonnegative(features, "prior_player_rush_yards_per_carry")
    return team_carries * carry_share * ypc
