"""Equal-volume directional accuracy of C2 vs B0 against the closing spread.

There is no existing "equal-volume directional accuracy" implementation
elsewhere in this repository (`nflverse_game_lines_audit.py` only audits
file coherence, it does not compare challengers), so the exact method is
specified here in full.

Definitions, computed only on games where a retrospective closing
`spread_line` is available (`REQUIRED_CLOSING_CONTROL` contract, benchmark
only, never a model input):

1. For each game, a model "picks a side" of the closing spread:
   `HOME` if its predicted home margin is strictly greater than
   `spread_line`, `AWAY` if strictly less, `PUSH` if exactly equal (no
   directional stance). The actual "cover side" is defined the same way
   from the real home margin: `HOME` if `actual_margin > spread_line`,
   `AWAY` if `actual_margin < spread_line`, `PUSH` (excluded -- a push can
   be neither a correct nor incorrect pick) otherwise.
2. The **disagreement set** is every non-push-actual game where B0 and C2
   pick opposite non-push sides. Games where both models agree, or where
   either model has no directional stance, are excluded -- this isolates
   exactly the games where the new features would have changed a real
   decision.
3. Each model's **edge** on a disagreement game is
   `abs(model_predicted_margin - spread_line)`: how far that model's own
   prediction sits from the closing number, i.e. how much conviction it
   is expressing on its own scale.
4. For each of `volume_fractions` (25%/50%/75%/100% by default), each
   model independently ranks the *same* disagreement set by its own edge,
   descending, and takes the top `ceil(fraction * N)` games (`N` = the
   disagreement-set size, so both models are compared at literally equal
   pick volume, not just equal population). The reported hit rate is the
   share of that model's own top-ranked picks whose side matched the
   actual cover side.

This directly answers "if each model bet its own most-confident equal-sized
slice of the disagreement games, whose slice covered more often" -- it does
not claim betting profitability, vig, or a deployable selector.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from nfl.research.game_market_b0 import REQUIRED_CLOSING_CONTROL


class GameMarketC2DirectionalError(ValueError):
    pass


DEFAULT_VOLUME_FRACTIONS = (0.25, 0.5, 0.75, 1.0)


def _pick_side(predicted_margin: float, spread_line: float) -> str:
    if predicted_margin > spread_line:
        return "HOME"
    if predicted_margin < spread_line:
        return "AWAY"
    return "PUSH"


def build_market_spread_index(market_rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Return {game_id: spread_line} for rows meeting the closing-control contract."""
    index: dict[str, float] = {}
    for source in market_rows:
        row = dict(source)
        for field, expected in REQUIRED_CLOSING_CONTROL.items():
            if row.get(field) != expected:
                raise GameMarketC2DirectionalError(
                    f"closing control {field} must be {expected!r}"
                )
        game_id = row["game_id"]
        if game_id in index:
            raise GameMarketC2DirectionalError(f"duplicate market row: {game_id}")
        index[game_id] = float(row["spread_line"])
    return index


def compute_equal_volume_directional_accuracy(
    paired_rows: Iterable[Mapping[str, Any]],
    market_spread_index: Mapping[str, float],
    *,
    volume_fractions: tuple[float, ...] = DEFAULT_VOLUME_FRACTIONS,
) -> dict[str, Any]:
    market_matched = []
    for row in paired_rows:
        spread_line = market_spread_index.get(row["game_id"])
        if spread_line is None:
            continue
        actual_side = _pick_side(row["actual_margin"], spread_line)
        if actual_side == "PUSH":
            continue
        b0_side = _pick_side(row["b0_margin"], spread_line)
        c2_side = _pick_side(row["c2_margin"], spread_line)
        market_matched.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "spread_line": spread_line,
            "actual_side": actual_side,
            "b0_side": b0_side,
            "c2_side": c2_side,
            "b0_edge": abs(row["b0_margin"] - spread_line),
            "c2_edge": abs(row["c2_margin"] - spread_line),
        })

    disagreement = [
        row for row in market_matched
        if row["b0_side"] != "PUSH" and row["c2_side"] != "PUSH"
        and row["b0_side"] != row["c2_side"]
    ]
    for row in disagreement:
        row["b0_correct"] = row["b0_side"] == row["actual_side"]
        row["c2_correct"] = row["c2_side"] == row["actual_side"]

    n = len(disagreement)
    by_fraction = []
    b0_by_edge = sorted(disagreement, key=lambda r: r["b0_edge"], reverse=True)
    c2_by_edge = sorted(disagreement, key=lambda r: r["c2_edge"], reverse=True)
    for fraction in volume_fractions:
        take = math.ceil(fraction * n) if n else 0
        b0_top = b0_by_edge[:take]
        c2_top = c2_by_edge[:take]
        by_fraction.append({
            "volume_fraction": fraction,
            "games": take,
            "b0_hit_rate": (
                sum(1 for r in b0_top if r["b0_correct"]) / take if take else None
            ),
            "c2_hit_rate": (
                sum(1 for r in c2_top if r["c2_correct"]) / take if take else None
            ),
        })

    return {
        "market_matched_games": len(market_matched),
        "disagreement_games": n,
        "disagreement_share_of_market_matched": (
            n / len(market_matched) if market_matched else None
        ),
        "by_volume_fraction": by_fraction,
    }
