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
