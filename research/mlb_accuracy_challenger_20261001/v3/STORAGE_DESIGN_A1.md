# FC-MLB-001A: immutable external storage for complete shadow tapes (DESIGN ONLY; NOT IMPLEMENTED OR ACTIVATED)

**Status:** design and implementation plan only. No bucket, token, release, workflow or code path exists. Nothing in this branch uploads, downloads or depends on external storage. Choosing and activating it is a SUPERCHAD/Jacob decision. Activation needs:
- new credentials;
- a network-policy change for the recording environment;
- retention spend.

## Problem
A complete (cache-free) tape is about **257 MB per unit** (the drill: whole-file sha256 `e2cf77b3…c4f7`, 612 exchanges).
- Two units a day is about 0.5 GB/day, or roughly 95 GB over a season.
- Committing tapes to git, split or not, makes the evidence ref and every clone grow without bound.
- Git LFS only hides the same external-storage dependency behind git. Its free quota (1 GB storage / 1 GB bandwidth) is exhausted by four units. It is rejected (`backtest/durable_artifact_persistence_2026-08-25.md`, option B).

## Existing FULL COUNT storage surfaces (checked)
| Surface | Exists today | Immutable | Fits | Verdict |
|---|---|---|---|---|
| Git evidence ref (`claude/mlb-v3-evidence`) | yes | append-only by convention and chain hashes, not by storage | no (size) | keeps the small artifacts and seals; not tapes |
| Actions artifacts | yes | yes while retained | no: at most 90–400 days of retention < horizon | rejected |
| GitHub Releases (Full-Count repo) | yes (repo) | yes, only if the repository's **immutable releases** setting is on (assets and tag locked after publish) | 2 GiB/asset | **mirror** (independent public download) |
| Cloudflare account (live-heartbeat Worker) | yes (account) | an R2 bucket with a **bucket lock** retention rule blocks overwrite/delete | no practical limit | **primary** (new bucket inside the existing account) |

No existing object store is in use. The cleanest reuse is the existing Cloudflare account (a new, dedicated R2 bucket) plus the existing repository's releases as an independent mirror.

## Design
1. **Identity is the content.**
   - The object key is `v3/tapes/sha256/<64-hex>.json.gz`: the whole-file sha256 of the exact tape bytes the runner hashed into `shadow_provenance.tape_sha256`.
   - The release mirror asset name is `<64-hex>.json.gz`, on the immutable release tag `v3-tape-<64-hex[:16]>`.
2. **The seal binds it.** Each A1 unit's seal and manifest provenance gain `tape_store`:
   `{"sha256": <hex>, "bytes": <n>, "primary": {"kind": "r2", "bucket": "fc-v3-evidence-tapes", "key": "v3/tapes/sha256/<hex>.json.gz", "retain_until": "<ISO>"}, "mirror": {"kind": "github-immutable-release", "repo": "werriesjacob1-cmyk/Full-Count", "tag": "v3-tape-<hex16>", "asset": "<hex>.json.gz"}}`.
   - The seal is TSA-timestamped and chained exactly as today, so the locator and hash are committed before outcomes exist.
   - The tape bytes leave the git evidence ref. Every other artifact stays in it.
3. **Write path** (runner, record mode, before the seal):
   - (a) hash the local tape;
   - (b) `PUT` to R2 with `If-None-Match: *` (create-only);
   - (c) `HEAD` the object, then download it and re-hash. The stored bytes must equal the local sha256 and size;
   - (d) only then write `tape_store` into the seal.
   - Any failure is a **MISS UNIT** (no seal, no backfill), exactly like other pre-seal failures.
4. **Mirror path:** a separate GitHub Actions workflow, triggered by the evidence push, with `contents: write` limited to that workflow job. It:
   - downloads by the sealed key;
   - re-hashes and refuses on mismatch;
   - publishes an immutable release whose single asset is the tape.
   The mirror is a convenience for independent download. It is never trusted without re-hashing.
5. **Verifier read path** (`verify_evidence`, `a1_drill verify`):
   - For each locator in the seal (primary, then mirror), download to a fresh temp file and compute sha256 and size.
   - Accept the **first** source whose bytes equal the sealed sha256 and size, and only then replay.
   - A source whose bytes differ is `TAPE_SUBSTITUTED`: recorded, not used, and a hard failure even if another source matches.
   - No source reachable or present gives `TAPE_MISSING`: fail closed.
   - A local copy is never trusted without re-hashing.
6. **Immutability and versions:**
   - The R2 bucket lock gives age-based retention until `retain_until`. Overwrite and delete are refused by storage, not by convention.
   - Even if storage were subverted, a substituted version cannot pass, because the verifier binds bytes to the sealed sha256. The lock prevents *loss*; the hash prevents *substitution*.
7. **Credentials:**
   - One R2 API token scoped to **Object Read & Write on the single bucket** (no account, list-all or admin scope). It is stored only as an environment secret of the recording environment, never committed or logged.
   - Lock rules and bucket settings are administered only by Jacob's account, never by the token.
   - Verification needs no credentials if the bucket gets a read-only public custom domain or `r2.dev` URL. Otherwise it uses the mirror (public release) or a separate read-only token.
8. **Retention horizon:**
   - At least the end of the 2027 confirmatory regime plus its evaluation and audit window.
   - Proposed `retain_until = 2030-12-31T00:00:00Z` for every object, set by bucket rule. Jacob decides.
   - Cost estimate: about 95 GB/season × $0.015/GB-month ≈ $1.5/month per season of tapes (R2 has no egress fee).
9. **Independent later download:**
   - Anyone can fetch either locator from the seal and run `sha256sum` against the sealed hash.
   - Codex/auditors need nothing but the seal and a network connection.

## Implementation plan (not started)
1. **Jacob:**
   - create the bucket `fc-v3-evidence-tapes` in the existing Cloudflare account;
   - add a bucket-lock rule (all objects, retain until the horizon);
   - optionally add a read-only public domain;
   - create the bucket-scoped token;
   - add it to the recording environment's secrets;
   - turn on immutable releases for the repository (Settings → Releases).
2. **Network policy:** allow the recording environment to reach `<account>.r2.cloudflarestorage.com` and the public read domain, through the existing TLS proxy. Never bypass it.
3. **Code** (new A1 revision, before any reactivation):
   - `tape_store.py`: `put_create_only`, `head`, `fetch_verified(sha256, size, locators)`;
   - runner write path (step 3) and seal field (step 2);
   - verifier read path (step 5);
   - remove tape bytes from the evidence-push path.
4. **Tests and mutants:** missing object, wrong bytes at the key, mirror-only, primary-only, overwrite attempt refused (stubbed S3 412), retention metadata absent, and a local tape copy ignored unless hashed.
5. **Drill:** record one drill to the bucket; replay it in a virgin environment B from the seal plus storage alone; Codex reproduces.
6. **Mirror workflow** and its own drill.

## Blockers to activation
- New credentials and a Cloudflare bucket (Jacob).
- The network allowance for the recording environment.
- Verifying that the repository's immutable-releases setting is on (unverified here).
- SUPERCHAD acceptance of the design.
- Literal board identity and frozen R5 are **separately blocked**; see AMENDMENT_A1.md. Storage does not resolve them.
