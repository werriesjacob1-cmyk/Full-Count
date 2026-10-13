"""One shared, authenticated pregame offer manifest per slate unit, with the
existing-policy champion and the fixed alternative-policy arms at matched N.

The manifest embeds its own inputs (capture record, schedule, minimal
candidate rows, feature snapshots), so verify_manifest() can rebuild it
byte-for-byte from those inputs plus the raw archive. Any essential source
that cannot be authenticated fails closed (ManifestError); non-essential gaps
are recorded per candidate with an explicit exception code, never imputed.
"""
import re
from datetime import datetime, timedelta

import fanduel as FDC
FD = FDC.FD
import features as F

MANIFEST_VERSION = "fc-acc-001-s1-manifest-1"
BLEND_WEIGHT_MODEL = 0.5          # FIXED by S1_PROTOCOL.md; never tuned
MAX_QUOTE_AGE = timedelta(minutes=45)
EVENT_MATCH_TOLERANCE = timedelta(minutes=90)
MODES = ("PROSPECTIVE", "DRY_RUN_NONCONFIRMATORY")
STAT_ALIAS = {"stolen_base": "stolen_bases"}
ARMS = ("CHAMPION_EXISTING_POLICY", "MARKET_ONLY", "BLEND_50_50")


class ManifestError(ValueError):
    pass


def _ts(v):
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ManifestError(f"timestamp must be timezone-aware: {v!r}")
    return t


def _matchup(name):
    return re.sub(r"\s*\([^)]*\)", "", str(name or "")).strip()


def _cand_min(r):
    """The minimal candidate fields the decision depends on (embedded for replay)."""
    p = r.get("prediction") or {}
    return {"candidate_id": r["candidate_id"], "game_pk": str(r["game_pk"]), "player_id": r.get("player_id"),
            "player_name": r.get("player_name"), "combo": bool(r.get("combo_player_ids")), "stat": r["stat"],
            "side": r["market_side"], "needs": r.get("needs"), "line": r.get("line"),
            "p": p.get("hit_probability"), "reliability": p.get("reliability"),
            "qc_status": (r.get("eligibility") or {}).get("qc_status"),
            "lineup_assumed": (r.get("eligibility") or {}).get("lineup_assumed"),
            "recommendation_status": (r.get("selector") or {}).get("recommendation_status"),
            "candidate_odds": (r.get("market") or {}).get("market_odds")}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build(*, unit, games, candidates, capture, archive, feature_snapshots, mode, model_versions):
    if mode not in MODES:
        raise ManifestError(f"unknown mode {mode!r}")
    decision_at = _ts(unit["decision_at"])
    FDC.verify_capture(capture, archive)                      # essential: authenticated source
    if capture["root"]["status"] != "OK":
        raise ManifestError("sportsbook root discovery failed: no authenticated offer universe")
    if _ts(capture["capture_completed_at"]) > decision_at:
        raise ManifestError("capture completed after the decision timestamp")
    offers = FDC.parse_offers(capture, archive)

    # games of this unit; any game already started at decision time is excluded and counted
    gmap, excluded_games = {}, {}
    for g in games:
        fp = _ts(g["first_pitch_utc"])
        if fp <= decision_at:
            excluded_games[str(g["game_pk"])] = "GAME_STARTED_BEFORE_DECISION"
        else:
            gmap[str(g["game_pk"])] = {"matchup": g["matchup"], "first_pitch_utc": g["first_pitch_utc"]}
    if not gmap:
        raise ManifestError("no unit game starts after the decision timestamp")

    # sportsbook event -> game (unique match on matchup and start time, else fail closed for that event)
    ev_status = {e["event_id"]: e["status"] for e in capture["events"]}
    event_game, event_issue = {}, {}
    for e in capture["events"]:
        hits = [pk for pk, g in gmap.items() if _matchup(e["event_name"]) == g["matchup"] and e["open_date"]
                and abs(_ts(e["open_date"]) - _ts(g["first_pitch_utc"])) <= EVENT_MATCH_TOLERANCE]
        if len(hits) == 1:
            event_game[e["event_id"]] = hits[0]
        else:
            event_issue[e["event_id"]] = "UNMAPPED_EVENT" if not hits else "AMBIGUOUS_EVENT"

    by_key = {}
    for o in offers:
        pk = event_game.get(o["sportsbook_event_id"])
        if pk is None:
            continue
        by_key.setdefault((pk, o["player_norm"], o["market"], o["side"], o["needs"]), []).append(o)
    pk_events = {}
    for eid, pk in event_game.items():
        pk_events.setdefault(pk, []).append(eid)

    rows, snaps = [], {}
    for r in candidates:
        c = _cand_min(r)
        code, offer = None, None
        if c["game_pk"] in excluded_games:
            code = excluded_games[c["game_pk"]]
        elif c["game_pk"] not in gmap:
            code = "GAME_NOT_IN_UNIT"
        elif c["combo"]:
            code = "NO_SINGLE_PLAYER_OFFER"
        elif not pk_events.get(c["game_pk"]):
            code = "MISSING_EVENT"
        elif any(ev_status.get(e) != "COMPLETE" for e in pk_events[c["game_pk"]]):
            code = "INCOMPLETE_CAPTURE"
        else:
            key = (c["game_pk"], FD.normalize_name(c["player_name"]), STAT_ALIAS.get(c["stat"], c["stat"]),
                   c["side"], _int(c["needs"]))
            found = by_key.get(key, [])
            prices = {o["american_odds"] for o in found}
            if not found:
                code = "MISSING_QUOTE"
            elif len(prices) > 1:
                code = "CONFLICTING_OFFERS"
            else:
                offer = min(found, key=lambda o: o["offer_id"])          # identical price: deterministic pick
                if _ts(offer["observation_completed_at"]) > decision_at:
                    code, offer = "OBSERVED_AFTER_DECISION", None
                elif decision_at - _ts(offer["observation_completed_at"]) > MAX_QUOTE_AGE:
                    code, offer = "STALE_QUOTE", None
        c["offer_status"] = code or "AUTHENTICATED"
        c["offer"] = None if offer is None else {k: offer[k] for k in (
            "offer_id", "sportsbook", "sportsbook_event_id", "market_type", "selection_id", "player_name", "market",
            "side", "line", "needs", "american_odds", "break_even_raw", "fair_novig", "raw_response_sha256",
            "request_path", "observation_completed_at")}
        c["candidate_price_differs"] = bool(offer and c["candidate_odds"] is not None
                                            and c["candidate_odds"] != offer["american_odds"])
        c["existing_policy_eligible"] = c["recommendation_status"] == "top_pick"
        c["alternative_policy_eligible"] = bool(offer and c["qc_status"] == "kept" and c["lineup_assumed"] is not True
                                                and c["reliability"] in ("A", "B", "C") and c["p"] is not None)
        if offer:
            snaps[c["candidate_id"]] = feature_snapshots.get(c["candidate_id"]) or F.snapshot(
                c["candidate_id"], c["stat"], None, None, captured_at=None, model_versions=model_versions)
        rows.append(c)
    rows.sort(key=lambda c: c["candidate_id"])

    def take(pool, key, n):
        out, seen = [], set()
        for c in sorted(pool, key=lambda c: (-key(c), c["candidate_id"])):
            k = (c["player_id"] or c["player_name"], c["stat"], c["side"])
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
            if len(out) == n:
                break
        return out

    champ_pool = [c for c in rows if c["existing_policy_eligible"] and c["offer"]]
    champion = take(champ_pool, lambda c: c["p"] or 0.0, 10 ** 6)
    n = len(champion)
    alt = [c for c in rows if c["alternative_policy_eligible"]]
    arms = {"CHAMPION_EXISTING_POLICY": champion,
            "MARKET_ONLY": take(alt, lambda c: c["offer"]["break_even_raw"], n) if n else [],
            "BLEND_50_50": take(alt, lambda c: BLEND_WEIGHT_MODEL * c["p"]
                                + (1 - BLEND_WEIGHT_MODEL) * c["offer"]["break_even_raw"], n) if n else []}
    arm_out = {}
    for name, sel in arms.items():
        arm_out[name] = {"policy": "EXISTING" if name.startswith("CHAMPION") else "ALTERNATIVE",
                         "n_target": n, "n_selected": len(sel),
                         "status": "ZERO_CHAMPION" if n == 0 else ("SHORTFALL" if len(sel) < n else "MATCHED"),
                         "selections": [{"candidate_id": c["candidate_id"], "offer_id": c["offer"]["offer_id"],
                                         "american_odds": c["offer"]["american_odds"],
                                         "break_even_raw": c["offer"]["break_even_raw"], "p": c["p"],
                                         "existing_policy_eligible": c["existing_policy_eligible"]} for c in sel]}
    unauth_champ = sorted(c["candidate_id"] for c in rows if c["existing_policy_eligible"] and not c["offer"])
    acct = {}
    for c in rows:
        acct[c["offer_status"]] = acct.get(c["offer_status"], 0) + 1
    feat_missing = sum(1 for s in snaps.values() if s["status"] != "COMPLETE")
    body = {"manifest_version": MANIFEST_VERSION, "mode": mode,
            "evidence_class": "PROSPECTIVE_SHADOW_RESEARCH_ONLY" if mode == "PROSPECTIVE" else "DRY_RUN_NONCONFIRMATORY",
            "unit": dict(unit), "model_versions": dict(model_versions), "blend_weight_model": BLEND_WEIGHT_MODEL,
            "inputs": {"capture": capture, "games": sorted(games, key=lambda g: str(g["game_pk"])),
                       "candidates": [_cand_min(r) for r in sorted(candidates, key=lambda r: r["candidate_id"])],
                       "feature_snapshots": {k: snaps[k] for k in sorted(snaps)}},
            "excluded_games": excluded_games, "event_mapping_issues": event_issue,
            "candidates": rows, "arms": arm_out,
            "accounting": {"candidates": len(rows), "offer_status": dict(sorted(acct.items())),
                           "champion_unauthenticated": unauth_champ,
                           "candidate_price_differs": sum(c["candidate_price_differs"] for c in rows),
                           "feature_snapshots": len(snaps), "feature_snapshots_missing_required": feat_missing},
            "first_pitch_utc_min": min(g["first_pitch_utc"] for g in gmap.values())}
    return {**body, "manifest_sha256": FDC.canonical_sha256(body)}


def verify_manifest(m, archive):
    body = {k: v for k, v in m.items() if k != "manifest_sha256"}
    if FDC.canonical_sha256(body) != m.get("manifest_sha256"):
        raise ManifestError("manifest hash does not rebuild")
    inp = m["inputs"]
    # rebuild from embedded inputs: candidates are stored in minimal form; reconstruct the
    # record shape build() reads, then require an identical manifest.
    cands = [{"candidate_id": c["candidate_id"], "game_pk": c["game_pk"], "player_id": c["player_id"],
              "player_name": c["player_name"], "combo_player_ids": [1] if c["combo"] else None, "stat": c["stat"],
              "market_side": c["side"], "needs": c["needs"], "line": c["line"],
              "prediction": {"hit_probability": c["p"], "reliability": c["reliability"]},
              "eligibility": {"qc_status": c["qc_status"], "lineup_assumed": c["lineup_assumed"]},
              "selector": {"recommendation_status": c["recommendation_status"]},
              "market": {"market_odds": c["candidate_odds"]}} for c in inp["candidates"]]
    rebuilt = build(unit=m["unit"], games=inp["games"], candidates=cands, capture=inp["capture"], archive=archive,
                    feature_snapshots=inp["feature_snapshots"], mode=m["mode"], model_versions=m["model_versions"])
    if rebuilt["manifest_sha256"] != m["manifest_sha256"]:
        raise ManifestError("manifest does not re-derive from its inputs and the raw archive")
    return m["manifest_sha256"]
