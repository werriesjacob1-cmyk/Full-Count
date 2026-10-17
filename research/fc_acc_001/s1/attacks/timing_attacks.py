#!/usr/bin/env python3
"""Amendment A2 timing/sealing attacks (Codex re-audit, PR #226 comment
6194240381). Runs unchanged against the v2 package (4e6a1f04) and the
corrected package, so the before/after record is reproducible:

    git worktree add <dir> 4e6a1f04 && python3 timing_attacks.py <dir>/research/fc_acc_001/s1
    python3 timing_attacks.py                     # corrected package

SYNTHETIC fixtures only; no network. Each attack prints VULNERABLE or REJECTED."""
import json
import os
import sys
import tempfile
from datetime import timedelta

S1 = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, S1)
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import schedule as SCH  # noqa: E402
import sources as S  # noqa: E402


def _z(t):
    return t.isoformat().replace("+00:00", "Z")


EARLIEST = min(S.ts(v) for v in X.FIRST_PITCH.values())


def _build(a, decision, **sched):
    unit = dict(X.UNIT, decision_at=decision)
    kw = {"cutoff_commitment": X.commitment(a)} if hasattr(X, "commitment") else {}   # A3+; A2 has no commitment
    return M.build(unit=unit, board=X.board(), schedule_source=X.schedule_source(a, **sched),
                   capture=X.capture(a), archive=a, feature_snapshots={}, mode="PROSPECTIVE", **kw)


def t1_edt_window_boundary():
    w = SCH.window_of("2026-10-15T21:30:00Z", "2026-10-15")          # 17:30 EDT
    return "REJECTED: NIGHT" if w == "NIGHT" else f"VULNERABLE: 17:30 EDT classified {w}"


def t2_dst_start_day_boundary():
    w = SCH.window_of("2026-03-08T21:30:00Z", "2026-03-08")          # spring-forward day, 17:30 EDT
    return "REJECTED: NIGHT" if w == "NIGHT" else f"VULNERABLE: 17:30 EDT on DST-start day classified {w}"


def t3_decision_after_unit_earliest_pitch():
    a = RA.RawArchive(tempfile.mkdtemp())
    try:
        m = _build(a, _z(EARLIEST + timedelta(minutes=10)))
    except M.ManifestError as e:
        return f"REJECTED: {e}"
    return (f"VULNERABLE: built a unit decision after its earliest pitch; dropped games "
            f"{m.get('excluded_games')}; unit first pitch now {m['first_pitch_utc_min']}")


def t4_prospective_seal_after_unit_earliest_pitch():
    a = RA.RawArchive(tempfile.mkdtemp())
    try:
        m = _build(a, _z(EARLIEST + timedelta(minutes=10)))
        kw = {"anchored_head": L.GENESIS} if "anchored_head" in L.seal.__code__.co_varnames else {}
        _, st = L.seal(tempfile.mkdtemp(), m, a, ledger_kind="PROSPECTIVE", now=_z(EARLIEST + timedelta(minutes=12)),
                       **kw)
    except (M.ManifestError, L.LedgerError) as e:
        return f"REJECTED: {type(e).__name__}: {e}"
    return f"VULNERABLE: PROSPECTIVE unit {st} after its earliest first pitch {_z(EARLIEST)}"


def t5_unpregame_game_silently_dropped():
    a = RA.RawArchive(tempfile.mkdtemp())
    pk = min(X.FIRST_PITCH, key=lambda k: X.FIRST_PITCH[k])
    m = _build(a, X.DECISION, state={pk: "Live"})
    champ = m["arms"]["CHAMPION_EXISTING_POLICY"]
    if m["unit_status"] == "NON_COMPARABLE" and champ["n_target"] == 3 and champ["status"] != "MATCHED":
        return f"REJECTED: NON_COMPARABLE, N={champ['n_target']}, champion {champ['status']}"
    return f"VULNERABLE: unit {m['unit_status']} with N={champ['n_target']} after dropping game {pk}"


ATTACKS = [(n, f) for n, f in sorted(globals().items()) if n[:2] in ("t1", "t2", "t3", "t4", "t5") and callable(f)]


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
