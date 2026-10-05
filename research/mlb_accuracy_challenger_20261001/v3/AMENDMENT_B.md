# V3 evidence-integrity amendment FC-MLB-001B (successor to FC-MLB-001A)

**Infrastructure only.**
- Criteria were frozen before any 001B code at ops `e5475e66c0b8a6be83f82e3e17828cb701b30c2c`: `engineering/ops/TASKS/FC-MLB-001B.md`, sha256 `f8efd4b563751731c6d78ce8949eebcf6e9745317c990cde9220febbe68ec652`.
- Base: `claude/mlb-v3-amendment-a1-20261005` @ `8ef01e4c22`, which contains the A1 a1-2 head `0478658601`.
- **V3 stays HELD.** Nothing here activates, seals, dispatches or collects. A1 is not rewritten.

## What 001B supersedes (and only this)
| A1 requirement | Replaced by |
|---|---|
| A1 R5 "zero reads outside pinned tree + overlay + HOME" (impossible; #91/6002668247, #91/6002665832) | **New R5, pinned runtime boundary:** every consequential mutable input is pinned, sealed, isolated or explicitly forbidden. |
| A1 raw literal-board equality (wall-clock stamps; same evidence) | **Scientific-payload identity** (`fc-mlb-001b-payload-v1`) + **evidence-envelope chronology**. |

All other A1 results are inherited:
- isolated HOME/XDG/TMP/cache;
- the hash lock and exact installed set;
- frozen R4 `GIT_CONFIG_*` `core.abbrev=10`;
- real CI exit codes;
- the corruption mutant;
- 0 misses / 0 unconsumed;
- no backfill;
- the four Oct 3–4 units untouched and invalid;
- science and prereg unchanged.

## New R5: the pinned runtime boundary (`sandbox.py`, `runtime_image.py`)
**Pinned runtime image:**
- `python:3.11-bookworm` (Docker Official Image), linux/amd64, OCI manifest **`sha256:9fd630803ec3446920ed6b64d20150bd724c1751e71817293a4230522c1a384a`**: CPython **3.11.17**, Debian bookworm glibc 2.36, `git`.
- It is fetched by digest (public ECR mirror or Docker Hub; transport only), and every blob is digest-verified.
- Layers are applied with OCI whiteouts. The extracted runtime gets a deterministic identity, `rootfs_tree_sha256` (`5b1f6985…f288` here), which is re-checked before every use and must match between record and replay.

**Sandbox:**
- Namespaces: `unshare` mount + pid; in **replay**, also a network namespace, so no network exists at all.
- The root is a tmpfs holding only the enumerated read-only binds below, and is then itself remounted read-only.
- `chroot --userspec=65534:65534`: the pipeline runs unprivileged.

| Surface (inside) | Class | Pinning |
|---|---|---|
| `/usr`, `/etc` (`bin`/`lib`/`lib64`/`sbin` → `usr`) | `PINNED_RUNTIME_IMAGE` | image digest + rootfs tree hash |
| `/v3b/tree` | `PINNED_SOURCE_TREE` | shadow pin `7d3ebacd55` (tree `02526a74f6`) |
| `/v3b/tree/data/odds/odds_<date>.json` | `SEALED_OVERLAY` | sealed sha256 |
| `/v3b/run/pin.git` (read-only) | `PINNED_GIT_OBJECTS` | a bare repo holding only the pin commit (`GIT_DIR`); frozen R4 injection |
| `/v3b/run/home`, `/v3b/run/tmp`, `/dev/shm` | `ISOLATED_RUN_STATE` | fresh and empty at start (breach = fail) |
| `/v3b/run/venv`, `/v3b/wheels` | `LOCKED_DEPENDENCIES` | `--require-hashes` lock; installed set == lock |
| `/v3b/io` | `SEALED_TAPE_AND_REPORT` | tape sha256 + size (fetched by identity) |
| `/v3b/code` | `AMENDMENT_CODE` | sha256 of `netrecord.py` + the lock, recorded |
| `/v3b/netca/ca.pem` (record only) | `RECORD_NETWORK_TRUST` | named and fingerprinted; replay has no network |
| `/proc` (own pid ns), `/dev/{null,zero,random,urandom,fd,std*}` | `KERNEL_VIRTUAL` | **not pinned**: enumerated, with kernel release / CPU count recorded |

**Absent** (forbidden by construction):
- host HOME and every host cache (pybaseball, pip, fontconfig);
- `/var`, the content of `/tmp`, `/root`, `/home`, `/sys`, `/opt`, `/mnt`;
- the host repository, and prior runs.

**Font discovery is eliminated:** the image's `fc-list`/`fc-match`/`fc-cache` are masked and the OS font directories are empty. matplotlib sees only its hash-locked bundled fonts. No fontconfig cache, host or image, is read.

**Determinism pins** (environment only; no code change):
- `PYTHONHASHSEED=0`, `TZ=UTC`, `LANG/LC_ALL=C.UTF-8`;
- BLAS/OpenMP thread count 1 (`OPENBLAS/OMP/MKL_NUM_THREADS=1`), so floating-point reduction order does not depend on the CPU count;
- `MPLBACKEND=Agg`.

**Instrument:** host `strace -f --pidns-translation -e trace=openat,open,execve,chroot,clone,clone3,fork,vfork` over the whole tree, in both setup (venv build) and run.
- A process is attributed to the sandbox from its `chroot()` onward, including every descendant. Earlier steps are the host *launcher* (`unshare`, `mount`, `mkdir`, `ln`, `touch`, `chroot`, `sh`) and are reported separately.
- Every successful sandboxed open/exec is classified.
- `FORBIDDEN_MUTABLE_STATE` (any path under `/var`, `/tmp/*`, `/root`, `/home`, `/run`, `/opt`, `/mnt`, `/media`, `/srv`, `/sys`) or `UNCLASSIFIED` (including any dirfd-relative open) **fails the run closed**.

### Limits (stated, not hidden)
- The trace records successful `open`/`openat`/`execve` only. `stat`, `readlink`, `getdents` and `mmap` of already-open files are not traced. The mount namespace, not the trace, is what makes undeclared host paths *absent*.
- `KERNEL_VIRTUAL` surfaces (`/proc/stat`, `/proc/self/cgroup`, `/proc/self/mountinfo`, `/proc/sys/vm/overcommit_memory`, `/dev/null`) and the kernel itself are not pinned. Their influence is tested empirically: the payload must replay byte-identically on a different machine and kernel.
- The host launcher (util-linux, coreutils, strace) is not part of the pinned image. It only constructs the mounts; it never reads data on the pipeline's behalf.
- Root is required on the host (namespaces, chroot); GitHub runners use sudo. The pipeline itself runs as uid/gid 65534.

## Board identity contract (`payload.py`, spec `fc-mlb-001b-payload-v1`, frozen)
- **Payload:** the board minus exactly `board_generated_at`, `sealed_at`, `board_sha256` (top level) and `records[*].generation_timestamp`; every other field, at any depth, is kept.
  - The removed stamps feed only envelope chronology (`manifest_v3` board age and `sealed_at ≤ cutoff`), never selection, ranking or evaluation.
- **Serialization:** `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`.
- **Record:** the payload bytes are sealed as `shadow_payload.json`. `scientific_payload_sha256` (with the spec, the runtime digest and the rootfs hash) is bound into the manifest's `shadow_provenance`, which is TSA-timestamped.
- **Replay:** the payload is regenerated from the replayed board and must be **literally byte-identical** and SHA-256-identical to the sealed bytes. There is no tolerance.
- **Envelope (`envelope_chronology`):**
  - the canonical self-hash is consistent;
  - every record's `generation_timestamp == board_generated_at`;
  - `board_generated_at ≤ sealed_at ≤ manifest cutoff ≤ TSA genTime < first pitch`.

## Tape storage (`tape_store.py`; design: `STORAGE_DESIGN_B.md`)
- The tape is content-addressed (`v3/tapes/sha256/<sha256>.json.gz`) and written create-only. The seal and manifest record key, sha256 and exact bytes.
- The verifier fetches by identity and hashes **before** replay:
  - missing → `TAPE_MISSING`;
  - wrong size → `TAPE_SIZE_MISMATCH`;
  - wrong bytes → `TAPE_HASH_MISMATCH`.
  All fail closed.
- Prospective mode requires the R2 store; anything else is a MISS UNIT.
- `R2Store` is implemented and tested against a local mock S3 server and the AWS SigV4 reference vector.
- **No Cloudflare resource was created or changed.**
- Drills use an isolated local store for record. A **drill-only, non-authoritative** git transport ref (`claude/mlb-v3-001b-drill-objects`, content-addressed parts) carries the same identity to a virgin environment B.

## Unchanged (science freeze)
- The challenger model, coefficients, selection, rankings, projected PA, hypotheses, equal-volume procedure and 2027 regime.
- The prereg (blob `71e7d2828df2`), and the shadow pin/tree/labels/pinned-artifact hashes.
- `manifest_v3`, `evaluate_v3`, `regimes`, `capture`, `seal` and `schedule_plan` are byte-identical to base.

**Files:**
- New: `runtime_image.py`, `sandbox.py`, `payload.py`, `tape_store.py`, `b_drill.py`, `test_b.py`, this note, `STORAGE_DESIGN_B.md`, `.github/workflows/v3-b-drill-replay.yml`.
- Changed:
  - `shadow.py`: `run_pipeline_b` added; A1 `run_pipeline` kept as the historical path.
  - `runner.py`: 001B record path, payload, tape store.
  - `verify_evidence.py`: B artifact set, `sealed_b_identity_problems`, `replay_shadow_b`.
  - `test_v3.py`: one runner test's mock follows the new record call.

## Results
**Pre-check** (historical A1 drill tape replayed inside the 001B sandbox; not the acceptance drill):
- 0 misses, 0 unconsumed, 0 forbidden/unclassified reads.
- Executables: `sh`, `env`, `git` and the venv `python`. No `fc-list`.
- Kernel surfaces: 5.
- The payload `d5c07bb6…da49` is **byte-identical** to the payload of the A1 record board. That board was recorded on a different interpreter build (host 3.11.15) and different system libraries, without thread pinning.

**The 001B drill A → B** (required for acceptance) is recorded in `b_drill/` and its runs are on Issue #91.
