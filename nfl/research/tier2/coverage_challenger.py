"""Tier 2 coverage-aware receiver challenger (research only; never B0).

    prediction = k * B0 * clip(ratio, RATIO_LO, RATIO_HI) ** alpha

`k` is the harness's DEV-fitted scale control for the market, so any change
relative to `k * B0` is the coverage information alone. `ratio` compares the
expected per-dropback quantity (targets, receptions or yards; see
coverage_features.expected) under two man-share scenarios:

    COMBINED  receiver's own man/zone rates; opponent's expected man share vs
              the man share the receiver actually faced in the same window
    F11_ONLY  receiver's own rates; league man share vs his faced share
    F12_ONLY  position-average rates; opponent man share vs league share

Both sides of every ratio come from the same prior seasons, so charting
drift between seasons (NGS vs FTN, and within FTN) cancels. When a required
profile is missing the row falls back to exactly `k * B0` and the reason is
recorded -- a prediction is never forced to change.
"""
from __future__ import annotations

from typing import Mapping

from nfl.research.tier2 import coverage_features as F

MODES = ("COMBINED", "F11_ONLY", "F12_ONLY")
RATIO_LO, RATIO_HI = 0.75, 1.0 / 0.75
QUANTITY = {"receptions": "receptions", "receiving_yards": "receiving_yards"}


def matchup_ratio(mode: str, market: str, receiver: Mapping | None,
                  position: Mapping | None, defense: Mapping | None,
                  league_man: float | None) -> tuple[float | None, str]:
    q = QUANTITY[market]
    if mode in ("COMBINED", "F11_ONLY"):
        if not receiver or receiver.get("status") != "OK":
            return None, (receiver or {}).get("status", "NO_RECEIVER_PROFILE")
        faced = receiver["exposure_man_share"]
        if mode == "COMBINED":
            if not defense or defense.get("status") != "OK":
                return None, (defense or {}).get("status", "NO_DEFENSE_PROFILE")
            target_share = defense["man_share"]
        else:
            if league_man is None:
                return None, "NO_LEAGUE_MIX"
            target_share = league_man
        den = F.expected(receiver, faced, q)
        num = F.expected(receiver, target_share, q)
    else:
        if not position or not defense or defense.get("status") != "OK":
            return None, (defense or {}).get("status", "NO_POSITION_OR_DEFENSE_PROFILE")
        den = F.expected(position, defense["league_man_share"], q)
        num = F.expected(position, defense["man_share"], q)
    if den <= 0:
        return None, "ZERO_DENOMINATOR"
    return min(max(num / den, RATIO_LO), RATIO_HI), "OK"


def predict(b0: float, k: float, ratio: float | None, alpha: float) -> float:
    base = k * b0
    return base if ratio is None else base * ratio ** alpha
