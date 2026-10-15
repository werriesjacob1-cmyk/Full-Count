"""One shared, authenticated pregame offer manifest per slate unit, with the
existing-policy champion and the fixed alternative-policy arms at equal volume.

Every input is source-bound and re-derivable:
  * candidates, probabilities and policy statuses come from the SEALED
    production board freeze (``board_freeze.verify_board_seal``), stored in the
    raw archive and referenced by hash;
  * games, start times, game state, confirmed lineups and probable starters
    come ONLY from an archived raw MLB statsapi schedule response observed by
    the decision cutoff (schedule.py);
  * offers come ONLY from archived FanDuel bytes whose event lineage is
    re-derived from the archived root (fanduel.py).

Equal volume: the champion target N is fixed from the board's existing-policy
``top_pick`` decisions in the unit BEFORE any offer filtering. Missing quotes,
incomplete sweeps and unverifiable identities reduce what can be selected but
never N; a unit whose coverage is incomplete is NON_COMPARABLE, and no arm is
ever labelled MATCHED in such a unit. A policy decision whose captured price
differs from the price it was made at is POLICY_PRICE_CHANGED_UNREVALIDATED
(not selected) rather than silently carried over.

A manifest is never certified prospective evidence by itself:
``certification.certified_prospective`` is always False here; only a verified
external anchor (anchor.py) can support that claim.
"""
import os
import re
import sys
from datetime import timedelta

import fanduel as FDC
import features as F
import schedule as SCH
import sources as S

FD = FDC.FD
sys.path.insert(0, os.path.join(FDC.ROOT, "research", "fc_acc_001"))
import board_freeze as BF  # noqa: E402
import offer_provenance as OP  # noqa: E402

MANIFEST_VERSION = "fc-acc-001-s1-manifest-2"
BLEND_WEIGHT_MODEL = 0.5          # FIXED by S1_PROTOCOL.md; never tuned
MAX_QUOTE_AGE = timedelta(minutes=45)
# AMENDMENT_A2: one common, rule-determined decision cutoff per DAY/NIGHT unit,
# exactly DECISION_LEAD before the unit's earliest scheduled first pitch in the
# archived pregame schedule. No per-game cutoffs, no operator-chosen times.
DECISION_LEAD = timedelta(minutes=30)
MODES = ("PROSPECTIVE", "DRY_RUN_NONCONFIRMATORY")
STAT_ALIAS = {"stolen_base": "stolen_bases"}
PITCHER_STATS = ("strikeouts", "pitcher_outs")
# single-player markets S1 can authenticate; anything else (nrfi_combined,
# combined_strikeouts, ...) is recorded MARKET_NOT_IN_S1_SCOPE, never joined
S1_STATS = frozenset({v[0] for v in FD.MARKET_MAP.values()} | set(PITCHER_STATS))
ARMS = ("CHAMPION_EXISTING_POLICY", "MARKET_ONLY", "BLEND_50_50")


class ManifestError(ValueError):
    pass


def _ts(v, field="timestamp"):
    try:
        return S.ts(v, field)
    except S.SourceError as exc:
        raise ManifestError(str(exc)) from exc


def _matchup(name):
    return re.sub(r"\s*\([^)]*\)", "", str(name or "")).strip()


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _cand_min(r):
    p = r.get("prediction") or {}
    return {"candidate_id": r["candidate_id"], "game_pk": str(r["game_pk"]),
            "player_id": None if r.get("player_id") in (None, "") else str(r["player_id"]),
            "player_name": r.get("player_name"), "team": r.get("team"),
            "combo": bool(r.get("combo_player_ids")), "stat": r["stat"], "side": r["market_side"],
            "needs": _int(r.get("needs")), "line": _float(r.get("line")),
            "p": p.get("hit_probability"), "reliability": p.get("reliability"),
            "qc_status": (r.get("eligibility") or {}).get("qc_status"),
            "lineup_assumed": (r.get("eligibility") or {}).get("lineup_assumed"),
            "recommendation_status": (r.get("selector") or {}).get("recommendation_status"),
            "candidate_odds": (r.get("market") or {}).get("market_odds")}


def _verified_board(board, unit, decision_at):
    try:
        BF.verify_board_seal(board)
    except Exception as exc:  # noqa: BLE001
        raise ManifestError(f"production board seal does not verify: {exc}") from exc
    if board.get("date") != unit["date"]:
        raise ManifestError("board date does not match the unit date")
    for f in ("board_generated_at", "sealed_at"):
        if _ts(board.get(f), f) > decision_at:
            raise ManifestError(f"board {f} is after the decision cutoff")
    return board


def build(*, unit, board, schedule_source, capture, archive, feature_snapshots, mode):
    if mode not in MODES:
        raise ManifestError(f"unknown mode {mode!r}")
    if unit.get("window") not in ("DAY", "NIGHT"):
        raise ManifestError("unit window must be DAY or NIGHT")
    decision_at = _ts(unit.get("decision_at"), "decision_at")
    board = _verified_board(board, unit, decision_at)
    board_sha, _ = archive.put_json(board)

    # ---- authenticated schedule: unit games, state, lineups, starters ----
    try:
        sched = SCH.load(schedule_source, archive, unit["date"], not_after=unit["decision_at"])
    except SCH.ScheduleError as exc:
        raise ManifestError(str(exc)) from exc
    try:
        unit_games = {pk: g for pk, g in sched.items()
                      if SCH.window_of(g["first_pitch_utc"], unit["date"]) == unit["window"]}
    except SCH.ScheduleError as exc:
        raise ManifestError(str(exc)) from exc
    if not unit_games:
        raise ManifestError("no scheduled games in this unit")
    # one common cutoff for the whole unit, before its EARLIEST first pitch; a
    # decision after any unit game starts is refused outright (no game is ever
    # dropped to make a late decision "pregame")
    earliest = min(unit_games.values(), key=lambda g: _ts(g["first_pitch_utc"]))["first_pitch_utc"]
    required_cutoff = _ts(earliest) - DECISION_LEAD
    if decision_at != required_cutoff:
        raise ManifestError(f"decision_at {unit['decision_at']} is not the unit cutoff "
                            f"{required_cutoff.isoformat()} (earliest first pitch {earliest} minus {DECISION_LEAD})")
    active = unit_games                        # every unit game stays in the unit and in N

    # ---- authenticated sportsbook capture ----
    try:
        FDC.verify_capture(capture, archive)
    except FDC.CaptureError as exc:
        raise ManifestError(f"capture does not verify: {exc}") from exc
    if capture["root"]["status"] != "OK":
        raise ManifestError("sportsbook root discovery failed: no authenticated offer universe")
    if _ts(capture["capture_completed_at"]) > decision_at:
        raise ManifestError("capture completed after the decision timestamp")
    offers, rejections = FDC.parse_offers(capture, archive, with_rejections=True)

    # ---- event -> game (unique on matchup and start time) and per-game coverage ----
    ev_status = {e["event_id"]: e["status"] for e in capture["events"]}
    event_game, event_issue = {}, {}
    for e in capture["events"]:
        hits = [pk for pk, g in active.items() if _matchup(e["event_name"]) == g["matchup"] and e["open_date"]
                and abs(_ts(e["open_date"]) - _ts(g["first_pitch_utc"])) <= SCH.EVENT_MATCH_TOLERANCE]
        if len(hits) == 1:
            event_game[e["event_id"]] = hits[0]
        elif hits:
            event_issue[e["event_id"]] = "AMBIGUOUS_EVENT"
    board_starts = board.get("game_start_times") or {}
    game_issue = {}
    for pk, g in active.items():
        evs = [eid for eid, gpk in event_game.items() if gpk == pk]
        if g["abstract_state"] != SCH.PREGAME_STATE:
            game_issue[pk] = "GAME_NOT_PREGAME_IN_RAW"
        elif pk not in board_starts:
            game_issue[pk] = "BOARD_MISSING_GAME"
        elif _ts(board_starts[pk]) != _ts(g["first_pitch_utc"]):
            game_issue[pk] = "BOARD_SCHEDULE_MISMATCH"
        elif not evs:
            game_issue[pk] = "MISSING_EVENT"
        elif len(evs) > 1:
            game_issue[pk] = "AMBIGUOUS_EVENT"
        elif ev_status[evs[0]] != "COMPLETE":
            game_issue[pk] = "INCOMPLETE_CAPTURE"

    # ---- offer index on exact identity; selection-id consistency ----
    by_key, sel_ident = {}, {}
    for o in offers:
        pk = event_game.get(o["sportsbook_event_id"])
        if pk is None:
            continue
        ident = (o["player_norm"], o["market"], o["side"], o["line"])
        sel_ident.setdefault((o["sportsbook_event_id"], o["selection_id"]), set()).add(ident)
        by_key.setdefault((pk,) + ident, []).append(o)
    bad_sel = {k for k, v in sel_ident.items() if len(v) > 1}

    records = sorted(board["records"], key=lambda r: r["candidate_id"])
    names = {}
    for r in records:
        c = _cand_min(r)
        if c["game_pk"] in active and not c["combo"] and c["player_id"]:
            names.setdefault(FD.normalize_name(c["player_name"]), set()).add(c["player_id"])
    ambiguous_names = {n for n, ids in names.items() if len(ids) > 1}

    rows, snaps_ok, snaps_rejected = [], {}, {}
    for r in records:
        c = _cand_min(r)
        code, offer, g = None, None, active.get(c["game_pk"])
        if g is None:
            code = "GAME_NOT_IN_UNIT"
        elif c["combo"]:
            code = "NO_SINGLE_PLAYER_OFFER"
        elif STAT_ALIAS.get(c["stat"], c["stat"]) not in S1_STATS:
            code = "MARKET_NOT_IN_S1_SCOPE"
        elif c["game_pk"] in game_issue:
            code = game_issue[c["game_pk"]]
        elif FD.normalize_name(c["player_name"]) in ambiguous_names:
            code = "AMBIGUOUS_PLAYER_NAME"
        else:
            ok, why = (SCH.starter_check if c["stat"] in PITCHER_STATS else SCH.lineup_check)(
                g, c["player_id"], c["team"])
            if not ok:
                code = why
            elif c["line"] is None or c["needs"] is None or c["side"] not in ("over", "under"):
                code = "CANDIDATE_IDENTITY_INCOMPLETE"
            else:
                key = (c["game_pk"], FD.normalize_name(c["player_name"]), STAT_ALIAS.get(c["stat"], c["stat"]),
                       c["side"], c["line"])
                found = by_key.get(key, [])
                if not found:
                    code = "MISSING_QUOTE"
                elif (len({(o["american_odds"], o["selection_id"], o["market_id"]) for o in found}) > 1
                      or any((o["sportsbook_event_id"], o["selection_id"]) in bad_sel for o in found)):
                    code = "CONFLICTING_OFFERS"
                else:
                    offer = min(found, key=lambda o: o["offer_id"])        # identical offers seen on several tabs
                    if offer["needs"] != c["needs"]:
                        code, offer = "LINE_NEEDS_INCONSISTENT", None
                    elif _ts(offer["observation_completed_at"]) > decision_at:
                        code, offer = "OBSERVED_AFTER_DECISION", None
                    elif decision_at - _ts(offer["observation_completed_at"]) > MAX_QUOTE_AGE:
                        code, offer = "STALE_QUOTE", None
                    else:
                        try:
                            OP.validate_offer(dict(offer, game_pk=c["game_pk"], player_id_or_name=c["player_id"]),
                                              decision_cutoff=unit["decision_at"],
                                              selection={"market": key[2], "side": c["side"], "line": c["line"],
                                                         "american_odds": offer["american_odds"]})
                        except OP.OfferProvenanceError:
                            code, offer = "OFFER_PROVENANCE_INVALID", None
        c["offer_status"] = code or "AUTHENTICATED"
        c["offer"] = None if offer is None else {k: offer[k] for k in (
            "offer_id", "sportsbook", "sportsbook_event_id", "market_id", "market_type", "selection_id",
            "player_name", "market", "side", "line", "needs", "american_odds", "break_even_raw", "fair_novig",
            "raw_response_sha256", "request_path", "observation_completed_at", "availability_verified",
            "availability_unverified_fields")}
        c["price_changed_since_policy"] = bool(offer) and c["candidate_odds"] != offer["american_odds"]
        c["alternative_policy_eligible"] = bool(offer and c["qc_status"] == "kept" and c["lineup_assumed"] is not True
                                                and c["reliability"] in ("A", "B", "C") and c["p"] is not None)
        snap = (feature_snapshots or {}).get(c["candidate_id"])
        if snap is not None:
            why = F.check(snap, candidate_id=c["candidate_id"], stat=c["stat"], decision_at=unit["decision_at"],
                          archive=archive, player_id=c["player_id"], game_date=unit["date"])
            (snaps_rejected if why else snaps_ok)[c["candidate_id"]] = why or snap["status"]
        rows.append(c)

    def dedup(pool, key):
        out, seen = [], set()
        for c in sorted(pool, key=lambda c: (-key(c), c["candidate_id"])):
            k = (c["player_id"] or c["player_name"], c["stat"], c["side"])
            if k not in seen:
                seen.add(k)
                out.append(c)
        return out

    # champion opportunities are fixed from the frozen policy decisions BEFORE offer filtering
    opportunities = dedup([c for c in rows if c["game_pk"] in active and c["recommendation_status"] == "top_pick"],
                          lambda c: c["p"] or 0.0)
    n = len(opportunities)
    for c in rows:
        c["champion_opportunity"] = any(c is o for o in opportunities)
        c["champion_status"] = None if not c["champion_opportunity"] else (
            c["offer_status"] if c["offer_status"] != "AUTHENTICATED" else
            "POLICY_PRICE_CHANGED_UNREVALIDATED" if c["price_changed_since_policy"] else "SELECTED")
    champion = [c for c in opportunities if c["champion_status"] == "SELECTED"]
    alt = [c for c in rows if c["alternative_policy_eligible"]]
    arms = {"CHAMPION_EXISTING_POLICY": champion,
            "MARKET_ONLY": dedup(alt, lambda c: c["offer"]["break_even_raw"])[:n],
            "BLEND_50_50": dedup(alt, lambda c: BLEND_WEIGHT_MODEL * c["p"]
                                 + (1 - BLEND_WEIGHT_MODEL) * c["offer"]["break_even_raw"])[:n]}

    reasons = []
    if capture["status"] != "COMPLETE":
        reasons.append(f"CAPTURE_{capture['status']}")
    reasons += sorted({f"GAME_{code}" for code in game_issue.values()})
    if len(champion) < n:
        reasons.append("CHAMPION_SHORTFALL")
    if any(len(arms[a]) < n for a in ARMS[1:]):
        reasons.append("ALTERNATIVE_SHORTFALL")
    unit_status = "ZERO_CHAMPION" if n == 0 else ("COMPARABLE" if not reasons else "NON_COMPARABLE")
    arm_out = {}
    for name, sel in arms.items():
        status = ("ZERO_CHAMPION" if n == 0 else "SHORTFALL" if len(sel) < n
                  else "MATCHED" if unit_status == "COMPARABLE" else "NON_COMPARABLE")
        arm_out[name] = {"policy": "EXISTING" if name.startswith("CHAMPION") else "ALTERNATIVE",
                         "publication_eligible": False, "n_target": n, "n_selected": len(sel), "status": status,
                         "selections": [{"candidate_id": c["candidate_id"], "offer_id": c["offer"]["offer_id"],
                                         "american_odds": c["offer"]["american_odds"],
                                         "break_even_raw": c["offer"]["break_even_raw"], "p": c["p"],
                                         "champion_opportunity": c["champion_opportunity"]} for c in sel]}

    used = [c for sel in arms.values() for c in sel]
    blockers = {"REMOTE_ANCHOR_NOT_VERIFIED", "MODEL_FEATURE_VALUES_UNVERIFIED_PRODUCTION",
                "CANDIDATE_POOL_LIMITED_TO_FREEZE_CHOSEN_LINES"}
    if mode != "PROSPECTIVE":
        blockers.add("DRY_RUN_MODE")
    if unit_status != "COMPARABLE":
        blockers.add(f"UNIT_{unit_status}")
    if any(not c["offer"]["availability_verified"] for c in used):
        blockers.add("SPORTSBOOK_AVAILABILITY_SCHEMA_UNMEASURED")
    acct = {}
    for c in rows:
        acct[c["offer_status"]] = acct.get(c["offer_status"], 0) + 1
    rej = {}
    for x in rejections:
        k = x["reason"].split(":", 1)[0]
        rej[k] = rej.get(k, 0) + 1
    body = {"manifest_version": MANIFEST_VERSION, "mode": mode,
            "evidence_class": "PROSPECTIVE_SHADOW_RESEARCH_ONLY" if mode == "PROSPECTIVE" else "DRY_RUN_NONCONFIRMATORY",
            "unit": dict(unit), "model_versions": dict(board.get("provenance") or {}),
            "blend_weight_model": BLEND_WEIGHT_MODEL,
            "inputs": {"board_object_sha256": board_sha, "schedule_source": schedule_source, "capture": capture,
                       "feature_snapshots": {k: feature_snapshots[k] for k in sorted(feature_snapshots or {})}},
            "unit_games": sorted(unit_games), "game_issues": game_issue,
            "decision_rule": {"rule": "AMENDMENT_A2_UNIT_EARLIEST_FIRST_PITCH_MINUS_LEAD",
                              "lead_minutes": int(DECISION_LEAD.total_seconds() // 60),
                              "unit_earliest_first_pitch_utc": earliest, "unit_tz": "America/New_York",
                              "day_night_split_local_hour": SCH.DAY_NIGHT_SPLIT_LOCAL_HOUR},
            "event_mapping_issues": event_issue, "candidates": rows, "arms": arm_out,
            "unit_status": unit_status, "non_comparable_reasons": reasons,
            "accounting": {"candidates": len(rows), "offer_status": dict(sorted(acct.items())),
                           "champion_opportunities": n, "champion_selected": len(champion),
                           "champion_not_selected": {c["candidate_id"]: c["champion_status"]
                                                     for c in opportunities if c["champion_status"] != "SELECTED"},
                           "parser_rejections": dict(sorted(rej.items())),
                           "feature_snapshots_admitted": dict(sorted(snaps_ok.items())),
                           "feature_snapshots_rejected": dict(sorted(snaps_rejected.items()))},
            "certification": {"certified_prospective": False, "blockers": sorted(blockers)},
            "first_pitch_utc_min": earliest}
    return {**body, "manifest_sha256": FDC.canonical_sha256(body)}


def verify_manifest(m, archive):
    body = {k: v for k, v in m.items() if k != "manifest_sha256"}
    if FDC.canonical_sha256(body) != m.get("manifest_sha256"):
        raise ManifestError("manifest hash does not rebuild")
    inp = m["inputs"]
    rebuilt = build(unit=m["unit"], board=archive.get_json(inp["board_object_sha256"]),
                    schedule_source=inp["schedule_source"], capture=inp["capture"], archive=archive,
                    feature_snapshots=inp["feature_snapshots"], mode=m["mode"])
    if rebuilt["manifest_sha256"] != m["manifest_sha256"]:
        raise ManifestError("manifest does not re-derive from its inputs and the raw archive")
    return m["manifest_sha256"]
