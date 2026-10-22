"""FC-ACC-014 data retrieval (public MLB StatsAPI). Raw responses are cached in
raw/ (gitignored); out/data_manifest.json records every file's sha256.

Normalized outputs (local, gitignored):
  out/batter_games.json   one row per batter regular-season game split (incl. 0-PA appearances)
  out/pitcher_starts.json one row per regular-season START (gamesStarted == 1), FC-ACC-004 parsing
  out/schedule.json       per game: date, venue, teams, probable pitchers, starting lineups, weather
  out/people.json         batSide / pitchHand by MLBAM id
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "out"
API = "https://statsapi.mlb.com/api/v1"
SEASONS = (2023, 2024, 2025, 2026)
MONTHS = ((3, 1, 3, 31), (4, 1, 4, 30), (5, 1, 5, 31), (6, 1, 6, 30), (7, 1, 7, 31), (8, 1, 8, 31),
          (9, 1, 9, 30), (10, 1, 10, 31))


def fetch(path, params, cache):
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = API + path + "?" + urllib.parse.urlencode(params, safe="[](),")
        req = urllib.request.Request(url, headers={"User-Agent": "Full-Count-offline-research/1.0"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    body = r.read()
                cache.write_bytes(body)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 + 3 * attempt)
        time.sleep(0.1)
    return json.loads(cache.read_bytes())


def ids_for(group, season):
    d = fetch("/stats", {"stats": "season", "group": group, "season": season, "sportIds": 1,
                         "playerPool": "ALL", "limit": 3000}, RAW / f"{group}_season_{season}.json")
    splits = d["stats"][0]["splits"]
    if group == "pitching":
        return sorted({int(s["player"]["id"]) for s in splits if int(s["stat"].get("gamesStarted") or 0) >= 1})
    return sorted({int(s["player"]["id"]) for s in splits if int(s["stat"].get("plateAppearances") or 0) >= 1})


def i(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def main():
    OUT.mkdir(exist_ok=True)
    people, batter_games, pitcher_starts, schedule = {}, [], [], {}
    for season in SEASONS:
        for group, kind in (("hitting", "b"), ("pitching", "p")):
            ids = ids_for(group, season)
            for bi in range(0, len(ids), 25):
                batch = ids[bi:bi + 25]
                d = fetch("/people", {"personIds": ",".join(map(str, batch)),
                                      "hydrate": f"stats(group=[{group}],type=[gameLog],season={season},gameType=R)"},
                          RAW / f"{kind}logs_{season}_{bi // 25:03}.json")
                for person in d.get("people", []):
                    pid = int(person["id"])
                    people.setdefault(pid, {"bat": (person.get("batSide") or {}).get("code"),
                                            "throw": (person.get("pitchHand") or {}).get("code")})
                    for block in person.get("stats", []):
                        if block.get("type", {}).get("displayName") != "gameLog":
                            continue
                        for it in block.get("splits", []):
                            if it.get("gameType") != "R":
                                continue
                            st = it["stat"]
                            base = {"season": season, "date": str(it["date"]), "game_pk": int(it["game"]["gamePk"]),
                                    "pid": pid, "team": (it.get("team") or {}).get("id"),
                                    "opp": (it.get("opponent") or {}).get("id"), "home": bool(it.get("isHome"))}
                            if kind == "b":
                                batter_games.append({**base, "pa": i(st.get("plateAppearances")), "ab": i(st.get("atBats")),
                                                     "h": i(st.get("hits")), "d2": i(st.get("doubles")), "d3": i(st.get("triples")),
                                                     "hr": i(st.get("homeRuns")), "bb": i(st.get("baseOnBalls")),
                                                     "so": i(st.get("strikeOuts")), "tb": i(st.get("totalBases")),
                                                     "r": i(st.get("runs")), "hbp": i(st.get("hitByPitch"))})
                            elif st.get("gamesStarted") == 1:
                                try:
                                    row = {**base, "pitches": int(st["numberOfPitches"]), "bf": int(st["battersFaced"]),
                                           "outs": int(st["outs"]), "ks": int(st["strikeOuts"]), "h": i(st.get("hits")),
                                           "d2": i(st.get("doubles")), "d3": i(st.get("triples")), "hr": i(st.get("homeRuns")),
                                           "bb": i(st.get("baseOnBalls"))}
                                except (KeyError, TypeError, ValueError):
                                    continue
                                if 0 <= row["outs"] <= 27 and 0 < row["bf"] <= 60 and 0 < row["pitches"] <= 200 \
                                        and 0 <= row["ks"] <= row["bf"]:
                                    pitcher_starts.append(row)
        for (m1, d1, m2, d2) in MONTHS:
            d = fetch("/schedule", {"sportId": 1, "gameType": "R", "startDate": f"{season}-{m1:02d}-{d1:02d}",
                                    "endDate": f"{season}-{m2:02d}-{d2:02d}",
                                    "hydrate": "lineups,probablePitcher,venue,weather"},
                      RAW / f"schedule_{season}_{m1:02d}.json")
            for day in d.get("dates", []):
                for g in day.get("games", []):
                    if g.get("gameType") != "R":
                        continue
                    L = g.get("lineups") or {}
                    schedule[str(g["gamePk"])] = {
                        "date": g.get("officialDate"), "season": season, "venue": (g.get("venue") or {}).get("id"),
                        "status": (g.get("status") or {}).get("detailedState"),
                        "home": g["teams"]["home"]["team"]["id"], "away": g["teams"]["away"]["team"]["id"],
                        "home_sp": (g["teams"]["home"].get("probablePitcher") or {}).get("id"),
                        "away_sp": (g["teams"]["away"].get("probablePitcher") or {}).get("id"),
                        "home_lineup": [p["id"] for p in L.get("homePlayers") or []],
                        "away_lineup": [p["id"] for p in L.get("awayPlayers") or []],
                        "weather": g.get("weather") or {}}
    keys = [(r["pid"], r["game_pk"]) for r in pitcher_starts]
    assert len(keys) == len(set(keys)), "duplicate pitcher start"
    for name, obj in (("batter_games", batter_games), ("pitcher_starts", pitcher_starts),
                      ("schedule", schedule), ("people", {str(k): v for k, v in people.items()})):
        (OUT / f"{name}.json").write_text(json.dumps(obj))
    manifest = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "api": API,
                "counts": {"batter_games": len(batter_games), "pitcher_starts": len(pitcher_starts),
                           "games": len(schedule), "people": len(people)},
                "normalized_sha256": {n: hashlib.sha256((OUT / f"{n}.json").read_bytes()).hexdigest()
                                      for n in ("batter_games", "pitcher_starts", "schedule", "people")},
                "raw": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                        for p in sorted(RAW.glob("*.json"))]}
    (OUT / "data_manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: v for k, v in manifest.items() if k != "raw"}, indent=1))


if __name__ == "__main__":
    main()
