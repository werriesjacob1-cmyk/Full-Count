"""External anchoring of the S1 ledger head -- research-only, NOT deployed.

Smallest mechanism: after each seal, publish the one-line ANCHOR STATEMENT for
the new chain head to an append-only, independently timestamped external log.
Proposed log (not provisioned; needs Jacob's authorization): a comment on a
dedicated GitHub issue, whose ``created_at`` is assigned by GitHub's server
and whose edits are visible in the comment's history. A chain head commits to
every earlier entry, so anchoring head k also anchors entries 1..k.

Verification never trusts the local ledger's clock or files: the verifier
retrieves anchors itself (e.g. the issue's comments via the API) and passes
them here as {"statement", "external_created_at", "external_ref"}. An entry is
ANCHORED_PREGAME only if some anchor covering it was created after the entry
was sealed and strictly before that unit's first pitch. Any anchor naming an
entry the local chain does not contain is a CONFLICT (deleted or forked local
ledger) and fails closed.

A4: cutoff commitments are chain entries too, so the head anchor covers them.
A cutoff commitment is ANCHORED_BEFORE_DEADLINE only if an anchor covering its
entry was created at/after it was recorded and no later than both its
commitment deadline and its cutoff. ``unit_evidence`` keeps the two claims
apart: LOCAL_ONLY (valid local chain, nothing anchored) is never
EXTERNALLY_ANCHORED_PROSPECTIVE, which needs BOTH the commitment anchored before
its deadline AND the seal anchored pregame.

Nothing here publishes. This module does not make the S1 ledger immutable; it
only defines and checks the claim.
"""
import re

import ledger as L
import manifest as M

PREFIX = "FC-ACC-001-S1-ANCHOR v1"
_RE = re.compile(rf"^{re.escape(PREFIX)} kind=(PROSPECTIVE|DRY_RUN) seq=(\d+) entry=([0-9a-f]{{64}}) "
                 r"unit=(\S+) (manifest|cutoff_commitment)=([0-9a-f]{64})$")


class AnchorError(ValueError):
    pass


def statement(entry):
    tail = (f"cutoff_commitment={entry['commitment_sha256']}" if L.is_cutoff(entry)
            else f"manifest={entry['manifest_sha256']}")
    return (f"{PREFIX} kind={entry['ledger_kind']} seq={entry['seq']} entry={entry['entry_sha256']} "
            f"unit={entry['unit_key']} {tail}")


def head_statement(ledger_dir):
    entries = L.read_chain(ledger_dir)
    if not entries:
        raise AnchorError("empty ledger: nothing to anchor")
    return statement(entries[-1])


def parse(text):
    m = _RE.match((text or "").strip())
    if not m:
        raise AnchorError(f"not an anchor statement: {text!r}")
    return {"ledger_kind": m.group(1), "seq": int(m.group(2)), "entry_sha256": m.group(3),
            "unit_key": m.group(4), "object": m.group(5), "object_sha256": m.group(6)}


def verify_anchors(ledger_dir, anchors, archive=None):
    """{unit_key: ANCHORED_PREGAME | ANCHORED_LATE | UNANCHORED} for seals and
    {"cutoff:"+unit_key: ANCHORED_BEFORE_DEADLINE | ANCHORED_LATE | UNANCHORED} for chained cutoff
    commitments; raises on conflict (an external anchor naming an entry the local chain lacks)."""
    entries = L.read_chain(ledger_dir, archive)
    by_seq = {e["seq"]: e for e in entries}
    covered = {e["seq"]: [] for e in entries}
    for a in anchors:
        st = parse(a["statement"])
        t = M._ts(a["external_created_at"], "external_created_at")
        e = by_seq.get(st["seq"])
        if e is None or e["entry_sha256"] != st["entry_sha256"] or statement(e) != a["statement"].strip():
            raise AnchorError(f"CONFLICT: external anchor seq {st['seq']} is not in the local chain "
                              f"({a.get('external_ref')})")
        for k in range(1, st["seq"] + 1):
            covered[k].append(t)
    out = {}
    for e in entries:
        ts = covered[e["seq"]]
        if L.is_cutoff(e):
            limit = min(M._ts(e["commitment_deadline_utc"]), M._ts(e["cutoff_utc"]))
            ok = [t for t in ts if M._ts(e["recorded_at"]) <= t <= limit]
            out["cutoff:" + e["unit_key"]] = ("ANCHORED_BEFORE_DEADLINE" if ok else "ANCHORED_LATE" if ts
                                              else "UNANCHORED")
            continue
        ok = [t for t in ts if M._ts(e["sealed_at"]) <= t < M._ts(e["first_pitch_utc_min"])]
        out[e["unit_key"]] = ("ANCHORED_PREGAME" if ok else "ANCHORED_LATE" if ts else "UNANCHORED")
    return out


def unit_evidence(ledger_dir, anchors, archive=None):
    """{unit_key: EXTERNALLY_ANCHORED_PROSPECTIVE | LOCAL_ONLY} for sealed PROSPECTIVE units.
    EXTERNALLY_ANCHORED_PROSPECTIVE needs the chained cutoff commitment anchored before its deadline
    AND the seal anchored pregame; anything less is a locally valid seal, not prospective evidence."""
    st = verify_anchors(ledger_dir, anchors, archive)
    out = {}
    for e in L.read_chain(ledger_dir, archive):
        if L.is_cutoff(e) or e["ledger_kind"] != "PROSPECTIVE":
            continue
        ok = st[e["unit_key"]] == "ANCHORED_PREGAME" and st.get("cutoff:" + e["unit_key"]) == "ANCHORED_BEFORE_DEADLINE"
        out[e["unit_key"]] = "EXTERNALLY_ANCHORED_PROSPECTIVE" if ok else "LOCAL_ONLY"
    return out


COMMIT_PREFIX = "FC-ACC-001-S1-CUTOFF-COMMITMENT v1"


def commitment_statement(commitment):
    u = commitment["unit"]
    return (f"{COMMIT_PREFIX} unit={u['date']}_{u['window']} cutoff={commitment['cutoff_utc']} "
            f"commitment={commitment['commitment_sha256']}")


def verify_commitment_anchor(commitment, anchors):
    """ANCHORED_BEFORE_DEADLINE | ANCHORED_LATE | UNANCHORED for one cutoff commitment.
    Anchors are retrieved independently by the verifier ({"statement","external_created_at"})."""
    want = commitment_statement(commitment)
    times = [M._ts(a["external_created_at"], "external_created_at") for a in anchors
             if a.get("statement", "").strip() == want]
    if not times:
        return "UNANCHORED"
    deadline = min(M._ts(commitment["commitment_deadline_utc"]), M._ts(commitment["cutoff_utc"]))
    return "ANCHORED_BEFORE_DEADLINE" if min(times) <= deadline else "ANCHORED_LATE"
