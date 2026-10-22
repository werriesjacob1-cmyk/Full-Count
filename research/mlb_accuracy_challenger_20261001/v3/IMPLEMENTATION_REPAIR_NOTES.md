# V3 implementation repair (Codex audit #91/5939071543)

| Item | Value |
|---|---|
| Repair base | `97484154b4819f150f6be84e69b1955d5905d965` |
| Prereg | `research/mlb_accuracy_challenger_prereg_v3_20261001.md`, **byte-identical** to `15fb1d539c16d368bd8835254d93bb8f0ccd4609` (sha256 `5eb56f2837e25d29c5821043955eefe52c0d5f0513e9b6099ac5facb2ff63442`) |

Every change below enforces existing V3 text. No eligibility, challenger, champion, cutoff, regime, promotion or evidence rule was changed.

Where the prereg needed interpretation to be enforced, the strictest reading consistent with its text is used. Those cases are marked **JUDGMENT** for audit.

## Blockers → repairs
| Codex blocker | Repair | Prereg text enforced |
|---|---|---|
| Quote identity | `manifest_v3.resolve_quote`: the matched market's `event_id` must equal the mapped, non-null event id (`QUOTE_EVENT_MISMATCH`). Non-null market and selection ids are required (`QUOTE_ID_MISSING`). The book must be FanDuel (`QUOTE_BOOK_MISMATCH`). **JUDGMENT:** when FanDuel shows no team slug, identity must be proven by `roster_proof`: the normalized name occurs exactly once across both teams' MLB active rosters (pregame schedule snapshot), and that entry is the board `player_id`. Otherwise `QUOTE_IDENTITY_UNPROVEN`. In the 2026-10-01 capture, every player-prop and pitcher-K runner carried a slug. | §5 "binds only to the exact offer", "Uniqueness", fail closed |
| Capture integrity | `build_manifest` calls `verify_capture` (a tampered capture is refused). The evaluator re-hashes every sealed artifact and **rebuilds the manifest from the sealed board + capture + schedule**, re-resolving every quote from the sealed capture bytes. It must hash identically (`MANIFEST_NOT_REPRODUCIBLE`). | §5 capture states, §6 hashing |
| Shadow input reproducibility | **Overlay:** `shadow.filter_overlay` keeps only game-line snapshots with `taken_at ≤` the copy instant (deterministic: every valid earlier snapshot, file order). The exact consumed bytes are sealed (`overlay.json`, sha256 in the provenance) along with the source commit and source sha256. Replay installs the sealed bytes, never current `data/odds`. A missing overlay keeps the pin's own behaviour (`line_movement()` returns `{}`; verified at the pin). **Network:** `netrecord.py` records every `requests` exchange (statsapi, FanGraphs, Savant, Rotowire, weather, FanDuel; the pin's only network path) and the run's start instant. Replay serves exactly those bytes with the clock started at the recorded instant (time-machine 2.16.0, pinned by hash), with no network access, and any miss fails. **Random:** only User-Agent selection, which affects no prediction. **Threads:** ordering is irrelevant to replay, which is keyed by request. The verifier requires replay-equivalence: identical except the run's own timestamps and the hash over them. | §8 "frozen code ... against live data", "A production board can never stand in for the champion" |
| External seal | `verify_evidence.py` is the only verifier, and it is mandatory. It walks `CHAIN.json` from the anchored genesis; rejects modified, removed, reordered or unchained seals; re-hashes every artifact; re-fetches the #91 receipt from the GitHub API (server `created_at`, issue and body checked); verifies RFC 3161 tokens from their bytes (signature, trust, imprint; genTime read from the token); and checks both are before the earliest covered first pitch. Stored `verified` or `gen_time` fields are ignored. `evaluate_from_evidence` has no chain, receipt or flag parameters. If GitHub is unreachable the result is `CONFIRMATORY_REJECT_EVIDENCE_UNVERIFIED`. | §7 |
| TSA | `seal.tsa_check` verifies stored token bytes under pinned trust (FreeTSA chain in `tsa_certs/`, DigiCert via the system store). One valid pregame token is sufficient and all tokens are recorded, per §7 R2. | §7 R2 |
| Activation | `activation.py`. It requires the env var **and** `v3/ACTIVATION.json`, which binds: repository, PR 220, protocol V3, the prereg commit and sha256 (fixed constants), the implementation commit and v3 tree, the #91 comment id and an activation time. The checkout must be exactly that commit/tree, with no local changes and an unmodified prereg file. The runner fetches the comment itself: it must be on #91, start with `JACOB AUTHORIZATION: ALLOW`, contain "V3 prospective activation", the prereg commit, the prereg sha256 and the implementation commit, and carry no agent marker. Any code change makes the record stale, and it can never authorize V4. **Limitation:** all agents and Jacob post from one GitHub account, so authorship cannot be proven from the API; the binding is to the exact content of a server-timestamped #91 comment in Jacob's format. | §16 |
| One look | `evaluate_v3.evaluate_from_evidence`: refuses before 2027-10-05 and refuses if `FINAL_ANALYSIS_<regime>.json` exists. It writes and publishes that lock **before** any outcome is opened. Outcomes come only from the pinned grader on sealed shadow boards; no production graded file is read. **JUDGMENT** on §13's "once every covered unit is graded or 7 days have passed": the analysis is allowed only if every covered game is terminal per statsapi (status, not outcomes), or the date is at least both 2027-10-12 **and** 7 days after the last covered unit. That intersection is permitted under every reading of the text. Postseason (§11, "once after the Series"): not before 2026-11-16, once. | §13, §11, §15.2 |
| Scheduling | `schedule_plan.py`. Each unit's deadline comes from the schedule snapshot's earliest timed first pitch (TBD games ignored). The required start time is first pitch minus (shadow 20 min + capture 15 min + manifest 2 min + seal 8 min + safety 10 min). `guard()` runs before every runner step and before pushing; on failure the runner writes `MISSED_UNIT.json` (no seal, no confirmatory use, no backfill). `MIN_LEAD_S` = the 10-minute safety margin, enforced. No routine is scheduled. | §4, §16 |
| Tests and mutation | 64 deterministic tests. The real anchor TSA tokens are used for the cryptographic cases. 49 critical-guard mutants were tried and all are killed. | — |

## Drills (DRILL_NONCONFIRMATORY, pre-boundary, never evaluable)
- **Record/replay of the pinned pipeline:** 296 HTTP exchanges, 0 misses, 0 unconsumed. Only run timestamps differ.
- **Repaired runner on the 2026-10-01 NIGHT unit** (`drill2_2026-10-01_NIGHT/`):
  - artifact hashes match;
  - the manifest rebuilt from the sealed artifacts is identical;
  - the shadow board replayed from the sealed tape and overlay is replay-equivalent (82 s, no network).

## Open operational items (not scientific rules)
- **Evidence size.** A shadow tape is about 8 MB (gzip) per unit, roughly 2.5–3 GB per season if stored on the evidence ref in this repository. Where tapes are stored (this ref, a separate evidence repository, or release assets) is an activation-time decision. The protocol requires only that the sealed bytes be retrievable and hash-bound.
- **Runner environment.** It needs `requirements-v3.txt` (time-machine) plus a GitHub token for the receipts.

Alligator.

## Final-delta blocker repair (Codex #91/5941258168, SUPERCHAD #91/5941272601)
- **Repair base:** `fff5989abb5d69f134e353422e339a640d8d9f1c`.
- **Prereg:** still byte-identical. All four repairs enforce existing V3 text.

| Blocker | Repair | V3 text enforced |
|---|---|---|
| 1. Team-slug identity | `roster_proof` is now required on **every** quote. The normalized name must occur exactly once across both MLB active rosters, carry the board `player_id`, and sit on the board team's roster in this game. A matching slug must also agree, and a mismatched slug stays `QUOTE_TEAM_MISMATCH`. Same-team or suffix collisions, a wrong or stale id, a wrong side, or a missing roster give `QUOTE_IDENTITY_UNPROVEN`. | §5 "binds only to the exact offer" |
| 2. TBD first pitch | A game with `start_time_tbd` fails gate 16 ("first pitch is after the cutoff" cannot be established), giving `FIRST_PITCH_TBD_NOT_TIMED`. It is never in `covered_games` and never sets the manifest deadline. `schedule_plan.seal_deadline_consistent` makes the runner refuse to seal (MISS UNIT, no backfill) a unit with no timed covered game, or whose manifest deadline is earlier than the planned one. | §6 gate 16; §4 "finish before the earliest covered first pitch" |
| 3. One-look terminal states | The early path unlocks only if every covered game is final under the **pinned grader's own rule** (`grade_results.is_final` at the pin: codedGameState F/O, or "final"/"completed" in detailedState). Postponed, Suspended and Cancelled are not final there (their picks stay "ungraded"), so they wait for the unchanged conservative grace path. A refused attempt writes no lock. | §13 "every covered unit is graded" |
| 4. Frozen coefficients | `evaluate_from_evidence(evidence_root, regime)` has no coefficient parameter. `verify_evidence.load_frozen_coefficients()` reads the fixed artifact once, requires sha256 `3c9e2c01…2009`, and parses those same bytes. This happens **before** the one-look check, the lock and any outcome access. A unit whose seal `challenger_version` is not that sha256 is invalid before the lock. The runner seals only that version. | §10 frozen coefficients |

**Tests:** 88 (64 previous + 24 new). **Mutation:** 68/68 killed (the 49 previous guards plus 19 new).
