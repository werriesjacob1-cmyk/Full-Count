# V3 tape storage: temporary GitHub Actions artifacts, then R2 (Jacob, 2026-10-06)

**Decision (Jacob, 2026-10-06):**
- GitHub Actions artifacts are the **temporary** authoritative tape store, for the FC-MLB-001B drill and for short-term prospective V3 collection.
- Cloudflare R2 is **not** required for activation now. It remains the planned **durable** store, for when Jacob adds a payment method.
- Actions artifacts are **not** an archival solution. Every artifact-backed tape must be migrated byte-for-byte to durable storage before its retention threatens it. This is milestone **FC-MLB-001C**.
- The R2 design and code stay in place unchanged: `STORAGE_DESIGN_B.md`, `tape_store.R2Store`, `put_verified`, and the mock-S3 mutants in `test_b.py`.

## 1. Temporary storage contract (`fc-v3-gha-artifact-temp-1`, `tape_store.GHA_KIND = "github-actions-artifact"`)
| Requirement | Mechanism |
|---|---|
| Content address = whole-file SHA-256 | The artifact name is `v3-tape-<sha256>`. It holds exactly one file, `fc-v3-tape-<sha256>.json.gz`, and the key is `tape_store.key_for(sha256)`. |
| Exact byte size recorded | `bytes` in the sealed locator (manifest `shadow_provenance.tape_store`) and in the proof. |
| Artifact / run identity recorded | Locator: repository, run_id, run_attempt, artifact_name, file_name. Proof `tape_artifact.json` (a **sealed unit file**): artifact_id, zip digest, zip bytes, created_at, expires_at, configured retention. |
| Download by exact identity | `artifact_api.fetch`: the artifact list of **that run**, filtered by **that sha-bound name**, and by **that artifact id** when sealed. Exactly one match is required, else `TAPE_MISSING`. |
| Bytes hashed before replay | The zip must hash to GitHub's recorded digest. It must contain exactly the one tape file. Then `tape_store._verify` checks the sealed size and sha256. The verifier opens only the file that `artifact_api` downloaded (`V3B_ARTIFACT_DOWNLOAD_DIR`). |
| Size / SHA mismatch | `TAPE_SIZE_MISMATCH` / `TAPE_HASH_MISMATCH`: fail closed. |
| Missing / expired | `TAPE_MISSING` / `TAPE_EXPIRED` (expired flag, or HTTP 410): fail closed. |
| Corruption | A corrupt zip, a flipped byte, truncation, an extra file or a wrong file name all fail closed. |
| No silent fallback | No runner-local cache, no staged copy and no other source is ever read. A missing download is `TAPE_MISSING`. |
| Transport not trusted | Only the sealed sha256 and size are authoritative. The GitHub digest and artifact id are checked, but they are only evidence about the transport. |

**Write path (prospective, inside the bound Actions workflow `reactivation/v3_unit_record.workflow.template.yml`):**
1. `runner.py --phase stage`:
   - records in the pinned sandbox and binds the locator into the manifest;
   - stages exactly one tape;
   - **no seal yet**.
2. `actions/upload-artifact` (pinned by SHA): `retention-days` = the bound value (90), `compression-level: 0`.
3. `artifact_api.py confirm`:
   - downloads the uploaded artifact again by exact identity and re-hashes the tape;
   - writes `tape_artifact.json`, including `expires_at` as GitHub reports it.
4. `runner.py --phase publish`:
   - seals only when the proof names exactly the manifest-bound identity and no staged file changed;
   - otherwise it is a **MISS UNIT**, with no backfill.
5. Job `verify-b` runs on a **fresh** runner:
   - it downloads the exact artifact and runs the full `verify_unit` (sandboxed no-network replay, literal payload bytes);
   - then it runs `retention_monitor.py --live`.

The verifier also requires this for any artifact-backed unit: the sealed `tape_artifact.json` must prove that same identity (`TEMP_STORE_PROOF_INVALID` otherwise).

## 2. Retention risk (explicit; never silent)
- **Configured retention: 90 days.** This is the repository's effective retention, measured on drill artifact `11369873498`: created 2026-10-05T21:03:13Z, expires 2027-01-03T20:49:20Z. The expiry is anchored to the workflow run start.
  - Agents cannot read the repository retention setting (GitHub API 403). Jacob can confirm it under Settings › Actions › General › Artifact and log retention.
  - The exact `retention-days` value is an activation binding.
- **Recorded per unit:** created_at, the configured retention, and GitHub's `expires_at`, all in the sealed proof. `retention_monitor.py` computes days remaining. With `--live` it also re-reads GitHub's current expiry, and the earlier expiry governs.
- **Thresholds (conservative):**

| Status | Days remaining | Exit | Action |
|---|---|---|---|
| OK | > 45 | 0 | none |
| **MIGRATE_WARNING** | ≤ **45** (day 45 of 90) | 10 | `::warning::` annotation; the dispatcher reports `RETENTION_ALERT`, and the trigger session posts it on #91 |
| **MIGRATE_CRITICAL** | ≤ **30** | 20 | `::error::`; #91 alert to Jacob on every tick until migrated |
| EXPIRED / PROOF_MISSING / LIVE_MISMATCH / MIGRATION_INVALID | ≤ 0, or unprovable | 20 | evidence lost or unprovable: reported, never hidden |
| MIGRATED_DURABLE | n/a | 0 | a valid byte-preserving R2 record exists |

**Why 45 days:**
- Migration needs a Cloudflare account, a payment method, a bucket with a lock and scoped tokens (Jacob's actions, `STORAGE_DESIGN_B.md`), and then one Actions run per batch. Warning at half the retention period leaves 45 days for that. CRITICAL follows 15 days later, with 30 days still remaining.
- **The 2026 postseason (DESCRIPTIVE SHADOW)** has its look on or after 2026-11-16. Units recorded from 2026-10-07 expire around 2027-01-05, so they can be read at the look without migration. They still warn from about 2026-11-21 onward.
- **The 2027 regular season (CONFIRMATORY)** is different. Its look is on or after 2027-10-05, while an April 2027 unit expires around July 2027. **Confirmatory evidence cannot survive to its look on artifacts alone**, so FC-MLB-001C must be done before 2027 confirmatory units age past 45 days. The monitor makes that impossible to miss.
- Optional, not done by agents: if the repository is private, Jacob could raise artifact retention up to 400 days in Settings. That would buy time but would not replace migration.

## 3. Milestone FC-MLB-001C: migrate to R2 (or equivalent durable object storage)
> Migrate authoritative V3 tape storage from GitHub Actions artifacts to Cloudflare R2 or equivalent durable object storage before temporary artifact retention threatens any prospective evidence.

The preserved R2 contract (`fc-mlb-001b-r2-cas-1`, `STORAGE_DESIGN_B.md`):
- content-addressed key; whole-tape SHA-256; exact size;
- create-only (`If-None-Match: *`) with payload-bound `x-amz-content-sha256`;
- immutable retention via a bucket lock;
- verified read-back; fail closed;
- narrowly scoped runner credentials (Object Read & Write on one bucket);
- retention administered with a separate admin privilege.

**No R2 resources exist; none were created.**

**Byte-preserving migration (`tape_migration.py`; designed and tested now, executed only after Jacob sets up R2):**
1. Fetch the exact sealed artifact: `artifact_api.fetch`, using the run, the sha-bound name, the sealed artifact id and the zip digest.
2. Verify the source **independently** against the sealed sha256 and size, from the chained seal → sealed manifest → locator.
3. `put_verified` to R2: create-only upload of **the same bytes** (no rewrite, regeneration or recompression), then read back and verify identical sha256 and size. A second independent read-back follows.
4. Write a **create-only** `STORAGE_MIGRATIONS/<unit>.json` on the evidence ref, as an append-only commit. It records:
   - the sealed (original) locator, verbatim;
   - the original artifact identity (run, artifact id, zip digest, created/expires) as provenance;
   - the source verification;
   - the durable R2 locator;
   - `record_sha256`.
5. The seal, manifest, CHAIN, payload and sealed locator are **never modified**. The scientific identity (tape sha256 and size, payload bytes, seal hash) is unchanged.
   - The verifier fetches from the durable locator only when the record validates against the sealed identity.
   - An invalid record is `STORAGE_MIGRATION_INVALID`; it is never ignored.
   - `V3B_VERIFY_TAPE_SOURCE=sealed` forces the original artifact, as a cross-check while it still exists.
6. Any failure leaves no record. The artifact remains authoritative until it expires, and the monitor keeps alerting.

Tests: `test_temp_storage.py` (15 tests) covers:
- a fake GitHub artifacts API and the mock S3 from `test_b.py`;
- every fetch mutant;
- thresholds and exit codes;
- live expiry;
- proof required;
- byte-preserving migration with sealed bytes unchanged;
- tampered records;
- refusals that leave no record.

Alligator
