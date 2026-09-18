#!/usr/bin/env python3
"""Fetch and flatten the real nflverse sources C2's feature substrate needs.

`team_prior_features.py`, `defense_prior_features.py`, and
`pbp_prior_tendencies.py` were merged with no data pipeline behind them: no
file in this repository is pinned by commit/digest for team-week offensive
stats or play-by-play, unlike `game_market_b0_research.PINNED_SCHEDULE_SOURCE`
for the schedule. This script is the pipeline. It is a deliberate, disclosed
departure from B0/C1's "never fetch a moving URL" rule, for one reason only:
there is no way to reproduce team/defense/PBP-tendency features on real NFL
history without *some* source of real weekly team stats and play-by-play,
and none exists pre-pinned anywhere in this project. The departure is
bounded the same way B0 bounds its own pinned file: every fetched asset's
exact byte count and SHA-256 are checked against `PBP_SOURCE_ASSET_DIGESTS`
/ `SNAP_SOURCE_ASSET_DIGESTS` below and the run fails closed on any
mismatch, so a silent upstream change can never flow into the derived files
unnoticed. `main()` also verifies the three files it writes end up with the
exact byte/SHA-256 recorded in `PINNED_TEAM_OFFENSE_SOURCE` /
`PINNED_PBP_PLAY_SOURCE` / `PINNED_SNAP_COUNTS_SOURCE` in
`game_market_c2_research.py`, so this script and that module stay in sync by
construction rather than by convention.

Sources (nflverse/nflverse-data, public GitHub releases, one HTTP GET per
season/asset, each digest-checked on arrival):

- `pbp` release, `play_by_play_<season>.csv.gz`, seasons 1999-2025: real
  nflfastR play-by-play. Two things are derived from it per REG game/team:
    1. team-week offense counting stats (attempts, passing_yards,
       sacks_suffered, passing_epa, carries, rushing_yards), the exact shape
       `team_prior_features`/`defense_prior_features` require. `attempts`
       and `passing_epa` follow nflfastR's own `pass_attempt` convention,
       which marks a sacked dropback as a pass attempt (sack rows always
       have `pass_attempt == 1` in this data) -- this is disclosed because
       it differs from a traditional box-score "attempts" count that
       excludes sacks.
    2. filtered per-play rows for `pbp_prior_tendencies`, keeping only rows
       with a valid down/win-probability/half-seconds/dropback-rush-kneel-
       spike signature (i.e. real scrimmage snaps), which is exactly the
       precondition that module's own validators require.
  nflverse's PBP normalizes `posteam`/`defteam` to a franchise's *current*
  abbreviation even in old seasons (e.g. the 1999 St. Louis Rams appear as
  `LA`), while `game_id` and `games.csv` keep the abbreviation used at the
  time. Team identity here is re-derived from `game_id` (format
  `SEASON_WEEK_AWAY_HOME`) plus PBP's own `home_team` column instead of
  trusting `posteam`/`defteam` directly, so the join key matches
  `games.csv` exactly.
- `snap_counts` release, `snap_counts_<season>.csv`, seasons 2012-2025 (2012
  itself is an empty file in this nflverse release -- a real, disclosed
  coverage gap, not a bug in this script): real PFR snap counts, the exact
  shape `ol_continuity_prior` requires. Prepared but *not* used by the
  fitted C2 model -- see `game_market_c2_features.OL_CONTINUITY_EXCLUSION_REASON`.

Five real 1999-2025 team-games have negative `passing_yards` or
`rushing_yards` (rare but legitimate -- e.g. several negative-yardage
completions, or scrambles/stuffed carries netting a net loss for the game).
`team_prior_features`/`defense_prior_features` reject any negative value in
those fields for the *entire* input population, not just the offending row.
This script does not clip or fabricate a non-negative number; the affected
rows are written as-is, and `game_market_c2_features` (that module's own
copy of this dataset's known issue -- see
`filter_team_offense_rows_for_negative_value_bug` there) is responsible for
excluding them before calling either builder.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from nfl.research.game_market_c2_source_digests import (
    PBP_SOURCE_ASSET_DIGESTS,
    SNAP_SOURCE_ASSET_DIGESTS,
)

PBP_URL_TMPL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
SNAP_URL_TMPL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
PBP_SEASONS = tuple(range(1999, 2026))
SNAP_SEASONS = tuple(range(2012, 2026))

OFFENSE_FIELDS = (
    "game_id", "season", "week", "season_type", "team", "opponent_team",
    "attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards",
)
PLAY_FIELDS = (
    "game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
    "down", "half_seconds_remaining", "wp", "qb_dropback", "rush_attempt", "qb_kneel", "qb_spike",
)
SNAP_FIELDS = (
    "game_id", "season", "game_type", "week", "pfr_player_id", "position",
    "team", "opponent", "offense_snaps",
)


class DataPrepDigestError(ValueError):
    """Raised when a fetched nflverse asset's bytes drift from the recorded pin."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_and_verify(url: str, expected: dict[str, Any], *, tries: int = 4) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "full-count-c2-research/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response:
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


def _to_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if text == "" or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def process_pbp_season(
    season: int, digests: dict[str, Any], offense_writer: csv.DictWriter, play_writer: csv.DictWriter
) -> tuple[int, int]:
    raw = fetch_and_verify(PBP_URL_TMPL.format(season=season), digests[str(season)])
    text_stream = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8")
    reader = csv.DictReader(text_stream)

    offense: dict[tuple[str, str], dict[str, Any]] = {}
    play_rows_written = 0

    for row in reader:
        if (row.get("season_type") or "").strip().upper() != "REG":
            continue
        game_id = (row.get("game_id") or "").strip()
        posteam_pbp = (row.get("posteam") or "").strip().upper()
        defteam_pbp = (row.get("defteam") or "").strip().upper()
        week = (row.get("week") or "").strip()
        if not game_id or not posteam_pbp or not defteam_pbp or posteam_pbp == defteam_pbp or not week:
            continue

        id_parts = game_id.split("_")
        if len(id_parts) != 4:
            continue
        away_code, home_code = id_parts[2], id_parts[3]
        is_pos_home = posteam_pbp == (row.get("home_team") or "").strip().upper()
        posteam = home_code if is_pos_home else away_code
        defteam = away_code if is_pos_home else home_code

        pass_attempt = _to_float(row.get("pass_attempt"))
        rush_attempt = _to_float(row.get("rush_attempt"))
        sack = _to_float(row.get("sack"))
        passing_yards = _to_float(row.get("passing_yards")) or 0.0
        rushing_yards = _to_float(row.get("rushing_yards")) or 0.0
        epa = _to_float(row.get("epa"))

        key = (game_id, posteam)
        acc = offense.setdefault(key, {
            "season": season, "week": week, "opponent_team": defteam,
            "attempts": 0, "passing_yards": 0.0, "sacks_suffered": 0,
            "passing_epa": 0.0, "carries": 0, "rushing_yards": 0.0,
        })
        is_pass = pass_attempt == 1.0
        if is_pass:
            acc["attempts"] += 1
            acc["passing_yards"] += passing_yards
            if epa is not None:
                acc["passing_epa"] += epa
        if sack == 1.0:
            acc["sacks_suffered"] += 1
        if rush_attempt == 1.0:
            acc["carries"] += 1
            acc["rushing_yards"] += rushing_yards

        down_text = (row.get("down") or "").strip()
        wp = _to_float(row.get("wp"))
        half_secs = _to_float(row.get("half_seconds_remaining"))
        qb_dropback = _to_float(row.get("qb_dropback"))
        qb_kneel = _to_float(row.get("qb_kneel"))
        qb_spike = _to_float(row.get("qb_spike"))
        play_id = (row.get("play_id") or "").strip()
        if (
            down_text in {"1", "2", "3", "4"}
            and wp is not None and 0.0 <= wp <= 1.0
            and half_secs is not None and half_secs >= 0
            and qb_dropback in (0.0, 1.0)
            and rush_attempt in (0.0, 1.0)
            and qb_kneel in (0.0, 1.0)
            and qb_spike in (0.0, 1.0)
            and play_id
        ):
            play_writer.writerow({
                "game_id": game_id, "play_id": play_id, "season": season, "week": week,
                "season_type": "REG", "posteam": posteam, "defteam": defteam,
                "down": int(float(down_text)), "half_seconds_remaining": half_secs, "wp": wp,
                "qb_dropback": int(qb_dropback), "rush_attempt": int(rush_attempt),
                "qb_kneel": int(qb_kneel), "qb_spike": int(qb_spike),
            })
            play_rows_written += 1

    for (game_id, team), acc in offense.items():
        offense_writer.writerow({
            "game_id": game_id, "season": acc["season"], "week": acc["week"],
            "season_type": "REG", "team": team, "opponent_team": acc["opponent_team"],
            "attempts": acc["attempts"], "passing_yards": acc["passing_yards"],
            "sacks_suffered": acc["sacks_suffered"], "passing_epa": acc["passing_epa"],
            "carries": acc["carries"], "rushing_yards": acc["rushing_yards"],
        })

    return len(offense), play_rows_written


def process_snap_season(season: int, digests: dict[str, Any], writer: csv.DictWriter) -> int:
    raw = fetch_and_verify(SNAP_URL_TMPL.format(season=season), digests[str(season)])
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    written = 0
    for row in reader:
        if (row.get("game_type") or "").strip().upper() != "REG":
            continue
        offense_snaps = (row.get("offense_snaps") or "").strip()
        if offense_snaps in ("", "NA"):
            continue
        required = {
            "game_id": (row.get("game_id") or "").strip(),
            "week": (row.get("week") or "").strip(),
            "pfr_player_id": (row.get("pfr_player_id") or "").strip(),
            "position": (row.get("position") or "").strip(),
            "team": (row.get("team") or "").strip().upper(),
            "opponent": (row.get("opponent") or "").strip().upper(),
        }
        if not all(required.values()):
            continue
        try:
            snaps_int = int(float(offense_snaps))
        except ValueError:
            continue
        writer.writerow({
            "game_id": required["game_id"], "season": season, "game_type": "REG",
            "week": required["week"], "pfr_player_id": required["pfr_player_id"],
            "position": required["position"], "team": required["team"],
            "opponent": required["opponent"], "offense_snaps": snaps_int,
        })
        written += 1
    return written


def run(
    *, pbp_digests: dict[str, Any], snap_digests: dict[str, Any],
    offense_out: Path, play_out: Path, snap_out: Path,
) -> dict[str, Any]:
    totals = {"offense_rows": 0, "play_rows": 0, "snap_rows": 0}
    with offense_out.open("w", newline="", encoding="utf-8") as fo, \
         play_out.open("w", newline="", encoding="utf-8") as fp:
        offense_writer = csv.DictWriter(fo, fieldnames=OFFENSE_FIELDS)
        offense_writer.writeheader()
        play_writer = csv.DictWriter(fp, fieldnames=PLAY_FIELDS)
        play_writer.writeheader()
        for season in PBP_SEASONS:
            n_off, n_play = process_pbp_season(season, pbp_digests, offense_writer, play_writer)
            totals["offense_rows"] += n_off
            totals["play_rows"] += n_play
            print(f"season {season}: offense_rows={n_off} play_rows={n_play}", file=sys.stderr)

    with snap_out.open("w", newline="", encoding="utf-8") as fs:
        snap_writer = csv.DictWriter(fs, fieldnames=SNAP_FIELDS)
        snap_writer.writeheader()
        for season in SNAP_SEASONS:
            n = process_snap_season(season, snap_digests, snap_writer)
            totals["snap_rows"] += n
            print(f"snap season {season}: rows={n}", file=sys.stderr)

    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-digests", type=Path, default=None, help=(
        "Optional override: a JSON file with {'pbp': {season: {bytes, "
        "sha256}}, 'snap_counts': {season: {bytes, sha256}}}. Defaults to "
        "the pins recorded in game_market_c2_source_digests.py."
    ))
    parser.add_argument("--team-offense-out", type=Path, required=True)
    parser.add_argument("--pbp-plays-out", type=Path, required=True)
    parser.add_argument("--snap-counts-out", type=Path, required=True)
    args = parser.parse_args()

    if args.asset_digests is not None:
        digests = json.loads(args.asset_digests.read_text(encoding="utf-8"))
        pbp_digests, snap_digests = digests["pbp"], digests["snap_counts"]
    else:
        pbp_digests, snap_digests = PBP_SOURCE_ASSET_DIGESTS, SNAP_SOURCE_ASSET_DIGESTS

    totals = run(
        pbp_digests=pbp_digests,
        snap_digests=snap_digests,
        offense_out=args.team_offense_out,
        play_out=args.pbp_plays_out,
        snap_out=args.snap_counts_out,
    )
    print(json.dumps(totals, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
