#!/usr/bin/env python3
"""Forward-shadow-only sources (2026 is a live season with no pinned PBP).

`player_derived_team_offense(season)` sums a team's real per-player weekly
stats into the team-offense row shape `team_prior_features` expects. It is
used ONLY for 2026 weeks with no digest-pinned PBP. `validate_against_pbp`
measures how far it sits from the pinned PBP rows on 2025, where both
exist, so the source substitution is quantified rather than assumed.

`load_schedule` reads nflverse/nfldata `games.csv` (a live asset) and
records its SHA-256 and fetch time. It supplies only game identity and
kickoff for the target week, never outcomes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import statistics
import time
import urllib.request
from collections import defaultdict

from nfl.research.nflverse_history import player_stats_url

SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
_SUM_FIELDS = ("attempts", "passing_yards", "sacks_suffered", "passing_epa", "carries", "rushing_yards")


def _fetch(url: str) -> tuple[str, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "full-count-mission10/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def player_derived_team_offense(season: int) -> dict:
    text, sha, fetched = _fetch(player_stats_url(season))
    totals: dict[tuple, dict] = {}
    for raw in csv.DictReader(io.StringIO(text)):
        if (raw.get("season_type") or "").upper() != "REG" or not raw.get("game_id"):
            continue
        key = (raw["game_id"], int(raw["week"]), raw["team"].upper(), raw["opponent_team"].upper())
        t = totals.setdefault(key, {f: 0.0 for f in _SUM_FIELDS})
        for f in _SUM_FIELDS:
            v = raw.get(f)
            t[f] += float(v) if v not in (None, "", "NA") else 0.0
    rows = [
        {"game_id": g, "season": season, "week": w, "season_type": "REG", "team": team, "opponent_team": opp, **t}
        for (g, w, team, opp), t in totals.items()
    ]
    return {"rows": rows, "source_url": player_stats_url(season), "raw_sha256": sha, "fetched_at_utc": fetched}


def validate_against_pbp(season: int, pbp_rows) -> dict:
    derived = {(r["season"], r["week"], r["team"]): r for r in player_derived_team_offense(season)["rows"]}
    diffs = []
    for r in pbp_rows:
        d = derived.get((r["season"], r["week"], r["team"]))
        if d:
            diffs.append((d["attempts"] + d["sacks_suffered"]) - (r["attempts"] + r["sacks_suffered"]))
    return {
        "season": season, "n_team_games_matched": len(diffs),
        "mean_dropback_diff_derived_minus_pbp": statistics.fmean(diffs),
        "mean_abs_dropback_diff": statistics.fmean(abs(x) for x in diffs),
        "max_abs_dropback_diff": max(abs(x) for x in diffs),
    }


def load_schedule(season: int) -> dict:
    text, sha, fetched = _fetch(SCHEDULE_URL)
    games = []
    for raw in csv.DictReader(io.StringIO(text)):
        if raw.get("season") == str(season) and (raw.get("game_type") or "").upper() == "REG":
            games.append({
                "game_id": raw["game_id"], "season": season, "week": int(raw["week"]),
                "away_team": raw["away_team"].upper(), "home_team": raw["home_team"].upper(),
                "gameday": raw.get("gameday"), "gametime": raw.get("gametime"),
            })
    return {"games": games, "source_url": SCHEDULE_URL, "raw_sha256": sha, "fetched_at_utc": fetched}
