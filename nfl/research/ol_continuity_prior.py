"""Strictly-prior offensive-line continuity features from PFR/nflverse snap counts.

This module consumes completed-game snap-count rows and emits team-game features
*before* the current game's snap distribution enters history. It measures prior
OL rotation/stability; it does not infer the upcoming starting five or injury
status.

Source caveat: present-day historical PFR snap-count files are retrospective
outcome data, not a timestamped vintage archive. Their use here is limited to
facts from games completed before the target game.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping


OL_POSITIONS = frozenset({"C", "G", "T", "OL", "OT", "OG"})
REQUIRED_COLUMNS = frozenset({
    "game_id", "season", "game_type", "week", "pfr_player_id", "position",
    "team", "opponent", "offense_snaps",
})


class OLContinuityError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OLContinuityError(f"{field} must be a non-empty string")
    return value.strip()


def _int(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise OLContinuityError(f"{field} must be an integer")
    try:
        result = int(float(value))
    except (TypeError, ValueError) as exc:
        raise OLContinuityError(f"{field} must be an integer") from exc
    try:
        if float(value) != result:
            raise OLContinuityError(f"{field} must be an integer")
    except (TypeError, ValueError) as exc:
        raise OLContinuityError(f"{field} must be an integer") from exc
    if result < minimum:
        raise OLContinuityError(f"{field} must be >= {minimum}")
    return result


def _mean(history: deque[dict[str, Any]], field: str) -> float | None:
    if not history:
        return None
    return sum(float(row[field]) for row in history) / len(history)


def _top5_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> float | None:
    if not a or not b:
        return None
    left, right = set(a), set(b)
    union = left | right
    return len(left & right) / len(union) if union else None


def build_prior_ol_continuity(
    source_rows: Iterable[Mapping[str, Any]], *, rolling_window: int = 5
) -> list[dict[str, Any]]:
    """Build one prior-only OL stability row per observed REG team-game.

    All team snap rows are accepted because team offensive snaps are estimated
    conservatively as the maximum offensive snaps logged by any player on that
    team in the game. OL metrics then use only recognized OL positions.
    """
    if isinstance(rolling_window, bool) or not isinstance(rolling_window, int) or rolling_window <= 0:
        raise OLContinuityError("rolling_window must be a positive integer")

    rows = [dict(row) for row in source_rows]
    if not rows:
        return []

    games: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    seen_player_game: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_COLUMNS.difference(row.keys()))
        if missing:
            raise OLContinuityError(
                f"snap row {index} missing fields: {', '.join(missing)}"
            )
        season = _int(row["season"], "season", 2012)
        week = _int(row["week"], "week", 1)
        game_type = _text(row["game_type"], "game_type").upper()
        if game_type != "REG":
            raise OLContinuityError("OL continuity builder accepts REG rows only")
        game_id = _text(row["game_id"], "game_id")
        team = _text(row["team"], "team").upper()
        opponent = _text(row["opponent"], "opponent").upper()
        if team == opponent:
            raise OLContinuityError("team and opponent must differ")
        player_id = _text(row["pfr_player_id"], "pfr_player_id")
        position = _text(row["position"], "position").upper()
        snaps = _int(row["offense_snaps"], "offense_snaps", 0)
        player_key = (game_id, team, player_id)
        if player_key in seen_player_game:
            raise OLContinuityError(f"duplicate player/game snap row: {player_key}")
        seen_player_game.add(player_key)

        key = (season, week, team, game_id)
        summary = games.setdefault(key, {
            "season": season, "week": week, "game_type": game_type,
            "team": team, "game_id": game_id, "opponents": set(),
            "team_offense_snaps": 0, "ol": [],
        })
        summary["opponents"].add(opponent)
        summary["team_offense_snaps"] = max(summary["team_offense_snaps"], snaps)
        if position in OL_POSITIONS and snaps > 0:
            summary["ol"].append((player_id, snaps))

    observations: list[dict[str, Any]] = []
    for summary in games.values():
        if len(summary["opponents"]) != 1:
            raise OLContinuityError(
                f"game/team has ambiguous opponent identity: {summary['game_id']} {summary['team']}"
            )
        team_snaps = summary["team_offense_snaps"]
        if team_snaps <= 0:
            raise OLContinuityError(
                f"game/team has no offensive snaps: {summary['game_id']} {summary['team']}"
            )
        if not summary["ol"]:
            raise OLContinuityError(
                f"game/team has no recognized OL snap rows: {summary['game_id']} {summary['team']}"
            )
        ol_sorted = sorted(summary["ol"], key=lambda item: (-item[1], item[0]))
        total_ol_snaps = sum(snaps for _, snaps in ol_sorted)
        top5 = tuple(player for player, _ in ol_sorted[:5])
        top5_snaps = sum(snaps for _, snaps in ol_sorted[:5])
        observations.append({
            "season": summary["season"], "week": summary["week"],
            "game_type": summary["game_type"], "game_id": summary["game_id"],
            "team": summary["team"], "opponent_team": next(iter(summary["opponents"])),
            "team_offense_snaps": team_snaps,
            "ol_players_with_snap": len(ol_sorted),
            "ol_full_game_equivalents": total_ol_snaps / team_snaps,
            "ol_top5_snap_share": top5_snaps / total_ol_snaps,
            "ol_top5_ids": top5,
        })

    observations.sort(key=lambda r: (r["season"], r["week"], r["team"], r["game_id"]))
    histories: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=rolling_window))
    output: list[dict[str, Any]] = []
    for current in observations:
        history = histories[current["team"]]
        last_two_overlap = None
        if len(history) >= 2:
            last_two_overlap = _top5_overlap(
                history[-1]["ol_top5_ids"], history[-2]["ol_top5_ids"]
            )
        output.append({
            "season": current["season"], "week": current["week"],
            "game_type": current["game_type"], "game_id": current["game_id"],
            "team": current["team"], "opponent_team": current["opponent_team"],
            "prior_games_n": len(history), "rolling_window": rolling_window,
            "prior_mean_team_offense_snaps": _mean(history, "team_offense_snaps"),
            "prior_mean_ol_players_with_snap": _mean(history, "ol_players_with_snap"),
            "prior_mean_ol_full_game_equivalents": _mean(history, "ol_full_game_equivalents"),
            "prior_mean_ol_top5_snap_share": _mean(history, "ol_top5_snap_share"),
            "prior_last_two_top5_jaccard": last_two_overlap,
            "prior_last_game_top5_ids": list(history[-1]["ol_top5_ids"]) if history else [],
            "feature_semantics": "STRICTLY_PRIOR_REG_PFR_SNAP_OL_CONTINUITY",
            "upcoming_starting_five_known": False,
            "current_game_snap_counts_used": False,
            "source_vintage_is_point_in_time_archive": False,
        })
        history.append(current)
    return output
