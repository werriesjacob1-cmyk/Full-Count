# V3 evidence-integrity amendment A1 (task FC-MLB-001A)

**Infrastructure only.** Criteria frozen before any code at ops `b54055041f` (`engineering/ops/TASKS/FC-MLB-001A.md`, sha256 `7049c6cb…3c8a`). Authorization: SUPERCHAD disposition; Jacob is final authority.

Base is the activated implementation `504ca9cdb972d218907eb4eec13852cf5a7981d2`:
- v3 tree `a7c94431628c7714cfadd0e9ed911c490687ad20`;
- commit tree `c90dc9d66ace57f7f1a42763d2c0b90da7806728`;
- prereg blob `71e7d2828df20a01f476376efcc0c651828dcaf2` (unchanged).

V3 collection stays **HELD**: trigger `trig_011u98uXVuFEipPfbTT6KGur` is disabled. This amendment does not activate, reactivate, seal or collect anything.

## Defect being fixed (#91: 5971140470, 5983051660, 5985109220, 5998885845)
- The frozen shadow pin `7d3ebacd55` calls `pybaseball.cache.enable()`.
- In the runner's long-lived container, most Statcast/Savant responses came from `~/.pybaseball/cache` and never reached the sealed tape.
- In a virgin environment, replays of the Oct 3–4 units miss 219–225 requests. They remain **DESCRIPTIVE SHADOW — SEALED ON TIME, BUT NOT REPRODUCIBLE FROM SEALED ARTIFACTS ALONE**.
- Secondary defects:
  - provenance depended on `git rev-parse --short` (clone depth / `core.abbrev`);
  - the pin's runtime packages were installed ad hoc.

## What A1 changes
| Req | Mechanism | Code |
|---|---|---|
| R1 record isolation | Every RECORD runs in a fresh root: empty `HOME`, `XDG_*`, `PYBASEBALL_CACHE`, `MPLCONFIGDIR`, `TMPDIR`. A non-empty root at start is `ISOLATION_BREACH` (fail closed). The environment is explicit, never inherited. | `isolation.IsolatedRun` |
| R2 replay isolation | Every REPLAY (including `verify_evidence.replay_shadow` and `a1_drill verify`) gets its own new root. Nothing is shared with the record run except the sealed artifacts and the hash-verified wheel files. | same |
| R3 deterministic dependencies | `shadow-requirements.lock` lists 40 packages, `name==version --hash=sha256:…`, CPython 3.11 linux x86_64. Direct pins are exactly the versions the pin's own `requirements.txt` names as verified; transitive packages were resolved once and frozen. Playwright (test-only) is excluded and `time-machine` (replay clock) included. Each run: fresh venv; `pip install --no-deps --only-binary=:all: --require-hashes`; the installed set must equal the lock exactly (bootstrap `pip`/`setuptools` reported, not used). The user site is disabled. | `isolation.build_venv/verify_installed` |
| R4 provenance identity | **As frozen:** every git call of the pinned pipeline runs with the injected command-scope config `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=core.abbrev`, `GIT_CONFIG_VALUE_0=10`. This overrides repo-local `core.abbrev`. System/global git config is disabled (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`). No PATH shim and no persistent git config are written. The pinned tree is unmodified. (The a1-1 PATH shim was removed in a1-2 after Codex's audit.) | `isolation.GIT_INJECTED_CONFIG` |
| R5 hidden-state guard | (a) sealed fingerprint fields: lock, installed set, python, empty-at-start flags, injected git config. (b) An in-process `sys.addaudithook` guard (process-level only) that fails closed on reads outside its operational allowlist. (c) **The frozen drill trace method:** the whole process tree runs under `strace -f` (openat/open/execve), and every successful call is classified (`process_trace`). Frozen-R5 violations, meaning anything outside the pinned tree + overlay + isolated HOME, are counted and reported as a FAILED check, never waived. **R5 status: BLOCKED**; see below. | `netrecord.install_guard`, `isolation.summarize_trace` |
| R6 cross-environment replay | Record in environment A (isolated root in the builder's container). Replay in environment B: a fresh GitHub-hosted VM, a shallow checkout and a fresh venv (`.github/workflows/v3-a1-drill-replay.yml`). | `a1_drill.py`, workflow |

**Sealed evidence:**
- A1 units carry `shadow_env.json`, the record-environment fingerprint: lock sha256, installed-set sha256, python, isolation flags, git rule, guard result, env key names, and passthrough key names (values never recorded).
- The verifier requires:
  - record/replay equality of the lock and the installed set;
  - the same Python minor version;
  - clean isolation and zero guard violations;
  - **record conformance**: the record's `a1_version` is current and its git identity is the frozen injected `core.abbrev=10`.
- It also requires literal board identity.
- Legacy units have no fingerprint and are replayed in a clean environment. They are expected to fail, and the amended verifier never "repairs" them.

## Audit-hook operational allowlist (NOT a redefinition of frozen R5)
The in-process audit hook needs an allowlist to run at all:
- the pinned tree;
- the run root (home, tmp, venv);
- `netrecord.py`;
- the stdlib;
- in record mode, the named CA bundle;
- read-only OS runtime prefixes (`isolation.OS_RUNTIME_PREFIXES`).

This list is an implementation detail of check (b). It is **not** the R5 criterion. Frozen R5 permits only the pinned tree, the sealed overlay and the isolated HOME. The process-tree trace (c) reports every read outside those three as a frozen-R5 violation.

## Exact guarantees and limits (no overclaiming)
- **Guaranteed:**
  - no inherited HOME, user-site, pybaseball, matplotlib or XDG cache can influence a record or replay;
  - the Python package set is exactly the lock;
  - board provenance is clone-shape independent under the frozen R4 mechanism;
  - a read by the pipeline's own Python process outside the audit allowlist aborts the run;
  - every successful open/exec of the whole process tree is recorded and classified;
  - the network is reachable only in record mode; replay serves the tape and fails on any miss or unconsumed exchange.
- **Not guaranteed:**
  - This is not an OS sandbox. Nothing *prevents* a subprocess or native library from reading a host path. The trace only *records* it, and records only successful open/openat/execve calls; stat/readlink/getdents are not traced.
  - The OS image (glibc, libm, openssl, fontconfig, CA store) is not pinned.
  - The interpreter must be CPython 3.11.x on linux x86_64.

## R5 — BLOCKED: the frozen criterion is technically overbroad (STOP; SUPERCHAD/Jacob decide)
Frozen R5 says: *"Any file the pipeline reads outside the pinned tree, the sealed overlay and the isolated HOME is a failure. This is checked by the existing drill trace method."* The drill trace method (`strace -f`, whole process tree) applied to the drill replay (`a1_drill/` results; local and environment B) records about 2,900 successful reads/execs outside those three surfaces in every run. Representative classes:

| Class | Why the pipeline cannot run without it |
|---|---|
| `INTERPRETER_STDLIB` (~260) | CPython must load its own standard library; it also loads the host `sitecustomize`. |
| `RUN_ROOT_VENV` (~2,270) | The locked packages (R3) live in the run root's venv, not in HOME. |
| `SHARED_LIBRARIES` (~55) | The dynamic loader must read `ld.so.cache`, libc, libm, libssl, libstdc++ and others. |
| `EXECUTABLES` | `git` (needed by R4 itself), and `fc-list` (spawned by matplotlib). |
| `REPO_GIT_DIR` | `git rev-parse` for R4 reads the repository object store; for a worktree it lives outside the pinned tree. **Frozen R4 requires reads that frozen R5 forbids.** |
| `SEALED_TAPE_AND_REPORT`, `AMENDMENT_CODE` | The replay must read the sealed tape and `netrecord.py`. |
| `OS_VIRTUAL_SYS` / `PROC` / `DEV` | numpy/OpenBLAS read the CPU topology (`/sys/devices/system/cpu/{possible,online}`) and cgroup CPU quota (thread count); `/dev/shm` semaphores. |
| `HOST_STATE` | **matplotlib → `fc-list` reads the host fontconfig cache `/var/cache/fontconfig/*`. The audit hook never saw this; it is genuine hidden host state.** It is rendering-only and does not feed the board, but it is outside every permitted surface. |
| `OS_CONFIG` / `OS_DATA` | `/etc/fonts`, `openssl.cnf`, locale (`C.utf8`), and zoneinfo `UTC`. |

**No implementation can make a CPython pipeline satisfy the literal frozen R5.** It must at minimum read its interpreter, stdlib, shared libraries, the R3 venv and the sealed tape. The R4 git call additionally reads the git object store. An OS-level sandbox (mount namespace / bubblewrap; available here and, via sudo, on GitHub runners) can make the readable surface explicit and enumerable. It cannot shrink it to the three frozen surfaces, short of copying the runtime *into* HOME, which would satisfy the letter while changing nothing of substance. Amending R5 (for example to an enumerated, sealed list of read-only runtime surfaces enforced by an OS sandbox, with the fontconfig cache redirected into HOME and BLAS thread counts pinned) is a criterion change. That is **for SUPERCHAD/Jacob, not the builder.** A1 does not redefine R5. The gate reports `r5_process_trace_frozen_surfaces: FAIL`.

## Literal shadow-board identity — NOT achieved (STOP; exact fields)
The verifier now compares **literal bytes**. The old `replay_equivalent` (timestamp-stripped) comparison is kept only as a labelled diagnostic, never as the criterion.
- Record A's raw pipeline bytes were not retained by the a1-1 runner; it re-serialized the board.
- The comparison is therefore the sealed record board bytes (gunzipped `shadow_board.json.gz`; sha256 `6f89a976…362b`) against replay B's board serialized by the identical sealing serializer. Replay B's raw pipeline bytes are retained as well.
- New units also seal `shadow_board_raw.json.gz`, enabling a raw-to-raw comparison.

**Result: not identical.** Exactly four field paths differ; every other byte is identical, and the serialized lengths are equal (254,693 bytes):

| Field | Count |
|---|---|
| `board_generated_at` | 1 |
| `sealed_at` | 1 |
| `board_sha256` | 1 (the pin's canonical hash over a body that includes the two stamps) |
| `records[*].generation_timestamp` | 135 |

All four come from the process clock. Replay runs on a `time-machine` clock that starts at the recorded start instant and **ticks**, so the stamps equal the recorded start plus the replay's own elapsed time.
- Record A: 17:44:39.986 → 17:47:30.056.
- A local replay: 17:46:53.955.
- Each replay differs.

Literal identity would need the clock itself to be taped and replayed, or the criterion amended. Either is a SUPERCHAD/Jacob decision. The criterion is **not** changed to canonical equivalence here.

## Unchanged (scientific immutability)
- Challenger model, coefficients, harness, `manifest_v3` resolution, `evaluate_v3`, regimes, ranking/selection, equal-volume rules, the 2027 confirmatory regime, the prereg (blob identical), the shadow pin/tree/labels/pinned-artifact hashes, and the schedule guard.
- Files changed: `netrecord.py`, `shadow.py` (`run_pipeline` only), `runner.py` (fingerprint artifact; literal record board bytes `shadow_board_raw.json.gz`), `verify_evidence.py` (artifact set; literal replay comparison; record conformance).
- Files added: `isolation.py`, `a1_drill.py`, locks, tests, this note, `STORAGE_DESIGN_A1.md` (design only), and the drill workflow.

## Before any reactivation (frozen acceptance bar)
1. Drill recorded in A and replayed in a different virgin B, with 0 misses, 0 unconsumed and exact hashes, provenance and timestamps.
2. Codex reproduces B independently.
3. SUPERCHAD acceptance.
4. Dispatcher changes on `claude/mlb-v3-ops` (bootstrap, if any).
5. A **new** Jacob activation record bound to the amended commit/tree.
6. Jacob's explicit trigger re-enable.

Local tests passing never reactivate V3.

## Results (drill — NOT A PROSPECTIVE UNIT)
**Record A** (builder container, isolated root, commit `e6fdee0230` = a1-1, v3 tree `ed32e97b`):
- Live inputs from the 2026-10-05 NIGHT slate; 612 HTTP exchanges taped; audit guard 0 violations.
- HOME/pybaseball cache/TMP empty at start; python 3.11.15; lock `33034a7f…`; installed set `da98e4af…`; manifest `04f0b810…`; TSA 17:49:21Z.
- Recorded with the a1-1 **PATH shim** (not frozen R4) and **without** the process-tree trace. Every verifier therefore reports `record_conforms_to_current_a1: FAIL` and `r5_record_process_trace: FAIL` for this drill.
- A conforming drill needs a new record. None was made, because the task said not to collect.
- Attempt history: `a1_drill/ATTEMPTS/`.

**Correction to the earlier environment-B claim:**
- Run 37353256200 was green while required tests were red (Codex reproduced this).
  - Test output was piped through `tail` without `pipefail`.
  - `test_a1.DrillArtifacts` ran from the wrong directory and could not import.
- Its "PASS on every check" is withdrawn; it is superseded by the repaired gate (see `.github/workflows/v3-a1-drill-replay.yml` header and `a1_drill/B_RUNS.md`).

**Repaired gate, verify result on the drill** (local, and environment B on the exact head; run ids on Issue #91):
- **Integrity gate PASS:**
  - sha256sums (no changed or unlisted file), artifact set, provenance `7d3ebacd55`, board/capture/schedule/manifest hashes;
  - manifest reproducible, overlay, tape identity, both TSA tokens pregame;
  - **0 misses, 0 unconsumed, 0 guard violations**;
  - identical lock/installed set.
- **Frozen-conformance gate FAIL:**
  - `literal_board_identity` (the 4 clock fields above);
  - `r5_process_trace_frozen_surfaces` (about 2,900 out-of-surface reads);
  - `r5_record_process_trace` (record not traced);
  - `record_conforms_to_current_a1` (a1-1 shim record).
- **Job result FAIL. That is the honest state.**

**Shallow / full / `core.abbrev` (frozen R4 mechanism, tests):**
- Full, shallow, hostile local abbrev 4 and hostile abbrev 12 all give `HEAD[:10]`, with `.git/config` unchanged and no shim.
- The abbrev-7 shallow mutant gives 7 characters without the injection (rejected) and `HEAD[:10]` with it.

**Legacy units** (`a1_drill/LEGACY_CHECK.json`, read-only):
- 2026-10-03_DAY: 228 misses.
- 2026-10-03_NIGHT: 225 misses.
- 2026-10-04_DAY: 219 misses.
- 2026-10-04_NIGHT: 223 misses.
- Each has 1 unconsumed exchange, and all files are unchanged. They are not reproducible, not repaired and not replaced.

## Open items before reactivation (not solved by A1)
1. **R5 criterion decision** (BLOCKED above) and **literal-identity decision** (clock taping vs criterion): SUPERCHAD/Jacob.
2. **Evidence storage:** `STORAGE_DESIGN_A1.md`, design only.
   - A content-addressed immutable external store bound by the whole-tape sha256 in the seal; the verifier fetches by identity and hashes before replay; fail closed.
   - Not implemented, not activated.
   - `runner.py` in prospective mode still does not split or upload tapes.
3. **A conforming drill** (a1-2 record with the frozen R4 injection and the process-tree trace), recorded only after 1–2 are decided.
4. **Dispatcher:**
   - `claude/mlb-v3-ops` `v3_dispatch.py` pins `AUTH_COMMIT 504ca9cdb9` / tree `a7c9443` and must move to the amended commit.
   - A **new Jacob activation record** is required.
   - The dispatcher must also provide `strace`.
5. **Codex** independent rerun of the exact-head gate.
