# FC-MLB-001B — V3 evidence-integrity successor to A1: pinned runtime boundary + scientific-payload identity (infrastructure only)

## ACCEPTANCE_CRITERIA
Frozen 2026-10-05, before any 001B code. Authority: SUPERCHAD disposition relayed by Jacob on 2026-10-05; Jacob is final authority. Changes need SUPERCHAD.

### 0. Identities and lineage
- **Parent task:** FC-MLB-001A. Its criteria are frozen at ops `b54055041f1e768f862c025cca8be3588b449d72` (`TASKS/FC-MLB-001A.md`).
  - A1 is NOT rewritten. It remains a permanent audit record that two frozen requirements were technically mis-specified.
- **Implementation base:** `claude/mlb-v3-amendment-a1-20261005` @ `8ef01e4c221cdc272a4c2d4d90b0655e33d88acd`.
  - It contains the A1 a1-2 repaired head `0478658601b2e58a49bd15e8fc0ec090bd7e7ca2` (v3 tree `2e5a4eee1ebc79d611cf537a8359bcdfc3eda7d7`) plus a handoff-only commit.
- **V3 lineage** (all unchanged by 001B):
  - activated implementation `504ca9cdb972d218907eb4eec13852cf5a7981d2` (v3 tree `a7c94431628c`, prereg blob `71e7d2828df2`);
  - shadow pin `7d3ebacd55c34c6ec78030bdeca70799e56d4764` (tree `02526a74f6`).
- **Implementation branch:** `claude/mlb-v3-amendment-b-20261005`.

### 1. Supersession (exactly two A1 requirements; nothing else)
| A1 requirement superseded | Why it is technically mis-specified | Evidence |
|---|---|---|
| **A1 R5** ("any file the pipeline reads outside the pinned tree, the sealed overlay and the isolated HOME is a failure") | A CPython pipeline must read its interpreter, stdlib, shared libraries, the R3 locked venv, the sealed tape and the replay shim. Frozen R4's git call must read a git object store. The whole-process trace counted 2,866 such reads on a clean runner. "Zero reads outside three directories" cannot be met by any implementation and does not express the real goal. | #91/6002668247 (Claude STOP report); #91/6002665832 (Codex delta audit); runs 37370084563 / 37370057443; A1 `v3/AMENDMENT_A1.md` §R5 BLOCKED |
| **A1 §3.3 "an exact shadow-board hash"** read as raw literal-board equality | The pinned board embeds wall-clock evidence stamps (`board_generated_at`, `sealed_at`, the self-referential `board_sha256`, per-record `generation_timestamp`). Replay runs on a ticking replay clock and cannot reproduce historical wall-clock times. Exactly those four fields differed; every other byte was identical. | same as above; A1 `AMENDMENT_A1.md` §Literal identity |

All other A1 requirements and results are **inherited unchanged** (§2).

### 2. Inherited from A1 (must still hold in 001B)
- I1. Isolated per-run HOME / XDG / TMP / cache state. Each is empty at start; a non-empty one is a breach.
- I2. Hash-locked Python dependencies (`shadow-requirements.lock`, `--require-hashes`). The installed set must equal the lock exactly, or the run fails closed.
- I3. Deterministic provenance via the frozen `GIT_CONFIG_COUNT=1` / `GIT_CONFIG_KEY_0=core.abbrev` / `GIT_CONFIG_VALUE_0=10` mechanism. No PATH shim. The board must show `git_sha == SHADOW_PIN[:10]` under full and shallow clones and hostile local `core.abbrev`.
- I4. Real CI exit propagation: pipefail, no masking, required tests never skipped.
- I5. A controlled corruption mutant must make the CI job exit nonzero.
- I6. Replay: 0 misses and 0 unconsumed exchanges.
- I7. No prospective backfill. The four Oct 3–4 units remain DESCRIPTIVE SHADOW, not reproducible, and untouched.
- I8. Science and prereg are unchanged (§7).

### 3. NEW R5 — pinned runtime boundary
**Principle:** no mutable application, data, cache, user or prior-run state outside the sealed/pinned execution boundary may influence the scientific board.

The criterion is NOT "zero reads outside three directories". It is that **every consequential mutable input is pinned, sealed, isolated, or explicitly forbidden**.

Every execution dependency is in exactly one class:

- **Allowed and pinned:**
  - the frozen source tree (shadow pin);
  - sealed capture / tape / overlay;
  - the isolated HOME/XDG/TMP/cache;
  - hash-locked Python dependencies;
  - the pinned interpreter/runtime image (immutable digest);
  - pinned required system libraries (from that image);
  - deterministic git object/code identity;
  - the amendment code at the exact amendment commit.
- **Explicitly forbidden** (any successful read = failure):
  - host user caches;
  - pre-existing pybaseball caches;
  - prior-run temp data;
  - unsealed application data;
  - the host fontconfig cache;
  - arbitrary user files;
  - mutable working directories outside the isolated run root.
- **Kernel/runtime virtual surfaces** (`/proc`, `/sys`, devices, certificates, resolver files, …), if read:
  - enumerate them;
  - record the relevant runtime/environment fingerprint (kernel release, CPU count, etc.);
  - demonstrate through cross-environment replay that their variation does not change the scientific payload;
  - never claim they are cryptographically pinned if they are not.
- **Record-only network surfaces** (proxy CA bundle, resolver files): enumerated and fingerprinted by name. Replay runs with no network.

Instrument: `strace -f` (or an equivalent whole-process-tree observation) over the entire pipeline process tree in record and replay. Every successful open/exec is classified. Forbidden-class reads must be 0, and unclassified reads must be 0.

### 4. Pinned runtime image
- Record and replay run inside an explicitly pinned Linux runtime identified by an **immutable image digest**. It provides the exact Python line/version, the required system libraries, and any font/fontconfig files actually needed.
- The host `/var/cache/fontconfig` must not be used.
- If matplotlib/font discovery is unnecessary to the scientific pipeline, that dependency is **eliminated from the execution path**, not allowlisted.
- The runtime-image digest (and the verified extracted-runtime identity) is recorded in evidence. Replay must use the identical image digest.

### 5. NEW board identity contract
- **Scientific payload** (`fc-mlb-001b-payload-v1`, frozen here):
  - The shadow board with exactly these evidence-generation fields removed:
    - top level `board_generated_at`, `sealed_at`, `board_sha256`;
    - per record (`records[*]`) `generation_timestamp`.
  - Every other field is included unchanged, at every depth: predictions, rankings, players, games, model and inputs, provenance, labels, counts.
  - No field that can influence selection or evaluation may be removed. (The removed stamps are consumed only by envelope chronology checks: `manifest_v3.build_manifest` board age and `sealed_at ≤ cutoff`.)
  - Canonical serialization, frozen: `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`. `scientific_payload_sha256` = SHA-256 of those bytes.
- **At RECORD:** build the canonical payload, serialize it with the frozen serializer, seal the payload bytes, and place `scientific_payload_sha256` in the evidence envelope (manifest shadow provenance and seal/summary).
- **At REPLAY:** regenerate the payload independently from the replayed board, serialize it with the identical serializer, and require **literal byte equality** and **exact SHA-256 equality** with the sealed payload. No fuzzy comparison, no numerical tolerance.
- **Evidence envelope** (retained separately, never replay-compared): the real generation timestamp, the seal timestamp, receipts, TSA, the payload hash, and the capture/manifest/tape identities. The verifier validates their **chronology and integrity**:
  - the self-hash is consistent;
  - `board_generated_at ≤ sealed_at ≤` manifest cutoff `≤` TSA genTime `<` first pitch;
  - each record's `generation_timestamp == board_generated_at`.

### 6. Storage — R2 authoritative prospective tape store (code + design; no account changes)
- A dedicated V3 evidence bucket or dedicated locked prefix.
- The object key is content-addressed by the whole-tape SHA-256. The exact byte size is stored in the seal.
- Retention/bucket lock extends past the full prospective evidence horizon.
- Writes are create-only and content-addressed.
- The runner token is scoped only to the required object operations. It must NOT be able to alter or remove the bucket-lock policy; lock administration uses a separate privilege.
- The verifier downloads the exact object and hashes its bytes before replay. Unavailable object, hash mismatch and size mismatch each **FAIL CLOSED**.
- No Git LFS. No ~257 MB/unit in normal git history. A GitHub Release mirror is not required.
- **No Cloudflare resource is created or changed** without Jacob's separate account-level authorization.
- The code path is implemented and tested against an isolated mock/local object store. The deliverable lists the exact account-level actions Jacob would need to authorize.

### 7. Science freeze (STOP if violated)
No change to:
- the challenger model or coefficients;
- selection logic, rankings, projected PA;
- hypotheses, the equal-volume procedure, the 2027 confirmatory regime;
- the original preregistration (blob `71e7d2828df2`);
- the frozen shadow scientific code (pin `7d3ebacd55`, tree `02526a74f6`).

If 001B would require any of these, STOP.

### 8. Acceptance (all required; Codex reproduces; V3 stays HELD)
1. These criteria are committed before any 001B code.
2. Tests and mutants for:
   - I1–I3 under the pinned runtime;
   - forbidden-read detection (a mutant reading host/forbidden state is caught);
   - payload canonicalization (a changed scientific field changes the bytes; envelope-only changes do not);
   - the store (missing / hash mismatch / size mismatch / overwrite all fail closed);
   - runtime-digest mismatch fails.
3. **New drill** labelled `FC-MLB-001B EVIDENCE-INTEGRITY DRILL — NOT A PROSPECTIVE UNIT`, recorded after these criteria and the conforming implementation exist. The old A1 drill is historical only.
   - **Environment A:** a fresh isolated pinned runtime; complete network tape; no host cache; whole-process trace captured; scientific payload hash produced.
   - **Environment B:** a different virgin runner/runtime instance that retrieves only the frozen/sealed drill artifacts (the tape through the content-addressed store path) and replays.
4. **B acceptance:**
   - misses = 0; unconsumed = 0;
   - scientific payload bytes literally identical; scientific payload SHA-256 identical;
   - capture, manifest, schedule and tape hashes identical;
   - provenance identical;
   - dependency and runtime identity valid (same image digest, same lock and installed set);
   - zero forbidden mutable-state reads and zero unclassified reads in A and B;
   - kernel/virtual surfaces enumerated and fingerprinted;
   - envelope chronology valid.
5. A corruption mutant makes the CI job nonzero; the clean exact head job is green.
6. Codex independently reproduces B and reports on #91.
7. **No reactivation under this task.** That requires SUPERCHAD acceptance, the R2 account actions authorized by Jacob, dispatcher work, a new Jacob activation record and an explicit trigger re-enable, each separately.

### 9. Must NOT
- Update the live dispatcher.
- Create an activation.
- Re-enable trigger `trig_011u98uXVuFEipPfbTT6KGur`.
- Write a prospective seal.
- Touch the evidence ref `claude/mlb-challenger-v3-evidence` or modify PR #220.
- Merge, deploy or promote.
- Touch the four Oct 3–4 units.

## BUILDER_NOTES
(owner only)
- **Runtime:** OCI image `python:3.11-bookworm` linux/amd64, fetched by manifest digest from the public ECR mirror of Docker Official Images (Docker Hub rate-limited this egress). Layers are digest-verified and extracted to a read-only rootfs.
- **Sandbox:** `unshare` mount + pid (+ net in replay) namespaces, with `chroot` into a tmpfs root holding only the enumerated binds. Host `strace -f` wraps the whole tree.
- **Font discovery:** remove `fc-list` / OS fonts from the sandbox view; matplotlib falls back to its locked bundled fonts.
- **Drill timing:** the pinned pipeline uses its own "today". The earliest drill is the 2026-10-06 slate, after 05:00Z and before the guard deadline (NIGHT first pitch 22:00Z).
- **Drill tape transport to B:** a non-authoritative content-addressed git-object branch (drill only), read through the same verify-before-replay store path.

## LOG
- 2026-10-05 criteria frozen (SUPERCHAD disposition after #91/6002668247 + #91/6002665832). V3 held.
