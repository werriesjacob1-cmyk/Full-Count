"""Tier 2 F11/F12 data layer: coverage-labelled dropbacks with on-field players.

Joins nflfastR play-by-play (dropback, target, completion, yards) to nflverse
`pbp_participation` (defensive coverage labels and the ids of every offensive
player on the field) on (game_id, play_id).

Source facts this module encodes (measured 2026-09-25, see README):

* Coverage labels (`defense_man_zone_type`, `defense_coverage_type`) exist
  only for seasons 2018-2025. 2018-2022 come from NFL NGS; 2023-2025 from
  FTN, which uses a different taxonomy (adds COMBO, COVER_9, BLOWN).
* `route` is recorded for the TARGETED receiver only -- it is
  target-conditioned by construction and is deliberately not used here.
* The only all-player opportunity denominator is on-field participation
  (`offense_players`). Opportunity is therefore "targets per on-field
  coverage-labelled dropback" -- NOT targets per route run. Routes run are
  not observed and are never inferred.
* Participation for 2023+ is released only after the postseason, so no
  in-season participation exists. Feature builders must use only COMPLETED
  seasons before the target season (see coverage_features).

Unknown labels stay the string ``UNKNOWN``; they never become MAN or ZONE.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable

UNKNOWN = "UNKNOWN"
COVERAGE_SEASONS = tuple(range(2018, 2026))
NGS_SEASONS = tuple(range(2018, 2023))
FTN_SEASONS = tuple(range(2023, 2026))

MAN_ZONE = {"MAN_COVERAGE": "MAN", "ZONE_COVERAGE": "ZONE"}
# Families shared by both taxonomies are kept; source-specific or degenerate
# labels collapse to OTHER (never silently merged into a real family).
FAMILIES = {"COVER_0": "COVER_0", "COVER_1": "COVER_1", "COVER_2": "COVER_2",
            "COVER_3": "COVER_3", "COVER_4": "COVER_4", "COVER_6": "COVER_6",
            "2_MAN": "2_MAN"}
OTHER_FAMILY = "OTHER"

# Franchise relocations: normalize both pbp and weekly-stats team codes.
TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}


def team(code: str) -> str:
    code = (code or "").strip()
    return TEAM_ALIASES.get(code, code)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _participation_index(path: Path) -> dict[tuple[str, str], dict]:
    out = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            mz = MAN_ZONE.get((r.get("defense_man_zone_type") or "").strip(), UNKNOWN)
            fam_raw = (r.get("defense_coverage_type") or "").strip()
            if fam_raw in ("", "NA"):
                fam = UNKNOWN
            else:
                fam = FAMILIES.get(fam_raw, OTHER_FAMILY)
            players = [p for p in (r.get("offense_players") or "").split(";") if p.strip()]
            out[(r["nflverse_game_id"], str(r["play_id"]).split(".")[0])] = {
                "man_zone": mz, "family": fam, "family_raw": fam_raw or UNKNOWN,
                "offense_players": players}
    return out


def _num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_dropbacks(season: int, pbp_path: Path, participation_path: Path) -> list[dict]:
    """One row per REG/POST dropback (sacks and spikes excluded) that has a
    participation record. Coverage fields may be UNKNOWN."""
    part = _participation_index(participation_path)
    rows = []
    with gzip.open(pbp_path, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("qb_dropback") != "1" or r.get("pass_attempt") != "1":
                continue
            if r.get("sack") == "1" or r.get("qb_spike") == "1" or r.get("two_point_attempt") == "1":
                continue
            key = (r["game_id"], str(r["play_id"]).split(".")[0])
            p = part.get(key)
            if p is None or not p["offense_players"]:
                continue
            target = (r.get("receiver_player_id") or "").strip()
            rows.append({
                "season": season, "week": int(float(r["week"])), "season_type": r["season_type"],
                "game_id": r["game_id"], "play_id": key[1],
                "posteam": team(r["posteam"]), "defteam": team(r["defteam"]),
                "man_zone": p["man_zone"], "family": p["family"], "family_raw": p["family_raw"],
                "offense_players": p["offense_players"],
                "target": target if target not in ("", "NA") else None,
                "complete": r.get("complete_pass") == "1",
                "yards": _num(r.get("yards_gained")) if r.get("complete_pass") == "1" else 0.0,
            })
    return rows


def build_all(pbp_dir: Path, participation_dir: Path, cache: Path,
              seasons: Iterable[int] = COVERAGE_SEASONS) -> tuple[list[dict], dict]:
    """Build (or load) the dropback table for `seasons`; returns rows and a
    provenance dict with the SHA-256 of every source file."""
    cache.mkdir(parents=True, exist_ok=True)
    rows, prov = [], {"pbp": {}, "participation": {}}
    for season in seasons:
        pbp = pbp_dir / f"play_by_play_{season}.csv.gz"
        par = participation_dir / f"pbp_participation_{season}.csv"
        prov["pbp"][str(season)] = sha256_file(pbp)
        prov["participation"][str(season)] = sha256_file(par)
        out = cache / f"dropbacks_{season}_{prov['pbp'][str(season)][:12]}_{prov['participation'][str(season)][:12]}.json.gz"
        if out.exists():
            with gzip.open(out, "rt", encoding="utf-8") as fh:
                part = json.load(fh)
        else:
            part = build_dropbacks(season, pbp, par)
            with gzip.open(out, "wt", encoding="utf-8") as fh:
                json.dump(part, fh)
        rows.extend(part)
    return rows, prov
