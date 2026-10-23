# FC-ACC-001-S1 PROTOCOL AMENDMENT A2: unit timing and the decision/seal rule

**Label:** POST-HOC PROTOCOL AMENDMENT, made before any S1 unit was observed or
sealed. S1 is not activated, and no prospective data exists. This amendment
does not edit `S1_PROTOCOL.md`, `PREREG.md` or `AMENDMENT_A1.md`. Their bytes
are pinned by `test_fc_acc_001_s1_capture.py::EvidencePreservation`:

| File | sha256 |
|---|---|
| `S1_PROTOCOL.md` | `736bc944…d8b42b` |
| `PREREG.md` | `e6466a71…dbbb` |
| `AMENDMENT_A1.md` | `36b6f32a…0180c` |

**Trigger.** Codex re-audit of `4e6a1f04`
(https://github.com/werriesjacob1-cmyk/Full-Count/pull/226#issuecomment-6194240381)
raised two P1s. Both were reproduced before the repair with
`s1/attacks/timing_attacks.py`, run against a `4e6a1f04` worktree: 5 of 5
VULNERABLE. The record is in `out/s1_a2_timing_attacks_before_after.json`.

## A2.1 DAY/NIGHT boundary (implementation corrected to the frozen §3 rule)

`S1_PROTOCOL.md` §3 already says: "DAY is first pitch before 17:00
America/New_York; NIGHT is the rest." The v2 code instead split at a fixed
22:00 UTC. That is correct only under EST. Under EDT it labelled 17:00–17:59
local starts as DAY; Codex's probe, `2026-10-15T21:30Z` (17:30 EDT), returned
DAY.

**Rule, now implemented in `schedule.window_of`:**
- Convert the timezone-aware first pitch with the IANA zone `America/New_York`.
- DAY when the local time is before 17:00:00. NIGHT when it is at or after
  17:00:00.
- The unit date is the MLB official date of the archived schedule entry.
- A start after local midnight that belongs to the previous official date is
  NIGHT.
- A local date before the official date is malformed and rejected.
- Naive or local wall-clock timestamps are rejected. This includes
  DST-ambiguous times such as `2026-11-01T01:30` on the fall-back date.
  An aware instant is never ambiguous.

## A2.2 One common decision cutoff per unit (resolves the decision/seal contradiction)

DAY and NIGHT remain the preregistered experimental units. For each unit:

1. **Unit games:** every game in the archived pregame statsapi schedule (the
   raw bytes, observed by the cutoff) whose official date and New York window
   match the unit.
2. **Earliest first pitch E_u:** the minimum scheduled first pitch over those
   games, taken from that same archived schedule evidence.
3. **Decision cutoff C_u = E_u − 30 minutes.** It is determined by rule and
   never chosen by an operator. A manifest whose `decision_at` differs from C_u
   is refused, which includes any decision at or after E_u.
4. **Availability by C_u.** Every included candidate (the sealed production
   board: `board_generated_at` and `sealed_at` ≤ C_u), every sportsbook offer
   (capture completed ≤ C_u; quote age ≤ 45 minutes), every schedule, lineup and
   starter observation, and every feature or workload observation must be
   available by C_u. Anything later is rejected.
5. **Freezing.** All challenger selections, and the champion N, are frozen at
   C_u.
6. **Volume.** The champion N counts the board's `top_pick` decisions across
   **all** unit games, before any missing-offer filtering.
   - No game is ever dropped from a unit to make a late decision look pregame.
   - A unit game that is not pregame in the raw schedule (`GAME_NOT_PREGAME_IN_RAW`), is missing from the board, or has a missing, ambiguous or incomplete sportsbook event stays in the unit and in N. It makes the unit `NON_COMPARABLE`.
   - Missing quotes produce a champion `SHORTFALL`.
   - `MATCHED` exists only in a `COMPARABLE` unit.
7. **Seal.** There is one seal per unit, with `C_u ≤ sealed_at < E_u` (PROSPECTIVE).
   - The ledger refuses a second, different manifest for an already-sealed unit.
   - Information from a later run or a later per-game observation can never be combined into it.
   - Later per-run snapshots are retained for completeness only.

**Superseded.** The per-game "last pregame run for each game" rule in
`EXPORT_DESIGN.md` (`73e4f601`) and the v2 behaviour that excluded
already-started games from a unit are both superseded. Both are kept in git
history.

**Unchanged:**
- the arms, the fixed 0.5 blend, eligibility labels, offer-provenance
  requirements, outcome metrics, the stopping rule, and the exploratory versus
  confirmatory regimes;
- the activation status, which remains BLOCKED.

**Lead time.** The 30-minute lead is fixed now, before any unit exists. With
`MAX_QUOTE_AGE` at 45 minutes, a usable capture must complete between
C_u − 45 minutes and C_u. Choosing a different lead later would need a new
labelled amendment made before the affected units.

Alligator
