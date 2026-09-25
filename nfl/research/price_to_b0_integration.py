"""Manual price-to-authoritative-B0 research join; no workflow or public selector.

Consumes an already sealed B0 shadow board and an already captured FanDuel
archive. A B0 board sealed after the offer can never be joined retroactively.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from nfl.archive.provenance import utcnow
from nfl.normalize.player_prop_markets import normalize_payload
from nfl.prospective.shadow_snapshot import seal_snapshot, validate_pregame_timing
from nfl.research.price_aware_offer_capture import (
    GAME, read_authoritative_b0, match_authoritative_b0,
    strict_observed_prices, verify_capture)
from nfl.research.price_aware_offers import (
    _hash, _time, evaluate_offer, write_evidence)

VERSION = "NFL_PRICE_TO_SEALED_B0_V1"


def validate_b0(snapshot: dict) -> None:
    """Reapply the existing sealer's identity/cardinality contract."""
    rebuilt = seal_snapshot(snapshot["records"], slate_date=snapshot["slate_date"],
                            code_sha=snapshot["code_sha"],
                            source_vintage=snapshot["source_vintage"],
                            sealed_at=snapshot["sealed_at"])
    if rebuilt["snapshot_sha256"] != snapshot["snapshot_sha256"]:
        raise ValueError("B0 seal or structural identity mismatch")
    # A content hash alone permits a synthetically sealed row observed after
    # its stated seal. Reapply the prospective cutoff to every record.
    validate_pregame_timing(snapshot["records"], snapshot["sealed_at"])
    if _time(snapshot["sealed_at"]) >= _time("2026-09-25T00:15:00Z"):
        raise ValueError("B0 snapshot at or after canonical kickoff")


def raw_offer_matches(folder: Path, manifest: list[dict], candidate: dict,
                      raw_sha: str) -> bool:
    """Independently match frozen quote/selection fields to raw book bytes."""
    entries = [e for e in manifest if e.get("sha256") == raw_sha and e.get("raw_file")]
    if len(entries) != 1:
        return False
    entry = entries[0]
    wrapper = json.loads((folder/entry["raw_file"]).read_text(encoding="utf-8"))
    raw = gzip.decompress(base64.b64decode(wrapper["data"], validate=True))
    if hashlib.sha256(raw).hexdigest() != raw_sha:
        return False
    payload = json.loads(raw)
    markets = payload.get("attachments", {}).get("markets", {})
    matching_markets = [m for key, m in markets.items()
                        if str(m.get("marketId", key)) == candidate["market_id"]]
    if len(matching_markets) != 1:
        return False
    market = matching_markets[0]
    if (str(market.get("eventId")) != str(candidate["event_id"]) or
            market.get("marketStatus") != "OPEN" or market.get("inPlay") is not False):
        return False
    if candidate.get("quote_timestamp") is not None:
        evidence = candidate.get("quote_evidence")
        if not isinstance(evidence, dict):
            return False
        # marketTime in the current FanDuel payload is the event time, not
        # the time the displayed odds originated. Never accept it as a quote.
        fields = {"market.priceUpdatedAt": "priceUpdatedAt",
                  "market.lastUpdatedAt": "lastUpdatedAt",
                  "market.oddsUpdatedAt": "oddsUpdatedAt"}
        field = fields.get(evidence.get("source_field"))
        if (field is None or market.get(field) != candidate["quote_timestamp"] or
                evidence.get("source_sha256") != raw_sha or
                evidence.get("market_id") != candidate["market_id"]):
            return False
    if not strict_observed_prices(candidate, market):
        return False
    normalized = normalize_payload(payload, captured_at=entry["observed_at"])
    keys = ("event_id", "market_id", "player_name", "market", "shape")
    price_keys = (("line", "over_odds", "under_odds", "over_selection_id", "under_selection_id")
                  if candidate["shape"] == "primary" else
                  ("threshold", "yes_odds", "selection_id"))
    return any(all(c.get(k) == candidate.get(k) for k in keys+price_keys)
               for c in normalized["candidates"])


def _b0_prices(candidate: dict, b0: dict) -> list[dict]:
    """Use B0's frozen binary probability only where push mass is zero."""
    if candidate["shape"] != "primary" or candidate["line"] % 1 != .5:
        return []
    over, under = b0.get("model_over_probability"), b0.get("model_under_probability")
    if type(over) not in (float, int) or type(under) not in (float, int):
        raise ValueError("B0 probabilities absent")
    if (not math.isfinite(over) or not math.isfinite(under) or
            min(over, under) < 0 or max(over, under) > 1 or
            not math.isclose(over+under, 1, abs_tol=1e-10)):
        raise ValueError("B0 probabilities invalid")
    out = []
    for side, odds, selection, win in (
        ("OVER",candidate["over_odds"],candidate["over_selection_id"],over),
        ("UNDER",candidate["under_odds"],candidate["under_selection_id"],under)):
        payout = odds/100 if odds > 0 else 100/abs(odds)
        out.append({"side":side,"selection_id":selection,"observed_american_odds":odds,
                    "model_win_probability":win,"model_push_probability":0.0,
                    "model_loss_probability":1-win,
                    "break_even_probability":1/(1+payout),
                    "expected_net_units":win*payout-(1-win),
                    "probability_source":"SEALED_AUTHORITATIVE_B0_EMPIRICAL"})
    overround = sum(p["break_even_probability"] for p in out)
    for p in out:
        p["market_fair_probability"] = p["break_even_probability"] / overround
        p["model_edge_vs_fair"] = p["model_win_probability"] - p["market_fair_probability"]
        p["model_edge_vs_break_even"] = p["model_win_probability"] - p["break_even_probability"]
    return out


def integrate(capture_dir: Path, output: Path, *, b0_path: Path | None = None,
              as_of: str | None = None) -> dict:
    """Create one immutable research artifact; never overwrite an earlier run."""
    captured = verify_capture(capture_dir)
    if captured["canonical_game_id"] != GAME or captured["game"]["game_id"] != GAME:
        raise ValueError("capture canonical game mismatch")
    if str(captured["book_event"]["event_id"]) not in {
            str(r["candidate"]["event_id"]) for r in captured["records"]}:
        raise ValueError("book event not represented in price records")
    now = as_of or utcnow()
    shadow = read_authoritative_b0(b0_path) if b0_path else None
    if shadow:
        validate_b0(shadow)
    rows = []
    for original in captured["records"]:
        c = dict(original["candidate"])
        raw_ok = raw_offer_matches(capture_dir, captured["sources"], c,
                                   original["source_sha256"])
        if not raw_ok:
            raise ValueError("sealed candidate does not match raw book quote")
        # B0 is a two-sided primary-line prediction. An alternate ladder must
        # never inherit a same-player projection as if it were that selection.
        b0 = match_authoritative_b0(c, shadow) if c["shape"] == "primary" else None
        if b0 and any(b0.get(key) != c.get(key) for key in (
                "player_name", "team", "over_selection_id", "under_selection_id")):
            b0 = None
        if b0 and b0.get("event_id") != c["event_id"]:
            raise ValueError("B0 event mismatch")
        if b0:
            c["authoritative_b0_status"] = "JOINED"
            c["authoritative_b0_snapshot_sha256"] = shadow["snapshot_sha256"]
            c["availability_status"] = b0["availability_status"]
            c["decision_status"] = b0["decision_status"]
        priced = evaluate_offer(c, original["distribution"], as_of=now,
                                raw_source_sha256=original["source_sha256"])
        reasons = list(priced["reasons"])
        if shadow is None:
            reasons.append("AUTHORITATIVE_B0_SNAPSHOT_UNAVAILABLE")
        elif _time(shadow["sealed_at"]) > _time(c["captured_at"]):
            reasons.append("B0_SEALED_AFTER_OFFER")
        elif b0 is None:
            reasons.append("NO_EXACT_PRIOR_B0_QUOTE_JOIN")
        if c["shape"] != "primary" or c["line"] % 1 != .5:
            reasons.append("B0_PROBABILITY_UNSUPPORTED_FOR_THRESHOLD")
        b0_prices = _b0_prices(c,b0) if b0 and c["shape"] == "primary" else []
        status = ("NO_PLAY" if priced["decision_status"] == "NO_PLAY"
                  else "QUARANTINED" if reasons else "SHADOW_ONLY")
        rows.append({"candidate":c, "source_sha256":original["source_sha256"],
                     "original_price_record_sha256":original["record_sha256"],
                     "authoritative_b0_record":b0,
                     "b0_observation_id":b0.get("observation_id") if b0 else None,
                     "b0_projection":b0.get("model_projection") if b0 else None,
                     "b0_prices":b0_prices, "challenger_record_sha256":original["record_sha256"],
                     "challenger_model":original["distribution"]["model_version"],
                     "evaluated_at":now, "decision_status":status,
                     "reasons":sorted(set(reasons)), "bettable":False})
    result = {"schema_version":VERSION,"research_only":True,"bettable":False,
              "canonical_game_id":GAME,"book_event_id":captured["book_event"]["event_id"],
              "capture_snapshot_sha256":captured["snapshot_sha256"],
              "capture_started_at":captured["started_at"],
              "capture_source_verification":captured["_verification"],
              "b0_snapshot_sha256":shadow["snapshot_sha256"] if shadow else None,
              "b0_code_sha":shadow["code_sha"] if shadow else None,
              "b0_source_vintage":shadow["source_vintage"] if shadow else None,
              "b0_sealed_at":shadow["sealed_at"] if shadow else None,
              "integrated_at":now,"records":rows,
              "counts":dict(Counter(r["decision_status"] for r in rows)),
              "policy":"RESEARCH_ONLY; no wager, public pick, or model promotion"}
    result["integration_sha256"] = _hash(result)
    write_evidence(output,result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir",type=Path,required=True)
    parser.add_argument("--b0-snapshot",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    value = integrate(args.capture_dir,args.output,b0_path=args.b0_snapshot)
    print(json.dumps({"counts":value["counts"],"seal":value["integration_sha256"],
                      "b0_joined":value["b0_snapshot_sha256"] is not None}))
