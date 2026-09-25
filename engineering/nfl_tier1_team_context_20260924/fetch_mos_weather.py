#!/usr/bin/env python3
"""Fetch GFS MOS forecasts (IEM archive) for every OUTDOOR REG game 2016-2026.

Writes /tmp/claude-0/nfl_tier1_c/mos_weather_table.json: game_id -> features
+ provenance (station, runtime, raw SHA-256, retrieval time). Runs whose
guidance would not yet have been issued (runtime + 5 h > now) are skipped
and recorded as NOT_YET_ISSUED -- never filled with a later run.
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from nfl.research.tier1.team_context_data import (STADIUMS, kickoff_utc, load_schedule,  # noqa: E402
                                                  stadium_roof_types)
from nfl.research.tier1.team_context_weather import (STATION_BY_TITLE, fetch_mos,  # noqa: E402
                                                     mos_features, mos_runtime_for)

WORK = Path("/tmp/claude-0/nfl_tier1_c")


def main():
    schedule = load_schedule(Path("/tmp/claude-0/nfl_tier1_shared/schedules/games.csv"), first_season=2016)
    roof = stadium_roof_types(schedule)
    now = datetime.now(timezone.utc)
    jobs, table = [], {}
    for g in schedule:
        if g["game_type"] != "REG":
            continue
        title = STADIUMS.get(g["stadium"], (None,))[0]
        rt = roof.get(title) if title else None
        if rt != "outdoors":
            table[g["game_id"]] = {"status": "NOT_OUTDOORS" if rt else "ROOF_UNKNOWN", "roof_type": rt}
            continue
        station = STATION_BY_TITLE.get(title)
        if station is None:
            table[g["game_id"]] = {"status": "NO_MOS_STATION", "stadium": title}
            continue
        runtime = mos_runtime_for(g["gameday"])
        issued = datetime.strptime(runtime, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) + timedelta(hours=5)
        if issued > now:
            table[g["game_id"]] = {"status": "NOT_YET_ISSUED", "station": station, "runtime": runtime}
            continue
        jobs.append((g, station, runtime))
    print("jobs", len(jobs), flush=True)

    def work(job):
        g, station, runtime = job
        meta = fetch_mos(station, runtime, WORK / "mos")
        ko = kickoff_utc(g["gameday"], g["gametime"])
        feats = mos_features(meta.get("rows", []), ko, runtime)
        return g["game_id"], {"status": "OK" if feats["f6_fcst_wind_kt"] != "UNKNOWN" else "MOS_NOT_COVERING_KICKOFF",
                              "station": station, "runtime": runtime, "kickoff_utc": ko.isoformat(),
                              "raw_sha256": meta.get("raw_sha256"), "retrieved_at": meta.get("retrieved_at"),
                              "error": meta.get("error"), **feats}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (gid, rec) in enumerate(pool.map(work, jobs)):
            table[gid] = rec
            if i % 200 == 0:
                print(i, gid, rec["status"], flush=True)
    body = json.dumps(table, sort_keys=True, indent=1).encode()
    (WORK / "mos_weather_table.json").write_bytes(body)
    counts = {}
    for rec in table.values():
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    print("sha256", hashlib.sha256(body).hexdigest(), counts)


if __name__ == "__main__":
    main()
