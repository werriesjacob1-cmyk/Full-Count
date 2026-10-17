#!/usr/bin/env python3
"""Dedicated pre-seal FanDuel player-prop capture -- preregistration v3, section 5.

Research only. Self-contained: it does NOT import or modify production code
(odds_fanduel.py, prop_snapshot.py); it reads the same public FanDuel
endpoints and records the raw offer identity those modules discard:
event id/name/open date, market id/type/status/in-play, runner selection id,
name, status, handicap, team slug and American odds.

Every event records its own fetch start/completion times and the success of
every tab fetched. The capture records CAPTURE_STARTED_AT and
CAPTURE_COMPLETED_AT; a capture that did not finish has no completed time and
is unusable. A failed tab is never read as "market absent": quote resolution
(manifest_v3.py) maps it to EVENT_NOT_OBSERVED.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

CAPTURE_VERSION = "mlb-v3-fanduel-capture-1"
BOOK = "fanduel"
AK = "FhMFpcPWXMeyZxOx"
HOSTS = ("sbapi.nj", "sbapi.pa", "sbapi.az", "sbapi.co")
TABS = ("batter-props", "popular", "pitcher-props", "lasers", "moonshots")
DEFAULT_BUDGET_S = 900


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(obj):
    encoded = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def team_slug(url):
    """'https://.../team/mlb/philadelphia_phillies.png' -> 'philadelphia_phillies'."""
    m = re.search(r"/team/mlb/([a-z0-9_]+)\.png", url or "")
    return m.group(1) if m else None


def http_fetch_json(path, timeout=20):
    import requests
    ua = {"User-Agent": "Mozilla/5.0 (FULL COUNT research capture)", "Accept": "application/json"}
    last = None
    for host in HOSTS:
        try:
            r = requests.get(f"https://{host}.sportsbook.fanduel.com/api/{path}", headers=ua, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"all FanDuel hosts failed ({last})")


def _runner(rn):
    odds = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
    return {"selection_id": rn.get("selectionId"), "runner_name": rn.get("runnerName"),
            "runner_status": rn.get("runnerStatus"), "handicap": rn.get("handicap"),
            "result_type": ((rn.get("result") or {}).get("type") or None),
            "team_slug": team_slug(rn.get("secondaryLogo")),
            "american": int(odds) if odds is not None else None}


def _market(m):
    return {"market_id": m.get("marketId"), "market_type": m.get("marketType"),
            "market_name": m.get("marketName"), "market_status": m.get("marketStatus"),
            "in_play": bool(m.get("inPlay")), "event_id": m.get("eventId"),
            "runners": [_runner(rn) for rn in (m.get("runners") or []) if isinstance(rn, dict)]}


def capture(fetch_json=http_fetch_json, clock=utc_now, monotonic=time.monotonic, budget_s=DEFAULT_BUDGET_S):
    """One complete sweep. Returns the capture dict (with capture_sha256)."""
    t0 = monotonic()
    out = {"capture_version": CAPTURE_VERSION, "book": BOOK, "capture_started_at": clock(),
           "capture_completed_at": None, "discovery": None, "events": []}
    try:
        root = fetch_json(f"content-managed-page?page=CUSTOM&customPageId=mlb&_ak={AK}")
        evs = (((root or {}).get("attachments") or {}).get("events") or {})
        games = [e for e in evs.values() if isinstance(e, dict) and " @ " in (e.get("name") or "")
                 and e.get("eventId") not in (None, "")]
        out["discovery"] = {"status": "OK" if games else "EMPTY", "n_events": len(games)}
    except Exception as exc:  # noqa: BLE001
        out["discovery"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        games = []
    for e in sorted(games, key=lambda x: str(x["eventId"])):
        ev = {"event_id": e["eventId"], "event_name": e.get("name"), "open_date": e.get("openDate"),
              "fetch_started_at": None, "fetch_completed_at": None, "tabs": {}, "markets": []}
        if monotonic() - t0 > budget_s:
            ev["status"] = "NOT_FETCHED_BUDGET"
            out["events"].append(ev)
            continue
        ev["fetch_started_at"] = clock()
        seen = {}
        for tab in TABS:
            try:
                page = fetch_json(f"event-page?eventId={e['eventId']}&tab={tab}&_ak={AK}")
                mk = ((page or {}).get("attachments") or {}).get("markets")
                if not isinstance(mk, dict):
                    raise ValueError("missing attachments.markets")
                for m in mk.values():
                    if isinstance(m, dict) and m.get("marketId") is not None:
                        seen[str(m["marketId"])] = _market(m)
                ev["tabs"][tab] = "OK"
            except Exception as exc:  # noqa: BLE001
                ev["tabs"][tab] = f"FAILED: {type(exc).__name__}: {exc}"
        ev["fetch_completed_at"] = clock()
        ev["markets"] = [seen[k] for k in sorted(seen)]
        ev["status"] = "COMPLETE" if all(v == "OK" for v in ev["tabs"].values()) else "PARTIAL"
        out["events"].append(ev)
    if out["discovery"]["status"] == "OK":
        out["capture_completed_at"] = clock()
    out["status"] = ("COMPLETE" if out["capture_completed_at"] and all(ev["status"] == "COMPLETE" for ev in out["events"])
                     else "INCOMPLETE")
    out["capture_sha256"] = canonical_sha256({k: v for k, v in out.items() if k != "capture_sha256"})
    return out


def verify_capture(cap):
    if canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"}) != cap.get("capture_sha256"):
        raise ValueError("capture hash does not rebuild")
    if cap.get("capture_version") != CAPTURE_VERSION:
        raise ValueError("unexpected capture version")
    return cap
