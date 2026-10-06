# FC-MLB-001C — capsule (fields live in WORK_QUEUE.json; this file holds detail only)

**Milestone (Jacob, 2026-10-06):**
> Migrate authoritative V3 tape storage from GitHub Actions artifacts to Cloudflare R2 or equivalent durable object storage before temporary artifact retention threatens any prospective evidence.

**Status:** standing obligation.
- **Blocked on Jacob:** a Cloudflare payment method and account setup (`STORAGE_DESIGN_B.md`, "Exact Cloudflare setup").
- Not an activation prerequisite. Must not be silently dropped.

## ACCEPTANCE_CRITERIA
- **Preserved R2 contract `fc-mlb-001b-r2-cas-1`** (code unchanged since 001B `dac663c0a2`):
  - content-addressed key; whole-tape SHA-256; exact size;
  - create-only (`If-None-Match: *`) with payload-bound `x-amz-content-sha256`;
  - immutable retention via a bucket lock;
  - verified read-back; fail closed;
  - narrowly scoped runner credentials (Object Read & Write, one bucket);
  - separate admin privilege for retention.
- **No evidence loss.** For every chained unit whose sealed tape locator is `github-actions-artifact`, `tape_migration.py`:
  1. fetches the exact sealed artifact (run, sha-bound name, sealed artifact id, GitHub zip digest);
  2. verifies the source independently against the sealed sha256 and size;
  3. uploads **the same bytes** create-only, then reads back and verifies an identical sha256 and size (twice);
  4. writes a create-only `STORAGE_MIGRATIONS/<unit>.json` on the evidence ref (append-only commit), which records:
     - the verbatim sealed locator;
     - the original artifact identity (run, artifact id, zip digest, created/expires) as provenance;
     - the durable locator;
     - `record_sha256`.
- **Never** rewrite or regenerate a tape. Never modify a seal, the manifest, CHAIN, the payload or the sealed locator. The scientific identity is unchanged.
- **Verifier:**
  - a valid record → fetch from the durable locator;
  - an invalid record → `STORAGE_MIGRATION_INVALID` (never ignored);
  - `V3B_VERIFY_TAPE_SOURCE=sealed` still cross-checks the artifact while it exists.
- **Done when:**
  - `retention_monitor.py` reports `MIGRATED_DURABLE` for every artifact-backed unit;
  - a fresh-runner `verify_unit` replays at least one migrated unit from R2;
  - Codex has reviewed it.
- **Deadline rule:**
  - no artifact-backed unit may reach `MIGRATE_CRITICAL` (≤ 30 days remaining) unmigrated without a #91 alert to Jacob on every dispatcher tick;
  - `MIGRATE_WARNING` (≤ 45 days) starts the migration request.
  - 2027 confirmatory units cannot reach their 2027-10-05 look on 90-day artifacts, so this must complete before they age past 45 days.

## BUILDER_NOTES
- Built and tested ahead of time (synthetic only) on `claude/mlb-v3-reactivation-prep-20261005` @ `2e97b013d2`:
  - `v3/tape_migration.py`, `v3/retention_monitor.py`, `v3/artifact_api.py`, `v3/STORAGE_TEMPORARY.md`;
  - tests in `test_temp_storage.py`.
- No R2 resource exists. Agents create none without Jacob's separate authorization.

## LOG
- 2026-10-06 — milestone recorded per Jacob's storage decision. Tooling pre-built on prep `2e97b013d2`. BLOCKED on Jacob's R2 account; no deadline pressure until the first artifact-backed unit exists.
