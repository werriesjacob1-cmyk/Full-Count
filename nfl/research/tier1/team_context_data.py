"""Tier 1 workstream C data layer: schedule context, stadiums, PBP team summaries.

Everything here turns raw, hashed nflverse sources into compact per-game
summaries. It computes NO rolling or prior feature; `team_context_features`
does that under the strictly-prior rule.

Sources (all read-only; SHA-256 recorded by the evaluation script):

* nflverse/nfldata ``games.csv`` -- schedule, rest, roof, surface, stadium,
  CLOSING spread/total (retrospective control only), OBSERVED temp/wind
  (never used as a pregame feature here).
* nflfastR play-by-play -- per-team-game offensive volume/tendency summaries
  and per-defense allowed-by-receiver-position summaries.
* Stadium coordinates: the English Wikipedia coordinates API
  (``prop=coordinates``) for the article titles in `STADIUMS`; the raw
  response is saved and hashed. Time zones are IANA zone names assigned
  per stadium city (static facts, listed below for review).

Team abbreviations are normalized to current franchise codes
(OAK->LV, SD->LAC, STL->LA) because nflfastR PBP and nflverse weekly stats
already use current codes while games.csv keeps historical ones.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from nfl.research.tier1.contract import UNKNOWN

TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
ET = ZoneInfo("America/New_York")

# games.csv stadium name -> (Wikipedia article title, IANA time zone).
STADIUMS: dict[str, tuple[str, str]] = {
    "Georgia Dome": ("Georgia Dome", "America/New_York"),
    "Mercedes-Benz Stadium": ("Mercedes-Benz Stadium", "America/New_York"),
    "M&T Bank Stadium": ("M&T Bank Stadium", "America/New_York"),
    "Gillette Stadium": ("Gillette Stadium", "America/New_York"),
    "Highmark Stadium": ("New Era Field", "America/New_York"),
    "New Era Field": ("New Era Field", "America/New_York"),
    "Bank of America Stadium": ("Bank of America Stadium", "America/New_York"),
    "Soldier Field": ("Soldier Field", "America/Chicago"),
    "Paul Brown Stadium": ("Paycor Stadium", "America/New_York"),
    "Paycor Stadium": ("Paycor Stadium", "America/New_York"),
    "FirstEnergy Stadium": ("Cleveland Browns Stadium", "America/New_York"),
    "Huntington Bank Field": ("Cleveland Browns Stadium", "America/New_York"),
    "AT&T Stadium": ("AT&T Stadium", "America/Chicago"),
    "Empower Field at Mile High": ("Empower Field at Mile High", "America/Denver"),
    "Sports Authority Field at Mile High": ("Empower Field at Mile High", "America/Denver"),
    "Ford Field": ("Ford Field", "America/Detroit"),
    "Deutsche Bank Park": ("Waldstadion (Frankfurt)", "Europe/Berlin"),
    "Allianz Arena": ("Allianz Arena", "Europe/Berlin"),
    "FC Bayern Munich Stadium": ("Allianz Arena", "Europe/Berlin"),
    "Lambeau Field": ("Lambeau Field", "America/Chicago"),
    "NRG Stadium": ("NRG Stadium", "America/Chicago"),
    "Reliant Stadium": ("NRG Stadium", "America/Chicago"),
    "Lucas Oil Stadium": ("Lucas Oil Stadium", "America/Indiana/Indianapolis"),
    "EverBank Field": ("EverBank Stadium", "America/New_York"),
    "EverBank Stadium": ("EverBank Stadium", "America/New_York"),
    "TIAA Bank Stadium": ("EverBank Stadium", "America/New_York"),
    "Tottenham Hotspur Stadium": ("Tottenham Hotspur Stadium", "Europe/London"),
    "Tottenham Stadium": ("Tottenham Hotspur Stadium", "Europe/London"),
    "Arrowhead Stadium": ("Arrowhead Stadium", "America/Chicago"),
    "GEHA Field at Arrowhead Stadium": ("Arrowhead Stadium", "America/Chicago"),
    "SoFi Stadium": ("SoFi Stadium", "America/Los_Angeles"),
    "StubHub Center": ("Dignity Health Sports Park", "America/Los_Angeles"),
    "Los Angeles Memorial Coliseum": ("Los Angeles Memorial Coliseum", "America/Los_Angeles"),
    "Wembley Stadium": ("Wembley Stadium", "Europe/London"),
    "Twickenham Stadium": ("Twickenham Stadium", "Europe/London"),
    "Bernabeu": ("Santiago Bernabéu Stadium", "Europe/Madrid"),
    "Melbourne Cricket Ground": ("Melbourne Cricket Ground", "Australia/Melbourne"),
    "Azteca Stadium": ("Estadio Azteca", "America/Mexico_City"),
    "Estadio Banorte": ("Estadio Azteca", "America/Mexico_City"),
    "Hard Rock Stadium": ("Hard Rock Stadium", "America/New_York"),
    "U.S. Bank Stadium": ("U.S. Bank Stadium", "America/Chicago"),
    "Nissan Stadium": ("Nissan Stadium (Nashville)", "America/Chicago"),
    "Caesars Superdome": ("Caesars Superdome", "America/Chicago"),
    "Mercedes-Benz Superdome": ("Caesars Superdome", "America/Chicago"),
    "MetLife Stadium": ("MetLife Stadium", "America/New_York"),
    "Oakland-Alameda County Coliseum": ("Oakland Coliseum", "America/Los_Angeles"),
    "Ring Central Coliseum": ("Oakland Coliseum", "America/Los_Angeles"),
    "Stade de France": ("Stade de France", "Europe/Paris"),
    "Lincoln Financial Field": ("Lincoln Financial Field", "America/New_York"),
    "State Farm Stadium": ("State Farm Stadium", "America/Phoenix"),
    "University of Phoenix Stadium": ("State Farm Stadium", "America/Phoenix"),
    "Acrisure Stadium": ("Acrisure Stadium", "America/New_York"),
    "Heinz Field": ("Acrisure Stadium", "America/New_York"),
    "Maracana Stadium": ("Maracanã Stadium", "America/Sao_Paulo"),
    "Arena Corinthians": ("Arena Corinthians", "America/Sao_Paulo"),
    "Qualcomm Stadium": ("San Diego Stadium", "America/Los_Angeles"),
    "CenturyLink Field": ("Lumen Field", "America/Los_Angeles"),
    "Lumen Field": ("Lumen Field", "America/Los_Angeles"),
    "Levi's Stadium": ("Levi's Stadium", "America/Los_Angeles"),
    "Raymond James Stadium": ("Raymond James Stadium", "America/New_York"),
    "Allegiant Stadium": ("Allegiant Stadium", "America/Los_Angeles"),
    "FedExField": ("Northwest Stadium", "America/New_York"),
    "Northwest Stadium": ("Northwest Stadium", "America/New_York"),
}

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
GRASS_SURFACES = {"grass", "dessograss"}
TURF_SURFACES = {"fieldturf", "matrixturf", "sportturf", "astroturf", "a_turf", "astroplay"}


def norm_team(team: str) -> str:
    team = (team or "").strip().upper()
    return TEAM_ALIASES.get(team, team)


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- stadiums
def fetch_stadium_coordinates(out_dir: Path, session: Any = None) -> dict[str, Any]:
    """Fetch Wikipedia coordinates for every STADIUMS title; save raw + table.

    Returns {"table": {title: {"lat", "lon"}}, "raw_sha256", "retrieved_at"}.
    """
    import requests  # local import: tests never hit the network

    session = session or requests.Session()
    session.headers.update({"User-Agent": "FullCountResearch/1.0 (github.com/werriesjacob1-cmyk/Full-Count)"})
    titles = sorted({title for title, _tz in STADIUMS.values()})
    raw_pages: list[Any] = []
    table: dict[str, dict[str, float]] = {}
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for start in range(0, len(titles), 20):
        chunk = titles[start:start + 20]
        for attempt in range(6):
            resp = session.get(WIKIPEDIA_API, params={
                "action": "query", "titles": "|".join(chunk), "prop": "coordinates",
                "redirects": 1, "colimit": "max", "coprimary": "primary",
                "format": "json"}, timeout=60)
            if resp.status_code != 429:
                break
            time.sleep(5 * (attempt + 1))
        resp.raise_for_status()
        body = resp.json()
        raw_pages.append(body)
        query = body.get("query", {})
        back = {t: t for t in chunk}
        for key in ("normalized", "redirects"):
            for item in query.get(key, []):
                for original, current in list(back.items()):
                    if current == item["from"]:
                        back[original] = item["to"]
        by_title = {p.get("title"): p for p in query.get("pages", {}).values()}
        for original in chunk:
            page = by_title.get(back[original])
            coords = (page or {}).get("coordinates") or []
            if coords:
                table[original] = {"lat": float(coords[0]["lat"]), "lon": float(coords[0]["lon"]),
                                   "resolved_title": back[original]}
    raw = json.dumps(raw_pages, sort_keys=True, ensure_ascii=False).encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wikipedia_stadium_coordinates_raw.json").write_bytes(raw)
    result = {"source": WIKIPEDIA_API, "retrieved_at": retrieved_at,
              "raw_sha256": sha256_bytes(raw), "table": table,
              "missing_titles": sorted(set(titles) - set(table))}
    (out_dir / "stadium_coordinates.json").write_text(json.dumps(result, indent=2, sort_keys=True,
                                                                 ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def stadium_location(name: str, coords: Mapping[str, Mapping[str, float]]) -> dict[str, Any] | None:
    entry = STADIUMS.get((name or "").strip())
    if entry is None:
        return None
    title, tz = entry
    point = coords.get(title)
    if point is None:
        return None
    return {"title": title, "tz": tz, "lat": point["lat"], "lon": point["lon"]}


# --------------------------------------------------------------------------- schedule
def kickoff_utc(gameday: str, gametime: str) -> datetime | None:
    """games.csv gameday/gametime are US Eastern local time."""
    if not gameday or not gametime:
        return None
    local = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return local.astimezone(timezone.utc)


def _float_or_unknown(value: str) -> Any:
    text = (value or "").strip()
    if text in ("", "NA"):
        return UNKNOWN
    out = float(text)
    return out if math.isfinite(out) else UNKNOWN


def load_schedule(path: Path, *, first_season: int = 2015) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            season = int(raw["season"])
            if season < first_season:
                continue
            rows.append({
                "game_id": raw["game_id"], "season": season, "week": int(raw["week"]),
                "game_type": raw["game_type"], "gameday": raw["gameday"], "gametime": raw["gametime"],
                "weekday": raw["weekday"], "away_team": norm_team(raw["away_team"]),
                "home_team": norm_team(raw["home_team"]), "location": raw["location"],
                "away_score": raw["away_score"], "home_score": raw["home_score"],
                "away_rest": _float_or_unknown(raw["away_rest"]),
                "home_rest": _float_or_unknown(raw["home_rest"]),
                "spread_line": _float_or_unknown(raw["spread_line"]),
                "total_line": _float_or_unknown(raw["total_line"]),
                "roof": (raw["roof"] or "").strip(), "surface": (raw["surface"] or "").strip().lower(),
                "stadium_id": raw["stadium_id"], "stadium": (raw["stadium"] or "").strip(),
                "completed": (raw["home_score"] or "").strip() not in ("", "NA"),
            })
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    return rows


def stadium_roof_types(schedule: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Static roof type per stadium (Wikipedia title), from COMPLETED games only.

    games.csv `roof` semantics (verified on 2016-2025 values): 'dome' = fixed
    roof, 'outdoors' = open-air, 'open'/'closed' = the OBSERVED state of a
    retractable roof on game day (decided on the day, often because of the
    weather) -- so the state is not used; only 'retractable' is. Blank values
    and unplayed games are not evidence. 2026 future rows carry contradictory
    placeholders (e.g. 'dome' for Melbourne Cricket Ground, open-air), which
    is why stadiums without a completed game stay UNKNOWN.
    """
    seen: dict[str, Counter] = defaultdict(Counter)
    for row in schedule:
        entry = STADIUMS.get(row["stadium"])
        if entry is None or not row["completed"] or not row["roof"]:
            continue
        seen[entry[0]][row["roof"]] += 1
    out = {}
    for title, counts in seen.items():
        if counts.get("open") or counts.get("closed"):
            out[title] = "retractable"
        elif len(counts) == 1:
            out[title] = next(iter(counts))
        else:
            out[title] = UNKNOWN  # contradictory history
    return out


def surface_type(surface: str) -> str:
    s = (surface or "").strip().lower()
    if s in GRASS_SURFACES:
        return "grass"
    if s in TURF_SURFACES:
        return "turf"
    return UNKNOWN


def team_home_stadiums(schedule: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], str]:
    """(season, team) -> modal home stadium title among that season's Home games.

    The season schedule (venues) is published before the season, so this is
    a pregame fact."""
    counts: dict[tuple[int, str], Counter] = defaultdict(Counter)
    for row in schedule:
        entry = STADIUMS.get(row["stadium"])
        if row["location"] == "Home" and entry is not None:
            counts[(row["season"], row["home_team"])][entry[0]] += 1
    return {key: c.most_common(1)[0][0] for key, c in counts.items()}


def team_game_context(schedule: list[Mapping[str, Any]], coords: Mapping[str, Mapping[str, float]]
                      ) -> dict[tuple[str, str], dict[str, Any]]:
    """(game_id, team) -> F1/F6/F7 static and market context for that team-game.

    F1 uses the CLOSING spread/total from games.csv: label
    CLOSING_LINE_PROXY_RETROSPECTIVE. nflverse `spread_line` is the expected
    HOME margin (positive = home favored), so the team's expected margin is
    +spread for home, -spread for away and the implied team total is
    total/2 + team_margin/2.
    """
    roof_types = stadium_roof_types(schedule)
    homes = team_home_stadiums(schedule)
    title_tz = {title: tz for title, tz in STADIUMS.values()}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for g in schedule:
        loc = stadium_location(g["stadium"], coords)
        ko = kickoff_utc(g["gameday"], g["gametime"])
        for side, team, opp in (("home", g["home_team"], g["away_team"]),
                                ("away", g["away_team"], g["home_team"])):
            spread, total = g["spread_line"], g["total_line"]
            if spread == UNKNOWN or total == UNKNOWN:
                margin = implied = UNKNOWN
            else:
                margin = spread if side == "home" else -spread
                implied = total / 2.0 + margin / 2.0
            rest = g[f"{side}_rest"]
            opp_rest = g["away_rest" if side == "home" else "home_rest"]
            row: dict[str, Any] = {
                "game_id": g["game_id"], "season": g["season"], "week": g["week"],
                "game_type": g["game_type"], "team": team, "opponent": opp, "side": side,
                "neutral_site": g["location"] == "Neutral",
                "kickoff_utc": ko.isoformat().replace("+00:00", "Z") if ko else UNKNOWN,
                # F1 (closing lines, retrospective proxy)
                "f1_team_margin_closing": margin, "f1_total_closing": total,
                "f1_implied_team_total_closing": implied,
                # F7 rest
                "f7_rest_days": rest,
                "f7_rest_diff": (rest - opp_rest) if UNKNOWN not in (rest, opp_rest) else UNKNOWN,
                "f7_short_week": (rest <= 5) if rest != UNKNOWN else UNKNOWN,
                "f7_post_bye": (rest >= 13) if rest != UNKNOWN else UNKNOWN,
            }
            # F6 static environment
            title = loc["title"] if loc else None
            row["f6_roof_type"] = roof_types.get(title, UNKNOWN) if title else UNKNOWN
            row["f6_surface"] = surface_type(g["surface"]) if g["completed"] else (
                surface_type(g["surface"]) if g["surface"] else UNKNOWN)
            row["stadium_title"] = title or UNKNOWN
            # F7 travel / time zone
            home_title = homes.get((g["season"], team))
            home_pt = coords.get(home_title) if home_title else None
            if loc and home_pt and ko:
                row["f7_travel_km"] = round(haversine_km((home_pt["lat"], home_pt["lon"]),
                                                         (loc["lat"], loc["lon"])), 1)
                home_off = ko.astimezone(ZoneInfo(title_tz[home_title])).utcoffset().total_seconds() / 3600
                game_local = ko.astimezone(ZoneInfo(loc["tz"]))
                game_off = game_local.utcoffset().total_seconds() / 3600
                shift = game_off - home_off  # >0: the team travelled east
                row["f7_tz_shift_east_hours"] = shift
                row["f7_abs_tz_change"] = abs(shift)
                body_clock_hour = (game_local.hour + game_local.minute / 60.0 - shift) % 24
                row["f7_west_to_east_early"] = bool(shift >= 2 and game_local.hour < 14)
                row["f7_body_clock_kickoff_hour"] = round(body_clock_hour, 2)
                row["f7_international"] = loc["tz"].split("/")[0] not in ("America",)
            else:
                for key in ("f7_travel_km", "f7_tz_shift_east_hours", "f7_abs_tz_change",
                            "f7_west_to_east_early", "f7_body_clock_kickoff_hour", "f7_international"):
                    row[key] = UNKNOWN
            out[(g["game_id"], team)] = row
    return out


# --------------------------------------------------------------------------- positions
def player_positions(weekly_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """player_id -> modal position group among WR/TE/RB/QB (FB/HB -> RB).

    Position is a roster attribute, not an outcome; the modal value across
    the corpus is used (documented simplification)."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in weekly_rows:
        pos = row["position"]
        pos = "RB" if pos in ("FB", "HB") else pos
        counts[row["player_id"]][pos] += 1
    return {pid: c.most_common(1)[0][0] for pid, c in counts.items()}


def position_group(pos: str) -> str | None:
    return pos if pos in ("WR", "TE", "RB") else None


# --------------------------------------------------------------------------- PBP
PBP_COLUMNS = ["game_id", "play_id", "season", "week", "season_type", "posteam", "defteam",
               "down", "half_seconds_remaining", "game_seconds_remaining", "wp", "qb_dropback",
               "rush_attempt", "qb_kneel", "qb_spike", "pass", "xpass", "pass_attempt", "sack",
               "complete_pass", "receiver_player_id", "passing_yards", "receiving_yards", "epa",
               "two_point_attempt", "fixed_drive"]

TEAM_GAME_FIELDS = ("plays", "dropbacks", "pass_attempts", "completions", "pass_yards",
                    "proe_sum", "proe_n", "neutral_plays", "neutral_dropbacks",
                    "neutral_sec_sum", "neutral_sec_n")
POS_FIELDS = ("targets", "receptions", "yards", "epa")


def summarize_pbp(pbp_path: Path, positions: Mapping[str, str]) -> tuple[list[dict], list[dict]]:
    """Per-(game, posteam) offensive summary and per-(game, posteam, defteam, pos) targets.

    Scrimmage play = qb_dropback or rush_attempt, excluding kneels, spikes and
    two-point tries. Neutral = downs 1-2, wp in [0.2, 0.8], >120 s left in the
    half (the `pbp_prior_tendencies` v1 definition). Neutral seconds/play =
    drop in game clock from the previous scrimmage snap of the same drive
    (clipped to [0, 60]; includes clock stoppages, so it is a pace proxy).
    PROE = mean(pass - xpass) over scrimmage plays with non-missing xpass.
    """
    import pandas as pd

    df = pd.read_csv(pbp_path, usecols=PBP_COLUMNS, low_memory=False)
    df = df[df["season_type"].isin(["REG", "POST"]) & df["posteam"].notna() & df["defteam"].notna()]
    for col in ("qb_dropback", "rush_attempt", "qb_kneel", "qb_spike", "pass_attempt", "sack",
                "complete_pass", "two_point_attempt"):
        df[col] = df[col].fillna(0).astype(int)
    scrim = df[((df.qb_dropback == 1) | (df.rush_attempt == 1)) & (df.qb_kneel == 0)
               & (df.qb_spike == 0) & (df.two_point_attempt == 0)].copy()
    scrim = scrim.sort_values(["game_id", "play_id"])
    scrim["neutral"] = (scrim["down"].isin([1, 2]) & scrim["wp"].between(0.2, 0.8)
                        & (scrim["half_seconds_remaining"] > 120))
    prev_clock = scrim.groupby(["game_id", "posteam", "fixed_drive"])["game_seconds_remaining"].shift(1)
    delta = (prev_clock - scrim["game_seconds_remaining"]).clip(lower=0, upper=60)
    scrim["sec_ok"] = scrim["neutral"] & delta.notna()
    scrim["sec"] = delta.where(scrim["sec_ok"], 0.0)
    scrim["is_att"] = ((scrim.pass_attempt == 1) & (scrim.sack == 0)).astype(int)
    scrim["cmp"] = scrim["complete_pass"] * scrim["is_att"]
    scrim["pyds"] = scrim["passing_yards"].fillna(0.0) * scrim["is_att"]
    scrim["proe_ok"] = scrim["xpass"].notna() & scrim["pass"].notna()
    scrim["proe"] = (scrim["pass"] - scrim["xpass"]).where(scrim["proe_ok"], 0.0)
    scrim["n_db"] = scrim["neutral"] & (scrim.qb_dropback == 1)
    grouped = scrim.groupby(["game_id", "posteam", "defteam", "season", "week", "season_type"])
    agg = grouped.agg(plays=("play_id", "size"), dropbacks=("qb_dropback", "sum"),
                      pass_attempts=("is_att", "sum"),
                      completions=("cmp", "sum"),
                      pass_yards=("pyds", "sum"), proe_sum=("proe", "sum"), proe_n=("proe_ok", "sum"),
                      neutral_plays=("neutral", "sum"), neutral_dropbacks=("n_db", "sum"),
                      neutral_sec_sum=("sec", "sum"), neutral_sec_n=("sec_ok", "sum")).reset_index()
    team_rows = []
    for rec in agg.to_dict("records"):
        row = {"game_id": rec["game_id"], "team": norm_team(rec["posteam"]),
               "opponent": norm_team(rec["defteam"]), "season": int(rec["season"]),
               "week": int(rec["week"]), "season_type": rec["season_type"]}
        for f in TEAM_GAME_FIELDS:
            row[f] = float(rec[f])
        team_rows.append(row)

    tg = scrim[(scrim.is_att == 1) & scrim["receiver_player_id"].notna()].copy()
    tg["pos"] = tg["receiver_player_id"].map(lambda p: position_group(positions.get(p, "")) or "OTHER")
    tg["rec"] = tg["complete_pass"]
    tg["yds"] = tg["receiving_yards"].fillna(0.0)
    tg["epa_v"] = tg["epa"].fillna(0.0)
    pagg = tg.groupby(["game_id", "posteam", "defteam", "season", "week", "season_type", "pos"]).agg(
        targets=("play_id", "size"), receptions=("rec", "sum"), yards=("yds", "sum"),
        epa=("epa_v", "sum")).reset_index()
    pos_rows = []
    for rec in pagg.to_dict("records"):
        pos_rows.append({"game_id": rec["game_id"], "team": norm_team(rec["posteam"]),
                         "opponent": norm_team(rec["defteam"]), "season": int(rec["season"]),
                         "week": int(rec["week"]), "season_type": rec["season_type"],
                         "pos": rec["pos"], **{f: float(rec[f]) for f in POS_FIELDS}})
    return team_rows, pos_rows


def write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def read_csv(path: Path, numeric: Iterable[str]) -> list[dict[str, Any]]:
    numeric = set(numeric)
    out = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = dict(raw)
            row["season"], row["week"] = int(raw["season"]), int(raw["week"])
            for f in numeric:
                row[f] = float(raw[f])
            out.append(row)
    return out


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["STADIUMS", "TEAM_ALIASES", "fetch_stadium_coordinates", "haversine_km", "iso_now",
           "kickoff_utc", "load_schedule", "norm_team", "player_positions", "position_group",
           "read_csv", "stadium_location", "stadium_roof_types", "summarize_pbp", "surface_type",
           "team_game_context", "team_home_stadiums", "write_csv", "timedelta"]
