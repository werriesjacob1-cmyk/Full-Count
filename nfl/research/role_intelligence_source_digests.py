"""Pinned upstream nflverse asset digests for the WR/RB role-intelligence substrate.

Companion to `game_market_c2_source_digests.py`, which already pins
`SNAP_SOURCE_ASSET_DIGESTS` (nflverse `snap_counts` release, 2012-2025) and
`PBP_SOURCE_ASSET_DIGESTS` (nflverse `pbp` release, 1999-2025).
`role_intelligence_data_prep.py` imports and reuses those two dicts directly
rather than re-pinning the same upstream bytes under a second name. This
module pins only the two sources that workstream did not already need:
the nflverse player-ID crosswalk and the pre-2025 nflverse depth charts.

Every digest below was recorded from the actual bytes downloaded on
2026-09-19 over `https://github.com/nflverse/nflverse-data/releases/download/...`.
`role_intelligence_data_prep.py` fails closed if a re-fetched asset's bytes
ever drift from what is recorded here.

## Player-ID crosswalk

Source: `https://github.com/nflverse/nflverse-data/releases/download/players/players.csv`
(release tag `players`, single non-seasonal asset). Columns include `gsis_id`
and `pfr_id`. This is required because `snap_counts_<season>.csv` only
carries `pfr_player_id` + player name, while every other source this
substrate uses (`stats_player_week`, `injuries`, `depth_charts`) keys on the
stable `gsis_id` already used throughout this repo (see `nflverse_history.py`,
`qb_continuity_features.py`). Joining `snap_counts` by name alone would be
exactly the "name-only join" the dataset contract requires this project to
quarantine rather than trust; going through `players.csv`'s own `pfr_id` ->
`gsis_id` mapping keeps every join on a stable ID.

## Depth charts

Source: `https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_<season>.csv`
(release tag `depth_charts`). Real, disclosed coverage gap found in this
build: nflverse's depth-chart schema breaks completely at the 2025 season.
2012-2024 assets share one schema (`season,club_code,week,game_type,
depth_team,last_name,first_name,football_name,formation,gsis_id,
jersey_number,position,elias_id,depth_position,full_name`, ~3MB/season). The
2025 asset (`depth_charts_2025.csv`, 52,917,870 bytes, verified 2026-09-19)
is a completely different ESPN-derived daily-snapshot schema
(`dt,team,player_name,espn_id,gsis_id,pos_grp_id,pos_grp,pos_id,pos_name,
pos_abb,pos_slot,pos_rank`, no `season`/`week`/`game_type`/`depth_team`
columns at all -- this is exactly the "source break 2025+" the dataset
contract predicted for depth charts. This module therefore pins digests for
2012-2024 only; `role_intelligence_data_prep.py` does not attempt to coerce
the 2025 schema into the pre-2025 shape, and depth-chart-derived features are
UNKNOWN for 2025 rather than silently wrong. Seasons before 2012 are real
nflverse depth-chart releases too (back to 2001 per the dataset contract) but
were not pinned here -- this build's role-state window starts at 2012 to
match `snap_counts`' own start year (see `role_intelligence_data_prep.py`
module docstring for why the whole substrate uses one shared window rather
than a different one per source).
"""
from __future__ import annotations

PLAYERS_CROSSWALK_SOURCE = {
    "upstream": "nflverse/nflverse-data release 'players', asset players.csv",
    "bytes": 7259734,
    "sha256": "801d5fec2fc21c54ad585415e8e551ae9d1de7c601a8c3768504b7ce59b579b6",
}

# nflverse release tag 'depth_charts', asset depth_charts_<season>.csv.
# 2012-2024 only -- see module docstring for the 2025 schema break.
DEPTH_CHART_SOURCE_ASSET_DIGESTS = {
    "2012": {"bytes": 3424734, "sha256": "e091a7fa0343fe3ae0bcee9202e1d8ca1ff859db398d20ab751017df29e85719"},
    "2013": {"bytes": 3402741, "sha256": "68a48cb5c3488aa505c2612c21797353c794c9ce5094e5071cc866373e71fd71"},
    "2014": {"bytes": 2991003, "sha256": "37c5d02473ebec605aaf68747a50ec77f02347f68f16e69cb5d84862f75c6d3a"},
    "2015": {"bytes": 3404905, "sha256": "7c8e3fc562a112faf76e093f36b2a81f6adc731a55d7d52e363f9d2d9b95b0d9"},
    "2016": {"bytes": 3366662, "sha256": "1a462b965a31f70a76568e0900485cec7f6ab202ade15df0a2d0d79f4ffc7537"},
    "2017": {"bytes": 3367336, "sha256": "cc8a9e6991236510ada3fd1a71ed0ebd3df91f2130c8abbf11e69150adc0eb7d"},
    "2018": {"bytes": 3365843, "sha256": "2c44efb1d96f8b8fef8a8ae42c44db340e97921745420d96b012778e184255bb"},
    "2019": {"bytes": 3335603, "sha256": "328405d1feb09cc4b23922c26bf23adf7d6a6e894ba0e7305b88dda793710647"},
    "2020": {"bytes": 3327076, "sha256": "45a315dd7f3e7837faf06a9c9cf995e1c131017a748482b2caaa111ffa0b9372"},
    "2021": {"bytes": 3447809, "sha256": "91e3384ff13146c77f02fed2c3eca163d35868dd62da6b794c5b51df1d15b724"},
    "2022": {"bytes": 3467936, "sha256": "4c64ee59666431c49cbc0a818cc3165886012cf72b545771a77c0805aa621b9c"},
    "2023": {"bytes": 3426502, "sha256": "583f83842590e7e7d3417e0cd9c5c9e38f8696353bb754561d80e5583d2a98cf"},
    "2024": {"bytes": 3391616, "sha256": "f210a33774611f7f78a89af789b61c6289a2631a21227a1a3c63c973c23588b7"},
}

# Recorded so the schema break is checkable, not just asserted in prose.
DEPTH_CHART_SCHEMA_BREAK_SEASONS = frozenset({2025})
DEPTH_CHART_2025_OBSERVED = {
    "bytes": 52917870,
    "header": (
        "dt,team,player_name,espn_id,gsis_id,pos_grp_id,pos_grp,pos_id,"
        "pos_name,pos_abb,pos_slot,pos_rank"
    ),
}

__all__ = [
    "PLAYERS_CROSSWALK_SOURCE",
    "DEPTH_CHART_SOURCE_ASSET_DIGESTS",
    "DEPTH_CHART_SCHEMA_BREAK_SEASONS",
    "DEPTH_CHART_2025_OBSERVED",
]
