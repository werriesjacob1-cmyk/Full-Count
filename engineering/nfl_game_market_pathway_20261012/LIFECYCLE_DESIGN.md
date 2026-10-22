# FC-NFL-004 prospective lifecycle: what runs now, and the one blocked step

## Operational now (no write permission)

The workflow `nfl-live-game-market-shadow.yml` keeps `contents: read` and adds read-only `actions: read`. Each scheduled run does the following:

1. `fetch-prior` lists this workflow's `nfl-game-market-research-ledger` artifacts.
   - It keeps those for the same branch that are not expired, newest 3.
   - It downloads each by exact artifact id, and requires the zip's sha256 to equal GitHub's reported digest.
   - Extraction is guarded against path traversal.
   - It writes `sources_manifest.json`: artifact id, run id, head sha, digest, expiry.
2. `replay` re-verifies every frozen record, grade and disposition, and copies it byte-for-byte. Identical files dedupe; a same-path, different-bytes collision fails closed.
3. `freeze` writes new vintages into the same ledger. Each v2 record binds the archived FanDuel root sha256. The slate status is COMPLETE, PARTIAL_COVERAGE, NO_EVENTS or DISCOVERY_FAILURE (the step fails).
4. `grade` handles each ungraded freeze:
   - after kickoff + 3h: ESPN `STATUS_FINAL`, bound to the same identity hash, then `grade_game_market` settlement;
   - the movement reference is the latest later pre-kickoff research vintage, labelled `NOT_A_VERIFIED_CLOSE`;
   - postponed games, games still not final after 7 days, and missing authoritative events get a create-only terminal **disposition**, never a silent pending state.
5. The merged ledger is re-uploaded as the next rolling artifact, retained 90 days. Raw payloads stay in the per-run artifact.

Verified locally:
- unit tests for every step;
- byte-identical replay of the 30 real committed freezes;
- real `grade` returning PENDING before kickoff.

The 23:40Z and 04:30Z session check-ins exercise the real final.

## Not durable: the remaining blocked step

Artifacts expire. The rolling chain survives only while a run happens at least every 90 days. The NFL off-season (about February to September) exceeds that, so a season's ledger would be lost.

**Smallest safe durable design — requires Jacob's explicit authorization:**

- A separate job, gated on the research job's success, with `permissions: contents: write`.
  - It pushes only `data/nfl_game_market_research/**` to a dedicated non-default branch `research/nfl-game-market-ledger`, never to `main`.
  - Branch protection must forbid force-push and deletion.
- **Append-only check before push.** For every path already on the branch, its bytes must be unchanged; only new files may be added. Each new file must pass `merge_ledgers` verification. Any modification or deletion aborts.
- **No public exposure.** Nothing is deployed and the branch is excluded from Pages. Research only.

Until it is authorized, the session check-ins and manual commits on the PR branch are the durable record. Those are the 2026-10-12 17:01Z and 17:34Z vintages, plus grades once the games are final.

Alligator

---

## Evidence chain rules (Codex reviews 6129802534 and 6133401456)

**Run history is the durable marker.** In the epoch's chain, a sealed link exists only if that run's step `Upload sealed evidence-chain link [<epoch_id>]` succeeded. The two PR-branch drill runs predate the tagged name and are honoured as epoch-1 only.

Run and step history survives artifact deletion and expiry, so `load_chain` reads it **first**:

| Situation | Result |
|---|---|
| Bootstrap requested while the epoch has any sealed history on the branch (even with every artifact gone) | `BOOTSTRAP_REFUSED`. An epoch is never restarted. |
| History exists but no artifact remains | `MISSING_EXPECTED_ARTIFACT` |
| A historical sealed upload, including one from a *failed* run, is no longer listed | `MISSING_EXPECTED_ARTIFACT`. No rollback by deletion. |
| Head `sequence` differs from the number of sealed uploads in history | `SEQUENCE_GAP` |
| History cannot be read | `HISTORY_UNVERIFIABLE`. Unknown history never permits a restart. |

**Seal and upload gating.** `chain-seal` and the upload run only if `chain-load`, `freeze` **and** `grade` all succeeded. A failed or partial step is never certified. In addition, `seal_chain` refuses any truncated or unverifiable file (`TAMPERED`).

## New-epoch recovery procedure (the only way past a broken chain)

Use this when an epoch is unrecoverable: an expired head, missing evidence, a sequence gap, or unreadable history.

1. **Record the loss** in `ENGINEERING_HANDOFF.md` and on Issue #91: which epoch, the last verified sequence and manifest, and what was lost. Nothing is silently discarded.
2. **Commit a new anchor** with a NEW `epoch_id` (for example `fc-nfl-004-epoch-2`), built with `anchor-build`:
   - its baseline is the committed records plus any evidence the reviewers explicitly choose to carry forward (for example, a last verified link's ledger committed to the repo);
   - its `reason` states the incident.
3. **Change the workflow's chain artifact name and upload step name** to the new epoch. The workflow tests enforce that they match the anchor.
4. Get **Codex review and Jacob's authorization** for that commit.
5. **Dispatch the workflow once** with `bootstrap_epoch=<new epoch_id>`. A repeat bootstrap of the same epoch is refused.

The old epoch's artifacts and history stay as they are. They are never rewritten.

## Evidence boundary: committed baseline vs PR-branch drill

- **Committed baseline (anchored, permanent in git): 30 records.**
  - 15 v1 freezes at 2026-10-12T17:01Z (code `cb731be0`) and 15 v2 freezes at 17:34Z (code `9644db45`).
  - Anchor `25e816a5…`.
- **PR-branch drill records: 30 so far, research-only, artifact-only.**
  - Link 1: 15 freezes at about 21:29Z (run 38310850808).
  - Link 2: 15 freezes at about 21:31Z (run 38311050193).
  - Further links come from the check-ins.
  - These live only in `claude/trusting-tesla-71g8mc` chain artifacts. They are **not** part of the baseline, are **not** committed, and expire about 2027-01-10.
  - They exist to prove the lifecycle, including the post-final grade. They are not promotion evidence.
- **After a future authorized merge**, main starts its own branch-scoped chain: one explicit `workflow_dispatch` on `main` with `bootstrap_epoch=fc-nfl-004-epoch-1`. It is seeded from the committed 30.
  - History checks are branch-scoped, so the PR-branch drill does not block main.
  - Main does not inherit the drill records.
  - If Jacob wants any drill records kept, the explicit route is to commit them into a NEW anchor epoch through the procedure above. They are never migrated silently.

## Retention limits (not solved here)

- Chain links, per-run raw root, schedule and event payloads all expire after 90 days. GitHub run and step history is also finite and deletable.
- These rules make loss **detectable and fail-closed**. They do **not** make evidence permanent.
- The chain keeps derived JSON. Raw source bytes live only in per-run artifacts.
- Permanent retention needs the separately authorized write path described above; it is not implemented.

Alligator
