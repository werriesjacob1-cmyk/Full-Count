"""Authenticated MLB schedule, confirmed lineups and probable starters, parsed
ONLY from an archived raw statsapi schedule response.

Request: the same endpoint and hydrate production uses in
mlb_daily.fetch_lineups (schedule, hydrate=lineups,probablePitcher). Shapes
follow that function's live-verified notes: lineups.awayPlayers/homePlayers
are flat {"id","fullName"} objects in batting order; probablePitcher is
{"id","fullName"}. A game whose lineup arrays are empty has NO confirmed
lineup -- never "assumed".
"""
import json
from datetime import date as _date, timedelta
from zoneinfo import ZoneInfo

import sources as S

SOURCE = "mlb_statsapi_schedule"
HYDRATE = "lineups,probablePitcher"
PREGAME_STATE = "Preview"
# S1_PROTOCOL.md §3 (frozen): DAY is first pitch before 17:00 America/New_York;
# NIGHT is the rest. Converted with the IANA zone, so EDT/EST are both handled.
# (v2 used a fixed 22:00 UTC split, which is only correct under EST -- Codex
# re-audit 6194240381; see AMENDMENT_A2.md.)
UNIT_TZ = ZoneInfo("America/New_York")
DAY_NIGHT_SPLIT_LOCAL_HOUR = 17


class ScheduleError(ValueError):
    pass


def request_for(date):
    return f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate={HYDRATE}"


def window_of(first_pitch_utc, unit_date):
    """DAY/NIGHT for a game whose MLB official date is ``unit_date``.

    The timestamp must be timezone-aware (naive or local wall-clock times --
    including DST-ambiguous ones such as 01:30 on the fall-back date -- are
    rejected; an aware instant is never ambiguous). A start that falls after
    local midnight (a late game whose official date is the previous day) is
    NIGHT; a local date before the official date is malformed."""
    try:
        t = S.ts(first_pitch_utc, "first_pitch_utc")
        official = _date.fromisoformat(unit_date)
    except (S.SourceError, ValueError, TypeError) as exc:
        raise ScheduleError(f"invalid first pitch or unit date: {exc}") from exc
    local = t.astimezone(UNIT_TZ)
    if local.date() < official:
        raise ScheduleError(f"first pitch {first_pitch_utc} is before its official date {unit_date} in New York")
    if local.date() > official:
        return "NIGHT"
    return "DAY" if local.hour < DAY_NIGHT_SPLIT_LOCAL_HOUR else "NIGHT"


def _ids(players):
    out = []
    for p in players or []:
        if not isinstance(p, dict) or p.get("id") in (None, ""):
            raise ScheduleError("lineup entry without a player id")
        out.append(str(p["id"]))
    if len(out) != len(set(out)):
        raise ScheduleError("duplicate player in a lineup")
    return out


def parse(raw, unit_date):
    """{game_pk: {...}} for games on unit_date. Raises on any malformed game."""
    games = {}
    for d in json.loads(raw).get("dates") or []:
        if d.get("date") != unit_date:
            continue
        for g in d.get("games") or []:
            pk = g.get("gamePk")
            teams = g.get("teams") or {}
            try:
                away, home = teams["away"], teams["home"]
                rec = {"game_pk": str(pk), "away_team": away["team"]["name"], "home_team": home["team"]["name"],
                       "first_pitch_utc": g["gameDate"],
                       "abstract_state": (g.get("status") or {}).get("abstractGameState"),
                       "detailed_state": (g.get("status") or {}).get("detailedState"),
                       "away_sp_id": str((away.get("probablePitcher") or {}).get("id") or "") or None,
                       "home_sp_id": str((home.get("probablePitcher") or {}).get("id") or "") or None,
                       "away_lineup": _ids((g.get("lineups") or {}).get("awayPlayers")),
                       "home_lineup": _ids((g.get("lineups") or {}).get("homePlayers"))}
                S.ts(rec["first_pitch_utc"], "gameDate")
            except (KeyError, TypeError, S.SourceError) as exc:
                raise ScheduleError(f"malformed schedule game {pk!r}: {exc}") from exc
            if pk in (None, "") or rec["game_pk"] in games:
                raise ScheduleError(f"missing or duplicate gamePk {pk!r}")
            rec["matchup"] = f"{rec['away_team']} @ {rec['home_team']}"
            games[rec["game_pk"]] = rec
    return games


def load(rec, archive, unit_date, *, not_after):
    """Verify the archived source record (exact request, observed by the cutoff) and parse it."""
    try:
        raw = S.verify(rec, archive, source=SOURCE, request=request_for(unit_date), not_after=not_after)
    except S.SourceError as exc:
        raise ScheduleError(f"schedule source not verifiable: {exc}") from exc
    return parse(raw, unit_date)


def lineup_check(game, player_id, team):
    """(verified, code) for a batter: present in a CONFIRMED raw lineup on the matching side."""
    pid = str(player_id) if player_id not in (None, "") else None
    if pid is None:
        return False, "PLAYER_ID_MISSING"
    if not game["away_lineup"] and not game["home_lineup"]:
        return False, "LINEUP_NOT_CONFIRMED_IN_RAW"
    side = "away" if pid in game["away_lineup"] else "home" if pid in game["home_lineup"] else None
    if side is None:
        # with only one side posted, an absent player may belong to the unposted side
        if not game["away_lineup"] or not game["home_lineup"]:
            return False, "LINEUP_NOT_CONFIRMED_IN_RAW"
        return False, "PLAYER_NOT_IN_RAW_LINEUP"
    if team not in (None, "") and team != game[f"{side}_team"]:
        return False, "TEAM_MISMATCH"
    return True, None


def starter_check(game, player_id, team):
    pid = str(player_id) if player_id not in (None, "") else None
    side = "away" if pid and pid == game["away_sp_id"] else "home" if pid and pid == game["home_sp_id"] else None
    if side is None:
        return False, "STARTER_NOT_PROBABLE_IN_RAW"
    if team not in (None, "") and team != game[f"{side}_team"]:
        return False, "TEAM_MISMATCH"
    return True, None


EVENT_MATCH_TOLERANCE = timedelta(minutes=90)
