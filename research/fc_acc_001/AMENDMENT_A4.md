# FC-ACC-001-S1 PROTOCOL AMENDMENT A4: cutoff commitments are chained ledger evidence

**Label:** POST-HOC PROTOCOL AMENDMENT (integrity repair).
- It was made before any S1 unit was observed or sealed. S1 is not activated, and no prospective data exists.
- It changes no science: the selection, arms, equal-volume rule, unit cutoff rule (A2/A3), commitment deadlines (A3) and manifest content are untouched. The synthetic dry-run `manifest_sha256` is unchanged: `0e00051072b5903b61c2adc57dbb76a56a68ea3e081f3955022371cf0d09eaff`.
- It edits none of the byte-pinned evidence (`test_fc_acc_001_s1_capture.py::EvidencePreservation`): `S1_PROTOCOL.md`, `PREREG.md`, `AMENDMENT_A1.md`, `AMENDMENT_A2.md` and `AMENDMENT_A3.md`, or the v2, A2 and A3 attack records. The A3 files are newly pinned by A4.

**Trigger.** Codex's A3 certification HOLD of `9df0520c` (https://github.com/werriesjacob1-cmyk/Full-Count/pull/226#issuecomment-6197211757). It found that A3's "first commitment wins" rule held only while a standalone local file survived:
- the cutoff file was not inventoried by `read_chain`/`restore_index`;
- a different commitment could be recorded after deleting it;
- `verify_ledger` passed without it;
- idempotent re-seals returned NOOP before the cutoff check;
- a PROSPECTIVE seal was allowed with no commitment at all.

**Reproduction before repair.** `s1/attacks/a4_attacks.py` was run against a clean `9df0520c` worktree: **7 of 8 VULNERABLE**. The eighth, concurrent conflicting writers, was already serialized by the lock. The record is in `out/s1_a4_attacks_before_after.json`.

| Attack | `9df0520c` (A3) | A4 |
|---|---|---|
| c1 delete the cutoff, record a different one (C 21:30 → 21:50) | VULNERABLE (RECORDED) | REJECTED (MISSING_LINK) |
| c2 delete the cutoff after the seal; `verify_ledger` | VULNERABLE (passes) | REJECTED (MISSING_LINK) |
| c3 recovery after the deletion | VULNERABLE (`restore_index` → `[]`, commitment gone) | REJECTED (restored byte-identically from the chain) |
| c4 replace the cutoff; seal a manifest built on the replacement | VULNERABLE (SEALED) | REJECTED (index ≠ chain entry) |
| c5 tampered cutoff, then re-seal the original manifest | VULNERABLE (IDEMPOTENT_NOOP) | REJECTED |
| c6 PROSPECTIVE seal with no commitment anywhere | VULNERABLE (SEALED, NON_COMPARABLE) | REJECTED |
| c7 delete the whole ledger, record a different first commitment, verify against the published anchor | VULNERABLE (UNANCHORED, no CONFLICT) | REJECTED (CONFLICT) |
| c8 two concurrent writers with different commitments | REJECTED (one wins) | REJECTED (one wins) |

## A4.1 A cutoff commitment is a hash-chain entry

Each `ledger.record_cutoff` call appends a `CUTOFF_COMMITMENT` entry to the existing `chain.jsonl`. It uses the same `seq`/`prev_entry_sha256`/`entry_sha256` chain as seals; no new storage system is added. The entry records:
- `unit_key`, `ledger_kind`;
- `commitment_sha256`;
- `commitment_object_sha256`: the full commitment, stored as a content-addressed raw-archive object;
- `cutoff_utc`, `commitment_deadline_utc`, `recorded_at`;
- `evidence_status: LOCAL_COMMITMENT_UNANCHORED`.

`cutoffs/<unit>.json` is now only an index, a deterministic copy of the entry, exactly like the seal index `units/<unit>.json`. Seal entries keep the A1–A3 schema.

`read_chain` verifies both index sets:
- **missing index:** `MISSING_LINK`;
- **index ≠ entry:** mismatch;
- **index without entry:** `ORPHAN`;
- **duplicate commitment per unit:** refused;
- **commitment chained after its unit's seal:** refused;
- **commitment entry recorded after its deadline:** refused;
- **with the archive given:** the commitment object must re-derive from its archived schedule (`cutoff.verify`).

Every check fails closed. `verify_ledger` and `head` therefore fail whenever a commitment is deleted or replaced.

## A4.2 Recovery can never create a different first commitment

- `restore_index` rewrites a missing `cutoffs/` index byte-identically from its chain entry. It never touches the chain.
- `record_cutoff` and `logged_cutoff` read the commitment from the **verified chain**, never from a file. After recovery the first commitment stands, and a different one is refused ("cutoffs never move").
- A commitment can no longer be recorded once its unit is sealed.
- A truncated or deleted chain is not locally recoverable.
- Like a PROSPECTIVE seal, a **PROSPECTIVE commitment must name the externally anchored chain head it extends.** A wholesale-deleted ledger therefore cannot restart with an honest anchored head. If the caller lies about the head, `anchor.verify_anchors` against the independently retrieved anchors raises `CONFLICT`.

## A4.3 PROSPECTIVE seal contract (fail closed)

Inside the ledger lock, and **before** any idempotent NOOP:
1. **No chained commitment for the unit:** refused ("no cutoff commitment was chained before its deadline").
2. **Manifest carries no commitment:** refused.
3. **Manifest's commitment ≠ the chained one:** refused.
4. **Late commitment:** recording after the deadline is refused (A3). A chained entry whose `recorded_at` is after its deadline fails verification, and rewriting `recorded_at` breaks the chain hash.
5. **Unverifiable commitment** (the commitment object is missing, or does not re-derive from its archived schedule): verification fails.
6. **Only then** an identical re-seal returns `IDEMPOTENT_NOOP`, and a new seal must still extend the anchored head.

DRY_RUN ledgers may hold a unit without a commitment (as before), but a commitment present there must match.

## A4.4 Local validity is not prospective evidence

- `anchor.statement` covers commitment entries as well (`… cutoff_commitment=<sha>`). The anchor of a head covers every earlier entry, commitments included.
- `verify_anchors` reports, for each commitment, `cutoff:<unit>` as one of:
  - `ANCHORED_BEFORE_DEADLINE`: a covering anchor created at or after `recorded_at`, and no later than both the commitment deadline and the cutoff;
  - `ANCHORED_LATE`;
  - `UNANCHORED`.
- `anchor.unit_evidence` returns `EXTERNALLY_ANCHORED_PROSPECTIVE` **only** when the commitment is anchored before its deadline **and** the seal is anchored pregame. Otherwise it returns `LOCAL_ONLY`.
- **No anchor has been published or verified.** Every S1 unit is `LOCAL_ONLY`, `certified_prospective` stays false, and nothing is certified.

## A4.5 Tests

`test_fc_acc_001_s1_capture.py`: **64 tests OK**. The 11 new `ChainedCutoff` tests cover:
- all 8 A4 attacks;
- chain structure;
- deletion, recovery, and refusal of a different commitment afterwards;
- replacement and tampering of the index or the chain entry;
- a missing commitment object;
- missing links and orphans;
- the PROSPECTIVE contract (missing, late, no anchor named, manifest lacking it, different, stale anchor);
- validation before NOOP;
- wholesale deletion → CONFLICT;
- concurrent conflicting commitments, 3 rounds × 4 processes: exactly one is chained;
- concurrent sealers: exactly one seal.

The 29 earlier attacks (19 + 5 + 5) are still rejected.

## Remaining blockers

**To one isolated, nonconfirmatory live diagnostic (no scored or prospective unit):** Jacob's separate authorization. Its purpose is to measure raw FanDuel source shape, latency and size, and statsapi schedule availability by the 09:00 ET (DAY) and 15:00 ET (NIGHT) deadlines.

**To prospective activation:**
1. Codex certification of A4.
2. Provision an independently timestamped **external anchor log**, plus a verifier that fetches it itself. This needs Jacob's authorization; none is published.
3. Durable raw and ledger storage outside git.
4. An early **schedule-only observer** that can commit before 09:00 ET. The production MLB workflow's first run (14:30 UTC) is after the EDT DAY deadline (13:00 UTC), and no production schedule response is archived as an S1 commitment.
5. A measured source-availability drill before any change to the deadlines.
6. The full-candidate export and the freeze-overwrite fix (`EXPORT_DESIGN.md`).
7. A PROSPECTIVE start date and stopping rule, committed and anchored first.
8. Jacob's activation.
