"""Raw-preserving FanDuel sweep and offer parsing from archived bytes only.

Sweep structure follows PR #220 v3 capture.py (root discovery, per-event tabs,
per-request timestamps, time budget, explicit completeness), which is NOT
imported (unmerged). Unlike it, every response's ORIGINAL BYTES are archived
content-addressed, and offers are derived exclusively by re-parsing those
archived bytes, so each offer binds to sha256(raw response).

Market parsing reuses production odds_fanduel (MARKET_MAP, TWO_SIDED_MARKETS,
_parse_two_sided, _parse_outs_runner, normalize_name) and
prop_probability.devig_two_sided, so research offers mean exactly what the
production pipeline means by them.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import odds_fanduel as FD  # noqa: E402
import prop_probability as PP  # noqa: E402

CAPTURE_VERSION = "fc-acc-001-s1-fanduel-capture-1"
SPORTSBOOK = "fanduel"
TABS = ("batter-props", "popular", "pitcher-props", "lasers", "moonshots")
ROOT_PATH = f"content-managed-page?page=CUSTOM&customPageId=mlb&_ak={FD.AK}"


class CaptureError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_sha256(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def live_fetch_bytes(path, timeout=20):
    """Real network fetch returning (host, raw bytes). Only used by an
    explicitly authorized live capture; never by tests or the dry run."""
    import requests
    last = None
    for host in FD.HOSTS:
        try:
            r = requests.get(f"https://{host}.sportsbook.fanduel.com/api/{path}", headers=FD.UA, timeout=timeout)
            if r.status_code == 200:
                return host, r.content
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"all FanDuel hosts failed ({last})")


def _request(archive, fetch_bytes, clock, path):
    rec = {"path": path, "started_at": clock(), "completed_at": None, "host": None,
           "raw_sha256": None, "bytes": None, "status": None}
    try:
        host, raw = fetch_bytes(path)
        json.loads(raw)                                  # must be parseable JSON
        sha, _ = archive.put(raw)
        rec.update(host=host, raw_sha256=sha, bytes=len(raw), status="OK")
    except Exception as exc:  # noqa: BLE001
        rec["status"] = f"FAILED: {type(exc).__name__}: {exc}"
    rec["completed_at"] = clock()
    return rec


def sweep(archive, fetch_bytes, clock=utc_now, monotonic=time.monotonic, budget_s=900):
    """One complete capture sweep. Returns a capture record with capture_sha256."""
    t0 = monotonic()
    cap = {"capture_version": CAPTURE_VERSION, "sportsbook": SPORTSBOOK, "capture_started_at": clock(),
           "capture_completed_at": None, "root": None, "events": []}
    cap["root"] = _request(archive, fetch_bytes, clock, ROOT_PATH)
    games = []
    if cap["root"]["status"] == "OK":
        evs = ((json.loads(archive.get(cap["root"]["raw_sha256"])).get("attachments") or {}).get("events") or {})
        games = sorted(((str(e["eventId"]), e.get("name"), e.get("openDate")) for e in evs.values()
                        if isinstance(e, dict) and " @ " in (e.get("name") or "") and e.get("eventId") not in (None, "")))
    for eid, name, open_date in games:
        ev = {"event_id": eid, "event_name": name, "open_date": open_date, "requests": []}
        if monotonic() - t0 > budget_s:
            ev["status"] = "NOT_FETCHED_BUDGET"
        else:
            ev["requests"] = [_request(archive, fetch_bytes, clock, f"event-page?eventId={eid}&tab={t}&_ak={FD.AK}")
                              for t in TABS]
            ev["status"] = "COMPLETE" if all(r["status"] == "OK" for r in ev["requests"]) else "PARTIAL"
        cap["events"].append(ev)
    ok = cap["root"]["status"] == "OK" and bool(games) and all(e["status"] == "COMPLETE" for e in cap["events"])
    cap["capture_completed_at"] = clock()
    cap["status"] = "COMPLETE" if ok else ("NO_EVENTS" if cap["root"]["status"] == "OK" and not games else "INCOMPLETE")
    cap["capture_sha256"] = canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"})
    return cap


def verify_capture(cap, archive):
    if canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"}) != cap.get("capture_sha256"):
        raise CaptureError("capture hash does not rebuild")
    if cap.get("capture_version") != CAPTURE_VERSION or cap.get("sportsbook") != SPORTSBOOK:
        raise CaptureError("unexpected capture version or sportsbook")
    for rec in [cap["root"]] + [r for e in cap["events"] for r in e["requests"]]:
        if rec["status"] == "OK":
            raw = archive.get(rec["raw_sha256"])                  # re-hashes
            if len(raw) != rec["bytes"]:
                raise CaptureError(f"archived size mismatch for {rec['path']}")
            if rec["completed_at"] < rec["started_at"]:
                raise CaptureError("request completed before it started")
    return cap


def _needs_of(line):
    return int(line) + 1 if float(line).is_integer() else int(line + 0.5)


def _offer(base, **kw):
    o = dict(base, **kw)
    o["break_even_raw"] = round(PP.implied_probability(o["american_odds"]), 6)   # vig included
    o["offer_id"] = canonical_sha256({k: o[k] for k in ("raw_response_sha256", "sportsbook_event_id", "market_type",
                                                        "selection_id", "player_norm", "market", "side", "needs",
                                                        "american_odds")})
    return o


def parse_offers(cap, archive):
    """Every offer, re-derived from archived bytes. In-play markets are skipped."""
    verify_capture(cap, archive)
    offers = []
    for ev in cap["events"]:
        for rec in ev["requests"]:
            if rec["status"] != "OK":
                continue
            page = json.loads(archive.get(rec["raw_sha256"]))
            for m in ((page.get("attachments") or {}).get("markets") or {}).values():
                if not isinstance(m, dict) or m.get("inPlay"):
                    continue
                mt = m.get("marketType") or ""
                base = {"sportsbook": SPORTSBOOK, "sportsbook_event_id": ev["event_id"], "event_name": ev["event_name"],
                        "event_open_date": ev["open_date"], "market_type": mt, "market_id": m.get("marketId"),
                        "raw_response_sha256": rec["raw_sha256"], "request_path": rec["path"], "host": rec["host"],
                        "observation_started_at": rec["started_at"], "observation_completed_at": rec["completed_at"]}
                if mt in FD.MARKET_MAP:
                    stat, need = FD.MARKET_MAP[mt]
                    for rn in m.get("runners") or []:
                        odds = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
                        if odds is None:
                            continue
                        offers.append(_offer(base, selection_id=rn.get("selectionId"), player_name=rn.get("runnerName"),
                                             player_norm=FD.normalize_name(rn.get("runnerName")), market=stat,
                                             side="over", line=need - 0.5, needs=need, american_odds=int(odds),
                                             fair_novig=None, two_sided=False))
                elif mt in FD.TWO_SIDED_MARKETS or mt.endswith("_OUTS_RECORDED_SB"):
                    stat = "strikeouts" if mt in FD.TWO_SIDED_MARKETS else "pitcher_outs"
                    sides, player, line, sel = {}, None, None, {}
                    for rn in m.get("runners") or []:
                        odds = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
                        if odds is None:
                            continue
                        if stat == "pitcher_outs":
                            p = FD._parse_outs_runner(rn.get("runnerName"))
                            if not p:
                                continue
                            player, side, line = p
                        else:
                            nm = (rn.get("runnerName") or "").strip()
                            side = "OVER" if nm.endswith("Over") else "UNDER" if nm.endswith("Under") else None
                            if side is None or rn.get("handicap") is None:
                                continue
                            player, line = nm.rsplit(" ", 1)[0].strip(), float(rn["handicap"])
                        sides[side] = int(odds)
                        sel[side] = rn.get("selectionId")
                    if player is None or line is None or set(sides) != {"OVER", "UNDER"}:
                        continue                 # one-sided capture of a two-sided market: not an offer
                    t_over, t_under, _hold = PP.devig_two_sided(sides["OVER"], sides["UNDER"])
                    for side, fair in (("OVER", t_over), ("UNDER", t_under)):
                        offers.append(_offer(base, selection_id=sel[side], player_name=player,
                                             player_norm=FD.normalize_name(player), market=stat, side=side.lower(),
                                             line=line, needs=_needs_of(line), american_odds=sides[side],
                                             fair_novig=round(fair, 6), two_sided=True))
    return offers
