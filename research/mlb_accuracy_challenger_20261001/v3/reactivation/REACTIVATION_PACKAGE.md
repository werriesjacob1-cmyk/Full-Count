# V3 reactivation package (prepared; NOT executed)

**Status: PREPARED ONLY.**
- No activation record is signed and no dispatcher is changed.
- The trigger `trig_011u98uXVuFEipPfbTT6KGur` stays disabled. No evidence-ref write; PR #220 untouched; no Cloudflare resource created.
- Nothing is bound to a future or guessed SHA. Every binding is computed by `prepare_activation.py` from the exact commit Codex passes.

## Preconditions (all required, in order)
1. **The FC-MLB-001B drill passes**, on the clean head:
   - environment A on a GitHub runner, then environment B by exact artifact id;
   - 0 misses, 0 unconsumed, literal payload identity, 0 forbidden reads;
   - the corruption mutant goes red, then the clean rerun goes green.
2. **Codex passes that exact head** on #91.
3. **SUPERCHAD accepts.**
4. **Jacob** completes the R2 account actions (`STORAGE_DESIGN_B.md`, "Exact Cloudflare setup") and runs its one-time capability check.
5. **Jacob approves reactivation.**

## Execution sequence after approval (each step is explicit; agents never post Jacob's authorization)
| # | Step | Who | Tool / artifact |
|---|---|---|---|
| 1 | Bind the reviewed head: `prepare_activation.py --repo R --implementation-commit H --codex-passed-commit H --out D` (H = the exact Codex-passed full SHA) | Claude | `REACTIVATION_BINDINGS.json`, `JACOB_AUTHORIZATION.txt` |
| 2 | Post the text of `JACOB_AUTHORIZATION.txt` on #91 **as Jacob** (first line `JACOB AUTHORIZATION: ALLOW`; no agent marker) | **Jacob** | Issue #91 comment id C |
| 3 | `prepare_activation.py … --jacob-comment-id C --activation-timestamp <now>`. The record is validated by the existing `activation.check_comment` against the fetched comment. | Claude | `ACTIVATION_C.json` (+ sha256), bound `v3_dispatch.py` |
| 4 | Append `ACTIVATION/ACTIVATION_C.json` to the evidence ref `claude/mlb-challenger-v3-evidence` (append-only commit) | Claude, after Jacob's go | evidence ref |
| 5 | Update `claude/mlb-v3-ops` with the bound `v3_dispatch.py` (diff = `v3_dispatch.post_001b.diff`, placeholders bound) and its README | Claude, after Jacob's go | ops branch |
| 6 | Recording-environment secrets: `V3B_TAPE_STORE=r2`, endpoint, bucket and token (`STORAGE_DESIGN_B.md`) | **Jacob** | environment settings |
| 7 | Run the dispatcher with `--check` once in the runner session. The 001B preflight must pass: root, tools, ≥ 4 GB free, R2 configured, runtime digest = binding, wheels; the activation verifier returns `(True, [])`; the plan is listed. | Claude | dispatcher output |
| 8 | Optional integration proof: one `runner.py --mode drill` with `V3B_TAPE_STORE=r2`. This exercises real R2 put / read-back / verify without a prospective seal. | Claude, if Jacob wants | drill output |
| 9 | Update the trigger prompt (new comment id; 001B dispatcher), then **re-enable** `trig_011u98uXVuFEipPfbTT6KGur` | **Jacob** (explicit) | Routine |
| 10 | First unit: post-dispatch verification (`--verify-sealed`) and a #91 status | Claude | #91 |

## Bindings (filled by `prepare_activation.py`; none is a guess)
| Binding | Source |
|---|---|
| Final implementation full SHA | `--implementation-commit` = `--codex-passed-commit` (must be equal, full 40 hex, present) |
| Implementation v3 tree | `git rev-parse <sha>:research/mlb_accuracy_challenger_20261001/v3` |
| Criteria | FC-MLB-001B, ops `e5475e66c0`, file sha256 `f8efd4b5…ec652` |
| Prereg | commit `15fb1d539c…`, sha256 `5eb56f28…3442`, blob `71e7d282` (refuses if changed) |
| Runtime digest | `runtime_image.MANIFEST_DIGEST` at the commit (currently `sha256:9fd63080…384a`, CPython 3.11.17) |
| Scientific payload spec | `payload.SPEC` (`fc-mlb-001b-payload-v1`) |
| Storage contract | `tape_store.STORAGE_CONTRACT` (`fc-mlb-001b-r2-cas-1`) |
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
| **Storage** | Prospective `runner._store_tape` requires `V3B_TAPE_STORE=r2` and refuses anything else (MISS UNIT). No R2 bucket, lock or token exists. | **NEEDS JACOB ACCOUNT ACTION** (the only gating item besides the bindings) |
| **Secrets** | `V3B_R2_*` in the recording environment, plus the verifier read path (Object Read token or public read base) | **NEEDS JACOB ACCOUNT ACTION** |
| **Dispatcher** | Pins `504ca9cdb9` / `a7c9443` and activation `5953838152`; installs host `time_machine` (moot under 001B). The template + diff are prepared. | **NEEDS POST-CODEX SHA BINDING** |
| **Activation checks** | `activation.py` binds the exact commit and tree plus Jacob's comment; a new record is required. | **NEEDS POST-CODEX SHA BINDING** + Jacob comment |
| **Trigger** | Disabled; its prompt names comment `5953838152`. | **NEEDS JACOB** (explicit re-enable after binding) |
| **Evidence refs** | Append-only; units are pushed by git from the runner session (worked Oct 3–4). Activation record path is to be appended. | READY (append at step 4) |
| **Runner permissions** | Root, `unshare`/`mount`/`chroot`/`strace` present in this session type (proven here). The 001B preflight refuses otherwise. | READY (checked by step 7 `--check`) |
| **Disk** | About 1.6 GB of cached runtime plus about 0.8 GB per run. The preflight requires ≥ 4 GB free. | READY (checked by step 7) |
| **Dependencies** | Pipeline: the hash lock inside the pinned image (wheels resolved by exact hash). Host launcher: `requests` and four packages from the same lock, installed `--require-hashes` if missing. | READY |
| **Runtime image** | Fetched by digest (ECR first; Docker Hub is rate-limited here). Prefetched in preflight, outside any unit's budget. | READY |
| **Schedule** | `schedule_plan.plan` (55-minute budget, TBD excluded), hourly firing, 75-minute lead window. | READY |
| **Time budget** | The 001B shadow step adds: venv build in the sandbox, strace, and the R2 put plus a 257 MB read-back, all inside the 1200 s `shadow` budget. | **MUST WAIT FOR DRILL** (A's timings) + step 8 R2 timing |
| **TSA** | freetsa and digicert reached from both environments in A1 and 001B; tokens verified from bytes. | READY |
| **Receipt path** | `runner._github_post_comment` needs `GITHUB_TOKEN`/`GH_TOKEN` in the runner session; it worked for Oct 3–4. | READY (unchanged) |
| **GitHub permissions** | Release creation is not permitted for this session type (not needed). Evidence and ops pushes work. Actions artifacts are drill-only. | READY |
| **Branch protection** | `claude/*` refs are unprotected; evidence pushes succeeded. | READY |
| **Network** | The R2 endpoint domain is reachable through the proxy (TLS to Cloudflare's R2 edge); a real account endpoint is unproven until setup. | NEEDS JACOB ACCOUNT ACTION (step 7 of the setup check) |
| **Grading hooks** | Collection does not need them. At evaluation time: G1 (persistence) is fixed on the prep branch; G2 (pinned-grader environment) is open until 2026-11-16. | READY for collection; G2 before the first look |
| **Repo-wide "Test Suite" CI** | Red on the V3 lineage before 001B as well (production root tests); unrelated to V3 collection. | READY (not a V3 gate) |

**Actual blockers: none found** beyond the classified items, assuming the drill and Codex pass. The two genuinely outstanding external facts are:
1. Jacob's R2 account actions, with their capability check;
2. the drill's A timing, proving the heavier 001B shadow step fits its 1200 s budget.
