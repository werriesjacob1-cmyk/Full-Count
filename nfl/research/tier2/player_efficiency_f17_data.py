"""F17 data layer: per player-game receiving targets with nflfastR expectations,
and a settlement-aligned (snap-played) evaluation population.

Sources (nflverse):
* nflfastR play-by-play ``play_by_play_{season}.csv.gz``. OBSERVED per target:
  receiver, air_yards, complete_pass, yards_gained, yards_after_catch.
  DERIVED (nflfastR model outputs, not observations): ``cp`` (completion
  probability) and ``xyac_mean_yardage`` (expected yards after catch). Their
  published models were trained on historical seasons, so league-level
  structure for older seasons is not strictly out-of-sample (see README).
* PFR snap counts via nflverse ``snap_counts_{season}.csv`` (offense_snaps),
  joined to GSIS ids through nflverse ``players.csv`` (pfr_id -> gsis_id).

A target = pass attempt with an identified receiver, excluding sacks, spikes
and two-point tries. Observed receiving yards on a target = yards_gained if
complete else 0. Expected yards on a target = cp * (air_yards +
xyac_mean_yardage); targets lacking cp or xyac are counted in T/Y but not in
the expectation sums (TX/YX/XY), so observed-minus-expected compares like
with like.

Routes run, yards after contact, missed tackles and separation are NOT in
these sources and are never inferred here.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

POSITIONS = ("WR", "TE", "RB")
FIELDS = ("T", "Y", "TX", "YX", "XY", "CX", "CP", "YOE_C", "NC")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def game_targets(pbp_path: Path) -> list[dict]:
    """One row per (game, receiver): T targets, Y observed yards; over targets
    with both expectations: TX, YX observed yards, XY expected yards, CX
    completions, CP expected completions; over those completions: YOE_C sum of
    (yards_after_catch - xyac_mean_yardage), NC count."""
    agg: dict[tuple, dict] = {}
    with gzip.open(pbp_path, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("pass_attempt") != "1" or r.get("sack") == "1" or r.get("qb_spike") == "1" \
                    or r.get("two_point_attempt") == "1":
                continue
            rid = (r.get("receiver_player_id") or "").strip()
            if rid in ("", "NA"):
                continue
            key = (r["game_id"], rid)
            a = agg.get(key)
            if a is None:
                a = agg[key] = {"season": int(float(r["season"])), "week": int(float(r["week"])),
                                "season_type": r["season_type"], "game_id": r["game_id"],
                                "player_id": rid, **{f: 0.0 for f in FIELDS}}
            complete = r.get("complete_pass") == "1"
            yards = (_f(r.get("yards_gained")) or 0.0) if complete else 0.0
            a["T"] += 1
            a["Y"] += yards
            cp, air, xyac = _f(r.get("cp")), _f(r.get("air_yards")), _f(r.get("xyac_mean_yardage"))
            if cp is None or air is None or xyac is None:
                continue
            a["TX"] += 1
            a["YX"] += yards
            a["XY"] += cp * (air + xyac)
            a["CP"] += cp
            if complete:
                a["CX"] += 1
                yac = _f(r.get("yards_after_catch"))
                if yac is not None:
                    a["YOE_C"] += yac - xyac
                    a["NC"] += 1
    return sorted(agg.values(), key=lambda a: (a["season"], a["week"], a["game_id"], a["player_id"]))


def load_game_targets(pbp_dir: Path, seasons: Iterable[int], cache: Path) -> tuple[list[dict], dict]:
    cache.mkdir(parents=True, exist_ok=True)
    rows, prov = [], {}
    for s in seasons:
        path = Path(pbp_dir) / f"play_by_play_{s}.csv.gz"
        digest = sha256_file(path)
        prov[str(s)] = digest
        out = cache / f"f17_targets_{s}_{digest[:16]}.json.gz"
        if out.exists():
            with gzip.open(out, "rt", encoding="utf-8") as fh:
                part = json.load(fh)
        else:
            part = game_targets(path)
            with gzip.open(out, "wt", encoding="utf-8") as fh:
                json.dump(part, fh)
        rows.extend(part)
    return rows, prov


def load_crosswalk(players_csv: Path) -> dict[str, str]:
    out = {}
    with Path(players_csv).open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            pfr, gsis = (r.get("pfr_id") or "").strip(), (r.get("gsis_id") or "").strip()
            if pfr and gsis and pfr != "NA" and gsis != "NA":
                out[pfr] = gsis
    return out


def snap_population(snap_dir: Path, seasons: Iterable[int], crosswalk: dict[str, str]) -> tuple[list[dict], dict]:
    """REG player-games with offense_snaps > 0 at WR/TE/RB (snap-file position).
    Unmapped PFR ids are excluded and counted, never guessed."""
    rows, diag = [], defaultdict(int)
    for s in seasons:
        path = Path(snap_dir) / f"snap_counts_{s}.csv"
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("game_type") != "REG" or r.get("position") not in POSITIONS:
                    continue
                if (_f(r.get("offense_snaps")) or 0.0) <= 0:
                    diag["zero_offense_snaps"] += 1
                    continue
                gsis = crosswalk.get((r.get("pfr_player_id") or "").strip())
                if gsis is None:
                    diag["unmapped_pfr_id"] += 1
                    continue
                diag["rows"] += 1
                rows.append({"season": int(r["season"]), "week": int(r["week"]), "game_id": r["game_id"],
                             "player_id": gsis, "snap_position": r["position"], "team": r["team"],
                             "opponent_team": r["opponent"], "offense_snaps": _f(r["offense_snaps"])})
    return rows, dict(diag)
