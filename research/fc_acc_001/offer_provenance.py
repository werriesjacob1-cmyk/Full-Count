"""FC-ACC-001 offer-provenance requirements (Amendment A1 / S1 protocol).

A forward-study offer is usable ONLY if every field below is present and
consistent. This module validates offers and measures how far the EXISTING
evidence (sealed board freezes + committed ``data/props`` snapshots) satisfies
the requirement. It never fills a missing field: an unbindable or incomplete
offer is reported as such.
"""
import glob
import json
import os
import re
from datetime import datetime

from common import ROOT

REQUIRED_OFFER_FIELDS = (
    "sportsbook", "sportsbook_event_id", "game_pk", "player_id_or_name", "market",
    "side", "line", "american_odds", "observation_completed_at", "raw_response_sha256",
)


class OfferProvenanceError(ValueError):
    pass


def _ts(v):
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise OfferProvenanceError("timestamp must be timezone-aware")
    return t


def validate_offer(offer, *, decision_cutoff, selection=None):
    """Raise unless the offer is complete, observed before the decision cutoff,
    bound to a raw response, and (if given) identical to the selection's
    market/side/line/odds."""
    missing = [f for f in REQUIRED_OFFER_FIELDS if offer.get(f) in (None, "")]
    if missing:
        raise OfferProvenanceError(f"offer missing required provenance: {missing}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(offer["raw_response_sha256"])):
        raise OfferProvenanceError("raw_response_sha256 must be 64 lowercase hex")
    if _ts(offer["observation_completed_at"]) > _ts(decision_cutoff):
        raise OfferProvenanceError("offer observed after the decision cutoff")
    if selection is not None:
        for k in ("market", "side", "line", "american_odds"):
            if selection.get(k) != offer.get(k):
                raise OfferProvenanceError(f"selection {k} {selection.get(k)!r} != offer {offer.get(k)!r}")
    return True


_STAT = {"stolen_base": "stolen_bases"}


def _norm(name):
    return re.sub(r"[^a-z ]", "", str(name or "").lower()).strip()


def load_prop_snapshots(date):
    """Committed normalized FanDuel prop snapshots for one slate date. These are
    NOT raw responses: there is no raw byte hash to bind."""
    path = os.path.join(ROOT, f"data/props/props_{date}.json")
    if not os.path.exists(path):
        return []
    d = json.load(open(path))
    rows = []
    for s in d.get("snapshots") or []:
        for r in s["rows"]:
            rows.append({"kind": "one_sided", "taken_at": r["taken_at"], "event_id": r.get("event_id"),
                         "player": r.get("player_norm"), "stat": r["stat"], "needs": r.get("needs"),
                         "over": r.get("american")})
    for s in d.get("two_sided_snapshots") or []:
        for r in s["rows"]:
            rows.append({"kind": "two_sided", "taken_at": r["taken_at"], "event_id": None,
                         "player": r.get("player_norm"), "stat": r["market"], "needs": r.get("needs"),
                         "over": r.get("over_odds"), "book": r.get("book")})
    return rows


def bind_freeze_record(record, snapshots, sealed_at):
    """Best available binding of a frozen selection to a committed pre-seal
    snapshot row (same player, stat, line, side=over, exact odds). Returns an
    offer dict (raw_response_sha256 and, for two-sided rows, the FanDuel event id
    are genuinely unavailable -> None) or None when no row matches."""
    if record["market_side"] != "over" or record.get("combo_player_ids"):
        return None
    stat = _STAT.get(record["stat"], record["stat"])
    try:
        needs = int(record["needs"])
    except (TypeError, ValueError):
        return None
    cut = _ts(sealed_at)
    hits = [r for r in snapshots if r["player"] == _norm(record["player_name"]) and r["stat"] == stat
            and r["needs"] == needs and r["over"] == record["market"]["market_odds"] and _ts(r["taken_at"]) <= cut]
    if not hits:
        return None
    best = max(hits, key=lambda r: r["taken_at"])
    return {"sportsbook": "fanduel" if best["kind"] == "two_sided" and best.get("book") == "fanduel" else
            ("fanduel" if best["kind"] == "one_sided" else None),
            "sportsbook_event_id": best["event_id"], "game_pk": record["game_pk"],
            "player_id_or_name": record.get("player_id") or record["player_name"], "market": record["stat"],
            "side": record["market_side"], "line": record.get("line"), "american_odds": best["over"],
            "observation_completed_at": best["taken_at"], "raw_response_sha256": None,
            "snapshot_kind": best["kind"]}


def feasibility(slates_with_arms, arm_names):
    """For each arm's selections: how many bind to a pre-seal committed snapshot,
    and how many would pass validate_offer (expected 0: no raw-response hash)."""
    out = {}
    cache = {}
    for arm in arm_names:
        n = bound = valid = 0
        reasons = {}
        for s in slates_with_arms:
            snaps = cache.setdefault(s["date"], load_prop_snapshots(s["date"]))
            for r in s["arms"][arm]:
                n += 1
                off = bind_freeze_record(r, snaps, s["sealed_at"])
                if off is None:
                    reasons["no_pre_seal_snapshot_row"] = reasons.get("no_pre_seal_snapshot_row", 0) + 1
                    continue
                bound += 1
                try:
                    validate_offer(off, decision_cutoff=s["sealed_at"])
                    valid += 1
                except OfferProvenanceError as e:
                    key = str(e).split(":")[0]
                    reasons[key] = reasons.get(key, 0) + 1
        out[arm] = {"selections": n, "bound_to_pre_seal_snapshot": bound, "fully_valid_offers": valid,
                    "failure_reasons": reasons}
    return out
