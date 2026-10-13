"""Pregame pitcher workload from an archived MLB statsapi game-log response.

Only starts dated strictly BEFORE the game date are used. The game-log source
record must verify against the raw archive and must have been OBSERVED (fetch
completed) at or before the decision cutoff; otherwise nothing is returned.
"""
import json
from datetime import date

import sources as S

SOURCE = "mlb_statsapi_gamelog"


def request_for(player_id, season):
    return f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=gameLog&group=pitching&season={season}"


class WorkloadError(ValueError):
    pass


def _outs(stat):
    if stat.get("outs") is not None:
        return int(stat["outs"])
    ip = str(stat.get("inningsPitched") or "")
    if not ip:
        return None
    whole, _, frac = ip.partition(".")
    return int(whole) * 3 + int(frac or 0)


def pregame_workload(archive, source_record, player_id, game_date, *, decision_at, n=3):
    gd = date.fromisoformat(game_date)
    try:
        raw = S.verify(source_record, archive, source=SOURCE, request=request_for(player_id, gd.year),
                       not_after=decision_at)
    except S.SourceError as exc:
        raise WorkloadError(f"workload source rejected: {exc}") from exc
    stats = json.loads(raw).get("stats") or []
    splits = (stats[0].get("splits") or []) if stats else []
    starts = []
    for s in splits:
        st = s.get("stat") or {}
        if int(st.get("gamesStarted") or 0) < 1 or not s.get("date"):
            continue
        if date.fromisoformat(s["date"]) >= gd:          # same-day or later: not pregame information
            continue
        starts.append({"date": s["date"], "outs": _outs(st),
                       "pitches": int(st["numberOfPitches"]) if st.get("numberOfPitches") is not None else None})
    starts.sort(key=lambda x: x["date"], reverse=True)
    last = starts[:n]
    out = {"source": SOURCE, "player_id": str(player_id), "source_record": dict(source_record),
           "source_raw_sha256": source_record["raw_sha256"],
           "source_observed_at": source_record["completed_at"], "game_date": game_date,
           "last_starts": last, "starts_available": len(starts),
           "days_rest": (gd - date.fromisoformat(last[0]["date"])).days if last else None,
           "documented_pitch_limit": "UNKNOWN_NO_VERIFIED_SOURCE"}
    out["status"] = ("COMPLETE" if len(last) == n and all(x["outs"] is not None and x["pitches"] is not None for x in last)
                     else "PARTIAL" if last else "UNAVAILABLE")
    return out
