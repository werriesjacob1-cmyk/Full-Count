#!/usr/bin/env python3
"""Weather FORECAST VINTAGES for outdoor venues. PRIORITY 7.

THE POINT IS THE VINTAGE, NOT THE WEATHER. Realized game-day weather is
trivially reacquired forever. What cannot be reacquired is what the forecast
SAID on Friday, which is the only thing a Friday-cutoff experiment is allowed
to know. So each capture is stored as its own dated observation and never
overwritten by a later, better forecast.

SOURCE. api.weather.gov, the US National Weather Service -- a documented
public API, no key, explicitly intended for programmatic use. Verified
reachable 2026-09-11.

THE HONEST LIMIT OF THIS MODULE. NWS is keyed by latitude/longitude. Full Count
has no verified NFL venue coordinate table, and NFL-01 does not invent one:
guessed stadium coordinates would silently attach the wrong city's forecast to a
game, which is worse than having no forecast at all. So this module accepts
coordinates from a caller and, when none are supplied, records the gap as a
NOT_CHECKED row naming exactly what is missing. Building a verified venue table
is named as an open gap rather than quietly faked.
"""
from __future__ import annotations

from typing import Iterable, Optional

import requests

from nfl.archive.http import fetch
from nfl.archive.provenance import Fetched, NOT_CHECKED

SOURCE_ID = "weather_nws"


def capture(
    venues: Optional[Iterable[dict]] = None,
    session: Optional[requests.Session] = None,
) -> list[Fetched]:
    """`venues` items need `venue_id`, `lat`, `lon`; anything else is context."""
    session = session or requests.Session()
    venues = list(venues or [])
    if not venues:
        return [Fetched(
            source_id=SOURCE_ID, artifact="forecast_vintages", url="",
            outcome=NOT_CHECKED,
            context={"reason":
                     "no verified NFL venue coordinate table exists in NFL-01, "
                     "and coordinates were not invented. Forecast-vintage "
                     "archival is BLOCKED on building a venue->lat/lon table "
                     "from an authoritative source. Recorded as an open gap, "
                     "not as an absence of weather risk."},
        )]
    records = []
    for venue in venues:
        lat, lon = venue.get("lat"), venue.get("lon")
        vid = venue.get("venue_id")
        if lat is None or lon is None:
            records.append(Fetched(
                source_id=SOURCE_ID, artifact=f"forecast_{vid}", url="",
                outcome=NOT_CHECKED,
                context={"venue_id": vid, "reason": "no coordinates supplied"},
            ))
            continue
        records.append(fetch(
            SOURCE_ID, f"forecast_points_{vid}",
            f"https://api.weather.gov/points/{lat},{lon}",
            context={**venue, "role": "NWS gridpoint resolution for this venue"},
            session=session,
        ))
    return records
