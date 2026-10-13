#!/usr/bin/env python3
"""Reproduce Codex's five S1 capture attacks (PR #226 comment 6154204021)
against the PRE-REPAIR package at d316593c. Usage:

    git worktree add /tmp/s1pre d316593c
    python3 pre_repair_attacks.py /tmp/s1pre/research/fc_acc_001/s1

Each attack prints VULNERABLE (the defect reproduces) or REJECTED. SYNTHETIC
fixtures only; no network."""
import copy
import json
import os
import shutil
import sys
import tempfile

S1 = os.path.abspath(sys.argv[1])
sys.path.insert(0, S1)
import fanduel as FDC  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402

V = {"model_version": "t", "selection_policy_version": "1.0.0", "calibration_version": "1.0.0"}
DEC = f"{X.DATE}T21:30:00Z"
UNIT = {"date": X.DATE, "window": "NIGHT", "decision_at": DEC}
out = {}


def arch():
    return RA.RawArchive(tempfile.mkdtemp())


def build(a, cap, cands=None, snaps=None):
    return M.build(unit=UNIT, games=X.GAMES, candidates=cands or X.candidates(), capture=cap, archive=a,
                   feature_snapshots=snaps or {}, mode="DRY_RUN_NONCONFIRMATORY", model_versions=V)


def attack(name, fn):
    try:
        out[name] = "VULNERABLE: " + fn()
    except Exception as e:  # noqa: BLE001
        out[name] = f"REJECTED: {type(e).__name__}: {e}"


# 1. Forged event identity: edit an event id in the capture record and recompute its self-hash.
def a1():
    a = arch(); cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    cap["events"][0]["event_id"] = "FORGED_EVENT"
    cap["capture_sha256"] = FDC.canonical_sha256({k: v for k, v in cap.items() if k != "capture_sha256"})
    FDC.verify_capture(cap, a)
    forged = [o for o in FDC.parse_offers(cap, a) if o["sportsbook_event_id"] == "FORGED_EVENT"]
    assert forged and "eventId=E1" in forged[0]["request_path"]
    return f"verify_capture passed; {len(forged)} offers from request eventId=E1 emitted as FORGED_EVENT"


# 2a. Wrong line: candidate line 1.5 authenticated against a preserved 0.5 offer.
def a2a():
    a = arch(); cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    c = X.candidates(); c[1]["line"] = 1.5
    row = next(r for r in build(a, cap, c)["candidates"] if r["candidate_id"] == "c2")
    assert row["offer_status"] == "AUTHENTICATED" and row["offer"]["line"] == 0.5
    return "candidate line 1.5 AUTHENTICATED against offer line 0.5"


# 2b. Same name, two MLB player ids: both bind to the identical FanDuel offer.
def a2b():
    a = arch(); cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    c = X.candidates(); twin = copy.deepcopy(c[1]); twin.update(candidate_id="c2twin", player_id="222")
    c.append(twin)
    m = build(a, cap, c)
    r1, r2 = (next(r for r in m["candidates"] if r["candidate_id"] == k) for k in ("c2", "c2twin"))
    assert r1["offer"] and r2["offer"] and r1["offer"]["offer_id"] == r2["offer"]["offer_id"]
    return "two distinct player ids with one name bound to the same offer_id"


# 3. Mixed two-sided runners: "Ace Arm Over" + "Other Arm Under" -> "Other Arm OVER" with Ace's selection.
def a3():
    a = arch()
    base = X.make_fetch()
    def fetch(path):
        if "eventId=E1&tab=pitcher-props" in path:
            mk = {"k1": {"marketId": "k1", "marketType": "PITCHER_A_TOTAL_STRIKEOUTS", "inPlay": False, "runners": [
                {"selectionId": 31, "runnerName": "Ace Arm Over", "handicap": 5.5,
                 "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": -125}}},
                {"selectionId": 32, "runnerName": "Other Arm Under", "handicap": 5.5,
                 "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": 100}}}]}}
            return "synthetic", json.dumps({"attachments": {"markets": mk}}).encode()
        return base(path)
    cap = FDC.sweep(a, fetch, clock=X.make_clock())
    bad = [o for o in FDC.parse_offers(cap, a) if o["market"] == "strikeouts" and o["side"] == "over"]
    assert bad and bad[0]["player_name"] == "Other Arm" and bad[0]["selection_id"] == 31
    return "offer 'Other Arm OVER' carries Ace Arm's selection 31 / -125"


# 4a. Feature snapshot captured after first pitch enters a verified manifest.
def a4a():
    import features as F
    a = arch(); cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    s = {"c2": F.snapshot("c2", "hits", {"projected_pa": 4.1, "lineup_assumed": False, "signals": {}}, None,
                          captured_at=f"{X.DATE}T23:59:00Z", model_versions=V)}
    m = build(a, cap, snaps=s)
    M.verify_manifest(m, a)
    assert m["inputs"]["feature_snapshots"]["c2"]["captured_at"] > m["first_pitch_utc_min"]
    return "post-first-pitch feature snapshot accepted in a verified manifest"


# 4b. N defined after offer filtering: 4 policy top picks, one missing quote -> N=3 MATCHED.
def a4b():
    a = arch(); cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    c = X.candidates(); c[8]["selector"]["recommendation_status"] = "top_pick"   # c9: no quote
    m = build(a, cap, c)
    st = {k: (v["n_target"], v["status"]) for k, v in m["arms"].items()}
    assert all(v == (3, "MATCHED") for v in st.values())
    return f"4 policy top picks, 1 unquoted -> {st}"


# 4c. Partial event sweep still yields MATCHED for the remaining event.
def a4c():
    a = arch()
    cap = FDC.sweep(a, X.make_fetch(fail_paths=("eventId=E2&tab=lasers",)), clock=X.make_clock())
    m = build(a, cap)
    assert cap["status"] == "INCOMPLETE" and m["arms"]["CHAMPION_EXISTING_POLICY"]["status"] == "MATCHED"
    return "capture INCOMPLETE but unit arms MATCHED"


# 5. Delete a seal locally -> contradictory same-unit manifest seals as SEALED.
def a5():
    a = arch(); led = tempfile.mkdtemp()
    cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock())
    p, s1 = L.seal(led, build(a, cap), a, ledger_kind="DRY_RUN", now=DEC)
    os.remove(p)
    other = build(a, FDC.sweep(a, X.make_fetch(overrides={"slugger_hit": -260}), clock=X.make_clock()))
    _, s2 = L.seal(led, other, a, ledger_kind="DRY_RUN", now=DEC)
    assert (s1, s2) == ("SEALED", "SEALED")
    shutil.rmtree(led)
    return "after deleting the seal file a contradictory manifest sealed as SEALED"


for n, f in (("1_forged_event_identity", a1), ("2a_wrong_line", a2a), ("2b_same_name_two_ids", a2b),
             ("3_mixed_two_sided_runners", a3), ("4a_post_start_feature", a4a),
             ("4b_N_after_offer_filter", a4b), ("4c_partial_sweep_matched", a4c),
             ("5_reseal_after_local_delete", a5)):
    attack(n, f)
print(json.dumps(out, indent=1))
