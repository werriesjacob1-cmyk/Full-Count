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
| R4 provenance identity | The pinned code calls `git rev-parse --short HEAD` (`recommendation.git_sha`). A1 puts a `git` shim first on `PATH` that answers exactly that call with the first 10 hex characters of the full `HEAD` id; every other git call goes to real git. System/global git config is disabled. The board's `git_sha` is therefore `SHADOW_PIN[:10]` under shallow, full or any `core.abbrev`. The pinned tree is unmodified (tree id still verified). | `isolation._git_shim` |
| R5 hidden-state guard | `netrecord.py` installs a `sys.addaudithook` in the pipeline process. Every `open`/`listdir`/`scandir` outside the permitted surfaces is a violation and the run fails closed. Subprocess executables are logged (expected: `git` only). | `netrecord.install_guard` |
| R6 cross-environment replay | Record in environment A (isolated root in the builder's container). Replay in environment B: a fresh GitHub-hosted VM, a shallow checkout and a fresh venv (`.github/workflows/v3-a1-drill-replay.yml`). | `a1_drill.py`, workflow |

**Sealed evidence:**
- A1 units carry `shadow_env.json`, the record-environment fingerprint: lock sha256, installed-set sha256, python, isolation flags, git rule, guard result, env key names, and passthrough key names (values never recorded).
- The verifier requires record/replay equality of `a1_version`, the lock and the installed set; the same Python minor version; clean isolation; and zero guard violations.
- Legacy units have no fingerprint and are replayed in a clean environment. They are expected to fail, and the amended verifier never "repairs" them.

## Permitted surfaces for the pipeline process (R5)
- The pinned shadow tree (the run's cwd) and the sealed overlay inside it.
- The run's own isolated root: home, tmp, venv and shim.
- The amendment code directory (`netrecord.py`).
- The CPython standard library.
- Record mode only: the named CA bundle file.
- Read-only OS runtime files:
  - `/dev`, `/proc`, `/sys`;
  - TLS certificates;
  - time zone data;
  - name-resolution files (`hosts`, `resolv.conf`, `nsswitch.conf`, `gai.conf`, `host.conf`, `services`, `protocols`);
  - `mime.types`, `os-release`, fonts.

## Exact guarantees and limits (no overclaiming)
- **Guaranteed:**
  - no inherited HOME, user-site, pybaseball, matplotlib or XDG cache can influence a record or replay;
  - the Python package set is exactly the lock;
  - board provenance is clone-shape independent;
  - any file read or listed by the *pipeline's Python process* outside the permitted surfaces aborts the run;
  - the network is reachable only in record mode; replay serves the tape and fails on any miss or unconsumed exchange.
- **Not guaranteed (documented limits):**
  - This is process-level isolation, not an OS sandbox. Subprocess file I/O is not audited (only `git` is expected and logged).
  - Native libraries loaded via `dlopen` are not audited.
  - The OS image (glibc, openssl, CA store) is not pinned.
    - In record mode it affects the network layer.
    - Through the system `libm` it could in principle shift floating-point results.
    - The A→B cross-machine drill tests this empirically; any such drift appears as a non-equivalent replay and fails closed.
  - The interpreter must be CPython 3.11.x on linux x86_64 (the lock's wheels).
  - The pip wheel download cache may be shared across runs. It cannot alter content, because `--require-hashes` verifies every wheel.

## Unchanged (scientific immutability)
- Challenger model, coefficients, harness, `manifest_v3` resolution, `evaluate_v3`, regimes, ranking/selection, equal-volume rules, the 2027 confirmatory regime, the prereg (blob identical), the shadow pin/tree/labels/pinned-artifact hashes, and the schedule guard.
- Files changed: `netrecord.py`, `shadow.py` (`run_pipeline` only), `runner.py` (fingerprint artifact), `verify_evidence.py` (artifact set and replay checks).
- Files added: `isolation.py`, `a1_drill.py`, locks, tests, this note, and the drill workflow.

## Before any reactivation (frozen acceptance bar)
1. Drill recorded in A and replayed in a different virgin B, with 0 misses, 0 unconsumed and exact hashes, provenance and timestamps.
2. Codex reproduces B independently.
3. SUPERCHAD acceptance.
4. Dispatcher changes on `claude/mlb-v3-ops` (bootstrap, if any).
5. A **new** Jacob activation record bound to the amended commit/tree.
6. Jacob's explicit trigger re-enable.

Local tests passing never reactivate V3.

## Results (drill — NOT A PROSPECTIVE UNIT)
**Record A** (builder container, isolated root, commit `e6fdee0230`, v3 tree `ed32e97b`):
- Live inputs from the 2026-10-05 NIGHT slate; 612 HTTP exchanges taped; guard 0 violations.
- HOME/pybaseball cache/TMP empty at start; python 3.11.15; kernel 6.18 (glibc 2.39).
- Lock `33034a7f…`; installed set `da98e4af…`; manifest `04f0b810…`; TSA 17:49:21Z.
- Attempt history is in `a1_drill/ATTEMPTS/`: attempt 1 hit ENOSPC; attempt 2 failed closed on the guard (font-dir probes + stdlib zip, then named).

**Replay B** ([run 37353256200](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/37353256200), GitHub-hosted Azure VM, python 3.11.16, kernel 6.17-azure, shallow clone):
- **PASS on every check.**
- **0 misses, 0 unconsumed.**
- Board / capture / schedule / manifest / tape hashes identical to A; provenance `7d3ebacd55`; both TSA tokens valid pregame.
- Same-container pre-check: also PASS.

**Shallow / full / `core.abbrev`:**
- `test_provenance_identical_for_shallow_full_and_any_abbrev` passes: full, shallow, abbrev 4 and abbrev 12 all give `HEAD[:10]`.
- The mutant shows real git gives 4 characters.
- Environment B itself ran from a shallow clone.

**Legacy units** (`a1_drill/LEGACY_CHECK.json`, clean A1 environment, read-only):
- 2026-10-03_DAY: 228 misses.
- 2026-10-03_NIGHT: 225 misses.
- 2026-10-04_DAY: 219 misses.
- 2026-10-04_NIGHT: 223 misses.
- Each has 1 unconsumed exchange, and **all files are unchanged**. They remain NOT reproducible; A1 does not bless them.

## Open items before reactivation (not solved by A1)
1. **Evidence storage size.**
   - A complete (cache-free) tape is about 257 MB per unit, versus the old cache-masked tapes of a few MB.
   - GitHub rejects files over 100 MB, so the drill stores byte-exact ≤95 MB parts bound by the whole-file sha256.
   - Two units a day would add roughly 0.5 GB/day to the evidence ref.
   - The storage design is a SUPERCHAD/Jacob decision. Options: chunked git as in the drill, an external immutable store bound by sha256, or Git LFS.
   - `runner.py` in prospective mode does **not** yet split tapes. The seal/push path must adopt the chosen design before any reactivation.
2. **Dispatcher.**
   - `claude/mlb-v3-ops` `v3_dispatch.py` pins `AUTH_COMMIT 504ca9cdb9` / tree `a7c9443`. It must be updated to the amended commit.
   - A **new Jacob activation record** is required; the old one cannot bind the new tree.
   - The dispatcher's own `requirements-v3.txt` bootstrap becomes moot, because each run builds its own locked venv.
3. **Codex** independent challenge of B (clean Linux path: rerun the workflow or reproduce its steps).
