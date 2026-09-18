"""Leakage-safe rolling team tendencies from nflfastR play-by-play.

`neutral_dropback_rate_v1` definition:
- regular season
- 1st or 2nd down
- possession-team win probability in [0.20, 0.80]
- more than 120 seconds left in the half
- offensive scrimmage plays only
- kneels and spikes excluded
- qb_dropback counts sacks and scrambles as dropbacks per nflfastR semantics

Non-scrimmage source rows (timeouts, kicks, administrative rows) are ignored
before possession/down/WP validation. Current-game summaries enter rolling
history only after the target feature row is emitted.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping

REQUIRED_COLUMNS = frozenset({
    "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
    "down", "half_seconds_remaining", "wp", "qb_dropback", "rush_attempt",
    "qb_kneel", "qb_spike",
})

class PbpPriorTendencyError(ValueError):
    pass

def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PbpPriorTendencyError(f"{field} must be a non-empty string")
    return value.strip()

def _int(value: Any, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise PbpPriorTendencyError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PbpPriorTendencyError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise PbpPriorTendencyError(f"{field} must be an integer")
    if minimum is not None and result < minimum:
        raise PbpPriorTendencyError(f"{field} must be >= {minimum}")
    return result

def _float(value: Any, field: str) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise PbpPriorTendencyError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PbpPriorTendencyError(f"{field} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise PbpPriorTendencyError(f"{field} must be finite")
    return result

def _binary(value: Any, field: str) -> int:
    result = _int(value, field)
    if result not in (0, 1):
        raise PbpPriorTendencyError(f"{field} must be 0 or 1")
    return result

def build_prior_pbp_tendencies(
    source_rows: Iterable[Mapping[str, Any]], *, rolling_window: int = 5,
    wp_low: float = 0.20, wp_high: float = 0.80, two_minute_seconds: int = 120,
) -> list[dict[str, Any]]:
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise PbpPriorTendencyError("rolling_window must be a positive integer")
    if not (0 <= wp_low < wp_high <= 1):
        raise PbpPriorTendencyError("win-probability bounds must satisfy 0 <= low < high <= 1")
    if isinstance(two_minute_seconds, bool) or not isinstance(two_minute_seconds, int) or two_minute_seconds < 0:
        raise PbpPriorTendencyError("two_minute_seconds must be a non-negative integer")

    rows = [dict(row) for row in source_rows]
    if not rows:
        return []
    games: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    seen_plays: set[tuple[str, str]] = set()

    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise PbpPriorTendencyError(f"missing required PBP columns at row {index}: {', '.join(missing)}")
        season = _int(row["season"], "season", 1999)
        week = _int(row["week"], "week", 1)
        if _text(row["season_type"], "season_type").upper() != "REG":
            raise PbpPriorTendencyError("PBP tendency builder accepts REG rows only")
        game_id = _text(row["game_id"], "game_id")
        play_id = str(row["play_id"]).strip()
        if not play_id:
            raise PbpPriorTendencyError("play_id must be non-empty")
        play_key = (game_id, play_id)
        if play_key in seen_plays:
            raise PbpPriorTendencyError(f"duplicate game/play row: {play_key}")
        seen_plays.add(play_key)

        dropback = _binary(row["qb_dropback"], "qb_dropback")
        rush = _binary(row["rush_attempt"], "rush_attempt")
        kneel = _binary(row["qb_kneel"], "qb_kneel")
        spike = _binary(row["qb_spike"], "qb_spike")
        is_scrimmage = (dropback == 1 or rush == 1) and kneel == 0 and spike == 0
        if not is_scrimmage:
            continue

        team = _text(row["posteam"], "posteam").upper()
        opponent = _text(row["defteam"], "defteam").upper()
        if team == opponent:
            raise PbpPriorTendencyError("posteam and defteam must differ")
        down = _int(row["down"], "down", 1)
        half_seconds = _float(row["half_seconds_remaining"], "half_seconds_remaining")
        wp = _float(row["wp"], "wp")
        if not 0 <= wp <= 1:
            raise PbpPriorTendencyError("wp must be between 0 and 1")
        is_neutral = down in (1, 2) and wp_low <= wp <= wp_high and half_seconds > two_minute_seconds

        key = (season, week, team, game_id)
        summary = games.setdefault(key, {
            "season": season, "week": week, "team": team, "game_id": game_id,
            "opponents": set(), "scrimmage_plays": 0, "dropbacks": 0,
            "neutral_plays": 0, "neutral_dropbacks": 0,
        })
        summary["opponents"].add(opponent)
        summary["scrimmage_plays"] += 1
        if dropback == 1:
            summary["dropbacks"] += 1
        if is_neutral:
            summary["neutral_plays"] += 1
            if dropback == 1:
                summary["neutral_dropbacks"] += 1

    game_rows: list[dict[str, Any]] = []
    for summary in games.values():
        if len(summary["opponents"]) != 1:
            raise PbpPriorTendencyError(f"game/team has {len(summary['opponents'])} opponents: {summary['game_id']} {summary['team']}")
        game_rows.append({**{k: v for k, v in summary.items() if k != "opponents"}, "opponent_team": next(iter(summary["opponents"]))})

    game_rows.sort(key=lambda row: (row["season"], row["week"], row["team"], row["game_id"]))
    histories: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=rolling_window))
    output: list[dict[str, Any]] = []
    for current in game_rows:
        history = histories[current["team"]]
        prior_scrimmage = sum(row["scrimmage_plays"] for row in history)
        prior_dropbacks = sum(row["dropbacks"] for row in history)
        prior_neutral = sum(row["neutral_plays"] for row in history)
        prior_neutral_dropbacks = sum(row["neutral_dropbacks"] for row in history)
        output.append({
            "game_id": current["game_id"], "season": current["season"], "week": current["week"],
            "team": current["team"], "opponent_team": current["opponent_team"],
            "prior_games_n": len(history), "prior_scrimmage_plays_n": prior_scrimmage,
            "prior_dropbacks_n": prior_dropbacks, "prior_neutral_plays_n": prior_neutral,
            "prior_neutral_dropbacks_n": prior_neutral_dropbacks,
            "prior_dropback_rate": prior_dropbacks / prior_scrimmage if prior_scrimmage else None,
            "prior_neutral_dropback_rate_v1": prior_neutral_dropbacks / prior_neutral if prior_neutral else None,
            "prior_mean_scrimmage_plays_per_game": prior_scrimmage / len(history) if history else None,
            "neutral_definition": {
                "downs": [1, 2], "wp_low_inclusive": wp_low, "wp_high_inclusive": wp_high,
                "minimum_half_seconds_exclusive": two_minute_seconds, "kneels_excluded": True,
                "spikes_excluded": True, "scrambles_classified_as_dropbacks": True,
            },
            "feature_semantics": "STRICTLY_PRIOR_REG_PBP",
            "true_pace_available": False, "expected_plays_available": False,
        })
        history.append(current)
    return output
