#!/usr/bin/env python3
"""Codex's five S1 attacks (PR #226 comment 6154204021), plus variants, re-run
against the REPAIRED package. Each returns REJECTED (with the fail-closed code)
or VULNERABLE. SYNTHETIC fixtures only; no network. Imported by
test_fc_acc_001_s1_capture.py, which requires every attack to be REJECTED."""
import copy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fanduel as FDC  # noqa: E402
import features as F  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import workload as W  # noqa: E402


def _arch():
    return RA.RawArchive(tempfile.mkdtemp())


def _build(a, cap=None, recs=None, snaps=None, mode="DRY_RUN_NONCONFIRMATORY", **sched):
    return M.build(unit=X.UNIT, board=X.board(recs), schedule_source=X.schedule_source(a, **sched),
                   capture=cap or X.capture(a), archive=a, feature_snapshots=snaps or {}, mode=mode,
                   cutoff_commitment=X.commitment(a))


def _row(m, cid):
    return next(r for r in m["candidates"] if r["candidate_id"] == cid)


def _rehash(cap):
    cap["capture_sha256"] = FDC.canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"})
    return cap


def a1_forged_event_identity():
    a = _arch(); cap = X.capture(a)
    cap["events"][0]["event_id"] = "FORGED_EVENT"
    try:
        FDC.verify_capture(_rehash(cap), a)
    except FDC.CaptureError as e:
        return f"REJECTED: {e}"
    return "VULNERABLE"


def a1b_tab_request_rebound_to_other_event():
    a = _arch(); cap = X.capture(a)
    e1, e2 = cap["events"][0], cap["events"][1]
    e1["requests"][0] = copy.deepcopy(e2["requests"][0])
    try:
        FDC.verify_capture(_rehash(cap), a)
    except FDC.CaptureError as e:
        return f"REJECTED: {e}"
    return "VULNERABLE"


def a1c_market_from_foreign_event_inside_page():
    a = _arch(); cap = X.capture(a, overrides={"foreign_event": True})
    offers, rej = FDC.parse_offers(cap, a, with_rejections=True)
    if any(o["selection_id"] == "91" for o in offers):
        return "VULNERABLE"
    return "REJECTED: " + ",".join(sorted({r["reason"] for r in rej if r["market_id"] == "m9"}))


def a2a_wrong_line():
    a = _arch(); recs = X.records(); recs[1]["line"] = 1.5
    st = _row(_build(a, recs=recs), "c2")["offer_status"]
    return "VULNERABLE" if st == "AUTHENTICATED" else f"REJECTED: {st}"


def a2b_same_name_two_player_ids():
    a = _arch(); recs = X.records()
    twin = copy.deepcopy(recs[1]); twin.update(candidate_id="c2twin", player_id="8")
    recs.append(twin)
    m = _build(a, recs=recs)
    st = {_row(m, "c2")["offer_status"], _row(m, "c2twin")["offer_status"]}
    return "VULNERABLE" if "AUTHENTICATED" in st else f"REJECTED: {sorted(st)}"


def a2c_wrong_player_id_not_in_raw_lineup():
    a = _arch(); recs = X.records(); recs[1]["player_id"] = "999"
    st = _row(_build(a, recs=recs), "c2")["offer_status"]
    return "VULNERABLE" if st == "AUTHENTICATED" else f"REJECTED: {st}"


def a2d_wrong_team():
    a = _arch(); recs = X.records(); recs[1]["team"] = "Beta Bears"
    st = _row(_build(a, recs=recs), "c2")["offer_status"]
    return "VULNERABLE" if st == "AUTHENTICATED" else f"REJECTED: {st}"


def a3_mixed_two_sided_runners():
    a = _arch(); cap = X.capture(a, overrides={"mixed_two_sided": True})
    offers, rej = FDC.parse_offers(cap, a, with_rejections=True)
    if any(o["market"] == "strikeouts" for o in offers):
        return "VULNERABLE"
    st = _row(_build(a, cap=cap), "c5")["offer_status"]
    return f"REJECTED: parser {sorted({r['reason'] for r in rej if r['market_id'] == 'k1'})}; c5 {st}"


def a3b_two_sided_split_lines():
    a = _arch(); cap = X.capture(a, overrides={"split_line": True})
    offers = FDC.parse_offers(cap, a)
    return "VULNERABLE" if any(o["market"] == "strikeouts" for o in offers) else "REJECTED: INCOHERENT_TWO_SIDED_MARKET"


def a3c_suspended_market():
    a = _arch(); cap = X.capture(a, overrides={"suspend_k": True})
    offers, rej = FDC.parse_offers(cap, a, with_rejections=True)
    if any(o["market"] == "strikeouts" for o in offers):
        return "VULNERABLE"
    return "REJECTED: " + ",".join(sorted({r["reason"] for r in rej if r["market_id"] == "k1"}))


def a4a_post_start_feature_snapshot():
    a = _arch()
    s = {"c2": F.snapshot("c2", "hits", {"projected_pa": 4.1, "lineup_assumed": False, "signals": {}}, None,
                          captured_at=f"{X.DATE}T23:59:00Z", model_versions=X.VERSIONS)}
    m = _build(a, snaps=s)
    if "c2" in m["accounting"]["feature_snapshots_admitted"]:
        return "VULNERABLE"
    return f"REJECTED: {m['accounting']['feature_snapshots_rejected']['c2']}"


def a4a2_workload_observed_after_decision():
    a = _arch()
    try:
        W.pregame_workload(a, X.gamelog_source(a, hour=22), "9", X.DATE, decision_at=X.DECISION)
    except W.WorkloadError as e:
        return f"REJECTED: {e}"
    return "VULNERABLE"


def a4a3_edited_workload_inside_snapshot():
    a = _arch()
    wl = W.pregame_workload(a, X.gamelog_source(a), "9", X.DATE, decision_at=X.DECISION)
    wl["days_rest"] = 9
    s = {"c5": F.snapshot("c5", "strikeouts", {"signals": {}}, wl, captured_at=X.DECISION, model_versions=X.VERSIONS)}
    m = _build(a, snaps=s)
    if "c5" in m["accounting"]["feature_snapshots_admitted"]:
        return "VULNERABLE"
    return f"REJECTED: {m['accounting']['feature_snapshots_rejected']['c5']}"


def a4a4_schedule_observed_after_decision():
    a = _arch()
    try:
        M.build(
            unit=X.UNIT, board=X.board(), capture=X.capture(a), archive=a, feature_snapshots={},
            schedule_source=X.S.fetch(a, lambda r: ("statsapi", X.schedule_bytes()), X.make_clock(22, 0),
                                      source=X.SCH.SOURCE, request=X.SCH.request_for(X.DATE)),
            mode="DRY_RUN_NONCONFIRMATORY", cutoff_commitment=X.commitment(a))
    except M.ManifestError as e:
        return f"REJECTED: {e}"
    return "VULNERABLE"


def a4b_N_fixed_before_offer_filtering():
    a = _arch(); recs = X.records(); recs[8]["selector"]["recommendation_status"] = "top_pick"
    m = _build(a, recs=recs)
    arms = {k: (v["n_target"], v["n_selected"], v["status"]) for k, v in m["arms"].items()}
    if any(s == "MATCHED" for _, _, s in arms.values()) or m["arms"]["CHAMPION_EXISTING_POLICY"]["n_target"] != 4:
        return f"VULNERABLE: {arms}"
    return f"REJECTED: unit {m['unit_status']} {m['non_comparable_reasons']}; arms {arms}"


def a4c_partial_sweep():
    a = _arch()
    cap = FDC.sweep(a, X.make_fetch(fail_paths=("eventId=E2&tab=lasers",)), clock=X.make_clock())
    m = _build(a, cap=cap)
    if any(v["status"] == "MATCHED" for v in m["arms"].values()):
        return "VULNERABLE"
    return f"REJECTED: unit {m['unit_status']} {m['non_comparable_reasons']}"


def a4d_policy_price_changed():
    a = _arch(); recs = X.records(); recs[2]["market"]["market_odds"] = -150
    m = _build(a, recs=recs)
    st = _row(m, "c3")["champion_status"]
    return "VULNERABLE" if st == "SELECTED" else f"REJECTED: c3 {st}; unit {m['unit_status']}"


def a5_reseal_after_deleting_index():
    a = _arch(); led = tempfile.mkdtemp()
    p, _ = L.seal(led, _build(a), a, ledger_kind="DRY_RUN", now=X.DECISION)
    os.chmod(p, 0o644); os.remove(p)
    other = _build(a, cap=X.capture(a, overrides={"slugger_hit": -260}))
    try:
        L.seal(led, other, a, ledger_kind="DRY_RUN", now=X.DECISION)
    except L.LedgerError as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)
    return "VULNERABLE"


def _rec_cutoff(led, c, a, anchored):
    """Works on the A3 (unchained cutoffs) and A4 (chained, anchored-head) ledgers."""
    import inspect
    kw = ({"ledger_kind": "PROSPECTIVE", "anchored_head": anchored}
          if "ledger_kind" in inspect.signature(L.record_cutoff).parameters else {})
    return L.record_cutoff(led, c, a, now=f"{X.DATE}T18:30:00Z", **kw)


def _head_or_genesis(led):
    import inspect
    return L.head(led) if "ledger_kind" in inspect.signature(L.record_cutoff).parameters else L.GENESIS


def a5b_reseal_after_deleting_whole_ledger():
    a = _arch(); led = tempfile.mkdtemp()
    pm = _build(a, mode="PROSPECTIVE")
    _rec_cutoff(led, X.commitment(a), a, L.GENESIS)
    L.seal(led, pm, a, ledger_kind="PROSPECTIVE", anchored_head=_head_or_genesis(led), now=f"{X.DATE}T21:35:00Z")
    anchored = L.head(led)                                   # what the external anchor would hold
    shutil.rmtree(led); os.makedirs(led)
    try:                                                     # attacker re-records the cutoff too
        _rec_cutoff(led, X.commitment(a), a, L.GENESIS)
    except L.LedgerError as e:
        shutil.rmtree(led)
        return f"REJECTED: {e}"
    other = _build(a, cap=X.capture(a, overrides={"slugger_hit": -260}), mode="PROSPECTIVE")
    try:
        L.seal(led, other, a, ledger_kind="PROSPECTIVE", anchored_head=anchored, now=f"{X.DATE}T21:36:00Z")
    except L.LedgerError as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)
    return "VULNERABLE"


ATTACKS = [(n, f) for n, f in sorted(globals().items()) if n.startswith("a") and n[1].isdigit() and callable(f)]


def run():
    out = {}
    for n, f in ATTACKS:
        try:
            out[n] = f()
        except Exception as e:  # noqa: BLE001 - an unexpected crash is not a verified rejection
            out[n] = f"ERROR: {type(e).__name__}: {e}"
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
