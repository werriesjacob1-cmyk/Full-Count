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
| No Git LFS, no ~257 MB/unit in git, no release mirror | None is used. The certification drill alone moves its tape as ONE temporary GitHub Actions artifact (non-authoritative, hash-verified before replay); it is never used for units. |

## Failure semantics, as implemented (`tape_store.R2Store`, `put_verified`, `fetch_verified`; tests `test_b.R2Mutants`)
| Situation | Result |
|---|---|
| Local tape bytes ≠ (sha256, size) | refused **before any request** (`TAPE_HASH_MISMATCH` / `TAPE_SIZE_MISMATCH`) |
| PUT accepted | read back through the writer's own credentials and re-hashed → `write_status: CREATED`, `verified_readback: true` |
| Address already occupied by identical bytes (e.g. an earlier attempt whose response was lost) | 412 → read back + hash → `EXISTS_VERIFIED` (never rewritten) |
| Address occupied by different bytes | `OBJECT_CONFLICT` (fail closed; nothing sealed) |
| Object missing | `TAPE_MISSING` |
| Wrong object / truncated / one-byte substitution | `TAPE_SIZE_MISMATCH` / `TAPE_HASH_MISMATCH` |
| Credentials rejected (401/403) or absent | `STORE_AUTH_FAILED`: never retried, never treated as "missing" |
| Connection reset/drop, timeout, short body, 429/5xx | bounded retry (1s, 2s, 4s; four attempts), then `STORE_UNAVAILABLE` |
| Any store failure inside the prospective runner | **MISS UNIT** (recorded; no seal; no later backfill) |

**Retries cannot create ambiguous evidence.** PUT is create-only (`If-None-Match: *`) and payload-bound (`x-amz-content-sha256` = the tape's sha256, which the server verifies). A retry therefore either creates the single object or meets 412, after which the existing bytes are proven by hashing. The final state is always one verified object at the content address, or an exception.

**Sealed metadata.** The manifest's `shadow_provenance.tape_store` (TSA-timestamped, chained) carries:
`{store: "r2", endpoint, bucket, key: "v3/tapes/sha256/<sha256>.json.gz", sha256, bytes, etag, write_status, verified_readback: true}`.

## Exact Cloudflare setup for Jacob (none performed; requires separate account-level authorization)
| Item | Value |
|---|---|
| Account | the existing FULL COUNT Cloudflare account (the one hosting the `fc-live-heartbeat` Worker) |
| Bucket name | **`fc-v3-evidence-tapes`** (Standard storage class; default location; **no** lifecycle/expiration rules; **no** public write) |
| Bucket lock | one rule: name `v3-tapes-retain`, prefix **`v3/tapes/`**, retention **"retain until date"** |
| Minimum retention date | **2028-12-31T00:00:00Z**. This covers the 2027 confirmatory regime (last slate 2027-10-10), its one-look analysis (on/after 2027-10-05, grace to ≥ 2027-10-12 + 7 days), and a year of audit. Recommended: **2031-01-01T00:00:00Z**. |
| Runner token | R2 API token, permission **Object Read & Write**, **Apply to specific buckets only: `fc-v3-evidence-tapes`**, TTL ≥ the collection horizon. |
| Runner token MAY | `PutObject` (create-only by code), `GetObject`/`HeadObject` (read-back) on that bucket only |
| Runner token MAY NOT | create, delete or list buckets; read or change **bucket-lock rules**, lifecycle, CORS or public-access settings; touch any other bucket. Object-scoped tokens cannot reach bucket configuration. The Object tier also allows `DeleteObject`, but the bucket lock refuses deletion or overwrite of every object under `v3/tapes/` until the retention date. |
| Lock / retention privilege (separate) | **Only** Jacob's Cloudflare dashboard login (account Super Administrator), or an **Admin Read & Write** R2 token that is never placed in any runner, CI or agent environment. |
| Verifier read access | either an **Object Read only** token on the same bucket (for Codex/auditors), or enable the bucket's `r2.dev` / custom-domain public **read** URL (`V3B_R2_PUBLIC_BASE`; content-addressed, so public read cannot alter evidence) |
| Recording-environment secrets | `V3B_TAPE_STORE=r2`, `V3B_R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, `V3B_R2_BUCKET=fc-v3-evidence-tapes`, `V3B_R2_ACCESS_KEY_ID`, `V3B_R2_SECRET_ACCESS_KEY` (never committed or logged) |
| Network | The recording session reaches `*.r2.cloudflarestorage.com` through the existing TLS proxy. Checked 2026-10-05: DNS resolves and TLS reaches Cloudflare's R2 edge; a real account endpoint is still unproven. |

**One-time capability check after setup** (authorized smoke test; uses a non-locked prefix such as `smoke/`, or a throwaway bucket):
1. A PUT with `If-None-Match: *` on an existing key returns 412.
2. Overwrite and delete under `v3/tapes/` are refused.
3. The runner token gets 403 on bucket-lock read/modify.
4. A GET returns byte-identical content.
5. `x-amz-content-sha256` mismatch is rejected.

If R2 does not honour `If-None-Match`, the bucket lock still blocks overwrite, `put_verified` still proves the bytes by read-back, and SUPERCHAD is told before activation.

## Not done here (by design)
- Cloudflare resources, tokens, secrets and network policy.
- Moving prospective tapes to R2. The prospective runner path exists but V3 is HELD; there is no activation and no dispatcher change.
