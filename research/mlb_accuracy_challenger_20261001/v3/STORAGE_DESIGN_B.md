# FC-MLB-001B: Cloudflare R2 as the authoritative prospective tape store

**Status:**
- The code path is implemented and tested against an isolated mock: `tape_store.R2Store`, a local mock S3 server in `test_b.Store`, and the AWS SigV4 reference vector.
- **No Cloudflare resource has been created or changed.** Everything below under "account actions" needs Jacob's separate account-level authorization.
- The Cloudflare connectors available to this session are not authorized; no account capability was inspected.

## Contract (criteria §6)
| Requirement | Implementation |
|---|---|
| Dedicated bucket / locked prefix | Bucket `fc-v3-evidence-tapes`; every tape lives under the prefix `v3/tapes/sha256/`. |
| Content-addressed key | `v3/tapes/sha256/<whole-tape sha256>.json.gz` (`tape_store.key_for`). |
| Exact size sealed | `tape_store.bytes` in the manifest's `shadow_provenance` and the drill/seal summary. |
| Create-only writes | `PUT` with `If-None-Match: *` (412 → `ObjectExists`, never overwritten), plus `x-amz-content-sha256` = the tape's sha256, which binds the signed payload to the identity. The runner reads the object back and re-hashes it **before** sealing (`runner._store_tape`). |
| Retention past the horizon | R2 bucket-lock rule on the prefix (account action 2). |
| Runner token scoped to objects only | An R2 API token with **Object Read & Write** on this one bucket. Object-scoped tokens cannot read or modify bucket configuration, so they cannot alter or remove a bucket lock (verify in action 7). |
| Lock administration separate | Bucket and lock rules are managed only from Jacob's dashboard login, or an Admin token that is never given to the runner. |
| Verifier downloads the exact object and hashes it before replay | `tape_store.fetch_verified`: missing → `TAPE_MISSING`, size → `TAPE_SIZE_MISMATCH`, bytes → `TAPE_HASH_MISMATCH`, all fail closed. A locator whose key is not the content address of the sealed sha256 is refused. |
| Prospective requires R2 | `runner._store_tape`: `V3B_TAPE_STORE=r2` is mandatory in prospective mode; anything else is a MISS UNIT (no seal). |
| No Git LFS, no ~257 MB/unit in git, no release mirror | None is used. The drill-only git transport ref is non-authoritative and never used for units. |

## Account actions Jacob would need to authorize (exact; none performed)
1. **Create the bucket** `fc-v3-evidence-tapes` in the existing FULL COUNT Cloudflare account (R2 → Create bucket; default location; Standard storage class).
2. **Add a bucket-lock rule** (R2 → bucket → Settings → Bucket lock rules → Add rule):
   - name `v3-tapes-retain`;
   - prefix `v3/tapes/`;
   - retention **until 2031-01-01T00:00:00Z** (covers the 2027 confirmatory season, its evaluation and an audit window).
   - Objects under the prefix then cannot be deleted or overwritten before that date.
3. **Create the runner token** (R2 → Manage API tokens → Create): permission **Object Read & Write**, applied to **`fc-v3-evidence-tapes` only**, TTL ≥ the collection horizon. Record the Access Key ID and Secret once.
4. **Choose verifier read access:**
   - (a) a second token, **Object Read only**, on the same bucket, for Codex and auditors; or
   - (b) the public `r2.dev` URL / a custom domain for read-only public download (`V3B_R2_PUBLIC_BASE`). The evidence is content-addressed, so public read cannot alter it.
5. **Recording-environment secrets** (the V3 runner environment only):
   - `V3B_TAPE_STORE=r2`;
   - `V3B_R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`;
   - `V3B_R2_BUCKET=fc-v3-evidence-tapes`;
   - `V3B_R2_ACCESS_KEY_ID`, `V3B_R2_SECRET_ACCESS_KEY`.
   They must never be committed or logged.
6. **Network policy:** allow the recording environment to reach `<ACCOUNT_ID>.r2.cloudflarestorage.com` (and the public read host if 4b) through the existing TLS proxy.
7. **Capability verification, before any reactivation** (an authorized smoke test, outside the locked prefix or on a test bucket):
   - `If-None-Match: *` on an existing key returns 412;
   - an overwrite or delete under the locked prefix is refused;
   - the object token cannot read or change the lock rules (403);
   - a GET by key returns byte-identical content.
   If R2 does not honour `If-None-Match`, the runner must `HEAD` before `PUT`. The bucket lock still blocks overwrite, and the sha256 binding still blocks substitution.

## Not done here (by design)
- Cloudflare resources, tokens, secrets and network policy.
- Moving prospective tapes to R2. The prospective runner path exists but V3 is HELD; there is no activation and no dispatcher change.
