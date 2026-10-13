"""Pregame pitcher workload from an archived MLB statsapi game-log response.

Only starts dated strictly BEFORE the game date are used (never the game being
predicted, never anything after it). The source bytes are content-addressed in
the raw archive, so every value traces to sha256(raw response).
"""
import json
from datetime import date

STATSAPI_GAMELOG = "https://statsapi.mlb.com/api/v1/people/{pid}/stats?stats=gameLog&group=pitching&season={season}"


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


def pregame_workload(archive, raw_sha256, game_date, n=3):
    """{'status','last_starts':[{date,outs,pitches}], 'days_rest', 'source_raw_sha256'}"""
    gd = date.fromisoformat(game_date)
    payload = json.loads(archive.get(raw_sha256))
    stats = payload.get("stats") or []
    splits = (stats[0].get("splits") or []) if stats else []
    starts = []
    for s in splits:
        st = s.get("stat") or {}
        if int(st.get("gamesStarted") or 0) < 1 or not s.get("date"):
            continue
        d = date.fromisoformat(s["date"])
        if d >= gd:                      # same-day or later: not pregame information
            continue
        starts.append({"date": s["date"], "outs": _outs(st),
                       "pitches": int(st["numberOfPitches"]) if st.get("numberOfPitches") is not None else None})
    starts.sort(key=lambda x: x["date"], reverse=True)
    last = starts[:n]
    out = {"source": "mlb_statsapi_gamelog", "source_raw_sha256": raw_sha256, "game_date": game_date,
           "last_starts": last, "starts_available": len(starts),
           "days_rest": (gd - date.fromisoformat(last[0]["date"])).days if last else None,
           "documented_pitch_limit": "UNKNOWN_NO_VERIFIED_SOURCE"}
    out["status"] = ("COMPLETE" if len(last) == n and all(x["outs"] is not None and x["pitches"] is not None for x in last)
                     else "PARTIAL" if last else "UNAVAILABLE")
    return out
