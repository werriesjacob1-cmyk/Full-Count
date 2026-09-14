# FULL COUNT NFL MNF Execution Addendum — 2026-09-14

Status: planning-only. This addendum sharpens the safe implementation path for tonight's Denver at Kansas City research decision. It authorizes no merge, deploy, public pick, grading activation, model promotion, purchase, access change, or immutable evidence mutation.

## Confirmed repository finding: current-history defect location

`nfl/research/nflverse_history.py` already supports cross-season histories and emits each target row before appending that row to player history. That is the correct no-lookahead substrate.

The live workflow is the problem seam: `.github/workflows/nfl-live-passing-yards-shadow-board.yml` builds `prior_by_player` manually from `player_rows_by_season[2025]` only. Therefore an eligible, completed 2026 appearance can be omitted from a 2026-09-14 B0 projection even though the B0 contract is defined as the last five strictly prior appearances.

Do not rewrite the scorer. Repair the live-history assembly.

## Required 2026-history repair design

Keep the frozen 2023-2025 corpus and residual benchmark exactly as-is for B0 reproduction. Do not add mutable 2026 rows into the historical calibration residual population.

Separately acquire the current 2026 weekly player-stat source as a live input for present-game prior history only.

Required controls:

1. Fetch the canonical 2026 nflverse weekly player-stat file before the decision seal.
2. Record retrieval timestamp, source URL, byte count, SHA-256, and row count in the evidence artifact.
3. Validate the same required schema and numeric invariants used by the existing history builder.
4. Fail closed on offensive rows without stable player identity; do not infer identity.
5. Include only rows that are known completed strictly before the target event kickoff.
6. Never use target-game outcome data. If source timing cannot distinguish whether a same-week row is safely prior, quarantine rather than infer.
7. Combine safe 2026 completed appearances with 2025 prior appearances only for the current player's rolling B0 input, sort chronologically, then let `current_b0_projection()` take the last five.
8. Preserve team on each appearance so the existing role-continuity gate evaluates the genuinely latest prior team.
9. Keep the pooled residual distribution frozen at the existing 2024+2025 benchmark population of 1,228 residuals. Tonight is not a recalibration event.
10. Emit both source vintages in the final candidate evidence so an auditor can reconstruct exactly which rows were eligible.

### Minimum tests

Add dependency-free contracts proving:

- 2025 history plus one safe 2026 prior appearance causes B0 to use that 2026 row among the last five;
- a hypothetical target-game/current-or-future row is excluded;
- chronology crosses the season boundary correctly;
- current 2026 source is not added to the frozen residual benchmark;
- team continuity uses the latest safe prior appearance;
- missing/ambiguous completion timing fails closed;
- source schema/identity anomalies fail closed;
- existing B0 2024 and 2025 benchmark counts/MAEs remain byte-for-byte/numerically unchanged.

## Monday target repair: exact boundary

Add `workflow_dispatch.inputs.target_local_date` as an optional string. For `workflow_dispatch`, pass that value into `TARGET_LOCAL_DATE`; for scheduled runs leave existing Sunday derivation unchanged.

Validate with strict `YYYY-MM-DD` parsing before the first live sportsbook request. Do not accept permissive datetime coercion.

Prefer a small helper module such as `nfl/prospective/slate_target.py` with pure functions for:

- strict manual-date parsing;
- scheduled next-Sunday resolution in America/Chicago;
- Chicago-local event-date comparison.

The workflow should call the helper rather than duplicating untestable date math inside the YAML heredoc.

### Minimum tests

- manual 2026-09-14 resolves to Monday 2026-09-14;
- no manual input on Monday resolves to Sunday 2026-09-20;
- malformed dates (`09/14/2026`, datetime strings, impossible dates, blanks when manual input is declared required by caller) fail closed as appropriate;
- event at a UTC boundary is assigned by America/Chicago date, not UTC date;
- post-kickoff events never survive the existing timing gate.

## Preliminary run output contract

The first successful 2026-09-14 manual run should return, at minimum, for each normalized primary passing-yards candidate:

- event ID and kickoff;
- player name, GSIS ID, team, opponent;
- market ID;
- captured_at and sealed_at;
- line, OVER odds, UNDER odds;
- B0 projection and history_n_used;
- exact prior appearances actually used (season/week/type/team/yards/attempts);
- line gap;
- model OVER and UNDER probabilities;
- de-vigged market OVER and UNDER probabilities;
- market hold;
- research direction and raw research edge;
- role-continuity status;
- official-availability status;
- decision status (`SHADOW_ONLY` or `QUARANTINED` only);
- every quarantine reason;
- source URLs/hashes/vintages for roster, 2025 stats, 2026 current stats, FanDuel payload, and official inactive report.

No missing field should be silently filled from a web article or manual assumption.

## PICK/PASS audit rule

Do not convert `SHADOW_ONLY` into an official/public pick in code tonight.

SUPERCHAD may issue a research recommendation only after reviewing the final pregame artifact. PASS if any integrity gate is unresolved.

Because no production selector threshold has been earned, do not declare a pick solely because `research_edge > 0`. Require the candidate to survive:

- latest-line recapture;
- ±5-yard or nearest available alternate-line sensitivity;
- no identity/availability/provenance warning;
- no obvious regime mismatch severe enough that the pooled B0 residual distribution is not credible for the player tonight.

Return raw evidence even when the verdict is PASS.

## Do not spend reset tokens on these before the preliminary capture

- more rolling-window passing-yard challengers;
- public-site polish;
- broad 107-market normalization;
- merging PR #89;
- activating grading;
- building a complex spread/total model;
- tuning arbitrary edge thresholds.

After the DEN-KC preliminary capture is secure, spread/total readiness remains the next research lane.

Alligator
