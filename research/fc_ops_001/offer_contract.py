"""Research-only, fail-closed FanDuel offer parsing and champion joins.

The parser checks bytes and market structure. A caller must additionally use
PR #226's verified root/schedule/capture chain before calling any result an
authenticated sportsbook opportunity. No production code imports this file.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

from odds_fanduel import MARKET_MAP, TWO_SIDED_MARKETS, normalize_name

FIRST_INNING_TYPE = "***OVER/UNDER_0.5_RUNS_1ST_INNINGS"
MAX_AGE = timedelta(minutes=45)
_OUTS = re.compile(r"^(.*?)\s+(Over|Under)\s+([\d.]+)$", re.I)


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant.astimezone(timezone.utc) if instant.tzinfo else None


def _price(runner):
    price = ((runner.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
    return price if isinstance(price, int) and not isinstance(price, bool) and abs(price) >= 100 else None


def _line(value):
    try:
        line = float(value)
    except (TypeError, ValueError):
        return None
    return line if math.isfinite(line) and line >= 0 and line * 2 == int(line * 2) else None


def _break_even(american):
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def _two_sided_runner(runner, market_type):
    name = str(runner.get("runnerName") or "").strip()
    if market_type == FIRST_INNING_TYPE:
        return None, name.upper(), 0.5
    if market_type.endswith("_OUTS_RECORDED_SB"):
        match = _OUTS.fullmatch(name)
        return (match.group(1).strip(), match.group(2).upper(), _line(match.group(3))) if match else None
    match = re.fullmatch(r"(.+?)\s+(Over|Under)", name, re.I)
    return (match.group(1).strip(), match.group(2).upper(), _line(runner.get("handicap"))) if match else None


def parse_archived_page(raw_bytes, *, expected_sha256, source_request, source_host,
                        sportsbook_event_id, game_pk, event_start, observed_at,
                        decision_at, player_ids):
    """Return (offers, diagnostics) from one archived page, never a production quote.

    player_ids maps normalized player names to a unique MLB player ID. All
    identity and availability failures are kept as diagnostics, not offers.
    Root discovery, schedule binding and archive persistence remain upstream
    PR #226 responsibilities. A missing status is UNVERIFIED, not OPEN.
    """
    offers, diagnostics = [], []

    def fail(reason, market=None):
        diagnostics.append({"reason": reason, "market_id": (market or {}).get("marketId"),
                            "market_type": (market or {}).get("marketType")})

    if not isinstance(raw_bytes, bytes) or hashlib.sha256(raw_bytes).hexdigest() != expected_sha256:
        fail("RAW_RESPONSE_HASH_MISMATCH")
        return offers, diagnostics
    obs, start, decision = _time(observed_at), _time(event_start), _time(decision_at)
    if not all((obs, start, decision)):
        fail("MISSING_OR_INVALID_TIME")
        return offers, diagnostics
    if obs >= start or decision >= start:
        fail("IN_PLAY_OR_POST_START")
        return offers, diagnostics
    if obs > decision or decision - obs > MAX_AGE:
        fail("FUTURE_OR_STALE_OBSERVATION")
        return offers, diagnostics
    if not sportsbook_event_id or not game_pk:
        fail("MISSING_EVENT_OR_GAME_ID")
        return offers, diagnostics
    if (not source_host or not isinstance(source_request, str)
            or not re.search(r"(?:\?|&)eventId=" + re.escape(str(sportsbook_event_id)) + r"(?:&|$)", source_request)
            or not re.search(r"(?:\?|&)tab=[^&]+", source_request)):
        fail("SOURCE_REQUEST_EVENT_MISMATCH")
        return offers, diagnostics
    try:
        payload = json.loads(raw_bytes)
        markets = payload["attachments"]["markets"]
        if not isinstance(markets, dict):
            raise ValueError("markets is not an object")
    except (ValueError, KeyError, TypeError):
        fail("INVALID_MARKET_PAGE")
        return offers, diagnostics

    seen_identity = set()
    seen_selection = set()
    for market in markets.values():
        if not isinstance(market, dict):
            fail("INVALID_MARKET_OBJECT")
            continue
        market_type = market.get("marketType")
        if market_type not in MARKET_MAP and market_type not in TWO_SIDED_MARKETS \
                and not str(market_type).endswith("_OUTS_RECORDED_SB") and market_type != FIRST_INNING_TYPE:
            continue  # unsupported is not an available offer
        if str(market.get("eventId")) != str(sportsbook_event_id):
            fail("MARKET_EVENT_MISMATCH", market)
            continue
        if market.get("inPlay") is not False:
            fail("IN_PLAY_OR_UNVERIFIED", market)
            continue
        status = market.get("marketStatus")
        if status is None:
            fail("MARKET_STATUS_UNVERIFIED", market)
            continue
        if str(status).upper() != "OPEN":
            fail("MARKET_NOT_OPEN", market)
            continue
        market_id = market.get("marketId")
        runners = market.get("runners")
        if market_id in (None, "") or not isinstance(runners, list) or not runners:
            fail("MISSING_MARKET_ID_OR_RUNNERS", market)
            continue
        if any(not isinstance(r, dict) or r.get("runnerStatus") is None for r in runners):
            fail("RUNNER_STATUS_UNVERIFIED", market)
            continue
        if any(str(r["runnerStatus"]).upper() != "ACTIVE" for r in runners):
            fail("RUNNER_NOT_ACTIVE", market)
            continue
        if market_type in MARKET_MAP:
            stat, needs = MARKET_MAP[market_type]
            names = [normalize_name(r.get("runnerName")) for r in runners]
            if not all(names) or len(names) != len(set(names)):
                fail("DUPLICATE_OR_MISSING_PLAYER", market)
                continue
            parsed = [(r, name, "OVER", float(needs) - 0.5, stat, needs)
                      for r, name in zip(runners, names)]
        else:
            stat = ("nrfi_combined" if market_type == FIRST_INNING_TYPE else
                    "strikeouts" if market_type in TWO_SIDED_MARKETS else "pitcher_outs")
            if len(runners) != 2:
                fail("INCOHERENT_TWO_SIDED_PAIR", market)
                continue
            parts = [_two_sided_runner(r, market_type) for r in runners]
            if any(p is None or p[1] not in ("OVER", "UNDER") or p[2] is None for p in parts):
                fail("INCOHERENT_TWO_SIDED_PAIR", market)
                continue
            names = [normalize_name(p[0]) if p[0] else None for p in parts]
            if parts[0][2] != parts[1][2] or {p[1] for p in parts} != {"OVER", "UNDER"} \
                    or names[0] != names[1] or (stat != "nrfi_combined" and not names[0]):
                fail("INCOHERENT_TWO_SIDED_PAIR", market)
                continue
            line = parts[0][2]
            if line % 1 == 0:  # whole lines can push; this contract has no push rule
                fail("UNSUPPORTED_PUSH_LINE", market)
                continue
            needs = int(line + 0.5)
            parsed = [(r, name, p[1], line, stat, needs)
                      for r, name, p in zip(runners, names, parts)]
        if any(_price(r) is None or r.get("selectionId") in (None, "") for r, *_ in parsed):
            fail("MISSING_SELECTION_OR_ODDS", market)
            continue
        if len({str(r["selectionId"]) for r, *_ in parsed}) != len(parsed):
            fail("DUPLICATE_SELECTION_ID", market)
            continue
        implied = ({side: _break_even(_price(runner)) for runner, _, side, _, _, _ in parsed}
                   if market_type not in MARKET_MAP else {})
        denominator = sum(implied.values()) if implied else None
        proposed = []
        for runner, name, side, line, stat, needs in parsed:
            player_id = None if stat == "nrfi_combined" else player_ids.get(name)
            if stat != "nrfi_combined" and (player_id in (None, "") or isinstance(player_id, (list, tuple, set))):
                fail("PLAYER_ID_UNVERIFIED", market)
                proposed = []
                break
            identity = (str(market_id), str(runner["selectionId"]))
            if identity in seen_identity or str(runner["selectionId"]) in seen_selection:
                fail("DUPLICATE_OFFER_IDENTITY", market)
                proposed = []
                break
            proposed.append({"sportsbook": "fanduel", "source": "archived_fanduel_response",
                             "raw_response_sha256": expected_sha256,
                             "source_request": source_request, "source_host": source_host,
                             "sportsbook_event_id": str(sportsbook_event_id), "game_pk": str(game_pk),
                             "event_start": event_start, "observed_at": observed_at,
                             "market_type": market_type, "market_id": str(market_id),
                             "selection_id": str(runner["selectionId"]),
                             "player_norm": name, "player_id": str(player_id) if player_id is not None else None,
                             "market": stat, "side": side.lower(), "line": line,
                             "needs": needs, "american_odds": _price(runner),
                             "break_even_raw": _break_even(_price(runner)),
                             "fair_novig": (implied[side] / denominator if denominator else None),
                             "availability": "VERIFIED_OPEN_ACTIVE_PREGAME"})
        offers.extend(proposed)
        seen_identity.update((str(market_id), o["selection_id"]) for o in proposed)
        seen_selection.update(o["selection_id"] for o in proposed)
    return offers, diagnostics


def join_champion_decision(decision, offer, *, cutoff, verified_source):
    """Match PR #230 decision_rows to PR #226-like offers by exact wager identity.

    The export's projection.value is the selected wager line in this code
    regime, while needs is the stable canonical identity. Both must agree
    with the source offer. Never join on either field alone.
    """
    if not verified_source:
        return "SOURCE_NOT_VERIFIED"
    if not decision.get("new_publication_candidate"):
        return "NOT_NEW_PUBLICATION_CANDIDATE"
    if decision.get("recommendation_status") != "top_pick":
        return "NOT_CHAMPION_TOP_PICK"
    if not decision.get("canonical_id"):
        return "MISSING_DECISION_ID"
    if offer.get("availability") != "VERIFIED_OPEN_ACTIVE_PREGAME":
        return "OFFER_AVAILABILITY_UNVERIFIED"
    if offer.get("sportsbook") != "fanduel":
        return "SPORTSBOOK_MISMATCH"
    if str(decision.get("game_pk")) != str(offer.get("game_pk")):
        return "GAME_MISMATCH"
    if str(decision.get("player_id")) != str(offer.get("player_id")):
        return "PLAYER_MISMATCH"
    if not decision.get("team") or not offer.get("team") or decision.get("team") != offer.get("team"):
        return "TEAM_MISMATCH"
    if decision.get("market") != offer.get("market") or str(decision.get("wager_side")).lower() != offer.get("side"):
        return "MARKET_OR_SIDE_MISMATCH"
    needs = decision.get("needs")
    if not isinstance(needs, int) or needs < 1 or offer.get("needs") != needs \
            or offer.get("line") != needs - 0.5 or decision.get("line") != offer.get("line"):
        return "THRESHOLD_MISMATCH"
    if decision.get("market_odds") != offer.get("american_odds"):
        return "PRICE_CHANGED_UNREVALIDATED"
    if not offer.get("sportsbook_event_id") or not re.fullmatch(r"[0-9a-f]{64}", str(offer.get("raw_response_sha256"))) \
            or not offer.get("selection_id") or not offer.get("market_id") or not offer.get("market_type") \
            or not offer.get("source_request"):
        return "SOURCE_IDENTITY_MISSING"
    observed, decision_time = _time(offer.get("observed_at")), _time(cutoff)
    if not observed or not decision_time or observed > decision_time or decision_time - observed > MAX_AGE:
        return "OFFER_TIME_INVALID"
    return "MATCHED_RESEARCH_ONLY"


def join_s1_candidate(decision, s1_row, *, cutoff, source_chain_verified):
    """Adapt the exact PR #226 candidate+offer shape to the PR #230 row shape.

    This proves a structural, same-unit match only. It does not certify
    publication exposure, a complete game set, or an active forward run.
    """
    if not source_chain_verified:
        return "SOURCE_NOT_VERIFIED"
    if decision.get("canonical_id") != s1_row.get("candidate_id"):
        return "DECISION_ID_MISMATCH"
    for left, right in (("game_pk", "game_pk"), ("player_id", "player_id"),
                        ("team", "team"), ("market", "stat"), ("wager_side", "side"),
                        ("needs", "needs"), ("line", "line")):
        if decision.get(left) != s1_row.get(right):
            return "S1_CANDIDATE_IDENTITY_MISMATCH"
    if s1_row.get("offer_status") != "AUTHENTICATED" or not s1_row.get("offer"):
        return "S1_OFFER_NOT_AUTHENTICATED"
    if s1_row.get("candidate_odds") != decision.get("market_odds"):
        return "POLICY_PRICE_MISMATCH"
    offer = s1_row["offer"]
    adapted = dict(offer,
                   game_pk=s1_row.get("game_pk"), player_id=s1_row.get("player_id"),
                   team=s1_row.get("team"), source_request=offer.get("request_path"),
                   observed_at=offer.get("observation_completed_at"),
                   availability=("VERIFIED_OPEN_ACTIVE_PREGAME" if offer.get("availability_verified") is True
                                 else "UNVERIFIED"))
    return join_champion_decision(decision, adapted, cutoff=cutoff, verified_source=True)
