# V3 reactivation package (prepared; NOT executed)

**Status: PREPARED ONLY.**
- No activation record is signed and no dispatcher is changed.
- The trigger `trig_011u98uXVuFEipPfbTT6KGur` stays disabled. No evidence-ref write; PR #220 untouched; no Cloudflare resource created; no request branch created.
- Nothing is bound to a future or guessed SHA. Every binding is computed by `prepare_activation.py` from the exact commit Codex passes.

## Preconditions (all required, in order)
1. **The FC-MLB-001B drill passes**, on the clean head:
   - environment A on a GitHub runner, then environment B by exact artifact id;
   - 0 misses, 0 unconsumed, literal payload identity, 0 forbidden reads;
   - the corruption mutant goes red, then the clean rerun goes green.
2. **Codex passes that exact head** on #91.
3. **SUPERCHAD accepts.**
4. **Jacob approves reactivation.**

**Storage (Jacob, 2026-10-06): R2 is NOT a prerequisite.**
- The initial tape backend is the **temporary** GitHub Actions artifact store (`STORAGE_TEMPORARY.md`). Jacob creates no Cloudflare resource now, and no payment method is needed.
- The exact artifact retention configuration (`retention-days` 90; measured effective retention 90 days, anchored to run start; warning at ≤ 45 days remaining, critical at ≤ 30) is **activation evidence**. It is bound in `REACTIVATION_BINDINGS.json` (`tape_storage`) and stated in Jacob's text.
- Artifacts are **not archival**. **Milestone FC-MLB-001C** (migrate every artifact-backed tape byte-for-byte to R2 or equivalent durable storage before retention threatens it) is a standing obligation, tracked on the ops queue and enforced by `retention_monitor.py` on every dispatcher tick.
- The reviewed head must contain the temporary store (`artifact_api.py`, `retention_monitor.py`, `tape_migration.py`). `prepare_activation.py` refuses a commit without it. Codex therefore reviews the 001B drill head **and** this prep delta, and activation binds the single commit Codex passes.

## Execution sequence after approval (each step is explicit; agents never post Jacob's authorization)
| # | Step | Who | Tool / artifact |
|---|---|---|---|
| 1 | Bind the reviewed head: `prepare_activation.py --repo R --implementation-commit H --codex-passed-commit H --out D` (H = the exact Codex-passed full SHA) | Claude | `REACTIVATION_BINDINGS.json`, `JACOB_AUTHORIZATION.txt` |
| 2 | Post the text of `JACOB_AUTHORIZATION.txt` on #91 **as Jacob** (first line `JACOB AUTHORIZATION: ALLOW`; no agent marker) | **Jacob** | Issue #91 comment id C |
| 3 | `prepare_activation.py … --jacob-comment-id C --activation-timestamp <now>`. The record is validated by the existing `activation.check_comment` against the fetched comment. | Claude | `ACTIVATION_C.json` (+ sha256), bound `v3_dispatch.py` |
| 4 | Append `ACTIVATION/ACTIVATION_C.json` to the evidence ref `claude/mlb-challenger-v3-evidence` (append-only commit) | Claude, after Jacob's go | evidence ref |
| 5 | Update `claude/mlb-v3-ops` with the bound `v3_dispatch.py` (diff = `v3_dispatch.post_001b.diff`, placeholders bound) and its README. Create the request branch `claude/mlb-v3-unit-requests` containing **only** the bound `.github/workflows/v3-unit-record.yml` (sha256 bound in the dispatcher). | Claude, after Jacob's go | ops branch, request branch |
| 6 | **No storage secrets.** The workflow uses its own `GITHUB_TOKEN` (`contents: write`, `issues: write`, `actions: read`) and the bound `retention-days` | — | — |
| 7 | Run the dispatcher with `--check` once. The preflight must pass: bound template, git, request-branch workflow sha = binding, runtime digest = binding; the activation verifier returns `(True, [])`; the plan and the retention report are listed. | Claude | dispatcher output |
| 8 | Optional integration proof: the 001B drill already exercised Actions-artifact upload and exact-id download. A temporary-store drill can repeat it with stage, confirm and verify-b, without a seal. | Claude, if Jacob wants | drill output |
| 9 | Update the trigger prompt (new comment id; 001B dispatcher), then **re-enable** `trig_011u98uXVuFEipPfbTT6KGur` | **Jacob** (explicit) | Routine |
| 10 | First unit: the workflow's `verify-b` job (fresh runner, exact artifact, full replay) passes, and the retention report shows the unit `OK` with its `expires_at`. Then a #91 status. | Claude | #91 |
| — | **Standing (FC-MLB-001C):** every dispatcher tick reports retention. `RETENTION_ALERT` (≤ 45 days) goes to #91 for Jacob. Migration runs only after Jacob sets up R2 (`STORAGE_DESIGN_B.md`), via `tape_migration.py` (byte-preserving, create-only record, sealed identity unchanged). | Claude + **Jacob** (R2 account) | #91, evidence ref `STORAGE_MIGRATIONS/` |

## Bindings (filled by `prepare_activation.py`; none is a guess)
| Binding | Source |
|---|---|
| Final implementation full SHA | `--implementation-commit` = `--codex-passed-commit` (must be equal, full 40 hex, present) |
| Implementation v3 tree | `git rev-parse <sha>:research/mlb_accuracy_challenger_20261001/v3` |
| Criteria | FC-MLB-001B, ops `e5475e66c0`, file sha256 `f8efd4b5…ec652` |
| Prereg | commit `15fb1d539c…`, sha256 `5eb56f28…3442`, blob `71e7d282` (refuses if changed) |
| Runtime digest | `runtime_image.MANIFEST_DIGEST` at the commit (currently `sha256:9fd63080…384a`, CPython 3.11.17) |
| Scientific payload spec | `payload.SPEC` (`fc-mlb-001b-payload-v1`) |
| Storage contract (durable target) | `tape_store.STORAGE_CONTRACT` (`fc-mlb-001b-r2-cas-1`), unchanged and preserved for FC-MLB-001C |
| **Initial tape store (temporary)** | `tape_store.GHA_KIND` / `GHA_CONTRACT` (`github-actions-artifact` / `fc-v3-gha-artifact-temp-1`); `retention-days` (90, `--artifact-retention-days`); `retention_monitor.WARN_DAYS` / `CRITICAL_DAYS` (45 / 30); measured retention evidence (artifact `11369873498`); migration milestone text |
| Record workflow | `v3-unit-record.yml` bound from `v3_unit_record.workflow.template.yml` (commit, tree, activation record and sha256, retention); its sha256 is bound into the dispatcher (`RECORD_WORKFLOW_SHA256`) |
| Dependency lock | sha256 of `shadow-requirements.lock` at the commit (currently `33034a7f…`) |
| Evidence branch | `claude/mlb-challenger-v3-evidence` |
| Dispatcher | `AUTH_COMMIT`, `AUTH_TREE`, `RUNTIME_DIGEST`, `ACTIVATION_ON_EVIDENCE`, `ACTIVATION_SHA256` |
| Activation record | `ACTIVATION_<C>.json`, with all `activation.REQUIRED_FIELDS` (pr_number 220, protocol V3) |
| Trigger | `trig_011u98uXVuFEipPfbTT6KGur`, cron `7 13-23,0-1 * * *` (currently disabled) |
| Rollback point | Disable the trigger. Restore the ops dispatcher to the head recorded at preparation (`edeec42926` today). The evidence head at preparation is recorded. Sealed units stay on the append-only chain. |

## Reactivation blocker audit
Question: *if the 001B drill passes and Codex says PASS, what would still prevent collecting the next legitimate V3 unit?*

| Item | Finding | Class |
|---|---|---|
| **Storage** | Prospective `runner._store_tape` accepts `actions-artifact` (temporary authoritative: stage → upload → read-back proof → seal) or `r2`. No R2 resource is needed now. | READY on the prep head (needs Codex review); **R2 = FC-MLB-001C** (Jacob account action later, not a blocker) |
| **Retention** | Artifacts expire 90 days after the run start. `retention_monitor.py` warns at ≤ 45 days remaining, is critical at ≤ 30, and alerts on #91. | READY; **2027 confirmatory units cannot reach their 2027-10-05 look without FC-MLB-001C** |
| **Secrets** | None for storage (the workflow `GITHUB_TOKEN`). | READY |
| **Dispatcher** | Pins `504ca9cdb9` / `a7c9443` and activation `5953838152`; installs host `time_machine` (moot under 001B). The template + diff are prepared. | **NEEDS POST-CODEX SHA BINDING** |
| **Activation checks** | `activation.py` binds the exact commit and tree plus Jacob's comment; a new record is required. | **NEEDS POST-CODEX SHA BINDING** + Jacob comment |
| **Trigger** | Disabled; its prompt names comment `5953838152`. | **NEEDS JACOB** (explicit re-enable after binding) |
| **Evidence refs** | Append-only; units are pushed by git from the runner session (worked Oct 3–4). Activation record path is to be appended. | READY (append at step 4) |
| **Runner permissions** | Recording runs in GitHub Actions: sudo, `unshare`/`chroot`/`strace` and the rootfs identity were proven on GitHub runners (001B CI run 37385877630 and drill A). | READY (drill A proves it again) |
| **Disk** | A fresh hosted runner per unit (about 14 GB free); the runtime is fetched by digest each run. | READY |
| **Dependencies** | Pipeline: the hash lock inside the pinned image (wheels resolved by exact hash). Host launcher: `requests` and four packages from the same lock, installed `--require-hashes` if missing. | READY |
| **Runtime image** | Fetched by digest (ECR first; Docker Hub is rate-limited here). Prefetched in preflight, outside any unit's budget. | READY |
| **Schedule** | `schedule_plan.plan` (55-minute budget, TBD excluded), hourly firing, 75-minute lead window. | READY |
| **Time budget** | The 001B shadow step adds a venv build in the sandbox and strace. The artifact upload and read-back of about 257 MB happen between stage and publish, before the `seal` guard. Runner queue latency eats into the 75-minute lead. | **MUST WAIT FOR DRILL** (A's timings and upload/download times) |
| **TSA** | freetsa and digicert reached from both environments in A1 and 001B; tokens verified from bytes. | READY |
| **Receipt path** | `runner._github_post_comment` uses the workflow `GITHUB_TOKEN` (`issues: write`), so the receipt author is `github-actions[bot]`. `seal.verify_receipts_raw` binds the body, the time and the issue, not the author. | READY — **Codex to confirm** the author is not a required binding |
| **GitHub permissions** | Release creation is not permitted for this session type (not needed). Evidence, ops and request-branch pushes work. Artifact *metadata* is readable from sessions; artifact *downloads* only inside Actions, so post-seal replay is the `verify-b` job. | READY |
| **Branch protection** | `claude/*` refs are unprotected; evidence pushes succeeded. | READY |
| **Network** | Hosted runners reach statsapi, the odds sources and the TSAs (proven in A1 and 001B drill A). R2 reachability matters only for FC-MLB-001C. | READY |
| **Grading hooks** | Collection does not need them. At evaluation time: G1 (persistence) is fixed on the prep branch; G2 (pinned-grader environment) is open until 2026-11-16. | READY for collection; G2 before the first look |
| **Repo-wide "Test Suite" CI** | Red on the V3 lineage before 001B as well (production root tests); unrelated to V3 collection. | READY (not a V3 gate) |

**Actual blockers: none found** beyond the classified items, assuming the drill and Codex pass. **R2 is no longer an initial blocker** (Jacob, 2026-10-06). The outstanding facts are:
1. the drill's A timing (the 001B shadow step plus the artifact upload and read-back must fit before the seal guard);
2. Codex review of the temporary-store delta on the prep branch, including whether the receipt author binding matters.

**Standing obligation (not a blocker): FC-MLB-001C, the R2 migration**, which needs Jacob's Cloudflare setup before any artifact-backed evidence reaches ≤ 45 days remaining.
