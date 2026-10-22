"""Independent schedule denominator for the FC-ACC-004 official game-log sample."""
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent
source = json.loads((root / "report.json").read_text())
raw = root / "raw_all_starters"
by_year = defaultdict(lambda: defaultdict(set))
for path in raw.glob("logs_*.json"):
    d = json.loads(path.read_bytes())
    for p in d.get("people", []):
        for block in p.get("stats", []):
            if block.get("type", {}).get("displayName") != "gameLog":
                continue
            for x in block.get("splits", []):
                if x["stat"].get("gamesStarted") == 1 and x.get("gameType") == "R":
                    by_year[int(x["season"])][int(x["game"]["gamePk"])].add(int(p["id"]))

out = {}
for year in (2024, 2025, 2026):
    target = raw / f"schedule_{year}.json"
    if not target.exists():
        q = urllib.parse.urlencode({"sportId": 1, "startDate": f"{year}-03-01",
                                    "endDate": f"{year}-10-16", "gameType": "R"})
        request = urllib.request.Request("https://statsapi.mlb.com/api/v1/schedule?" + q,
                                         headers={"User-Agent": "Full-Count-offline-research/1.0"})
        target.write_bytes(urllib.request.urlopen(request, timeout=45).read())
    d = json.loads(target.read_bytes())
    games = {int(g["gamePk"]): g for day in d.get("dates", []) for g in day.get("games", [])
             if g.get("status", {}).get("codedGameState") == "F"}
    missing = sorted(set(games) - set(by_year[year]))
    incomplete = sorted(g for g in games if len(by_year[year].get(g, set())) != 2)
    extra = sorted(set(by_year[year]) - set(games))
    out[year] = {"final_regular_games": len(games), "with_any_starter_log": len(set(games) & set(by_year[year])),
                 "missing_game_ids": missing, "not_exactly_two_starter_logs": incomplete,
                 "starter_log_games_not_final_in_schedule": extra}
(root / "coverage.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
