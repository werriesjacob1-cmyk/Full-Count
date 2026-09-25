"""Tier 1 factor F4_RED_ZONE: red-zone and goal-line opportunity features.

Producer for Workstream D. Built from nflfastR play-by-play (nflverse
``play_by_play_{season}.csv.gz``), strictly prior to the target week.

Three quantities are kept DISTINCT on purpose:

* player opportunity   -- a player's targets/carries by field zone, as
                          per-game counts and as shares of his team's plays
                          in that zone (in the games he appeared in);
* team scoring opportunity -- red-zone trips per game and team plays per
                          game by field zone;
* conversion efficiency -- TD rate per red-zone trip (team) and TD per
                          red-zone opportunity (player).

Field zones use ``yardline_100`` (yards from the opponent's end zone) at the
snap. Features are emitted for the cumulative zones the mission names
(inside the 20, 10 and 5); the consumer works on the DISJOINT buckets
``Z5`` (1-5), ``Z10`` (6-10), ``Z20`` (11-20) and ``OUT`` (21-99) so no play
is counted twice.

Play exclusion rules (every rule is counted in ``diagnostics``):

1. ``play_deleted == 1``: excluded (not an official play).
2. ``play_type == 'no_play'``: excluded as an opportunity. These are plays
   nullified by penalty (and administrative rows); nflfastR blanks the
   rusher/receiver ids on them and they do not count in official stats or
   for settlement. They DO count for red-zone-trip detection (rule 9).
3. ``two_point_attempt == 1``: excluded from opportunities AND touchdowns.
   A two-point conversion is not a touchdown in nflverse weekly stats nor at
   the sportsbook, and every 2-pt try snaps from the 2 (it would inflate
   inside-the-5 counts). nflfastR sets ``pass_attempt``/``rush_attempt`` on
   2-pt tries, so a filter on those flags alone does NOT exclude them.
4. Extra points, field goals, punts, kickoffs and other special-teams plays
   are not opportunities.
5. ``qb_kneel`` / ``qb_spike`` plays are excluded (clock management).
6. Target: ``play_type == 'pass'``, ``pass_attempt == 1`` and a non-empty
   ``receiver_player_id``. Sacks and passes with no intended receiver are
   not targets.
7. Carry: ``play_type == 'run'``, ``rush_attempt == 1`` and a non-empty
   ``rusher_player_id``. QB scrambles are carries (nflfastR codes them as
   runs by the QB; official stats count them as rushes).
8. A play with an ACCEPTED penalty that still stands (``penalty == 1`` but
   ``play_type`` pass/run) is an official play and IS counted.
9. Red-zone trip: a drive (``game_id``, offense, ``fixed_drive``) with at
   least one non-deleted, non-2-pt, non-extra-point snap (pass, run, field
   goal, kneel, spike, or a ``no_play`` with a down) at ``yardline_100 <= 20``.
   The PAT/2-pt rows are excluded here because they are spotted at the 15/2
   AFTER a touchdown and would turn every TD drive into a "trip". A trip
   is converted when ``fixed_drive_result == 'Touchdown'``.
10. Missing ``yardline_100`` on an otherwise valid play: excluded, counted.
11. Laterals: the opportunity is credited only to the original rusher or
    receiver; lateral touchdowns are not credited to a bucket.
12. Touchdown credit: ``rush_touchdown == 1`` with ``td_player_id`` equal to
    the rusher, or ``pass_touchdown == 1`` with ``td_player_id`` equal to the
    receiver. Return / defensive / fumble-recovery TDs are not opportunity
    conversions (see the settlement note in the README).
13. Team codes are canonicalised to the franchise's current code on BOTH
    sides (``OAK->LV``, ``SD->LAC``, ``STL->LA``, ``LAR->LA``). Checked on the
    pinned files: the weekly-stats ``team`` column already reports ``LV`` for
    2016-2019 Oakland games while ``game_id`` keeps ``OAK``, so a join on
    period-accurate codes silently lost ~800 appearances' team context.

History includes REG and POST games (as the live B0 does). Player
appearances are weekly-stats rows with a role for the anytime-TD market
(carries + max(targets, receptions) > 0), the same appearance definition B0
uses; a player's zero-touch games are therefore not in his history.
Shrinkage targets (positional means, league team means) are RUNNING means
over strictly earlier weeks only, so no row sees the future.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from nfl.research.tier1 import contract as C
from nfl.research.tier1 import harness as H

FACTOR_ID = "F4_RED_ZONE"
SOURCE_PBP = "NFLVERSE_PBP"
SOURCE_WEEKLY = "NFLVERSE_WEEKLY_STATS"

ZONES = ("Z5", "Z10", "Z20", "OUT")          # disjoint: 1-5, 6-10, 11-20, 21-99
TYPES = ("tgt", "car")
BUCKETS = tuple(f"{t}_{z}" for t in TYPES for z in ZONES)
RZ_BUCKETS = tuple(b for b in BUCKETS if not b.endswith("OUT"))
CUMULATIVE = {"rz20": ("Z5", "Z10", "Z20"), "rz10": ("Z5", "Z10"), "rz5": ("Z5",)}
RED_ZONE_MAX = 20

# Pre-declared shrinkage strengths (not tuned on any partition).
M_PLAYER_GAMES = 3.0     # pseudo-games toward the positional per-game mean
M_TEAM_GAMES = 4.0       # pseudo-games toward the league team per-game mean
M_TRIPS = 10.0           # pseudo-trips toward the league TD-per-trip rate
M_OPPS = 10.0            # pseudo red-zone opportunities toward positional TD/opp
WINDOWS = {"L4": 4, "L8": 8}  # plus season-to-date "STD"
TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}
POSITION_GROUPS = {"RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE", "QB": "QB"}

PBP_COLUMNS = ["game_id", "season", "week", "season_type", "posteam", "defteam", "home_team",
               "play_type", "down", "yardline_100", "pass_attempt", "rush_attempt",
               "two_point_attempt", "extra_point_attempt", "qb_kneel", "qb_spike",
               "receiver_player_id", "rusher_player_id", "rush_touchdown", "pass_touchdown",
               "td_player_id", "penalty", "play_deleted", "fixed_drive", "fixed_drive_result"]

TRIP_PLAY_TYPES = {"pass", "run", "field_goal", "qb_kneel", "qb_spike"}


def position_group(position: str) -> str:
    return POSITION_GROUPS.get((position or "").upper(), "OTHER")


def canonical_team(code: str) -> str:
    code = (code or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


def zone_of(yardline_100: float) -> str:
    if yardline_100 <= 5:
        return "Z5"
    if yardline_100 <= 10:
        return "Z10"
    if yardline_100 <= 20:
        return "Z20"
    return "OUT"


def _flag(value: Any) -> bool:
    return str(value).strip() in ("1", "1.0")


def _empty_counts() -> dict[str, float]:
    return {b: 0.0 for b in BUCKETS}


@dataclass
class ParsedPbp:
    """Per-player-game and per-team-game aggregates of a set of plays."""
    player_games: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    team_games: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    diagnostics: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def parse_plays(records: Iterable[Mapping[str, Any]], parsed: ParsedPbp | None = None) -> ParsedPbp:
    """Aggregate play records (string-valued nflfastR columns) into ``parsed``.

    ``player_games`` is keyed (game_id, gsis_id) -> {team, season, week,
    counts{bucket}, tds{bucket}}; ``team_games`` is keyed (game_id, team) ->
    {season, week, counts{bucket}, rz_trips, rz_trip_tds, drives}.
    """
    out = parsed or ParsedPbp()
    diag = out.diagnostics
    trips: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rec in records:
        diag["plays_read"] += 1
        if _flag(rec.get("play_deleted")):
            diag["excluded_play_deleted"] += 1
            continue
        team = canonical_team(str(rec.get("posteam") or ""))
        defteam = canonical_team(str(rec.get("defteam") or ""))
        game_id = str(rec.get("game_id") or "").strip()
        if not team or team == "NA" or not defteam or team == defteam or not game_id:
            diag["excluded_no_offense_identity"] += 1
            continue
        season, week = int(float(rec["season"])), int(float(rec["week"]))
        tg = out.team_games.setdefault((game_id, team), {
            "season": season, "week": week, "counts": _empty_counts(),
            "rz_trips": 0, "rz_trip_tds": 0, "drives": 0})
        play_type = str(rec.get("play_type") or "").strip()
        two_pt = _flag(rec.get("two_point_attempt"))
        yl_text = str(rec.get("yardline_100") or "").strip()
        yardline = float(yl_text) if yl_text not in ("", "NA") else None
        drive = str(rec.get("fixed_drive") or "").strip()
        # Rule 9: red-zone trip detection.
        trip_snap = (not two_pt and not _flag(rec.get("extra_point_attempt"))
                     and (play_type in TRIP_PLAY_TYPES
                          or (play_type == "no_play" and str(rec.get("down") or "").strip()
                              not in ("", "NA"))))
        if trip_snap and drive and yardline is not None:
            dkey = (game_id, team, drive)
            d = trips.setdefault(dkey, {"rz": False, "td": False})
            d["rz"] = d["rz"] or yardline <= RED_ZONE_MAX
            d["td"] = d["td"] or str(rec.get("fixed_drive_result") or "").strip() == "Touchdown"
        # Opportunity classification.
        if play_type == "no_play":
            diag["excluded_no_play_nullified_or_admin"] += 1
            continue
        if (play_type in ("qb_kneel", "qb_spike") or _flag(rec.get("qb_kneel"))
                or _flag(rec.get("qb_spike"))):
            diag["excluded_kneel_or_spike"] += 1
            continue
        if play_type not in ("pass", "run"):
            continue
        if two_pt:
            diag["excluded_two_point_attempt"] += 1
            continue
        receiver = str(rec.get("receiver_player_id") or "").strip()
        rusher = str(rec.get("rusher_player_id") or "").strip()
        if play_type == "pass" and _flag(rec.get("pass_attempt")) and receiver and receiver != "NA":
            kind, pid, td = "tgt", receiver, (_flag(rec.get("pass_touchdown"))
                                              and str(rec.get("td_player_id") or "").strip() == receiver)
        elif play_type == "run" and _flag(rec.get("rush_attempt")) and rusher and rusher != "NA":
            kind, pid, td = "car", rusher, (_flag(rec.get("rush_touchdown"))
                                            and str(rec.get("td_player_id") or "").strip() == rusher)
        else:
            diag["excluded_no_intended_player"] += 1
            continue
        if yardline is None:
            diag["excluded_missing_yardline"] += 1
            continue
        if _flag(rec.get("penalty")):
            diag["counted_play_with_accepted_penalty"] += 1
        bucket = f"{kind}_{zone_of(yardline)}"
        pg = out.player_games.setdefault((game_id, pid), {
            "team": team, "season": season, "week": week,
            "counts": _empty_counts(), "tds": _empty_counts()})
        pg["counts"][bucket] += 1
        pg["tds"][bucket] += 1 if td else 0
        tg["counts"][bucket] += 1
        diag[f"counted_{kind}"] += 1
    for (game_id, team, _drive), d in trips.items():
        tg = out.team_games[(game_id, team)]
        tg["drives"] += 1
        if d["rz"]:
            tg["rz_trips"] += 1
            tg["rz_trip_tds"] += 1 if d["td"] else 0
    return out


def load_pbp(pbp_dir: Path, seasons: Iterable[int]) -> tuple[ParsedPbp, dict[str, str]]:
    """Parse the pinned play-by-play files; returns (parsed, {file: sha256})."""
    import pandas as pd  # local import: tests use parse_plays on dicts

    parsed = ParsedPbp()
    hashes: dict[str, str] = {}
    for season in seasons:
        path = Path(pbp_dir) / f"play_by_play_{season}.csv.gz"
        hashes[path.name] = H.sha256_file(path)
        frame = pd.read_csv(path, usecols=PBP_COLUMNS, dtype=str, keep_default_na=False)
        parse_plays(frame.to_dict("records"), parsed)
    return parsed, hashes


# --------------------------------------------------------------------------
# Strictly-prior feature walk
# --------------------------------------------------------------------------

def td_role(row: Mapping[str, Any]) -> float:
    return row["carries"] + max(row["targets"], row["receptions"])


@dataclass
class Request:
    season: int
    week: int
    game_id: str
    team: str
    player_id: str
    position: str


def _ratio(num: float, den: float, fallback: Any = C.UNKNOWN) -> Any:
    return num / den if den > 0 else fallback


class _Running:
    """Running (strictly prior) league and positional means."""

    def __init__(self) -> None:
        self.pos_apps: dict[str, float] = defaultdict(float)
        self.pos_counts: dict[str, dict[str, float]] = defaultdict(_empty_counts)
        self.pos_team_counts: dict[str, dict[str, float]] = defaultdict(_empty_counts)
        self.pos_rz_tds: dict[str, float] = defaultdict(float)
        self.team_games = 0.0
        self.team_counts = _empty_counts()
        self.trips = 0.0
        self.trip_tds = 0.0

    def pos_pg(self, pos: str, bucket: str) -> Any:
        return _ratio(self.pos_counts[pos][bucket], self.pos_apps[pos])

    def pos_share(self, pos: str, bucket: str) -> Any:
        return _ratio(self.pos_counts[pos][bucket], self.pos_team_counts[pos][bucket])

    def pos_rz_td_per_opp(self, pos: str) -> Any:
        return _ratio(self.pos_rz_tds[pos], sum(self.pos_counts[pos][b] for b in RZ_BUCKETS))

    def league_team_pg(self, bucket: str) -> Any:
        return _ratio(self.team_counts[bucket], self.team_games)

    def league_trips_pg(self) -> Any:
        return _ratio(self.trips, self.team_games)

    def league_td_per_trip(self) -> Any:
        return _ratio(self.trip_tds, self.trips)


def _shrink(total: float, n: float, prior: Any, m: float) -> Any:
    if C.is_unknown(prior):
        return _ratio(total, n)
    return (total + m * prior) / (n + m)


def _player_window_features(prefix: str, apps: list[dict], pos: str, run: _Running) -> dict[str, Any]:
    out: dict[str, Any] = {f"{prefix}_n_games": float(len(apps))}
    names = [f"{prefix}_{z}_{k}" for z in CUMULATIVE for k in ("targets_pg", "carries_pg", "opp_share")]
    names += [f"{prefix}_targets_pg", f"{prefix}_carries_pg", f"{prefix}_rz20_td_per_opp"]
    if not apps:
        return {**out, **{n: C.UNKNOWN for n in names}}
    n = float(len(apps))
    tot = _empty_counts()
    team_tot = _empty_counts()
    team_n = 0.0
    rz_tds = 0.0
    for a in apps:
        for b in BUCKETS:
            tot[b] += a["counts"][b]
        rz_tds += sum(a["tds"][b] for b in RZ_BUCKETS)
        if a["team_counts"] is not None:
            team_n += 1
            for b in BUCKETS:
                team_tot[b] += a["team_counts"][b]
    for zname, zones in CUMULATIVE.items():
        for kind, label in (("tgt", "targets_pg"), ("car", "carries_pg")):
            bs = [f"{kind}_{z}" for z in zones]
            prior = _sum_known([run.pos_pg(pos, b) for b in bs])
            out[f"{prefix}_{zname}_{label}"] = _shrink(sum(tot[b] for b in bs), n, prior, M_PLAYER_GAMES)
        bs = [f"{k}_{z}" for k in TYPES for z in zones]
        p_num = sum(_team_known_player_counts(apps, b) for b in bs)
        t_num = sum(team_tot[b] for b in bs)
        prior_num = sum(run.pos_counts[pos][b] for b in bs)
        prior_den = sum(run.pos_team_counts[pos][b] for b in bs)
        prior = _ratio(prior_num, prior_den)
        out[f"{prefix}_{zname}_opp_share"] = _shrink_share(p_num, t_num, team_n, prior)
    for kind, label in (("tgt", "targets_pg"), ("car", "carries_pg")):
        bs = [f"{kind}_{z}" for z in ZONES]
        prior = _sum_known([run.pos_pg(pos, b) for b in bs])
        out[f"{prefix}_{label}"] = _shrink(sum(tot[b] for b in bs), n, prior, M_PLAYER_GAMES)
    rz_opps = sum(tot[b] for b in RZ_BUCKETS)
    out[f"{prefix}_rz20_td_per_opp"] = _shrink(rz_tds, rz_opps, run.pos_rz_td_per_opp(pos), M_OPPS)
    return out


def _sum_known(values: list[Any]) -> Any:
    if any(C.is_unknown(v) for v in values):
        return C.UNKNOWN
    return sum(values)


def _team_known_player_counts(apps: list[dict], bucket: str) -> float:
    return sum(a["counts"][bucket] for a in apps if a["team_counts"] is not None)


def _shrink_share(player: float, team: float, team_games: float, prior: Any) -> Any:
    """Share of team plays, shrunk toward the positional share.

    The prior weighs as ``M_PLAYER_GAMES`` games of the player's own average
    team volume in the zone; with no team plays at all the share is the prior.
    """
    if team <= 0 or team_games <= 0:
        return prior
    raw = player / team
    if C.is_unknown(prior):
        return raw
    return (team_games * raw + M_PLAYER_GAMES * prior) / (team_games + M_PLAYER_GAMES)


def _team_window_features(prefix: str, games: list[dict], run: _Running) -> dict[str, Any]:
    out: dict[str, Any] = {f"{prefix}_n_games": float(len(games))}
    names = [f"{prefix}_rz_trips_pg", f"{prefix}_rz_td_per_trip", f"{prefix}_rz20_plays_pg"]
    if not games:
        return {**out, **{n: C.UNKNOWN for n in names}}
    n = float(len(games))
    trips = sum(g["rz_trips"] for g in games)
    tds = sum(g["rz_trip_tds"] for g in games)
    out[f"{prefix}_rz_trips_pg"] = _shrink(trips, n, run.league_trips_pg(), M_TEAM_GAMES)
    out[f"{prefix}_rz_td_per_trip"] = _shrink(tds, trips, run.league_td_per_trip(), M_TRIPS)
    rz_plays = sum(g["counts"][b] for g in games for b in RZ_BUCKETS)
    prior = _sum_known([run.league_team_pg(b) for b in RZ_BUCKETS])
    out[f"{prefix}_rz20_plays_pg"] = _shrink(rz_plays, n, prior, M_TEAM_GAMES)
    return out


def _consumer_inputs(apps: list[dict], team_games: list[dict], pos: str, run: _Running) -> dict[str, Any]:
    """Disjoint-bucket L8 inputs the touchdown consumer reads."""
    out: dict[str, Any] = {}
    n = float(len(apps))
    team_n = float(sum(1 for a in apps if a["team_counts"] is not None))
    for b in BUCKETS:
        out[f"L8_pg_{b}"] = (_shrink(sum(a["counts"][b] for a in apps), n, run.pos_pg(pos, b),
                                     M_PLAYER_GAMES) if apps else C.UNKNOWN)
        if apps:
            p = _team_known_player_counts(apps, b)
            t = sum(a["team_counts"][b] for a in apps if a["team_counts"] is not None)
            out[f"L8_share_{b}"] = _shrink_share(p, t, team_n, run.pos_share(pos, b))
        else:
            out[f"L8_share_{b}"] = C.UNKNOWN
        out[f"team_L8_plays_pg_{b}"] = (
            _shrink(sum(g["counts"][b] for g in team_games), float(len(team_games)),
                    run.league_team_pg(b), M_TEAM_GAMES) if team_games else C.UNKNOWN)
    for b in BUCKETS:
        # Raw L8 counts and the positional zone split of each opportunity type
        # (share of that type's opportunities falling in bucket b).
        out[f"L8_raw_{b}"] = float(sum(a["counts"][b] for a in apps)) if apps else C.UNKNOWN
        kind = b.split("_")[0]
        type_total = sum(run.pos_counts[pos][f"{kind}_{z}"] for z in ZONES)
        out[f"pos_frac_{b}"] = _ratio(run.pos_counts[pos][b], type_total)
    out["league_rz_td_per_trip"] = run.league_td_per_trip()
    return out


def build_features(requests: Iterable[Request], weekly_rows: list[dict], parsed: ParsedPbp,
                   *, information_cutoff: str = C.HISTORICAL_PRIOR_WEEKS,
                   check_prior: bool = True) -> tuple[dict[tuple, dict], dict[str, int]]:
    """Walk weeks in order; emit a feature row per request from strictly earlier weeks.

    Returns ({(season, week, game_id, player_id): feature_row}, diagnostics).
    Each week's requests are answered BEFORE that week's games are added.
    """
    reqs_by_week: dict[tuple[int, int], list[Request]] = defaultdict(list)
    for r in requests:
        reqs_by_week[(r.season, r.week)].append(r)
    apps_by_week: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in weekly_rows:
        if td_role(row) > 0:
            apps_by_week[(row["season"], row["week"])].append(row)
    teams_by_week: dict[tuple[int, int], list[tuple[str, dict]]] = defaultdict(list)
    for (game_id, team), tg in parsed.team_games.items():
        teams_by_week[(tg["season"], tg["week"])].append((team, {**tg, "game_id": game_id}))
    weeks = sorted(set(reqs_by_week) | set(apps_by_week) | set(teams_by_week))

    run = _Running()
    player_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=max(WINDOWS.values())))
    player_season: dict[tuple[str, int], list[dict]] = defaultdict(list)
    team_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=max(WINDOWS.values())))
    team_season: dict[tuple[str, int], list[dict]] = defaultdict(list)
    diag: dict[str, int] = defaultdict(int)
    out: dict[tuple, dict] = {}

    for wk in weeks:
        season, week = wk
        for r in reqs_by_week.get(wk, ()):
            pos = position_group(r.position)
            hist = list(player_hist[r.player_id])
            thist = list(team_hist[canonical_team(r.team)])
            std = player_season[(r.player_id, season)]
            tstd = team_season[(canonical_team(r.team), season)]
            if check_prior:
                C.assert_strictly_prior(
                    [(a["season"], a["week"]) for a in hist + std]
                    + [(g["season"], g["week"]) for g in thist + tstd], wk)
            feats: dict[str, Any] = {"position_group": pos}
            for name, size in WINDOWS.items():
                feats.update(_player_window_features(name, hist[-size:], pos, run))
            feats.update(_player_window_features("STD", std, pos, run))
            feats.update(_team_window_features("team_L8", thist, run))
            feats.update(_team_window_features("team_STD", tstd, run))
            feats.update(_consumer_inputs(hist, thist, pos, run))
            out[(r.season, r.week, r.game_id, r.player_id)] = C.feature_row(
                FACTOR_ID, season=r.season, week=r.week, game_id=r.game_id, team=r.team,
                gsis_id=r.player_id, features=feats, source_ids=[SOURCE_PBP, SOURCE_WEEKLY],
                information_cutoff=information_cutoff)
            diag["feature_rows"] += 1
        # --- add this week's games (after answering this week's requests) ---
        new_apps = []
        for row in apps_by_week.get(wk, ()):
            pg = parsed.player_games.get((row["game_id"], row["player_id"]))
            tg = parsed.team_games.get((row["game_id"], canonical_team(row["team"])))
            if pg is None:
                diag["appearance_without_pbp_opportunity"] += 1
            if tg is None:
                diag["appearance_team_not_in_pbp"] += 1
            if pg is not None and pg["team"] != canonical_team(row["team"]):
                diag["appearance_team_mismatch_pbp"] += 1
            app = {"season": season, "week": week,
                   "counts": dict(pg["counts"]) if pg else _empty_counts(),
                   "tds": dict(pg["tds"]) if pg else _empty_counts(),
                   "team_counts": dict(tg["counts"]) if tg else None,
                   "pos": position_group(row["position"])}
            player_hist[row["player_id"]].append(app)
            player_season[(row["player_id"], season)].append(app)
            new_apps.append(app)
        for app in new_apps:
            pos = app["pos"]
            run.pos_apps[pos] += 1
            run.pos_rz_tds[pos] += sum(app["tds"][b] for b in RZ_BUCKETS)
            for b in BUCKETS:
                run.pos_counts[pos][b] += app["counts"][b]
            if app["team_counts"] is not None:
                for b in BUCKETS:
                    run.pos_team_counts[pos][b] += app["team_counts"][b]
        for team, tg in teams_by_week.get(wk, ()):
            team_hist[team].append(tg)
            team_season[(team, season)].append(tg)
            run.team_games += 1
            run.trips += tg["rz_trips"]
            run.trip_tds += tg["rz_trip_tds"]
            for b in BUCKETS:
                run.team_counts[b] += tg["counts"][b]
    return out, dict(diag)


def historical_requests(weekly_rows: list[dict]) -> list[Request]:
    """One request per anytime-TD role appearance (REG and POST)."""
    return [Request(r["season"], r["week"], r["game_id"], r["team"], r["player_id"], r["position"])
            for r in weekly_rows if td_role(r) > 0]


def live_requests(weekly_rows: list[dict], *, target_season: int, target_week: int,
                  game_id: str, teams: Iterable[str]) -> list[Request]:
    """Players whose most recent appearance (before the target week) is with a
    target team in the target season. No roster/injury source is read here
    (that is F8, Workstream B); a player who has not appeared this season is
    not requested."""
    teams = set(teams)
    latest: dict[str, dict] = {}
    for r in weekly_rows:
        if td_role(r) <= 0 or (r["season"], r["week"]) >= (target_season, target_week):
            continue
        latest[r["player_id"]] = r
    return [Request(target_season, target_week, game_id, r["team"], pid, r["position"])
            for pid, r in sorted(latest.items())
            if r["season"] == target_season and r["team"] in teams]
