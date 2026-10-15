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

Nothing here publishes. This module does not make the S1 ledger immutable; it
only defines and checks the claim.
"""
import re

import ledger as L
import manifest as M

PREFIX = "FC-ACC-001-S1-ANCHOR v1"
_RE = re.compile(rf"^{re.escape(PREFIX)} kind=(PROSPECTIVE|DRY_RUN) seq=(\d+) entry=([0-9a-f]{{64}}) "
                 r"unit=(\S+) manifest=([0-9a-f]{64})$")


class AnchorError(ValueError):
    pass


def statement(entry):
    return (f"{PREFIX} kind={entry['ledger_kind']} seq={entry['seq']} entry={entry['entry_sha256']} "
            f"unit={entry['unit_key']} manifest={entry['manifest_sha256']}")


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
            "unit_key": m.group(4), "manifest_sha256": m.group(5)}


def verify_anchors(ledger_dir, anchors, archive=None):
    """{unit_key: ANCHORED_PREGAME | ANCHORED_LATE | UNANCHORED}; raises on conflict."""
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
        ok = [t for t in covered[e["seq"]] if M._ts(e["sealed_at"]) <= t < M._ts(e["first_pitch_utc_min"])]
        out[e["unit_key"]] = ("ANCHORED_PREGAME" if ok else "ANCHORED_LATE" if covered[e["seq"]] else "UNANCHORED")
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
