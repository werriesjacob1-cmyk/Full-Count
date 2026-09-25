"""F6 weather FORECASTS with a provable pre-kickoff vintage (GFS MOS via IEM).

Why not games.csv temp/wind: those are OBSERVED game weather of unknown
vintage -- using them as a pregame feature would leak the outcome period.

Why not Open-Meteo: its historical-forecast archive stitches the first hours
of successive model runs and does not return the run time, so the vintage
of a value at kickoff cannot be proven; the previous-runs API would, but on
2026-09-24 both returned "Daily API request limit exceeded" from this egress
(recorded in the report). The live Open-Meteo forecast API was blocked the
same way.

Source used instead: NOAA GFS MOS (MAV) bulletins archived by the Iowa
Environmental Mesonet (IEM) API ``/api/1/mos.json``, which returns the
model ``runtime`` with every row. Rule (pre-declared): use the 12Z run on
the calendar day BEFORE the ET game day. MAV guidance is disseminated about
4 hours after its runtime, so every value is at least ~20 hours pre-kickoff
-- the vintage is proven by the runtime field, not assumed.

Stations: the nearest major ASOS airport with MOS guidance to each OUTDOOR
stadium (list below, open to review). Domes need no weather; retractable
roofs are decided on game day, so their exposure is unknown and they are
not assigned a forecast. International venues have no MAV guidance ->
UNKNOWN.

Features at kickoff K (UTC): mean wind speed (kt) and temperature (F) over
MOS valid times in [K - 1.5h, K + 4.5h]; max 6-h PoP (%) over valid times
in (K, K + 6h] -- ``p06`` is the probability for the 6 h ending at ftime.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from nfl.research.tier1.contract import UNKNOWN

IEM_MOS_URL = "https://mesonet.agron.iastate.edu/api/1/mos.json"
MOS_MODEL = "GFS"

STATION_BY_TITLE: dict[str, str] = {
    "M&T Bank Stadium": "KBWI", "Gillette Stadium": "KBOS", "New Era Field": "KBUF",
    "Bank of America Stadium": "KCLT", "Soldier Field": "KMDW", "Paycor Stadium": "KCVG",
    "Cleveland Browns Stadium": "KCLE", "Empower Field at Mile High": "KDEN",
    "Lambeau Field": "KGRB", "EverBank Stadium": "KJAX", "Arrowhead Stadium": "KMCI",
    "Dignity Health Sports Park": "KLAX", "Los Angeles Memorial Coliseum": "KLAX",
    "Hard Rock Stadium": "KMIA", "Nissan Stadium (Nashville)": "KBNA", "MetLife Stadium": "KEWR",
    "Oakland Coliseum": "KOAK", "Lincoln Financial Field": "KPHL", "Acrisure Stadium": "KPIT",
    "San Diego Stadium": "KSAN", "Lumen Field": "KSEA", "Levi's Stadium": "KSJC",
    "Raymond James Stadium": "KTPA", "Northwest Stadium": "KDCA",
}


def mos_runtime_for(gameday: str) -> str:
    """12Z run on the ET calendar day before the game (pre-declared rule)."""
    day = datetime.strptime(gameday, "%Y-%m-%d") - timedelta(days=1)
    return day.strftime("%Y-%m-%d") + " 12:00"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def fetch_mos(station: str, runtime: str, cache_dir: Path, session: Any = None) -> dict[str, Any]:
    """Cached IEM MOS fetch: {"rows": [...], "sha256", "url", "retrieved_at"}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{station}_{runtime.replace(' ', 'T').replace(':', '')}.json"
    if path.exists():
        body = path.read_bytes()
        meta = json.loads(body)
        return meta
    import requests

    session = session or requests.Session()
    params = {"station": station, "model": MOS_MODEL, "runtime": runtime}
    last = None
    for attempt in range(4):
        try:
            resp = session.get(IEM_MOS_URL, params=params, timeout=60)
            if resp.status_code == 200:
                break
            last = f"HTTP {resp.status_code}"
        except Exception as exc:  # network hiccup: retry, then record
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    else:
        return {"rows": [], "error": last, "station": station, "runtime": runtime}
    raw = resp.content
    rows = [{k: r.get(k) for k in ("runtime", "ftime", "tmp", "wsp", "p06", "wdr")}
            for r in json.loads(raw).get("data", [])]
    meta = {"station": station, "runtime": runtime, "url": resp.url,
            "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "rows": rows}
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(meta, sort_keys=True))
    tmp.replace(path)
    return meta


def mos_features(rows: list[Mapping[str, Any]], kickoff: datetime, runtime: str) -> dict[str, Any]:
    """Forecast features at kickoff from one MOS run; UNKNOWN when not covered."""
    unknown = {"f6_fcst_wind_kt": UNKNOWN, "f6_fcst_temp_f": UNKNOWN, "f6_fcst_pop6": UNKNOWN,
               "f6_fcst_runtime": runtime}
    good = [r for r in rows if r.get("runtime") == runtime and r.get("ftime")]
    if not good:
        return unknown
    near = [r for r in good if kickoff - timedelta(hours=1.5) <= _parse(r["ftime"]) <= kickoff + timedelta(hours=4.5)]
    pops = [r["p06"] for r in good if r.get("p06") is not None
            and kickoff < _parse(r["ftime"]) <= kickoff + timedelta(hours=6)]
    winds = [r["wsp"] for r in near if r.get("wsp") is not None]
    temps = [r["tmp"] for r in near if r.get("tmp") is not None]
    if not winds or not pops:
        return unknown
    return {"f6_fcst_wind_kt": sum(winds) / len(winds),
            "f6_fcst_temp_f": sum(temps) / len(temps) if temps else UNKNOWN,
            "f6_fcst_pop6": float(max(pops)), "f6_fcst_runtime": runtime,
            "f6_fcst_lead_hours": round((kickoff - _parse(runtime)).total_seconds() / 3600, 2)}
