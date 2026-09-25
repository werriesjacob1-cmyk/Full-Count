#!/usr/bin/env python3
"""Fetch and merge the real nflverse sources the WR/RB role-intelligence substrate needs.

This is the network/parsing layer for
`engineering/NFL_ROLE_CHANGE_HISTORICAL_DATASET_CONTRACT_2026-09-18.md`'s
"Base historical source layers" and "Required identity fields" sections. It
produces one clean, merged `PlayerGameUsage` dict per (season, week, team,
player_id, WR-or-RB), which `role_intelligence_features.py` turns into the
long-format `target_game x team x player x role_dimension` rows. This module
does no rolling-window math, no baseline prediction, and no evaluation --
those are `role_intelligence_features.py` / `role_intelligence_baselines.py`.

## Sources (all real nflverse-data GitHub releases, digest-checked on arrival)

- `stats_player` release, `stats_player_week_<season>.csv` (same source/URL
  helper as `nflverse_history.py`, imported not duplicated): REG-only weekly
  player stats, keyed on the stable `player_id` (gsis) already used
  throughout this repo. Gives `targets`/`carries` and team-week totals for
  `target_share`/`carry_share`.
- `snap_counts` release, `snap_counts_<season>.csv`, seasons 2012-2025
  (digests reused from `game_market_c2_source_digests.SNAP_SOURCE_ASSET_DIGESTS`,
  not re-pinned here). Keyed on `pfr_player_id`, joined to the stable
  `player_id` via the `players` crosswalk below. Gives `offense_snaps` for
  `offense_snap_share`; the team-game denominator is
  `max(offense_snaps)` across that team's full roster for that game, the
  same convention `ol_continuity_prior.py` already uses for
  `team_offense_snaps` (not invented here).
- `players` release, `players.csv` (digest pinned in
  `role_intelligence_source_digests.PLAYERS_CROSSWALK_SOURCE`): the
  `pfr_id -> gsis_id` crosswalk `snap_counts` needs. Without it, joining
  `snap_counts` to everything else would be a name-only join, which the
  dataset contract requires this project to quarantine rather than trust.
- `depth_charts` release, `depth_charts_<season>.csv`, seasons 2012-2024
  only (digests in `role_intelligence_source_digests.DEPTH_CHART_SOURCE_ASSET_DIGESTS`;
  2025 is a disclosed schema break, see that module's docstring). Keyed on
  `gsis_id` directly. Gives real pregame depth-chart rank
  (`depth_team`) for WR/RB.
- `injuries` release, `injuries_<season>.csv` (same source/URL helper as
  `injury_availability_features.py`, imported not duplicated), seasons
  2009-2025 (`injury_availability_features.EARLIEST_COVERED_SEASON`). Weekly
  practice/game-status report filed before that week's games -- the same
  pregame-safe source and reasoning that module already established. Used
  here for the WR/RB teammate-unavailable trigger (`OUT`/`DOUBTFUL`), never
  for a realized label.
- `pbp` release, `play_by_play_<season>.csv.gz`, seasons 2012-2025 (digests
  reused from `game_market_c2_source_digests.PBP_SOURCE_ASSET_DIGESTS`, not
  re-pinned here). Per-play `receiver_player_id`/`rusher_player_id` are
  stable gsis IDs. Used to derive `red_zone_opportunity_share`,
  `goal_line_carry_share`, and PBP-derived opportunity proxies for
  `third_down_snap_share`/`two_minute_snap_share` (this substrate has no
  true snap-participation source before nflverse's own 2016+ `participation`
  release, which this task does not ingest -- see
  `role_intelligence_features.THIRD_DOWN_TWO_MINUTE_ARE_OPPORTUNITY_PROXIES`).
  Team identity is re-derived from `game_id` plus `home_team`, exactly as
  `game_market_c2_data_prep.py` already does, because nflverse's PBP
  normalizes `posteam`/`defteam` to a franchise's *current* abbreviation even
  in relocated-franchise seasons (2012-2025 includes STL->LA, SD->LAC,
  OAK->LV), while `stats_player_week`'s own `team` column (like
  `game_id`/`games.csv`) keeps the abbreviation used at the time. Re-deriving
  from `game_id` keeps the join key consistent with every other source here.

## Why the whole substrate uses one shared 2012-2025 window

Different dimensions have different real upstream floors (`target_share`/
`carry_share` could extend to 1999 on `stats_player_week` alone; `depth_charts`
starts 2001; only `snap_counts`/this task's PBP-derived opportunity
dimensions are bounded at 2012). Rather than emit rows whose available
dimension set silently changes era to era within the same table, this build
uses one shared window, 2012-2025, so every role-state row for a covered
game has the same *attempted* dimension set (some cells are still legitimately
UNKNOWN within that window for other reasons, e.g. depth chart in 2025, or a
player with no snap_counts row). Extending `target_share`/`carry_share`
alone back to 1999 is a real, documented, NOT-yet-built extension (see
`ENGINEERING_HANDOFF.md`), not a source-availability limit.

## Route share is not built in this task

`route_share` is a required role dimension in the contract but has no real
source in this build: nflverse's route-participation data comes from FTN
charting (2022+) or NGS/participation feeds this task does not ingest. Every
`route_share` row this substrate emits is `UNKNOWN_NO_SOURCE_INGESTED`, never
a fabricated or imputed number.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import time
import urllib.request
from typing import Any

from nfl.research.game_market_c2_source_digests import (
    PBP_SOURCE_ASSET_DIGESTS,
    SNAP_SOURCE_ASSET_DIGESTS,
)
from nfl.research.injury_availability_features import (
    EARLIEST_COVERED_SEASON as INJURY_EARLIEST_COVERED_SEASON,
    GAME_AFFECTING_STATUSES,
    injury_report_url,
)
from nfl.research.nflverse_history import player_stats_url
from nfl.research.role_intelligence_source_digests import (
    DEPTH_CHART_SCHEMA_BREAK_SEASONS,
    DEPTH_CHART_SOURCE_ASSET_DIGESTS,
    PLAYERS_CROSSWALK_SOURCE,
)

ROLE_POSITIONS = frozenset({"WR", "RB"})

PLAYERS_CROSSWALK_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)
DEPTH_CHART_URL_TMPL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "depth_charts/depth_charts_{season}.csv"
)
SNAP_COUNTS_URL_TMPL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "snap_counts/snap_counts_{season}.csv"
)
PBP_URL_TMPL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "pbp/play_by_play_{season}.csv.gz"
)

SUBSTRATE_SEASONS = tuple(range(2012, 2026))

# Standard convention: red zone is inside the opponent's 20; goal line is
# inside the opponent's 5 (both counted on `yardline_100`, the standard
# nflverse "yards from opponent's end zone" field). Two-minute is either
# half's final two minutes of game clock, restricted to Q2/Q4 so a stray
# `half_seconds_remaining` value in overtime is never counted.
RED_ZONE_YARDLINE_100_MAX = 20
GOAL_LINE_YARDLINE_100_MAX = 5
TWO_MINUTE_HALF_SECONDS_MAX = 120
TWO_MINUTE_QUARTERS = frozenset({"2", "4"})


class RoleIntelligenceDataPrepError(ValueError):
    """Raised when a fetched asset fails digest verification or schema checks."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_and_verify(url: str, expected: dict[str, Any], *, tries: int = 4) -> bytes:
    """Fetch `url` and fail closed unless its bytes match `expected` exactly."""
    last_exc: Exception | None = None
    data: bytes | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "full-count-role-intelligence-research/1.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            break
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    if data is None:
        raise RuntimeError(f"failed to fetch {url}: {last_exc}")

    if len(data) != expected["bytes"] or sha256_bytes(data) != expected["sha256"]:
        raise RoleIntelligenceDataPrepError(
            f"asset digest drift for {url}: expected bytes={expected['bytes']} "
            f"sha256={expected['sha256']}, got bytes={len(data)} sha256={sha256_bytes(data)}"
        )
    return data


# --------------------------------------------------------------------------
# Player-ID crosswalk (pfr_id -> gsis_id)
# --------------------------------------------------------------------------

def parse_players_crosswalk_csv(text: str) -> dict[str, str]:
    """Return {pfr_id: gsis_id} for every row carrying both stable IDs."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "gsis_id" not in reader.fieldnames or "pfr_id" not in reader.fieldnames:
        raise RoleIntelligenceDataPrepError("players crosswalk missing gsis_id/pfr_id columns")
    crosswalk: dict[str, str] = {}
    for row in reader:
        gsis_id = (row.get("gsis_id") or "").strip()
        pfr_id = (row.get("pfr_id") or "").strip()
        if not gsis_id or not pfr_id:
            continue
        # A pfr_id colliding with two different gsis_ids would make the
        # snap_counts join ambiguous; fail closed rather than pick one.
        if pfr_id in crosswalk and crosswalk[pfr_id] != gsis_id:
            raise RoleIntelligenceDataPrepError(
                f"ambiguous pfr_id {pfr_id!r}: maps to both {crosswalk[pfr_id]!r} and {gsis_id!r}"
            )
        crosswalk[pfr_id] = gsis_id
    return crosswalk


def fetch_players_crosswalk() -> dict[str, str]:
    data = fetch_and_verify(PLAYERS_CROSSWALK_URL, PLAYERS_CROSSWALK_SOURCE)
    return parse_players_crosswalk_csv(data.decode("utf-8"))


# --------------------------------------------------------------------------
# Weekly player stats (targets/carries + team-week totals)
# --------------------------------------------------------------------------

WEEKLY_STATS_REQUIRED_COLUMNS = frozenset({
    "player_id", "player_display_name", "position", "season", "week",
    "season_type", "team", "opponent_team", "targets", "carries",
})


def parse_weekly_stats_csv(text: str, season: int) -> list[dict[str, Any]]:
    """Return one REG-week row per player with numeric targets/carries.

    All positions are kept (not just WR/RB) because team-week target/carry
    totals -- the `target_share`/`carry_share` denominators -- must sum over
    every pass-catcher/rusher on the team, not only WR/RB. Position
    filtering to WR/RB happens later, in `build_player_game_usage_rows`.
    """
    reader = csv.DictReader(io.StringIO(text))
    missing = WEEKLY_STATS_REQUIRED_COLUMNS.difference(reader.fieldnames or [])
    if missing:
        raise RoleIntelligenceDataPrepError(
            f"weekly stats {season} missing required columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, Any]] = []
    for row in reader:
        if (row.get("season_type") or "").strip().upper() != "REG":
            continue
        player_id = (row.get("player_id") or "").strip()
        if player_id in {"", "0"}:
            # Matches nflverse_history.py's own audited-structural-zero
            # handling: blank-identity scaffolding rows, not player games.
            continue
        try:
            week = int(row["week"])
            targets = float(row["targets"] or 0)
            carries = float(row["carries"] or 0)
        except (TypeError, ValueError) as exc:
            raise RoleIntelligenceDataPrepError(
                f"weekly stats {season} row has non-numeric week/targets/carries: {row!r}"
            ) from exc
        rows.append({
            "player_id": player_id,
            "player_display_name": str(row["player_display_name"]),
            "position": str(row["position"]).strip().upper(),
            "season": season,
            "week": week,
            "team": str(row["team"]).strip().upper(),
            "opponent_team": str(row["opponent_team"]).strip().upper(),
            "targets": targets,
            "carries": carries,
        })
    return rows


def fetch_weekly_stats_rows(season: int) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        player_stats_url(season), headers={"User-Agent": "full-count-role-intelligence-research/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    return parse_weekly_stats_csv(data.decode("utf-8"), season)


# --------------------------------------------------------------------------
# Snap counts (offense_snaps, keyed by pfr_player_id)
# --------------------------------------------------------------------------

SNAP_REQUIRED_COLUMNS = frozenset({
    "game_id", "season", "game_type", "week", "pfr_player_id", "position",
    "team", "opponent", "offense_snaps",
})


def parse_snap_counts_csv(text: str, season: int, crosswalk: dict[str, str]) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []  # 2012 is a real, disclosed empty (header-only) release asset.
    missing = SNAP_REQUIRED_COLUMNS.difference(reader.fieldnames)
    if missing:
        raise RoleIntelligenceDataPrepError(
            f"snap_counts {season} missing required columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, Any]] = []
    for row in reader:
        if (row.get("game_type") or "").strip().upper() != "REG":
            continue
        pfr_player_id = (row.get("pfr_player_id") or "").strip()
        if not pfr_player_id:
            continue
        gsis_id = crosswalk.get(pfr_player_id)
        try:
            offense_snaps = float(row["offense_snaps"] or 0)
            week = int(row["week"])
        except (TypeError, ValueError) as exc:
            raise RoleIntelligenceDataPrepError(
                f"snap_counts {season} row has non-numeric week/offense_snaps: {row!r}"
            ) from exc
        rows.append({
            "game_id": str(row["game_id"]),
            "season": season,
            "week": week,
            "pfr_player_id": pfr_player_id,
            # None means "on the snap-count sheet but not joinable to a
            # stable gsis_id" -- a real, disclosed quarantine, never a
            # silent name-only join.
            "player_id": gsis_id,
            "position": str(row["position"]).strip().upper(),
            "team": str(row["team"]).strip().upper(),
            "opponent": str(row["opponent"]).strip().upper(),
            "offense_snaps": offense_snaps,
        })
    return rows


def fetch_snap_count_rows(season: int, crosswalk: dict[str, str]) -> list[dict[str, Any]]:
    key = str(season)
    if key not in SNAP_SOURCE_ASSET_DIGESTS:
        raise RoleIntelligenceDataPrepError(f"no pinned snap_counts digest for season {season}")
    data = fetch_and_verify(SNAP_COUNTS_URL_TMPL.format(season=season), SNAP_SOURCE_ASSET_DIGESTS[key])
    return parse_snap_counts_csv(data.decode("utf-8"), season, crosswalk)


# --------------------------------------------------------------------------
# Depth charts (real pregame rank, 2012-2024 only -- see module docstring)
# --------------------------------------------------------------------------

DEPTH_CHART_REQUIRED_COLUMNS = frozenset({
    "season", "club_code", "week", "game_type", "depth_team", "gsis_id", "position",
})


def parse_depth_chart_csv(text: str, season: int) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = DEPTH_CHART_REQUIRED_COLUMNS.difference(reader.fieldnames or [])
    if missing:
        raise RoleIntelligenceDataPrepError(
            f"depth_charts {season} missing required columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, Any]] = []
    for row in reader:
        if (row.get("game_type") or "").strip().upper() != "REG":
            continue
        position = str(row.get("position") or "").strip().upper()
        if position not in ROLE_POSITIONS:
            continue
        gsis_id = (row.get("gsis_id") or "").strip()
        if not gsis_id:
            continue
        try:
            depth_team = int(row["depth_team"])
            week = int(row["week"])
        except (TypeError, ValueError) as exc:
            raise RoleIntelligenceDataPrepError(
                f"depth_charts {season} row has non-integer depth_team/week: {row!r}"
            ) from exc
        rows.append({
            "season": season,
            "week": week,
            "team": str(row["club_code"]).strip().upper(),
            "player_id": gsis_id,
            "position": position,
            "depth_team": depth_team,
        })
    return rows


def fetch_depth_chart_rows(season: int) -> list[dict[str, Any]]:
    if season in DEPTH_CHART_SCHEMA_BREAK_SEASONS:
        raise RoleIntelligenceDataPrepError(
            f"depth_charts {season} uses the post-break ESPN daily-snapshot schema; not ingested"
        )
    key = str(season)
    if key not in DEPTH_CHART_SOURCE_ASSET_DIGESTS:
        raise RoleIntelligenceDataPrepError(f"no pinned depth_charts digest for season {season}")
    data = fetch_and_verify(DEPTH_CHART_URL_TMPL.format(season=season), DEPTH_CHART_SOURCE_ASSET_DIGESTS[key])
    return parse_depth_chart_csv(data.decode("utf-8"), season)


# --------------------------------------------------------------------------
# Weekly injury report (pregame OUT/DOUBTFUL trigger evidence)
# --------------------------------------------------------------------------

def fetch_injury_rows(season: int) -> list[dict[str, Any]]:
    if season < INJURY_EARLIEST_COVERED_SEASON:
        return []
    request = urllib.request.Request(
        injury_report_url(season), headers={"User-Agent": "full-count-role-intelligence-research/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    required = {"season", "game_type", "team", "week", "gsis_id", "report_status"}
    missing = required.difference(reader.fieldnames or [])
    if missing:
        raise RoleIntelligenceDataPrepError(
            f"injuries {season} missing required columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, Any]] = []
    for row in reader:
        if (row.get("game_type") or "").strip().upper() != "REG":
            continue
        gsis_id = (row.get("gsis_id") or "").strip()
        if not gsis_id:
            continue
        rows.append({
            "season": int(row["season"]),
            "week": int(row["week"]),
            "team": str(row["team"]).strip().upper(),
            "player_id": gsis_id,
            "report_status": str(row.get("report_status") or "").strip().upper(),
        })
    return rows


# --------------------------------------------------------------------------
# Play-by-play derived opportunity counts
# --------------------------------------------------------------------------

def _derive_pbp_team_identity(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return (game_id, week, posteam, defteam) using period-accurate codes.

    nflverse's PBP normalizes `posteam`/`defteam` to a franchise's *current*
    abbreviation even in relocated-franchise seasons. `game_id`'s own
    `SEASON_WEEK_AWAY_HOME` format (plus `home_team`) keeps the abbreviation
    used at the time, matching `stats_player_week`'s `team` column. This is
    the same derivation `game_market_c2_data_prep.py` already uses, applied
    here so PBP-derived rows join to weekly-stats rows by the same team code.
    """
    game_id = (row.get("game_id") or "").strip()
    posteam_pbp = (row.get("posteam") or "").strip().upper()
    defteam_pbp = (row.get("defteam") or "").strip().upper()
    week = (row.get("week") or "").strip()
    if not game_id or not posteam_pbp or not defteam_pbp or posteam_pbp == defteam_pbp or not week:
        return None
    id_parts = game_id.split("_")
    if len(id_parts) != 4:
        return None
    away_code, home_code = id_parts[2], id_parts[3]
    is_pos_home = posteam_pbp == (row.get("home_team") or "").strip().upper()
    posteam = home_code if is_pos_home else away_code
    defteam = away_code if is_pos_home else home_code
    return game_id, week, posteam, defteam


def _to_float_or_none(value: Any) -> float | None:
    text = (value or "").strip() if isinstance(value, str) else value
    if text in (None, ""):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def build_pbp_opportunity_rows(season: int) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, float]]]:
    """Stream-parse one season's PBP into per-player counts + team totals.

    Returns (player_rows, team_totals) where `team_totals` is keyed by
    `(game_id, team)` and holds the four opportunity-category denominators.
    Streaming (never materializing the season's full play list) keeps memory
    bounded across a 14-season 2012-2025 run.

    "Opportunity" = a target (pass attempt with a `receiver_player_id`) or a
    non-kneel, non-spike carry (`rusher_player_id`). Kneels/spikes are
    excluded because they are clock-management plays, not role usage --
    a disclosed, deliberate difference from `game_market_c2_data_prep.py`'s
    team-level "carries" (which does not exclude kneels), appropriate here
    because this is player-role work, not team-scoring work.
    """
    key = str(season)
    if key not in PBP_SOURCE_ASSET_DIGESTS:
        raise RoleIntelligenceDataPrepError(f"no pinned pbp digest for season {season}")
    raw = fetch_and_verify(PBP_URL_TMPL.format(season=season), PBP_SOURCE_ASSET_DIGESTS[key])
    text_stream = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8")
    reader = csv.DictReader(text_stream)

    per_player: dict[tuple[str, str, str], dict[str, float]] = {}
    per_team: dict[tuple[str, str], dict[str, float]] = {}

    def player_acc(game_id: str, team: str, player_id: str) -> dict[str, float]:
        return per_player.setdefault((game_id, team, player_id), {
            "red_zone_targets": 0.0, "red_zone_carries": 0.0,
            "goal_line_carries": 0.0,
            "third_down_targets": 0.0, "third_down_carries": 0.0,
            "two_minute_targets": 0.0, "two_minute_carries": 0.0,
        })

    def team_acc(game_id: str, team: str) -> dict[str, float]:
        return per_team.setdefault((game_id, team), {
            "team_red_zone_opportunities": 0.0,
            "team_goal_line_carries": 0.0,
            "team_third_down_opportunities": 0.0,
            "team_two_minute_opportunities": 0.0,
        })

    for row in reader:
        if (row.get("season_type") or "").strip().upper() != "REG":
            continue
        identity = _derive_pbp_team_identity(row)
        if identity is None:
            continue
        game_id, week, posteam, _defteam = identity

        pass_attempt = _to_float_or_none(row.get("pass_attempt"))
        rush_attempt = _to_float_or_none(row.get("rush_attempt"))
        qb_kneel = _to_float_or_none(row.get("qb_kneel"))
        qb_spike = _to_float_or_none(row.get("qb_spike"))
        receiver_id = (row.get("receiver_player_id") or "").strip()
        rusher_id = (row.get("rusher_player_id") or "").strip()

        is_target = pass_attempt == 1.0 and bool(receiver_id)
        is_carry = rush_attempt == 1.0 and bool(rusher_id) and qb_kneel != 1.0 and qb_spike != 1.0
        if not is_target and not is_carry:
            continue

        yardline_100 = _to_float_or_none(row.get("yardline_100"))
        down = (row.get("down") or "").strip()
        qtr = (row.get("qtr") or "").strip()
        half_seconds = _to_float_or_none(row.get("half_seconds_remaining"))

        in_red_zone = yardline_100 is not None and yardline_100 <= RED_ZONE_YARDLINE_100_MAX
        in_goal_line = yardline_100 is not None and yardline_100 <= GOAL_LINE_YARDLINE_100_MAX
        is_third_down = down == "3"
        is_two_minute = (
            qtr in TWO_MINUTE_QUARTERS
            and half_seconds is not None
            and half_seconds <= TWO_MINUTE_HALF_SECONDS_MAX
        )

        team_row = team_acc(game_id, posteam)
        if in_red_zone:
            team_row["team_red_zone_opportunities"] += 1
        if is_carry and in_goal_line:
            team_row["team_goal_line_carries"] += 1
        if is_third_down:
            team_row["team_third_down_opportunities"] += 1
        if is_two_minute:
            team_row["team_two_minute_opportunities"] += 1

        if is_target:
            p_row = player_acc(game_id, posteam, receiver_id)
            if in_red_zone:
                p_row["red_zone_targets"] += 1
            if is_third_down:
                p_row["third_down_targets"] += 1
            if is_two_minute:
                p_row["two_minute_targets"] += 1
        if is_carry:
            p_row = player_acc(game_id, posteam, rusher_id)
            if in_red_zone:
                p_row["red_zone_carries"] += 1
            if in_goal_line:
                p_row["goal_line_carries"] += 1
            if is_third_down:
                p_row["third_down_carries"] += 1
            if is_two_minute:
                p_row["two_minute_carries"] += 1

    player_rows = []
    for (game_id, team, player_id), counts in per_player.items():
        week = int(game_id.split("_")[1])
        player_rows.append({
            "season": season, "week": week, "game_id": game_id,
            "team": team, "player_id": player_id, **counts,
        })
    return player_rows, per_team


# --------------------------------------------------------------------------
# Merge into one PlayerGameUsage record per (season, week, team, WR/RB player)
# --------------------------------------------------------------------------

def _team_totals_from_weekly_stats(weekly_rows: list[dict[str, Any]]) -> dict[tuple[int, int, str], dict[str, float]]:
    totals: dict[tuple[int, int, str], dict[str, float]] = {}
    for row in weekly_rows:
        key = (row["season"], row["week"], row["team"])
        acc = totals.setdefault(key, {"team_targets": 0.0, "team_carries": 0.0})
        acc["team_targets"] += row["targets"]
        acc["team_carries"] += row["carries"]
    return totals


def _snap_indexes(
    snap_rows: list[dict[str, Any]],
) -> tuple[dict[tuple[int, int, str, str], float], dict[tuple[int, int, str], float]]:
    by_player: dict[tuple[int, int, str, str], float] = {}
    by_team_max: dict[tuple[int, int, str], float] = {}
    for row in snap_rows:
        team_key = (row["season"], row["week"], row["team"])
        by_team_max[team_key] = max(by_team_max.get(team_key, 0.0), row["offense_snaps"])
        if row["player_id"] is None:
            continue  # quarantined: no gsis_id crosswalk hit, see fetch_snap_count_rows
        player_key = (row["season"], row["week"], row["team"], row["player_id"])
        by_player[player_key] = by_player.get(player_key, 0.0) + row["offense_snaps"]
    return by_player, by_team_max


def _depth_index(depth_rows: list[dict[str, Any]]) -> dict[tuple[int, int, str, str], int]:
    return {
        (row["season"], row["week"], row["team"], row["player_id"]): row["depth_team"]
        for row in depth_rows
    }


def _injury_index(injury_rows: list[dict[str, Any]]) -> dict[tuple[int, int, str, str], str]:
    return {
        (row["season"], row["week"], row["team"], row["player_id"]): row["report_status"]
        for row in injury_rows
    }


_PBP_COUNT_FIELDS = (
    "red_zone_targets", "red_zone_carries", "goal_line_carries",
    "third_down_targets", "third_down_carries",
    "two_minute_targets", "two_minute_carries",
)
_PBP_TEAM_TOTAL_FIELDS = (
    "team_red_zone_opportunities", "team_goal_line_carries",
    "team_third_down_opportunities", "team_two_minute_opportunities",
)


def _pbp_indexes(
    pbp_player_rows: list[dict[str, Any]],
    pbp_team_totals: dict[tuple[str, str], dict[str, float]],
) -> tuple[dict[tuple[int, int, str, str], dict[str, float]], dict[tuple[int, int, str], dict[str, float]]]:
    """Aggregate PBP's (game_id, team[, player]) keys to (season, week, team[, player]).

    REG-season teams play exactly one game per week, so this is a sum over a
    single game_id in practice; summing rather than assuming uniqueness fails
    safe if that ever isn't true instead of silently dropping a game.
    """
    by_player: dict[tuple[int, int, str, str], dict[str, float]] = {}
    for row in pbp_player_rows:
        key = (row["season"], row["week"], row["team"], row["player_id"])
        acc = by_player.setdefault(key, {field: 0.0 for field in _PBP_COUNT_FIELDS})
        for field in _PBP_COUNT_FIELDS:
            acc[field] += row[field]

    by_team: dict[tuple[int, int, str], dict[str, float]] = {}
    for (game_id, team), counts in pbp_team_totals.items():
        season = int(game_id.split("_")[0])
        week = int(game_id.split("_")[1])
        key = (season, week, team)
        acc = by_team.setdefault(key, {field: 0.0 for field in _PBP_TEAM_TOTAL_FIELDS})
        for field in _PBP_TEAM_TOTAL_FIELDS:
            acc[field] += counts.get(field, 0.0)
    return by_player, by_team


def build_player_game_usage_rows(
    weekly_rows: list[dict[str, Any]],
    snap_rows: list[dict[str, Any]],
    depth_rows: list[dict[str, Any]],
    injury_rows: list[dict[str, Any]],
    pbp_player_rows: list[dict[str, Any]],
    pbp_team_totals: dict[tuple[str, str], dict[str, float]],
) -> list[dict[str, Any]]:
    """Merge every source into one WR/RB PlayerGameUsage record per player-game.

    Every merge key is a stable `player_id` (gsis) plus (season, week, team) --
    never a name. A dimension's raw counts are `None` (not zero) whenever
    that source has no row for this player-game, so a downstream "0.0 share"
    is never confused with "source did not cover this player-game."
    """
    team_totals = _team_totals_from_weekly_stats(weekly_rows)
    snap_by_player, snap_team_max = _snap_indexes(snap_rows)
    depth = _depth_index(depth_rows)
    injuries = _injury_index(injury_rows)
    pbp_by_player, pbp_by_team = _pbp_indexes(pbp_player_rows, pbp_team_totals)

    rows: list[dict[str, Any]] = []
    for row in weekly_rows:
        if row["position"] not in ROLE_POSITIONS:
            continue
        season, week, team, player_id = row["season"], row["week"], row["team"], row["player_id"]
        team_key = (season, week, team)
        player_key = (season, week, team, player_id)

        offense_snaps = snap_by_player.get(player_key)
        team_offense_snaps = snap_team_max.get(team_key)
        pbp_counts = pbp_by_player.get(player_key, {field: 0.0 for field in _PBP_COUNT_FIELDS})
        pbp_totals = pbp_by_team.get(team_key)

        rows.append({
            "season": season,
            "week": week,
            "team": team,
            "opponent_team": row["opponent_team"],
            "player_id": player_id,
            "player_display_name": row["player_display_name"],
            "position": row["position"],
            "targets": row["targets"],
            "carries": row["carries"],
            "team_targets": team_totals[team_key]["team_targets"],
            "team_carries": team_totals[team_key]["team_carries"],
            "offense_snaps": offense_snaps,
            "team_offense_snaps": team_offense_snaps,
            "red_zone_targets": pbp_counts["red_zone_targets"],
            "red_zone_carries": pbp_counts["red_zone_carries"],
            "team_red_zone_opportunities": pbp_totals["team_red_zone_opportunities"] if pbp_totals else None,
            "goal_line_carries": pbp_counts["goal_line_carries"],
            "team_goal_line_carries": pbp_totals["team_goal_line_carries"] if pbp_totals else None,
            "third_down_targets": pbp_counts["third_down_targets"],
            "third_down_carries": pbp_counts["third_down_carries"],
            "team_third_down_opportunities": pbp_totals["team_third_down_opportunities"] if pbp_totals else None,
            "two_minute_targets": pbp_counts["two_minute_targets"],
            "two_minute_carries": pbp_counts["two_minute_carries"],
            "team_two_minute_opportunities": pbp_totals["team_two_minute_opportunities"] if pbp_totals else None,
            "depth_team": depth.get(player_key),
            "injury_report_status": injuries.get(player_key),
        })

    rows.sort(key=lambda r: (r["season"], r["week"], r["team"], r["player_id"]))
    return rows


def fetch_season_bundle(season: int, crosswalk: dict[str, str]) -> dict[str, Any]:
    """Network-fetch every source for one season. Thin orchestration only."""
    weekly_rows = fetch_weekly_stats_rows(season)
    snap_rows = fetch_snap_count_rows(season, crosswalk) if str(season) in SNAP_SOURCE_ASSET_DIGESTS else []
    depth_rows = (
        fetch_depth_chart_rows(season)
        if season not in DEPTH_CHART_SCHEMA_BREAK_SEASONS and str(season) in DEPTH_CHART_SOURCE_ASSET_DIGESTS
        else []
    )
    injury_rows = fetch_injury_rows(season)
    pbp_player_rows, pbp_team_totals = (
        build_pbp_opportunity_rows(season) if str(season) in PBP_SOURCE_ASSET_DIGESTS else ([], {})
    )
    return {
        "weekly_rows": weekly_rows,
        "snap_rows": snap_rows,
        "depth_rows": depth_rows,
        "injury_rows": injury_rows,
        "pbp_player_rows": pbp_player_rows,
        "pbp_team_totals": pbp_team_totals,
    }


__all__ = [
    "RoleIntelligenceDataPrepError",
    "ROLE_POSITIONS",
    "SUBSTRATE_SEASONS",
    "RED_ZONE_YARDLINE_100_MAX",
    "GOAL_LINE_YARDLINE_100_MAX",
    "TWO_MINUTE_HALF_SECONDS_MAX",
    "fetch_and_verify",
    "parse_players_crosswalk_csv",
    "fetch_players_crosswalk",
    "parse_weekly_stats_csv",
    "fetch_weekly_stats_rows",
    "parse_snap_counts_csv",
    "fetch_snap_count_rows",
    "parse_depth_chart_csv",
    "fetch_depth_chart_rows",
    "fetch_injury_rows",
    "build_pbp_opportunity_rows",
    "build_player_game_usage_rows",
    "fetch_season_bundle",
]
