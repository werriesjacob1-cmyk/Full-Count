#!/usr/bin/env python3
"""Real-data loader shared by every Mission 10 target-share script.

One fetch, one cache, so the exploratory diagnosis, the locked holdout
evaluation and the forward-shadow freeze all read byte-identical inputs.

Sources (all already used elsewhere in this repo, nothing new ingested):
- nflverse `stats_player_week_<season>.csv` via
  `nflverse_history.player_stats_url` (REG weeks only).
- PBP-derived team offense rows via `game_market_c2_data_prep.
  process_pbp_season`, digest-pinned by `PBP_SOURCE_ASSET_DIGESTS`
  (1999-2025 only -- 2026 is a live asset and is NOT pinned, so the
  forward shadow never depends on it).

The cache records each source URL, the fetch time and a SHA-256 of the
parsed rows, so a later reader can tell whether two runs used the same data.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import pickle
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nfl.research.game_market_c2_data_prep import process_pbp_season
from nfl.research.game_market_c2_features import filter_team_offense_rows_for_negative_value_bug
from nfl.research.game_market_c2_source_digests import PBP_SOURCE_ASSET_DIGESTS
from nfl.research.nflverse_history import player_stats_url

CACHE_DIR = Path(os.environ.get(
    "FC_TS_CACHE_DIR", "/tmp/claude-0/-home-user-Full-Count/46a4218b-3a66-52d9-8838-d59c25a73d68/scratchpad/ts_cache",
))

_FLOAT_FIELDS = ("targets", "receptions", "attempts", "carries", "receiving_air_yards", "receiving_yards")


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "full-count-mission10/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read().decode("utf-8")


def load_player_weeks(season: int, *, refresh: bool = False) -> dict:
    """REG-season player-week rows for one season, cached. Returns
    {"rows": [...], "source_url", "fetched_at_utc", "rows_sha256"}."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"player_weeks_{season}.pkl"
    if path.exists() and not refresh:
        return pickle.loads(path.read_bytes())
    url = player_stats_url(season)
    text = _fetch_text(url)
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        if (raw.get("season_type") or "").strip().upper() != "REG":
            continue
        player_id = (raw.get("player_id") or "").strip()
        if player_id in ("", "0"):
            continue
        row = {
            "player_id": player_id,
            "player_name": raw.get("player_display_name") or raw.get("player_name") or "",
            "position": (raw.get("position") or "").strip().upper(),
            "season": int(raw["season"]),
            "week": int(raw["week"]),
            "game_id": (raw.get("game_id") or "").strip(),
            "team": (raw.get("team") or "").strip().upper(),
            "opponent_team": (raw.get("opponent_team") or "").strip().upper(),
        }
        for field in _FLOAT_FIELDS:
            value = raw.get(field)
            row[field] = float(value) if value not in (None, "", "NA") else 0.0
        rows.append(row)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    payload = {
        "rows": rows, "source_url": url,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows_sha256": digest,
    }
    path.write_bytes(pickle.dumps(payload))
    return payload


def load_team_offense(season: int, *, refresh: bool = False) -> dict:
    """Digest-pinned PBP-derived team-offense rows for one completed season."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"team_offense_{season}.pkl"
    if path.exists() and not refresh:
        return pickle.loads(path.read_bytes())
    offense_buf, play_buf = io.StringIO(), io.StringIO()
    offense_writer = csv.DictWriter(offense_buf, fieldnames=[
        "game_id", "season", "week", "season_type", "team", "opponent_team",
        "attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards",
    ])
    offense_writer.writeheader()
    play_writer = csv.DictWriter(play_buf, fieldnames=[
        "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
        "down", "half_seconds_remaining", "wp", "qb_dropback", "rush_attempt", "qb_kneel", "qb_spike",
    ])
    play_writer.writeheader()
    process_pbp_season(season, PBP_SOURCE_ASSET_DIGESTS, offense_writer, play_writer)
    offense_buf.seek(0)
    rows = list(csv.DictReader(offense_buf))
    for row in rows:
        row["season"] = int(row["season"])
        row["week"] = int(row["week"])
        for f in ("attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards"):
            row[f] = float(row[f])
    kept, excluded = filter_team_offense_rows_for_negative_value_bug(rows)
    payload = {
        "rows": kept, "n_excluded_negative_value_bug": len(excluded),
        "source": f"PBP_SOURCE_ASSET_DIGESTS[{season}]",
        "rows_sha256": hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest(),
    }
    path.write_bytes(pickle.dumps(payload))
    return payload


_CROSSWALK = None


def load_snap_rows(season: int, *, refresh: bool = False) -> dict:
    """Digest-pinned snap-count rows (`role_intelligence_data_prep.
    fetch_snap_count_rows`, reused unmodified), cached."""
    global _CROSSWALK
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"snap_rows_{season}.pkl"
    if path.exists() and not refresh:
        return pickle.loads(path.read_bytes())
    from nfl.research.role_intelligence_data_prep import fetch_players_crosswalk, fetch_snap_count_rows
    if _CROSSWALK is None:
        _CROSSWALK = fetch_players_crosswalk()
    rows = fetch_snap_count_rows(season, _CROSSWALK)
    payload = {
        "rows": rows, "source": f"SNAP_SOURCE_ASSET_DIGESTS[{season}] via fetch_snap_count_rows",
        "rows_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
    }
    path.write_bytes(pickle.dumps(payload))
    return payload


if __name__ == "__main__":
    t0 = time.time()
    seasons = [int(s) for s in sys.argv[1:]] or list(range(2018, 2027))
    for season in seasons:
        p = load_player_weeks(season)
        msg = f"player_weeks {season}: {len(p['rows'])} rows sha={p['rows_sha256'][:12]}"
        if season <= 2025:
            t = load_team_offense(season)
            msg += f" | team_offense {len(t['rows'])} rows sha={t['rows_sha256'][:12]}"
        print(msg, f"{time.time()-t0:.1f}s", flush=True)
