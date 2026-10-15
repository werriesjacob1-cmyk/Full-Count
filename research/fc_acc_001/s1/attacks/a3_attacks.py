#!/usr/bin/env python3
"""Amendment A3 attacks (Codex final timing audit, PR #226 comment
6195082449). Runs unchanged against the A2 package (0a4d23d0) and the A3
package:

    git worktree add <dir> 0a4d23d0 && python3 a3_attacks.py <dir>/research/fc_acc_001/s1
    python3 a3_attacks.py

SYNTHETIC fixtures only; no network. Each attack prints VULNERABLE or REJECTED."""
import inspect
import json
import os
import sys
import tempfile

S1 = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, S1)
import fanduel as FDC  # noqa: E402
import fixtures as X  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import schedule as SCH  # noqa: E402
import sources as S  # noqa: E402

A3 = "cutoff_commitment" in inspect.signature(M.build).parameters
D = X.DATE
DELAYED = {"900001": f"{D}T22:20:00Z", "900002": X.FIRST_PITCH["900002"]}     # earliest 22:00 -> 22:20


def _sched(a, hour, minute, **over):
    return S.fetch(a, lambda r: ("statsapi", X.schedule_bytes(**over)), X.make_clock(hour, minute),
                   source=SCH.SOURCE, request=SCH.request_for(D))


def _original_commitment(a):
    import cutoff as C
    return C.make(a, _sched(a, 18, 0), {"date": D, "window": "NIGHT"})


def _build(a, *, decision, board, sched, cap, commitment="ORIGINAL"):
    kw = {}
    if A3:
        kw["cutoff_commitment"] = _original_commitment(a) if commitment == "ORIGINAL" else commitment
    return M.build(unit=dict(X.UNIT, decision_at=decision), board=board, schedule_source=sched, capture=cap,
                   archive=a, feature_snapshots={}, mode="PROSPECTIVE", **kw)


def _delayed_inputs(a):
    """Codex's probe: schedule observed 21:35 shows 900001 delayed to 22:20; board 21:36/21:37;
    capture 21:40; decision 21:50 -- every input after the ORIGINAL 21:30 cutoff."""
    board = X.board(generated_at=f"{D}T21:36:00Z", sealed_at=f"{D}T21:37:00Z", starts=DELAYED)
    cap = FDC.sweep(a, X.make_fetch(), clock=X.make_clock(21, 40))
    return board, _sched(a, 21, 35, first_pitch=DELAYED), cap


def _verdict(m):
    if m["unit_status"] == "COMPARABLE":
        return f"VULNERABLE: COMPARABLE unit decided at {m['unit']['decision_at']} (cutoff moved later)"
    return f"REJECTED: unit {m['unit_status']} {m['non_comparable_reasons']}"


def u1_cutoff_extended_by_delayed_schedule():
    a = RA.RawArchive(tempfile.mkdtemp())
    board, sched, cap = _delayed_inputs(a)
    try:
        return _verdict(_build(a, decision=f"{D}T21:50:00Z", board=board, sched=sched, cap=cap))
    except M.ManifestError as e:
        return f"REJECTED: {e}"


def u1b_fresh_commitment_from_delayed_schedule():
    a = RA.RawArchive(tempfile.mkdtemp())
    board, sched, cap = _delayed_inputs(a)
    try:
        if A3:
            import cutoff as C
            late = C.make(a, sched, {"date": D, "window": "NIGHT"})
            m = _build(a, decision=f"{D}T21:50:00Z", board=board, sched=sched, cap=cap, commitment=late)
        else:
            m = _build(a, decision=f"{D}T21:50:00Z", board=board, sched=sched, cap=cap)
        return _verdict(m)
    except Exception as e:  # noqa: BLE001 - CutoffError or ManifestError
        return f"REJECTED: {type(e).__name__}: {e}"


def u1c_no_earlier_cutoff_evidence():
    a = RA.RawArchive(tempfile.mkdtemp())
    board, sched, cap = _delayed_inputs(a)
    try:
        return _verdict(_build(a, decision=f"{D}T21:50:00Z", board=board, sched=sched, cap=cap, commitment=None))
    except M.ManifestError as e:
        return f"REJECTED: {e}"


def _zero(m, label):
    if m["unit_status"] == "ZERO_CHAMPION" or any(v["status"] in ("ZERO_CHAMPION", "MATCHED") for v in m["arms"].values()):
        return f"VULNERABLE: {label} reported unit {m['unit_status']} with reasons {m['non_comparable_reasons']}"
    return f"REJECTED: unit {m['unit_status']} {m['non_comparable_reasons']}"


def u2_missing_game_reported_as_zero_champion():
    a = RA.RawArchive(tempfile.mkdtemp())
    recs = [r for r in X.records() if r["game_pk"] == "900002"]                  # no top picks; 900001 absent
    board = X.board(recs, starts={"900002": X.FIRST_PITCH["900002"]})
    m = _build(a, decision=X.DECISION, board=board, sched=X.schedule_source(a), cap=X.capture(a))
    return _zero(m, "board missing game 900001")


def u3_incomplete_capture_reported_as_zero_champion():
    a = RA.RawArchive(tempfile.mkdtemp())
    recs = X.records()
    for r in recs:
        r["selector"]["recommendation_status"] = "lean"
    cap = FDC.sweep(a, X.make_fetch(fail_paths=("eventId=E2&tab=lasers",)), clock=X.make_clock())
    m = _build(a, decision=X.DECISION, board=X.board(recs), sched=X.schedule_source(a), cap=cap)
    return _zero(m, "incomplete capture")


ATTACKS = [(n, f) for n, f in sorted(globals().items()) if n[:2] in ("u1", "u2", "u3") and callable(f)]


def run():
    out = {}
    for n, f in ATTACKS:
        try:
            out[n] = f()
        except Exception as e:  # noqa: BLE001
            out[n] = f"ERROR: {type(e).__name__}: {e}"
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
