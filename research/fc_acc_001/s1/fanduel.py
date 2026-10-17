"""Raw-preserving FanDuel sweep and offer parsing from archived bytes only.

Sweep structure follows PR #220 v3 capture.py (root discovery, per-event tabs,
per-request timestamps, time budget, explicit completeness), which is NOT
imported (unmerged). Every response body is archived content-addressed and
offers are derived exclusively by re-parsing archived bytes.

Lineage is re-derived, never trusted from the capture record:
root bytes -> event identities -> exact per-event tab request paths -> market
``eventId`` inside each archived page. A record whose events or paths differ
from what its own archived root implies fails verification.

Market parsing reuses production odds_fanduel (MARKET_MAP, TWO_SIDED_MARKETS,
_parse_outs_runner, normalize_name) and prop_probability.devig_two_sided.

Availability: ``marketStatus`` must be OPEN and ``runnerStatus`` ACTIVE when
present (values measured by this repository's own FanDuel observers and NFL
normalizer); any other value is rejected. When a field is ABSENT the offer is
kept but marked ``availability_verified: False`` -- the live MLB prop
response schema has not been measured by S1, so absence is never read as
"open".
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
import sources as S  # noqa: E402

CAPTURE_VERSION = "fc-acc-001-s1-fanduel-capture-2"
SPORTSBOOK = "fanduel"
SOURCE = "fanduel_sportsbook_api"
TABS = ("batter-props", "popular", "pitcher-props", "lasers", "moonshots")
ROOT_PATH = f"content-managed-page?page=CUSTOM&customPageId=mlb&_ak={FD.AK}"
OPEN_MARKET = "OPEN"
ACTIVE_RUNNER = "ACTIVE"
NOT_FETCHED = "NOT_FETCHED_BUDGET"


class CaptureError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_sha256(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def event_path(event_id, tab):
    return f"event-page?eventId={event_id}&tab={tab}&_ak={FD.AK}"


def live_fetch_bytes(path, timeout=20, deadline=None, monotonic=time.monotonic):
    """Real network fetch returning (host, response body). Only for an explicitly
    authorized live capture; never used by tests or the dry run. ``deadline``
    (monotonic seconds) bounds the whole host-failover loop."""
    import requests
    last = None
    for host in FD.HOSTS:
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            timeout = min(timeout, remaining)
        try:
            r = requests.get(f"https://{host}.sportsbook.fanduel.com/api/{path}", headers=FD.UA, timeout=timeout)
            if r.status_code == 200:
                return host, r.content
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"all FanDuel hosts failed ({last})")


def discover_events(root_raw):
    """Event identities implied by archived root bytes (MLB games only)."""
    evs = ((json.loads(root_raw).get("attachments") or {}).get("events") or {})
    out = []
    for e in evs.values():
        if not isinstance(e, dict) or e.get("eventId") in (None, "") or " @ " not in (e.get("name") or ""):
            continue
        out.append({"event_id": str(e["eventId"]), "event_name": e["name"], "open_date": e.get("openDate")})
    ids = [e["event_id"] for e in out]
    if len(ids) != len(set(ids)):
        raise CaptureError("root lists a duplicate eventId")
    return sorted(out, key=lambda e: e["event_id"])


def _not_fetched(path):
    return {"source": SOURCE, "request": path, "started_at": None, "completed_at": None, "host": None,
            "raw_sha256": None, "bytes": None, "status": NOT_FETCHED}


def _event_status(reqs):
    if all(r["status"] == "OK" for r in reqs):
        return "COMPLETE"
    return "NOT_FETCHED_BUDGET" if all(r["status"] == NOT_FETCHED for r in reqs) else "PARTIAL"


def _capture_status(root, events):
    if root["status"] != "OK":
        return "INCOMPLETE"
    if not events:
        return "NO_EVENTS"
    return "COMPLETE" if all(e["status"] == "COMPLETE" for e in events) else "INCOMPLETE"


def sweep(archive, fetch_bytes, clock=utc_now, monotonic=time.monotonic, budget_s=900):
    """One capture sweep. The budget is checked before EVERY request."""
    t0 = monotonic()
    cap = {"capture_version": CAPTURE_VERSION, "sportsbook": SPORTSBOOK, "capture_started_at": clock(),
           "capture_completed_at": None, "root": None, "events": []}
    cap["root"] = S.fetch(archive, fetch_bytes, clock, source=SOURCE, request=ROOT_PATH)
    events = []
    if cap["root"]["status"] == "OK":
        try:
            events = discover_events(archive.get(cap["root"]["raw_sha256"]))
        except Exception as exc:  # noqa: BLE001 - malformed root: no offer universe
            cap["root"]["status"] = f"FAILED: unparseable root: {type(exc).__name__}: {exc}"
    for ev in events:
        reqs = []
        for tab in TABS:
            path = event_path(ev["event_id"], tab)
            reqs.append(_not_fetched(path) if monotonic() - t0 > budget_s
                        else S.fetch(archive, fetch_bytes, clock, source=SOURCE, request=path))
        cap["events"].append(dict(ev, requests=reqs, status=_event_status(reqs)))
    cap["capture_completed_at"] = clock()
    cap["status"] = _capture_status(cap["root"], cap["events"])
    cap["capture_sha256"] = canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"})
    return cap


def verify_capture(cap, archive):
    """Re-derive the capture's lineage from archived bytes; raise on any mismatch."""
    if canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"}) != cap.get("capture_sha256"):
        raise CaptureError("capture hash does not rebuild")
    if cap.get("capture_version") != CAPTURE_VERSION or cap.get("sportsbook") != SPORTSBOOK:
        raise CaptureError("unexpected capture version or sportsbook")
    try:
        c0, c1 = S.ts(cap["capture_started_at"]), S.ts(cap["capture_completed_at"])
        root = cap["root"]
        expected = []
        if root.get("status") == "OK":
            S.verify(root, archive, source=SOURCE, request=ROOT_PATH)
            expected = discover_events(archive.get(root["raw_sha256"]))
        elif cap.get("events"):
            raise CaptureError("events recorded without a successful root discovery")
        got = [{k: e.get(k) for k in ("event_id", "event_name", "open_date")} for e in cap.get("events") or []]
        if got != expected:
            raise CaptureError("event identities do not match those derived from the archived root")
        recs = [root] if root.get("status") == "OK" else []
        for e in cap["events"]:
            paths = [r.get("request") for r in e.get("requests") or []]
            if paths != [event_path(e["event_id"], t) for t in TABS]:
                raise CaptureError(f"event {e['event_id']}: tab requests are not bound to its eventId")
            for r in e["requests"]:
                if r.get("source") != SOURCE:
                    raise CaptureError("request from an unexpected source")
                if r["status"] == "OK":
                    S.verify(r, archive, source=SOURCE)
                    recs.append(r)
                elif r["status"] == NOT_FETCHED and any(r.get(k) is not None for k in ("raw_sha256", "started_at")):
                    raise CaptureError("unfetched request carries observation data")
            if e.get("status") != _event_status(e["requests"]):
                raise CaptureError(f"event {e['event_id']}: status does not match its requests")
        for r in recs:
            if S.ts(r["started_at"]) < c0 or S.ts(r["completed_at"]) > c1:
                raise CaptureError("request observed outside the capture window")
        if cap.get("status") != _capture_status(root, cap["events"]):
            raise CaptureError("capture status does not match its requests")
    except S.SourceError as exc:
        raise CaptureError(str(exc)) from exc
    return cap


def _needs_of(line):
    return int(line) + 1 if float(line).is_integer() else int(line + 0.5)


def _odds(rn):
    v = ((rn.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
    if isinstance(v, bool) or not isinstance(v, int) or abs(v) < 100:
        return None
    return v


def _availability(market, runners):
    """(ok, unverified_fields, reason). Absent status fields are UNVERIFIED, never assumed open."""
    unverified = []
    ms = market.get("marketStatus")
    if ms is None:
        unverified.append("marketStatus")
    elif str(ms).upper() != OPEN_MARKET:
        return False, unverified, f"MARKET_NOT_OPEN:{ms}"
    if market.get("inPlay") is None:
        unverified.append("inPlay")
    for rn in runners:
        rs = rn.get("runnerStatus")
        if rs is None:
            unverified.append("runnerStatus")
        elif str(rs).upper() != ACTIVE_RUNNER:
            return False, unverified, f"RUNNER_NOT_ACTIVE:{rs}"
    return True, sorted(set(unverified)), None


def _offer(base, **kw):
    o = dict(base, **kw)
    o["break_even_raw"] = round(PP.implied_probability(o["american_odds"]), 6)   # vig included
    o["offer_id"] = canonical_sha256({k: o[k] for k in (
        "raw_response_sha256", "sportsbook_event_id", "market_id", "market_type", "selection_id", "player_norm",
        "market", "side", "line", "needs", "american_odds")})
    return o


def _two_sided_runner(stat, rn):
    if stat == "pitcher_outs":
        p = FD._parse_outs_runner(rn.get("runnerName"))
        return None if not p else (p[0], p[1], float(p[2]))
    nm = (rn.get("runnerName") or "").strip()
    side = "OVER" if nm.endswith(" Over") else "UNDER" if nm.endswith(" Under") else None
    if side is None or rn.get("handicap") is None:
        return None
    return nm.rsplit(" ", 1)[0].strip(), side, float(rn["handicap"])


def parse_offers(cap, archive, with_rejections=False):
    """Every offer re-derived from archived bytes, plus explicit rejections."""
    verify_capture(cap, archive)
    offers, rejected = [], []

    def reject(rec, m, reason):
        rejected.append({"request": rec["request"], "market_id": m.get("marketId"),
                         "market_type": m.get("marketType"), "reason": reason})

    for ev in cap["events"]:
        for rec in ev["requests"]:
            if rec["status"] != "OK":
                continue
            page = json.loads(archive.get(rec["raw_sha256"]))
            for m in ((page.get("attachments") or {}).get("markets") or {}).values():
                if not isinstance(m, dict):
                    continue
                mt = m.get("marketType") or ""
                if mt not in FD.MARKET_MAP and mt not in FD.TWO_SIDED_MARKETS and not mt.endswith("_OUTS_RECORDED_SB"):
                    continue                                   # not a market S1 prices
                if m.get("eventId") in (None, ""):
                    reject(rec, m, "MARKET_EVENT_ID_ABSENT")
                    continue
                if str(m["eventId"]) != ev["event_id"]:
                    reject(rec, m, "MARKET_EVENT_ID_MISMATCH")
                    continue
                if m.get("inPlay") is True:
                    reject(rec, m, "IN_PLAY")
                    continue
                runners = [rn for rn in (m.get("runners") or []) if isinstance(rn, dict)]
                ok, unverified, why = _availability(m, runners)
                if not ok:
                    reject(rec, m, why)
                    continue
                base = {"sportsbook": SPORTSBOOK, "sportsbook_event_id": ev["event_id"], "event_name": ev["event_name"],
                        "event_open_date": ev["open_date"], "market_type": mt, "market_id": str(m.get("marketId")),
                        "raw_response_sha256": rec["raw_sha256"], "request_path": rec["request"], "host": rec["host"],
                        "observation_started_at": rec["started_at"], "observation_completed_at": rec["completed_at"],
                        "availability_verified": not unverified, "availability_unverified_fields": unverified}
                if mt in FD.MARKET_MAP:
                    stat, need = FD.MARKET_MAP[mt]
                    names = [FD.normalize_name(rn.get("runnerName")) for rn in runners]
                    for rn, norm in zip(runners, names):
                        odds, sel = _odds(rn), rn.get("selectionId")
                        if not norm or names.count(norm) > 1:
                            reject(rec, m, f"AMBIGUOUS_RUNNER_NAME:{rn.get('runnerName')}")
                        elif sel in (None, ""):
                            reject(rec, m, f"MISSING_SELECTION_ID:{rn.get('runnerName')}")
                        elif odds is None:
                            reject(rec, m, f"MALFORMED_ODDS:{rn.get('runnerName')}")
                        else:
                            offers.append(_offer(base, selection_id=str(sel), player_name=rn.get("runnerName"),
                                                 player_norm=norm, market=stat, side="over", line=need - 0.5,
                                                 needs=need, american_odds=odds, fair_novig=None, two_sided=False))
                    continue
                stat = "strikeouts" if mt in FD.TWO_SIDED_MARKETS else "pitcher_outs"
                parsed = []
                for rn in runners:
                    p = _two_sided_runner(stat, rn)
                    if p is None or _odds(rn) is None or rn.get("selectionId") in (None, ""):
                        parsed = None
                        break
                    parsed.append((FD.normalize_name(p[0]), p[0], p[1], p[2], _odds(rn), str(rn["selectionId"])))
                # coherent pair: exactly one player, one line, one OVER and one UNDER, nothing else
                if (not parsed or len(parsed) != 2 or len({x[0] for x in parsed}) != 1
                        or len({x[3] for x in parsed}) != 1 or sorted(x[2] for x in parsed) != ["OVER", "UNDER"]):
                    reject(rec, m, "INCOHERENT_TWO_SIDED_MARKET")
                    continue
                side_of = {x[2]: x for x in parsed}
                t_over, t_under, _hold = PP.devig_two_sided(side_of["OVER"][4], side_of["UNDER"][4])
                for side, fair in (("OVER", t_over), ("UNDER", t_under)):
                    norm, name, _, line, odds, sel = side_of[side]
                    offers.append(_offer(base, selection_id=sel, player_name=name, player_norm=norm, market=stat,
                                         side=side.lower(), line=line, needs=_needs_of(line), american_odds=odds,
                                         fair_novig=round(fair, 6), two_sided=True))
    return (offers, rejected) if with_rejections else offers
