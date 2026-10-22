"""FC-ACC-013 data: FC-ACC-004's official StatsAPI starter dataset, re-retrieved
with identical endpoints and file names, then byte-compared with FC-ACC-004's
committed raw manifest (codex/fc-acc-004-offline-20261016 @ 86a09fc094).

The retrieval and start-parsing logic below is copied from
research/fc_acc_004/workload_exploratory.py (fetch, load_starts) so the start
population is the same; only the cache directory differs. Raw bytes stay local
(gitignored); their sha256 manifest is written to out/data_manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw" / "raw_all_starters"
OUT = ROOT / "out"
API = "https://statsapi.mlb.com/api/v1"
SEASONS = (2024, 2025, 2026)
ACC004_REF = "86a09fc0943862ec3ac405f7625b2145c6862381"


def fetch(path, params, cache):
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = API + path + "?" + urllib.parse.urlencode(params, safe="[],")
        request = urllib.request.Request(url, headers={"User-Agent": "Full-Count-offline-research/1.0"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                cache.write_bytes(body)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1 + 2 * attempt)
        time.sleep(0.12)
    return json.loads(cache.read_bytes())


def load_starts():
    candidates = set()
    for season in SEASONS:
        d = fetch("/stats", {"stats": "season", "group": "pitching", "season": season,
                             "sportIds": 1, "playerPool": "ALL", "limit": 1000},
                  RAW / f"season_{season}.json")
        candidates |= {int(s["player"]["id"]) for s in d["stats"][0]["splits"]
                       if int(s["stat"].get("gamesStarted") or 0) >= 1}
    ids = sorted(candidates)
    starts, missing = [], defaultdict(int)
    for season in SEASONS:
        for batch_index in range(0, len(ids), 25):
            batch = ids[batch_index:batch_index + 25]
            d = fetch("/people", {"personIds": ",".join(map(str, batch)),
                                  "hydrate": f"stats(group=[pitching],type=[gameLog],season={season},gameType=R)"},
                      RAW / f"logs_{season}_{batch_index // 25:02}.json")
            for person in d.get("people", []):
                for block in person.get("stats", []):
                    if block.get("type", {}).get("displayName") != "gameLog":
                        continue
                    for item in block.get("splits", []):
                        stat = item["stat"]
                        if stat.get("gamesStarted") != 1 or item.get("gameType") != "R":
                            continue
                        try:
                            pitches, bf = int(stat["numberOfPitches"]), int(stat["battersFaced"])
                            outs, ks = int(stat["outs"]), int(stat["strikeOuts"])
                            day, game_pk = str(item["date"]), int(item["game"]["gamePk"])
                        except (KeyError, TypeError, ValueError):
                            missing["required_fields"] += 1
                            continue
                        if not (0 <= outs <= 27 and 0 < bf <= 60 and 0 < pitches <= 200 and 0 <= ks <= bf):
                            missing["invalid_stat"] += 1
                            continue
                        starts.append({"season": season, "date": day, "game_pk": game_pk,
                                       "pitcher_id": int(person["id"]), "pitches": pitches,
                                       "bf": bf, "outs": outs, "ks": ks})
    keys = [(x["pitcher_id"], x["game_pk"]) for x in starts]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate pitcher-game")
    return starts, dict(missing), len(ids)


def compare_with_acc004():
    try:
        ref = json.loads(subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"{ACC004_REF}:research/fc_acc_004/report.json"]))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"FC-ACC-004 manifest unavailable: {exc}"}
    theirs = {m["path"].split("/", 1)[1]: m for m in ref["raw_manifest"]}
    mine = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RAW.glob("*.json"))}
    same = sorted(k for k in mine if k in theirs and theirs[k]["sha256"] == mine[k])
    diff = sorted(k for k in mine if k in theirs and theirs[k]["sha256"] != mine[k])
    return {"acc004_ref": ACC004_REF, "files": len(mine), "byte_identical": len(same), "differs": diff,
            "missing_here": sorted(set(theirs) - set(mine)), "extra_here": sorted(set(mine) - set(theirs)),
            "acc004_all_starts_by_year": ref.get("all_starts_by_year")}


def main():
    starts, missing, n_ids = load_starts()
    OUT.mkdir(exist_ok=True)
    (OUT / "starts.json").write_text(json.dumps(starts))
    manifest = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "candidate_pitchers": n_ids,
                "missing": missing, "starts_by_year": {y: sum(s["season"] == y for s in starts) for y in SEASONS},
                "raw": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                         "bytes": p.stat().st_size} for p in sorted(RAW.glob("*.json"))],
                "vs_fc_acc_004": compare_with_acc004()}
    (OUT / "data_manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: v for k, v in manifest.items() if k != "raw"}, indent=1))


if __name__ == "__main__":
    main()
