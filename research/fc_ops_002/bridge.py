"""Offline, research-only replay across S1 offers, champion decisions and M2.

This does not import unmerged PR #226/#230/#235 code or certify that a
synthetic/archived response was an executable sportsbook offer. The upstream
S1 capture, champion export and frozen-model artifact must be independently
verified before any prospective use; this bridge never activates capture.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from odds_fanduel import normalize_name
import prop_probability as pp
import recommendation as rec_policy
from research.fc_ops_001.offer_contract import join_s1_candidate, parse_archived_page

MODEL_SHA256 = "07115a546d839aeb7cf1ff0df45d9c9c27ac3a7757fc242d1393d38f8157207b"
SYNTHETIC = "SYNTHETIC_CONTRACT"
ARCHIVED = "ARCHIVED_OBSERVATIONAL"


class BridgeError(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else hashlib.sha256(canonical_bytes(value)).hexdigest()


def _require(ok, reason):
    if not ok:
        raise BridgeError(reason)


def _time(value):
    _require(isinstance(value, str), "INVALID_TIMESTAMP")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeError("INVALID_TIMESTAMP") from exc
    _require(ts.tzinfo is not None, "INVALID_TIMESTAMP")
    return ts.astimezone(timezone.utc)


def _verified_record_hash(record, field):
    _require(isinstance(record, dict) and isinstance(record.get(field), str), f"MISSING_{field.upper()}")
    body = {k: v for k, v in record.items() if k != field}
    _require(sha256(body) == record[field], f"BAD_{field.upper()}")


def frozen_probability(model, values):
    """Evaluate FC-ACC-015's stored M2 transform, without refitting it."""
    keys, median, missing, mu, sd, coef = (model.get(k) for k in
                                            ("keys", "median", "missing_indicator_for", "mu", "sd", "coef"))
    _require(all(isinstance(x, list) for x in (keys, median, missing, mu, sd, coef)), "MODEL_SCHEMA_INVALID")
    _require(len(keys) == len(median) and len(set(keys)) == len(keys), "MODEL_SCHEMA_INVALID")
    _require(all(k in keys for k in missing), "MODEL_SCHEMA_INVALID")
    _require(len(mu) == len(sd) == len(coef) - 1 == len(keys) + len(missing), "MODEL_SCHEMA_INVALID")
    _require(model.get("coef_order") == ["intercept", *keys, *(f"{k}__missing" for k in missing)],
             "MODEL_SCHEMA_INVALID")
    _require(isinstance(values, dict), "FEATURE_VALUES_MISSING")
    raw, absent = [], {}
    for key, med in zip(keys, median):
        value = values.get(key)
        bad = value is None
        if not bad:
            _require(isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value), "FEATURE_VALUE_INVALID")
        _require(isinstance(med, (int, float)) and math.isfinite(med), "MODEL_SCHEMA_INVALID")
        raw.append(float(med if bad else value))
        absent[key] = bad
    raw += [float(absent[k]) for k in missing]
    z = float(coef[0])
    for x, center, scale, weight in zip(raw, mu, sd, coef[1:]):
        _require(all(isinstance(v, (int, float)) and math.isfinite(v)
                     for v in (center, scale, weight)) and scale > 0, "MODEL_SCHEMA_INVALID")
        z += ((x - center) / scale) * weight
    return 1.0 / (1.0 + math.exp(-max(-700.0, min(700.0, z))))


def _offer_id(parsed):
    # Exact S1 fanduel._offer() identity fields, in its canonical JSON order.
    keys = ("raw_response_sha256", "sportsbook_event_id", "market_id", "market_type", "selection_id",
            "player_norm", "market", "side", "line", "needs", "american_odds")
    return sha256({k: parsed[k] for k in keys})


def _offer_from_archive(candidate, capture, archive, bindings, decision_at):
    """Re-parse exact archived bytes; return (strict offer, rejection reason)."""
    s1_offer = candidate.get("offer")
    if candidate.get("offer_status") != "AUTHENTICATED" or not isinstance(s1_offer, dict):
        return None, candidate.get("offer_status") or "S1_OFFER_MISSING"
    event_id = str(s1_offer.get("sportsbook_event_id") or "")
    binding = bindings.get(event_id)
    if not isinstance(binding, dict) or str(binding.get("game_pk")) != str(candidate.get("game_pk")):
        return None, "EVENT_GAME_BINDING_MISSING"
    evs = [e for e in capture.get("events", []) if str(e.get("event_id")) == event_id]
    if len(evs) != 1:
        return None, "CAPTURE_EVENT_MISSING_OR_DUPLICATE"
    raw_sha = s1_offer.get("raw_response_sha256")
    matching = [r for r in evs[0].get("requests", []) if r.get("status") == "OK"
                and r.get("raw_sha256") == raw_sha and r.get("request") == s1_offer.get("request_path")]
    if len(matching) != 1:
        return None, "CAPTURE_REQUEST_MISSING_OR_DUPLICATE"
    rec = matching[0]
    if rec.get("completed_at") != s1_offer.get("observation_completed_at"):
        return None, "OBSERVATION_TIME_MISMATCH"
    raw = archive.get(raw_sha)
    if not isinstance(raw, bytes):
        return None, "RAW_RESPONSE_MISSING"
    if sha256(raw) != raw_sha:
        return None, "RAW_RESPONSE_HASH_MISMATCH"
    players = binding.get("player_ids")
    if not isinstance(players, dict):
        return None, "PLAYER_BINDING_MISSING"
    offers, diagnostics = parse_archived_page(
        raw, expected_sha256=raw_sha, source_request=rec.get("request"), source_host=rec.get("host"),
        sportsbook_event_id=event_id, game_pk=candidate.get("game_pk"),
        event_start=binding.get("event_start"), observed_at=rec.get("completed_at"),
        decision_at=decision_at, player_ids=players)
    same = [o for o in offers if o["market_id"] == str(s1_offer.get("market_id"))
            and o["selection_id"] == str(s1_offer.get("selection_id"))]
    if len(same) != 1:
        return None, "STRICT_OFFER_REJECTED:" + (diagnostics[0]["reason"] if diagnostics else "NOT_FOUND")
    offer = same[0]
    identity = ("sportsbook", "sportsbook_event_id", "market_id", "market_type", "selection_id",
                "market", "side", "line", "needs", "american_odds", "break_even_raw",
                "fair_novig", "raw_response_sha256")
    if any(offer[k] != s1_offer.get(k) for k in identity):
        return None, "S1_OFFER_IDENTITY_MISMATCH"
    if s1_offer.get("availability_verified") is not True or s1_offer.get("offer_id") != _offer_id(offer):
        return None, "S1_OFFER_PROVENANCE_INVALID"
    if offer["player_id"] != str(candidate.get("player_id")) or offer["player_norm"] != normalize_name(s1_offer.get("player_name")):
        return None, "PLAYER_IDENTITY_MISMATCH"
    if binding.get("teams_by_player_id", {}).get(offer["player_id"]) != candidate.get("team"):
        return None, "TEAM_IDENTITY_MISMATCH"
    for key, s1_key in (("market", "stat"), ("side", "side"), ("line", "line"), ("needs", "needs")):
        if offer[key] != candidate.get(s1_key):
            return None, "CANDIDATE_OFFER_IDENTITY_MISMATCH"
    return dict(offer, team=candidate.get("team"), offer_id=s1_offer["offer_id"]), None


def _verify_root(capture, archive, bindings, decision_at):
    _verified_record_hash(capture, "capture_sha256")
    started, completed = _time(capture.get("capture_started_at")), _time(capture.get("capture_completed_at"))
    _require(started <= completed <= decision_at, "CAPTURE_AFTER_DECISION")
    root = capture.get("root") or {}
    _require(root.get("status") == "OK", "ROOT_DISCOVERY_MISSING")
    _require(root.get("source") == "fanduel_sportsbook_api" and root.get("request", "").startswith(
        "content-managed-page?page=CUSTOM&customPageId=mlb&_ak="), "ROOT_SOURCE_IDENTITY_MISMATCH")
    _require(started <= _time(root.get("started_at")) <= _time(root.get("completed_at")) <= completed,
             "ROOT_OBSERVATION_OUTSIDE_CAPTURE")
    raw_sha = root.get("raw_sha256")
    raw = archive.get(raw_sha)
    _require(isinstance(raw, bytes) and sha256(raw) == raw_sha, "ROOT_RESPONSE_HASH_MISMATCH")
    try:
        source_events = json.loads(raw)["attachments"]["events"]
        _require(isinstance(source_events, dict), "ROOT_EVENTS_INVALID")
    except (ValueError, KeyError, TypeError) as exc:
        raise BridgeError("ROOT_EVENTS_INVALID") from exc
    selected = [e for e in source_events.values() if isinstance(e, dict)
                and e.get("eventId") not in (None, "") and " @ " in (e.get("name") or "")]
    discovered = {str(e["eventId"]): e for e in selected}
    _require(len(discovered) == len(selected), "ROOT_DUPLICATE_EVENT_ID")
    events = capture.get("events")
    _require(isinstance(events, list) and len(events) == len(discovered), "ROOT_EVENT_CENSUS_MISMATCH")
    _require({str(e.get("event_id")) for e in events} == set(map(str, discovered)), "ROOT_EVENT_CENSUS_MISMATCH")
    for event in events:
        event_id = str(event["event_id"])
        src = discovered[event_id]
        _require(str(src.get("eventId")) == event_id and event.get("event_name") == src.get("name")
                 and event.get("open_date") == src.get("openDate"), "ROOT_EVENT_IDENTITY_MISMATCH")
        binding = bindings.get(event_id)
        _require(isinstance(binding, dict) and binding.get("sportsbook_start") == src.get("openDate"),
                 "ROOT_EVENT_BINDING_MISSING")
        for rec in event.get("requests") or []:
            if rec.get("status") == "OK":
                _require(rec.get("source") == "fanduel_sportsbook_api"
                         and rec.get("request", "").startswith("event-page?eventId=" + event_id + "&tab=")
                         and started <= _time(rec.get("started_at")) <= _time(rec.get("completed_at")) <= completed,
                         "PAGE_OBSERVATION_OUTSIDE_CAPTURE")


def _rank(pool, n, score):
    chosen, players, game_counts = [], set(), {}
    for row in sorted(pool, key=lambda r: (-score(r), r["candidate_id"])):
        player, game = row["player_id"], row["game_pk"]
        if player in players or game_counts.get(game, 0) >= 2:
            continue
        chosen.append(row["candidate_id"])
        players.add(player)
        game_counts[game] = game_counts.get(game, 0) + 1
        if len(chosen) == n:
            break
    return chosen


def _alternative_policy_gate(candidate, probability, offer, snap, archive, decision_at):
    """Require source-bound pregame Top Pick evidence for a replacement row."""
    if not isinstance(snap, dict) or snap.get("candidate_id") != candidate.get("candidate_id"):
        return "ALTERNATIVE_POLICY_EVIDENCE_MISSING"
    digest = snap.get("source_sha256")
    raw = archive.get(digest)
    if not isinstance(raw, bytes) or sha256(raw) != digest:
        return "ALTERNATIVE_POLICY_BYTES_MISSING"
    try:
        source = json.loads(raw)
    except (ValueError, TypeError):
        return "ALTERNATIVE_POLICY_SOURCE_INVALID"
    if source != {k: v for k, v in snap.items() if k != "source_sha256"}:
        return "ALTERNATIVE_POLICY_SOURCE_MISMATCH"
    try:
        if _time(snap.get("observed_at")) > decision_at:
            return "ALTERNATIVE_POLICY_EVIDENCE_LATE"
    except BridgeError:
        return "ALTERNATIVE_POLICY_TIME_INVALID"
    lower = snap.get("baseline_ci_lower")
    base = candidate.get("p")
    if not isinstance(lower, (int, float)) or isinstance(lower, bool) or not isinstance(base, (int, float)) or not 0 <= lower <= base:
        return "ALTERNATIVE_POLICY_CI_MISSING"
    if snap.get("baseline_p") != base or snap.get("lineup_confirmed") is not True or snap.get("data_fresh") is not True or snap.get("sample_n") in (None, 0) or snap.get("reliability") != candidate.get("reliability") or snap.get("reliability") not in rec_policy.TOP_PICK_MIN_RELIABILITY:
        return "ALTERNATIVE_POLICY_PREREQUISITE_FAIL"
    adjusted_lower = probability - (base - lower)
    if probability < rec_policy.TOP_PICK_MIN_PROB or adjusted_lower < 0:
        return "ALTERNATIVE_TOP_PICK_FLOOR_FAIL"
    verdict = pp.value_verdict(probability, offer["american_odds"], prob_lo=adjusted_lower,
                               min_roi=rec_policy.TOP_PICK_MIN_ROI, require_robust=True)
    return None if verdict["verdict"] == "BET" else "ALTERNATIVE_PRICE_OR_ROBUSTNESS_FAIL"


def build_ledger(*, champion_export, publication_manifest, s1_manifest, raw_archive, event_bindings,
                 frozen_model_bytes, feature_snapshots, policy_snapshots, evidence_class):
    """Produce a canonical, sealed *offline* decision ledger; no network I/O."""
    _require(evidence_class in (SYNTHETIC, ARCHIVED), "PROSPECTIVE_CERTIFICATION_UNSUPPORTED")
    _verified_record_hash(champion_export, "record_sha256")
    _verified_record_hash(s1_manifest, "manifest_sha256")
    _require(champion_export.get("schema_version") == "fc-acc-009-dashboard-decision-1"
             and champion_export.get("publication_status") == "INTENDED_NOT_EXPOSURE_PROVEN", "CHAMPION_SCHEMA_INVALID")
    _require(isinstance(publication_manifest, dict)
             and champion_export.get("manifest_sha256") == sha256(publication_manifest)
             and champion_export.get("manifest_artifact_id") == publication_manifest.get("artifact_id"),
             "PUBLICATION_MANIFEST_MISMATCH")
    _require(s1_manifest.get("manifest_version") == "fc-acc-001-s1-manifest-2", "S1_SCHEMA_INVALID")
    _require(isinstance(raw_archive, dict) and isinstance(event_bindings, dict), "ARCHIVE_OR_BINDING_MISSING")
    _require(isinstance(feature_snapshots, dict) and isinstance(policy_snapshots, dict),
             "FEATURE_OR_POLICY_SNAPSHOTS_MISSING")
    _require(isinstance(frozen_model_bytes, bytes), "FROZEN_MODEL_MISSING")
    model_sha = sha256(frozen_model_bytes)
    _require(evidence_class == SYNTHETIC or model_sha == MODEL_SHA256, "FROZEN_MODEL_SHA_MISMATCH")
    try:
        model = json.loads(frozen_model_bytes)["total_bases"]
    except (ValueError, KeyError, TypeError) as exc:
        raise BridgeError("FROZEN_MODEL_SCHEMA_INVALID") from exc
    unit = s1_manifest.get("unit") or {}
    cutoff = unit.get("decision_at")
    decision_at = _time(cutoff)
    prepared_at = _time((champion_export.get("run") or {}).get("prepared_at"))
    _require(prepared_at <= decision_at and prepared_at.astimezone(ZoneInfo("America/New_York")).date().isoformat() == unit.get("date"),
             "CHAMPION_OUTSIDE_UNIT_CUTOFF")
    capture = (s1_manifest.get("inputs") or {}).get("capture") or {}
    _verify_root(capture, raw_archive, event_bindings, decision_at)
    unit_games = s1_manifest.get("unit_games")
    _require(isinstance(unit_games, list) and len(unit_games) == len(set(unit_games)), "UNIT_GAMES_INVALID")
    _require(set(map(str, champion_export.get("observed_games") or [])) <= set(map(str, unit_games)),
             "CHAMPION_GAME_OUTSIDE_UNIT")
    ids = (champion_export.get("decision_ids") or {}).get("new_publication_candidates")
    n = (champion_export.get("counts") or {}).get("new_publication_policy_N")
    _require(isinstance(ids, list) and len(ids) == len(set(ids)) and isinstance(n, int)
             and not isinstance(n, bool) and n >= 0 and len(ids) == n, "CHAMPION_N_MISMATCH")
    prelim = (champion_export.get("preliminary_manifest") or {}).get("candidates")
    _require(isinstance(prelim, list)
             and sorted(c.get("canonical_id") for c in prelim) == sorted(ids), "PRELIMINARY_MANIFEST_ID_MISMATCH")
    staged = (champion_export.get("decision_ids") or {}).get("staged_manifest_candidates")
    staged_rows = publication_manifest.get("candidates")
    withheld = (champion_export.get("decision_ids") or {}).get("withheld_after_preparation")
    _require(isinstance(staged, list) and isinstance(staged_rows, list) and isinstance(withheld, list)
             and sorted(c.get("canonical_id") for c in staged_rows) == sorted(staged)
             and set(staged) <= set(ids) and sorted(set(ids) - set(staged)) == sorted(withheld)
             and (champion_export.get("counts") or {}).get("staged_publication_candidates") == len(staged),
             "STAGED_PUBLICATION_IDS_MISMATCH")
    decisions = champion_export.get("decision_rows")
    _require(isinstance(decisions, list), "CHAMPION_DECISIONS_MISSING")
    by_decision = {d.get("canonical_id"): d for d in decisions}
    _require(len(by_decision) == len(decisions) and all(d in by_decision for d in ids), "CHAMPION_DECISION_ID_MISMATCH")
    _require(all(by_decision[d].get("recommendation_status") == "top_pick"
                 and by_decision[d].get("new_publication_candidate") is True
                 and by_decision[d].get("already_public") is not True for d in ids), "CHAMPION_POLICY_ID_MISMATCH")
    rows = s1_manifest.get("candidates")
    _require(isinstance(rows, list), "S1_CANDIDATES_MISSING")
    by_s1 = {c.get("candidate_id"): c for c in rows}
    _require(len(by_s1) == len(rows), "DUPLICATE_S1_CANDIDATE")
    reasons = []
    if capture.get("status") != "COMPLETE":
        reasons.append("INCOMPLETE_CAPTURE")
    if set(map(str, unit_games)) != {str(b.get("game_pk")) for b in event_bindings.values()}:
        reasons.append("INCOMPLETE_GAME_BINDINGS")
    if s1_manifest.get("game_issues") or s1_manifest.get("event_mapping_issues"):
        reasons.append("GAME_OR_EVENT_MAPPING_ISSUE")
    if s1_manifest.get("unit_status") not in ("COMPARABLE", "ZERO_CHAMPION"):
        reasons.append("S1_UNIT_NOT_COMPARABLE")
    rejected, eligible = [], []
    for candidate in sorted(rows, key=lambda c: str(c.get("candidate_id"))):
        cid = candidate.get("candidate_id")
        if cid not in ids and candidate.get("alternative_policy_eligible") is not True:
            rejected.append({"candidate_id": cid, "reason": "S1_ALTERNATIVE_ELIGIBILITY_MISSING"})
            continue
        offer, why = _offer_from_archive(candidate, capture, raw_archive, event_bindings, cutoff)
        if why:
            rejected.append({"candidate_id": cid, "reason": why})
            continue
        if candidate.get("qc_status") != "kept" or candidate.get("lineup_assumed") is True \
                or candidate.get("reliability") not in ("A", "B", "C"):
            rejected.append({"candidate_id": cid, "reason": "PREGAME_ELIGIBILITY_UNVERIFIED"})
            continue
        p = candidate.get("p")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 < p < 1:
            rejected.append({"candidate_id": cid, "reason": "BASE_PROBABILITY_INVALID"})
            continue
        model_p, feature_hash = p, None
        if candidate.get("stat") == "total_bases" and candidate.get("needs") == 2:
            snap = feature_snapshots.get(cid)
            if not isinstance(snap, dict) or snap.get("status") != "COMPLETE" or snap.get("candidate_id") != cid \
                    or not isinstance(snap.get("source_sha256"), str) or len(snap["source_sha256"]) != 64 \
                    or not isinstance(snap.get("values"), dict):
                rejected.append({"candidate_id": cid, "reason": "FROZEN_FEATURES_MISSING"})
                continue
            feature_raw = raw_archive.get(snap["source_sha256"])
            if not isinstance(feature_raw, bytes) or sha256(feature_raw) != snap["source_sha256"]:
                rejected.append({"candidate_id": cid, "reason": "FROZEN_FEATURE_BYTES_MISSING"})
                continue
            try:
                feature_source = json.loads(feature_raw)
            except (ValueError, TypeError):
                feature_source = None
            if not isinstance(feature_source, dict) or any(
                    feature_source.get(k) != snap.get(k) for k in ("candidate_id", "observed_at", "values")):
                rejected.append({"candidate_id": cid, "reason": "FROZEN_FEATURE_SOURCE_MISMATCH"})
                continue
            if _time(snap.get("observed_at")) > decision_at:
                rejected.append({"candidate_id": cid, "reason": "LATE_FEATURE_SNAPSHOT"})
                continue
            model_p = frozen_probability(model, snap["values"])
            feature_hash = sha256(snap)
        if cid not in ids:
            gate = _alternative_policy_gate(candidate, model_p, offer, policy_snapshots.get(cid),
                                            raw_archive, decision_at)
            if gate:
                rejected.append({"candidate_id": cid, "reason": gate})
                continue
        eligible.append({"candidate_id": cid, "game_pk": str(candidate["game_pk"]),
                         "player_id": str(candidate["player_id"]), "team": candidate["team"],
                         "market": offer["market"], "side": offer["side"], "line": offer["line"],
                         "needs": offer["needs"], "sportsbook": offer["sportsbook"],
                         "sportsbook_event_id": offer["sportsbook_event_id"], "market_id": offer["market_id"],
                         "selection_id": offer["selection_id"], "offer_id": offer["offer_id"],
                         "american_odds": offer["american_odds"], "break_even_raw": offer["break_even_raw"],
                         "fair_novig": offer["fair_novig"], "raw_response_sha256": offer["raw_response_sha256"],
                         "observed_at": offer["observed_at"], "decision_at": cutoff,
                         "availability": offer["availability"], "eligibility": "STRICT_OFFER_AND_PREGAME",
                         "baseline_p": p, "challenger_p": model_p, "feature_sha256": feature_hash,
                         "model_identity": ("FC_ACC_015_FROZEN_TB_M2" if feature_hash else "S1_EXISTING_B0")})
    by_eligible = {e["candidate_id"]: e for e in eligible}
    champion = []
    for cid in ids:
        if cid not in by_eligible or cid not in by_s1:
            reasons.append("CHAMPION_OFFER_SHORTFALL:" + cid)
            continue
        status = join_s1_candidate(by_decision[cid], by_s1[cid], cutoff=cutoff, source_chain_verified=True)
        if status != "MATCHED_RESEARCH_ONLY":
            reasons.append("CHAMPION_JOIN_" + status + ":" + cid)
        else:
            champion.append(cid)
    if n == 0:
        market_only, challenger = [], []
    else:
        market_only = _rank(eligible, n, lambda r: r["break_even_raw"])
        def expected_roi(r):
            odds = r["american_odds"]
            profit = odds / 100 if odds > 0 else 100 / -odds
            return r["challenger_p"] * profit - (1 - r["challenger_p"])
        challenger = _rank(eligible, n, expected_roi)
    for name, selected in (("MARKET_ONLY", market_only), ("FROZEN_TB_CHALLENGER", challenger)):
        if len(selected) < n:
            reasons.append(name + "_SHORTFALL")
    if len(champion) != n:
        reasons.append("CHAMPION_SHORTFALL")
    champion_game_counts = {g: sum(by_eligible[c]["game_pk"] == g for c in champion)
                            for g in sorted({by_eligible[c]["game_pk"] for c in champion})}
    champion_player_counts = {p: sum(by_eligible[c]["player_id"] == p for c in champion)
                              for p in sorted({by_eligible[c]["player_id"] for c in champion})}
    status = ("NON_COMPARABLE" if reasons else "ZERO_CHAMPION" if n == 0 else "STRUCTURALLY_MATCHED")
    body = {"schema_version": "fc-ops-002-offline-ledger-1", "research_only": True,
            "evidence_class": evidence_class, "prospective_certified": False,
            "unit": {"date": unit.get("date"), "window": unit.get("window"), "decision_at": cutoff,
                     "game_pks": sorted(map(str, unit_games))},
            "input_sha256": {"champion_export": champion_export["record_sha256"],
                             "publication_manifest": champion_export["manifest_sha256"],
                             "s1_manifest": s1_manifest["manifest_sha256"], "frozen_model": model_sha,
                             "raw_responses": sorted(raw_archive)},
            "champion_N": n, "eligible_universe": eligible, "rejected_opportunities": rejected,
            "arms": {"ACTUAL_INTENDED_CHAMPION": champion, "FROZEN_TB_CHALLENGER": challenger,
                     "MARKET_ONLY": market_only},
            "shortfalls": {name: n - len(sel) for name, sel in
                           (("ACTUAL_INTENDED_CHAMPION", champion), ("FROZEN_TB_CHALLENGER", challenger),
                            ("MARKET_ONLY", market_only))},
            "unit_status": status, "non_comparable_reasons": sorted(set(reasons)),
            "portfolio_clusters": {"champion_game_counts": champion_game_counts,
                                   "champion_player_counts": champion_player_counts,
                                   "alternative_policy": "MAX_ONE_PER_PLAYER_MAX_TWO_PER_GAME"},
            "uncertainty": "FROZEN_MODEL_INTERVAL_NOT_VALIDATED_FOR_CROSS_MARKET_EV",
            "settlement": "PENDING_UNVERIFIED", "exposure": "STAGED_MANIFEST_NOT_EXPOSURE_PROVEN",
            "publication_binding": {"manifest_artifact_id": champion_export["manifest_artifact_id"],
                                    "staged_candidate_ids": staged,
                                    "withheld_after_preparation": withheld},
            "limitations": ["S1_CANDIDATE_POOL_ONLY_NOT_ALL_SPORTSBOOK_OFFERS",
                            "NO_SHARED_VERIFIED_RUN_ID", "UPSTREAM_SOURCE_AND_EXPORT_VERIFIERS_NOT_RUN_HERE",
                            "FEATURE_SOURCE_DERIVATION_NOT_INDEPENDENTLY_VERIFIED",
                            "ALTERNATIVE_POLICY_INPUT_DERIVATION_NOT_INDEPENDENTLY_VERIFIED",
                            "NO_AUTHENTIC_SETTLEMENT_OR_BOOK_VOID_RULES",
                            "NO_PROSPECTIVE_CERTIFICATION"]}
    return {**body, "ledger_sha256": sha256(body)}


def verify_ledger(record):
    _verified_record_hash(record, "ledger_sha256")
    return True


def evaluate_external_grades(ledger, grade_archive, grade_refs):
    """Link separately supplied grades and compute flat-stake returns.

    This verifies identity and bytes, not the independent truth of the scorer
    or sportsbook settlement rules. Incomplete arms get no ROI figure.
    """
    verify_ledger(ledger)
    _require(isinstance(grade_archive, dict) and isinstance(grade_refs, dict), "GRADES_MISSING")
    universe = {r["candidate_id"]: r for r in ledger["eligible_universe"]}
    selected = {cid for arm in ledger["arms"].values() for cid in arm}
    _require(set(grade_refs) <= selected, "GRADE_FOR_UNSELECTED_CANDIDATE")
    grades = {}
    for cid in sorted(grade_refs):
        digest = grade_refs[cid]
        raw = grade_archive.get(digest)
        _require(isinstance(raw, bytes) and sha256(raw) == digest, "GRADE_SOURCE_HASH_MISMATCH")
        try:
            grade = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise BridgeError("GRADE_SOURCE_INVALID") from exc
        row = universe[cid]
        for key in ("candidate_id", "game_pk", "player_id", "market", "side", "line", "needs", "offer_id"):
            _require(grade.get(key) == row.get(key) if key != "candidate_id" else grade.get(key) == cid,
                     "GRADE_IDENTITY_MISMATCH")
        _require(grade.get("status") in ("WIN", "LOSS", "VOID", "PUSH")
                 and isinstance(grade.get("settled_at"), str)
                 and isinstance(grade.get("grader_version"), str), "GRADE_STATUS_INVALID")
        _time(grade["settled_at"])
        if grade["status"] in ("VOID", "PUSH"):
            _require(isinstance(grade.get("sportsbook_rule_id"), str)
                     and grade["sportsbook_rule_id"], "VOID_PUSH_RULE_UNVERIFIED")
        grades[cid] = {"status": grade["status"], "grade_sha256": digest,
                       "grader_version": grade["grader_version"],
                       "sportsbook_rule_id": grade.get("sportsbook_rule_id")}
    results = {}
    for name, choices in ledger["arms"].items():
        statuses = {status: 0 for status in ("WIN", "LOSS", "VOID", "PUSH", "MISSING")}
        net = 0.0
        for cid in choices:
            status = grades[cid]["status"] if cid in grades else "MISSING"
            statuses[status] += 1
            if status == "WIN":
                odds = universe[cid]["american_odds"]
                net += odds / 100 if odds > 0 else 100 / -odds
            elif status == "LOSS":
                net -= 1
        complete = (ledger["unit_status"] == "STRUCTURALLY_MATCHED"
                    and len(choices) == ledger["champion_N"] and statuses["MISSING"] == 0)
        results[name] = {"counts": statuses, "flat_stake_net": net if complete else None,
                         "flat_stake_roi": net / len(choices) if complete and choices else None,
                         "complete": complete}
    body = {"schema_version": "fc-ops-002-external-grade-link-1", "research_only": True,
            "grade_truth_independently_verified": False, "ledger_sha256": ledger["ledger_sha256"],
            "grades": grades, "arms": results}
    return {**body, "grade_link_sha256": sha256(body)}


def write_once(path, record):
    """Create one local research seal; never overwrite a different record."""
    verify_ledger(record)
    payload = canonical_bytes(record) + b"\n"
    dest = Path(path)
    try:
        with dest.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return "CREATED"
    except FileExistsError:
        _require(dest.read_bytes() == payload, "WRITE_ONCE_CONFLICT")
        return "ALREADY_IDENTICAL"
