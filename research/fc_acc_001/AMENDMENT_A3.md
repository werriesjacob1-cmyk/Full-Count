# FC-ACC-001-S1 PROTOCOL AMENDMENT A3: pinned unit cutoffs and complete-coverage zero slates

**Label:** POST-HOC PROTOCOL AMENDMENT, made before any S1 unit was observed or
sealed. S1 is not activated, and no prospective data exists. It edits none of
the following, all byte-pinned by
`test_fc_acc_001_s1_capture.py::EvidencePreservation`:
- `S1_PROTOCOL.md`;
- `PREREG.md`;
- `AMENDMENT_A1.md`;
- `AMENDMENT_A2.md`;
- the v2 and A2 attack records.

It refines A2.2: the unit cutoff rule (earliest scheduled first pitch minus 30
minutes) is unchanged, but **which** schedule observation fixes it is now pinned.

**Trigger.** Codex final A2 audit of `0a4d23d0`
(https://github.com/werriesjacob1-cmyk/Full-Count/pull/226#issuecomment-6195082449)
raised two P1s. Both were reproduced before the repair with
`s1/attacks/a3_attacks.py`, run against a `0a4d23d0` worktree: 5 of 5
VULNERABLE. The record is in `out/s1_a3_attacks_before_after.json`.

## A3.1 The cutoff is committed once and never becomes later

**Defect.** Under A2, the cutoff was recomputed from whichever authenticated
schedule a build received. A schedule observed at 21:35 showing a delay from
22:00 to 22:20 moved the cutoff from 21:30 to 21:50. A board, a capture and a
decision, all made after the original 21:30 cutoff, then produced a COMPARABLE
unit.

**Rule (implemented in `s1/cutoff.py` and `manifest.build`).**

1. **Commitment deadline.** It is fixed in advance and does not depend on the
   schedule: **09:00 America/New_York for DAY** and **15:00 America/New_York
   for NIGHT**, on the unit's official date.
2. **Cutoff commitment.** It is made from an archived raw statsapi schedule
   observed at or before the commitment deadline, and no later than the cutoff
   it commits to. It records:
   - the unit's games and their first pitches;
   - E_u, the earliest first pitch;
   - C_u = E_u − 30 minutes;
   - the deadline;
   - the archived schedule record.

   It is content-addressed (`commitment_sha256`) and re-derivable from the
   archive. A commitment from a schedule observed after the deadline is
   refused.
3. **Recording.** The commitment is recorded write-once per unit
   (`ledger.record_cutoff`) by the deadline. The first commitment wins; a
   different later one is refused ("cutoffs never move"). A PROSPECTIVE seal
   must carry exactly the recorded commitment. A commitment never recorded
   before its deadline cannot be sealed PROSPECTIVE.
4. **`decision_at` must equal the committed C_u.** A newer authenticated
   schedule can never make the cutoff later. It can only:
   - **shorten the lead.** If a game now starts earlier than E_u, so that
     E_now − 30 minutes < C_u, the unit gets `CUTOFF_LEAD_SHORTENED` and is
     `NON_COMPARABLE`. The cutoff is not reset;
   - **invalidate.** If a unit game now starts at or before C_u, the manifest
     is refused;
   - **change membership.** If a committed game is missing from, or a new game
     appears in, the decision schedule, the unit gets
     `UNIT_GAMES_CHANGED_SINCE_COMMITMENT` and is `NON_COMPARABLE`. A committed
     game stays in N as `MISSING_FROM_DECISION_SCHEDULE`.
5. **No earlier evidence.** When no commitment exists, the manifest is still
   built, for accounting only, under the A2 rule. It is labelled
   `MISSING_CUTOFF_EVIDENCE`, is `NON_COMPARABLE`, and carries the
   `MISSING_CUTOFF_EVIDENCE` certification blocker. It is never certified.
6. **Independent verifiability.**
   - `anchor.commitment_statement(c)` gives the line to publish to the external
     append-only log before the deadline.
   - `anchor.verify_commitment_anchor` returns `ANCHORED_BEFORE_DEADLINE`,
     `ANCHORED_LATE` or `UNANCHORED`, using anchors the verifier retrieves
     itself.
   - **Nothing is published.** Every manifest carries
     `CUTOFF_COMMITMENT_NOT_EXTERNALLY_ANCHORED`. The local commitment log is
     not prospective proof.

## A3.2 ZERO_CHAMPION requires verified complete coverage

**Defect.** Under A2, N = 0 took precedence over coverage failures. A unit
whose board lacked a scheduled game, or whose capture was incomplete, was
labelled `ZERO_CHAMPION`.

**Rule.** These failures force `NON_COMPARABLE` **regardless of N**:
- any coverage failure: a capture that is not COMPLETE;
- any game issue: board missing the game, board/schedule mismatch, not pregame
  in raw, missing, ambiguous or incomplete event, or missing from the decision
  schedule;
- any cutoff issue: missing evidence, lead shortened, or games changed.

`ZERO_CHAMPION` is assigned only when coverage is verified complete **and** the
board's existing-policy `top_pick` count across all unit games is genuinely
zero.

Arm status comes only from `manifest.arm_status(unit_status, …)`, so an arm can
never report better than its unit. `manifest.unit_tally` re-verifies each
manifest and refuses any arm or unit label that contradicts this mapping.

**Unchanged:**
- the arms, the 0.5 blend, eligibility, offer provenance, metrics, stopping
  and regimes;
- the 30-minute lead and the 45-minute quote age;
- the activation status, which remains BLOCKED.

The commitment deadlines are fixed now, before any unit exists; changing them
needs a new labelled amendment made before the affected units.

Alligator
