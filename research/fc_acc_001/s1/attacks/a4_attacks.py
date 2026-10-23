#!/usr/bin/env python3
"""Amendment A4 attacks: cutoff-ledger integrity (Codex A3 certification HOLD,
PR #226 comment 6197211757). Runs unchanged against the A3 package (9df0520c)
and the A4 package:

    git worktree add <dir> 9df0520c && python3 a4_attacks.py <dir>/research/fc_acc_001/s1
    python3 a4_attacks.py

SYNTHETIC fixtures only; no network. Each attack prints VULNERABLE or REJECTED."""
import inspect
import json
import multiprocessing
import os
import shutil
import sys
import tempfile

S1 = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, S1)
import anchor as AN  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402

A4 = "ledger_kind" in inspect.signature(L.record_cutoff).parameters
D = X.DATE
COMMIT_AT, SEAL_AT = f"{D}T18:30:00Z", f"{D}T21:35:00Z"
DELAYED = {"900001": f"{D}T22:20:00Z", "900002": X.FIRST_PITCH["900002"]}     # C 21:30 -> 21:50
KEY = f"{D}_NIGHT"


def _record(led, c, a, anchored):
    kw = {"ledger_kind": "PROSPECTIVE", "anchored_head": anchored} if A4 else {}
    return L.record_cutoff(led, c, a, now=COMMIT_AT, **kw)


def _head(led):
    return L.head(led) if A4 else L.GENESIS          # A3 cutoffs were not chained: the seal extended GENESIS


def _build(a, commitment="ORIGINAL", delayed=False, **kw):
    """delayed=True: a self-consistent manifest for the REPLACEMENT commitment (C 21:50, delayed schedule)."""
    c = X.commitment(a) if commitment == "ORIGINAL" else commitment
    unit = dict(X.UNIT, decision_at=f"{D}T21:50:00Z") if delayed else X.UNIT
    over = {"first_pitch": DELAYED} if delayed else {}
    board = X.board(starts=DELAYED) if delayed else X.board()
    return M.build(unit=unit, board=board, schedule_source=X.schedule_source(a, **over), capture=X.capture(a, **kw),
                   archive=a, feature_snapshots={}, mode="PROSPECTIVE", cutoff_commitment=c)


def _cutoff_file(led):
    return os.path.join(led, "cutoffs", KEY + ".json")


def _rm(p):
    os.chmod(p, 0o644)
    os.remove(p)


def _setup(sealed=False):
    a, led = RA.RawArchive(tempfile.mkdtemp()), tempfile.mkdtemp()
    _record(led, X.commitment(a), a, L.GENESIS)
    if sealed:
        L.seal(led, _build(a), a, ledger_kind="PROSPECTIVE", anchored_head=_head(led), now=SEAL_AT)
    return a, led


def c1_delete_cutoff_then_record_a_different_one():
    a, led = _setup()
    anchored = _head(led)
    _rm(_cutoff_file(led))
    try:
        st = _record(led, X.commitment(a, first_pitch=DELAYED), a, anchored)
        return f"VULNERABLE: a different cutoff commitment was {st} after deleting the first"
    except (L.LedgerError, ValueError) as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)


def c2_delete_cutoff_after_seal_ledger_still_verifies():
    a, led = _setup(sealed=True)
    _rm(_cutoff_file(led))
    try:
        units = L.verify_ledger(led, a)
        return f"VULNERABLE: verify_ledger passed {units} with the cutoff commitment deleted"
    except L.LedgerError as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)


def c3_recovery_does_not_restore_the_first_commitment():
    a, led = _setup(sealed=True)
    original = open(_cutoff_file(led), "rb").read()
    _rm(_cutoff_file(led))
    try:
        restored = L.restore_index(led)
        if not os.path.exists(_cutoff_file(led)) or open(_cutoff_file(led), "rb").read() != original:
            return f"VULNERABLE: restore_index returned {restored}; the first commitment is gone"
        L.verify_ledger(led, a)
        return f"REJECTED: first commitment restored byte-identically from the chain ({restored})"
    except L.LedgerError as e:
        return f"VULNERABLE: recovery failed: {e}"
    finally:
        shutil.rmtree(led)


def c4_replace_cutoff_then_seal_a_manifest_with_the_replacement():
    a, led = RA.RawArchive(tempfile.mkdtemp()), tempfile.mkdtemp()
    _record(led, X.commitment(a), a, L.GENESIS)
    other = X.commitment(a, first_pitch=DELAYED)
    rec = json.load(open(_cutoff_file(led)))
    if "commitment" in rec:                                       # A3 layout: the file IS the commitment
        rec["commitment"] = other
    else:                                                         # A4 layout: the file mirrors a chain entry
        rec["commitment_sha256"], rec["cutoff_utc"] = other["commitment_sha256"], other["cutoff_utc"]
    _rm(_cutoff_file(led))
    with open(_cutoff_file(led), "wb") as fh:
        fh.write(RA.canonical_bytes(rec))
    try:
        anchored = _head(led)
        m = _build(a, commitment=other, delayed=True)
        _, st = L.seal(led, m, a, ledger_kind="PROSPECTIVE", anchored_head=anchored, now=f"{D}T21:55:00Z")
        return f"VULNERABLE: {st} a {m['unit_status']} manifest against a replaced cutoff commitment"
    except (L.LedgerError, ValueError) as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)


def c5_idempotent_reseal_skips_cutoff_validation():
    a, led = _setup(sealed=True)
    m = _build(a)
    other = X.commitment(a, first_pitch=DELAYED)
    rec = json.load(open(_cutoff_file(led)))
    if "commitment" in rec:
        rec["commitment"] = other
    else:
        rec["commitment_sha256"] = other["commitment_sha256"]
    _rm(_cutoff_file(led))
    with open(_cutoff_file(led), "wb") as fh:
        fh.write(RA.canonical_bytes(rec))
    try:
        _, st = L.seal(led, m, a, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=SEAL_AT)
        return f"VULNERABLE: {st} although the logged cutoff no longer matches the sealed manifest"
    except (L.LedgerError, ValueError) as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)


def c6_prospective_seal_with_no_cutoff_commitment_at_all():
    a, led = RA.RawArchive(tempfile.mkdtemp()), tempfile.mkdtemp()
    m = _build(a, commitment=None)
    try:
        _, st = L.seal(led, m, a, ledger_kind="PROSPECTIVE", anchored_head=L.GENESIS, now=SEAL_AT)
        return f"VULNERABLE: {st} a PROSPECTIVE {m['unit_status']} manifest with no cutoff commitment"
    except (L.LedgerError, ValueError) as e:
        return f"REJECTED: {e}"
    finally:
        shutil.rmtree(led)


def c7_wholesale_deletion_then_a_different_first_commitment():
    """The attacker deletes the whole ledger and records a different first commitment, then the
    verifier checks the externally published anchor of the ORIGINAL commitment."""
    a, led = RA.RawArchive(tempfile.mkdtemp()), tempfile.mkdtemp()
    original = X.commitment(a)
    _record(led, original, a, L.GENESIS)
    published = [{"statement": AN.head_statement(led) if A4 else AN.commitment_statement(original),
                  "external_created_at": f"{D}T18:31:00Z", "external_ref": "synthetic"}]
    shutil.rmtree(led)
    os.makedirs(led)
    try:
        st = _record(led, X.commitment(a, first_pitch=DELAYED), a, L.GENESIS)   # lies about the anchored head
        logged = L.logged_cutoff(led, KEY)
        status = AN.verify_anchors(led, published, a) if A4 else AN.verify_commitment_anchor(logged["commitment"], published)
        return f"VULNERABLE: different first commitment {st}; external check reports {status} (no CONFLICT)"
    except (L.LedgerError, AN.AnchorError, ValueError) as e:
        return f"REJECTED: {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(led)


def _race_record(led, root, delayed, q):
    a = RA.RawArchive(root)
    c = X.commitment(a, first_pitch=DELAYED) if delayed else X.commitment(a)
    try:
        q.put(_record(led, c, a, L.GENESIS))
    except L.LedgerError:
        q.put("REFUSED")


def c8_concurrent_conflicting_commitments():
    root, led = tempfile.mkdtemp(), tempfile.mkdtemp()
    RA.RawArchive(root)
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    ps = [ctx.Process(target=_race_record, args=(led, root, d, q)) for d in (False, True)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
    got = sorted(q.get(timeout=5) for _ in ps)
    shutil.rmtree(led)
    return f"REJECTED: {got}" if got == ["RECORDED", "REFUSED"] else f"VULNERABLE: {got}"


ATTACKS = [(n, f) for n, f in sorted(globals().items()) if n[:1] == "c" and n[1:2].isdigit() and callable(f)]


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
