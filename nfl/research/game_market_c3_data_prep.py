#!/usr/bin/env python3
"""Fetch and flatten the real nflverse sources C3's join layer needs.

C3 tests the project owner's named hypothesis that C2's margin weakness is
partly explained by missing starter/regime/availability information, by
joining `qb_continuity_features.py`/`injury_availability_features.py`'s
already-merged strictly-prior substrate onto C2's existing 8-feature rows.
Neither of those two modules ships with a pinned local dataset of its own
(they were merged as ingestion/feature-construction modules only, proven
against synthetic fixtures) -- exactly the same situation
`game_market_c2_data_prep.py` found itself in for the team/defense/PBP
substrate, and the same disclosed, digest-checked departure from B0/C1's
"never fetch a moving URL" rule is used here for the same one reason: there
is no way to reproduce QB-continuity/availability features on real NFL
history without *some* source of real weekly QB usage and weekly injury
reports, and none exists pre-pinned anywhere in this project.

Sources (nflverse/nflverse-data, public GitHub releases, one HTTP GET per
season/asset, each digest-checked on arrival against
`game_market_c3_source_digests.py`, exactly mirroring
`game_market_c2_data_prep.py`'s fail-closed-on-drift discipline):

- `stats_player` release, `stats_player_week_<season>.csv`, seasons
  1999-2025: nflverse's weekly player-stat file, the same source
  `qb_continuity_features.py`'s own docstring names
  (`nflverse_history.PLAYER_STATS_URL`) and the same one
  `passing_yards_baseline_research.py` already consumes for QB rows. Only
  QB, REG-season rows are kept in the derived output -- `attempts` is the
  only numeric field `qb_continuity_features.infer_team_week_starters`
  reads, and that function already ignores non-QB/non-REG rows itself, so
  pre-filtering here only reduces file size, it does not change which rows
  contribute to the built features. `game_id` is also kept (beyond
  `qb_continuity_features.REQUIRED_COLUMNS`) because nflverse's real
  `stats_player_week_<season>.csv` normalizes `team`/`opponent_team` to a
  franchise's *current* abbreviation even for historical rows (e.g. a 2009
  San Diego Chargers game shows `team=LAC`), exactly the same class of
  issue `game_market_c2_data_prep.py` already found and fixed for PBP
  `posteam`/`defteam` -- see
  `game_market_c3_features.normalize_qb_player_stat_team_identity`, which
  re-derives the true historical team from `game_id` plus the trusted
  schedule instead of trusting this source's `team`/`opponent_team` fields
  directly.
- `injuries` release, `injuries_<season>.csv`, seasons 2009-2025:
  nflverse's weekly injury/practice-status report, the exact source
  `injury_availability_features.py`'s own docstring documents and
  independently verifies (2009+ coverage floor). Seasons before 2009 are
  never fetched -- `injury_availability_features.season_is_covered` reports
  them as `SEASON_NOT_COVERED_BY_SOURCE` from the season number alone, with
  no network call required, so there is nothing to pin for 1999-2008. Only
  REG-season rows are kept in the derived output, matching C3's game-market
  scope (REG games only, exactly like B0/C1/C2). `date_modified` is kept
  (beyond `injury_availability_features.REQUIRED_COLUMNS`) because nflverse's
  real release contains a small number of genuine same-week status updates
  for the same player -- see
  `game_market_c3_features.INJURY_REPORT_DUPLICATE_STATUS_UPDATE_NOTE` --
  and resolving to the latest filed status needs it.

`main()` also verifies the two files it writes end up with the exact
byte/SHA-256 recorded in `PINNED_QB_PLAYER_STATS_SOURCE` /
`PINNED_INJURY_REPORT_SOURCE` in `game_market_c3_research.py`, so this
script and that module stay in sync by construction rather than by
convention -- the same closed loop `game_market_c2_data_prep.py` keeps with
`game_market_c2_research.py`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from nfl.research.game_market_c3_source_digests import (
    INJURY_SOURCE_ASSET_DIGESTS,
    PLAYER_STATS_SOURCE_ASSET_DIGESTS,
)

PLAYER_STATS_URL_TMPL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
INJURY_URL_TMPL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
PLAYER_STATS_SEASONS = tuple(range(1999, 2026))
INJURY_SEASONS = tuple(range(2009, 2026))

PLAYER_STAT_FIELDS = (
    "player_id", "position", "season", "week", "season_type", "team",
    "opponent_team", "attempts", "game_id",
)
INJURY_FIELDS = (
    "season", "game_type", "team", "week", "gsis_id", "position", "report_status",
    "date_modified",
)


class DataPrepDigestError(ValueError):
    """Raised when a fetched nflverse asset's bytes drift from the recorded pin."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_and_verify(url: str, expected: dict[str, Any], *, tries: int = 4) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "full-count-c3-research/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            break
        except Exception as exc:  # noqa: BLE001 - network errors are retried, then re-raised
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"failed to fetch {url}: {last_exc}")

    if len(data) != expected["bytes"] or sha256_bytes(data) != expected["sha256"]:
        raise DataPrepDigestError(
            f"asset digest drift for {url}: expected bytes={expected['bytes']} "
            f"sha256={expected['sha256']}, got bytes={len(data)} sha256={sha256_bytes(data)}"
        )
    return data


def process_player_stats_season(
    season: int, digests: dict[str, Any], writer: csv.DictWriter
) -> int:
    raw = fetch_and_verify(PLAYER_STATS_URL_TMPL.format(season=season), digests[str(season)])
    text_stream = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig")
    reader = csv.DictReader(text_stream)
    written = 0
    for row in reader:
        if (row.get("position") or "").strip().upper() != "QB":
            continue
        if (row.get("season_type") or "").strip().upper() != "REG":
            continue
        writer.writerow({field: row.get(field, "") for field in PLAYER_STAT_FIELDS})
        written += 1
    return written


def process_injury_season(season: int, digests: dict[str, Any], writer: csv.DictWriter) -> int:
    raw = fetch_and_verify(INJURY_URL_TMPL.format(season=season), digests[str(season)])
    text_stream = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig")
    reader = csv.DictReader(text_stream)
    written = 0
    for row in reader:
        if (row.get("game_type") or "").strip().upper() != "REG":
            continue
        writer.writerow({field: row.get(field, "") for field in INJURY_FIELDS})
        written += 1
    return written


def run(
    *, player_digests: dict[str, Any], injury_digests: dict[str, Any],
    player_stats_out: Path, injuries_out: Path,
) -> dict[str, Any]:
    totals = {"qb_player_stat_rows": 0, "injury_rows": 0}
    with player_stats_out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLAYER_STAT_FIELDS)
        writer.writeheader()
        for season in PLAYER_STATS_SEASONS:
            n = process_player_stats_season(season, player_digests, writer)
            totals["qb_player_stat_rows"] += n
            print(f"player stats season {season}: qb_reg_rows={n}", file=sys.stderr)

    with injuries_out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INJURY_FIELDS)
        writer.writeheader()
        for season in INJURY_SEASONS:
            n = process_injury_season(season, injury_digests, writer)
            totals["injury_rows"] += n
            print(f"injury season {season}: reg_rows={n}", file=sys.stderr)

    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-digests", type=Path, default=None, help=(
        "Optional override: a JSON file with {'player_stats': {season: "
        "{bytes, sha256}}, 'injuries': {season: {bytes, sha256}}}. Defaults "
        "to the pins recorded in game_market_c3_source_digests.py."
    ))
    parser.add_argument("--qb-player-stats-out", type=Path, required=True)
    parser.add_argument("--injuries-out", type=Path, required=True)
    args = parser.parse_args()

    if args.asset_digests is not None:
        digests = json.loads(args.asset_digests.read_text(encoding="utf-8"))
        player_digests, injury_digests = digests["player_stats"], digests["injuries"]
    else:
        player_digests, injury_digests = PLAYER_STATS_SOURCE_ASSET_DIGESTS, INJURY_SOURCE_ASSET_DIGESTS

    totals = run(
        player_digests=player_digests,
        injury_digests=injury_digests,
        player_stats_out=args.qb_player_stats_out,
        injuries_out=args.injuries_out,
    )
    print(json.dumps(totals, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
