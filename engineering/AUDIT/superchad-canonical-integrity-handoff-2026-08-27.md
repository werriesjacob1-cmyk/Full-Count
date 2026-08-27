# SUPERCHAD Canonical Integrity Handoff — 2026-08-27

Status: **LIVING HANDOFF — QUARANTINE BRANCH — STOP BEFORE MERGE**

## Authority / scope

- Repository: `werriesjacob1-cmyk/Full-Count`
- Branch: `superchad/canonical-integrity-safe-01`
- Exact base: `a3017bce8a9dd41919f546a9e011818c3bf68c15`
- Base branch: `claude/canonical-rebuild-and-accuracy-foundation-01`
- This branch is future recovery/integrity work only.
- The active canonical run, its pinned scientific worktree, and
  `canonical-durable-checkpoints` were not modified.
- No merge, deploy, restart, resume, repin, or production authorization.

## Truth standard

Everything below is source-level implementation unless explicitly marked
otherwise.

- VERIFIED-REPO: inspected directly in GitHub.
- SUPERCHAD-IMPLEMENTED: code/test committed to this quarantine branch.
- UNKNOWN-RUNTIME: not executed in Claude's real repository runtime.
- UNKNOWN-CI: no exact-SHA CI run was triggered for this branch.

Claude must independently rerun, red-team, and classify every item before use.

# Changes

## 1. Identity verification happens before recovery writes

Commits:
- `4412ebc464833603b4b86f75d787ff2a1c763e35`
- `001f898e5f87a96c1b400d8e341371012511f6df`

Pre-fix VERIFIED-REPO defect:
`restore_from_durable()` documented "nothing is written until identity passes"
but created the run/checkpoint directory and, on a fresh restore, wrote the
remote manifest before `assert_identity_compatible()`.

Change:
- durable manifest is loaded into memory first;
- identity is verified;
- only after compatibility passes is the local directory/manifest written.

Adversarial test:
- poison durable index identity;
- assert `IdentityMismatch`;
- assert no local run directory was created.

## 2. Local recovery skip verifies BOTH row and metadata bytes

Commits:
- `015fc2116da651701ef919f4229f245a2cd96c14`
- `034b90e80104c131db2e6f0d3bbc19ab4d7fd7ac`

Pre-fix VERIFIED-REPO defect:
a locally present date could be skipped when row SHA matched even if
`.meta.json` was corrupted or belonged to different checkpoint metadata.

Change:
- with verification enabled, local reuse requires non-null durable
  `data_sha256` AND `meta_sha256`, both matching;
- otherwise recovery falls through to verified remote restoration.

Test:
- corrupt only local meta;
- verify the date is restored rather than skipped;
- verify repaired meta SHA matches durable ledger.

## 3. Resume identity now includes evidence regime and candidate identity fields

Commits:
- `3efd6b929e44ab419d6c6d6d103a7fcfd57bdcf1`
- `ee8f2e0f98ccc1b7ec8b950a1f29181e23c548c1`

Pre-fix VERIFIED-REPO defect:
`build_durable_index()` recorded `evidence_regime` and
`candidate_identity_fields` inside durable identity, but
`assert_identity_compatible()` did not compare them.

Change:
both fields now participate in the resume compatibility gate.

Tests:
- changed evidence regime -> `IdentityMismatch`;
- changed candidate identity fields -> `IdentityMismatch`.

## 4. Required durable staging failures are fail-closed

Commits:
- `cec3bb947efd377289d21b6a2c2e33a2e1d13bf7`
- `bbc76204701f038904dbd9072fa9a09458c2aa77`

Pre-fix VERIFIED-REPO defect:
`stage_blob()` returned `None` on hash/update-index failure but callers ignored
that result and could continue toward a partial durable commit.

Change:
- every required meta/rows/manifest/index stage is checked;
- failure aborts before commit/push;
- a date with meta but missing required rows file fails instead of being
  published incompletely.

Test:
- monkeypatch git update-index to fail;
- assert no push;
- assert explicit required-stage failure reason.

## 5. Existing durable dates are checksum-verified, not path-trusted

Commits:
- `e2c72af6597ef2c4db4989c85f734d4116a2010f`
- `25ca4d981498a661f494b0218d489fa5c97c8bc7`

Pre-fix VERIFIED-REPO defect:
push code skipped a date whenever remote meta + row paths existed. The
docstring claimed "matching checksum", but no checksum comparison occurred.

Change:
- parent durable index ledger is loaded once;
- an already-present date is skipped only if local meta/data SHAs match the
  durable ledger;
- missing ledger entry or byte conflict fails closed;
- conflicting durable bytes are never silently overwritten.

Test:
- tamper local row after durable push;
- second push fails closed;
- remote restore repairs the local row;
- repaired SHA equals durable ledger.

Review risk:
Claude should specifically inspect concurrent/stale remote-ref behavior. This
patch verifies the parent it actually resolved; it does not claim to solve
multi-writer durable branch concurrency.

## 6. Environment drift is fail-closed by default

Commits:
- `d0f4c0940bab2f2220ed911863f84acbb80289ca`
- `033dfb1e72ba1dff192bb6ee4b55b02120799c14`

Pre-fix VERIFIED-REPO defect:
`assert_identity_compatible()` and `restore_from_durable()` defaulted
`allow_environment_drift=True`, so a materially changed Python/package
environment could be mixed into the same canonical run without explicit opt-in.

Change:
- default is now False;
- explicit `allow_environment_drift=True` remains available;
- returned environment difference report remains visible.

Test:
- monkeypatch current environment identity to a different Python version;
- default call must fail;
- explicit override succeeds while report still says incompatible.

Review risk:
the environment fingerprint includes broad pip-freeze identity. Claude should
decide whether fail-closed on *any* package drift is appropriately strict or
whether only critical-package/Python drift should block while unrelated package
drift remains descriptive. Do not weaken silently.

## 7. Fresh-clone CLI recovery ordering

Commits:
- `4c92285e7ebe09a9b418a2986c6ec681775d21ca`
- `15f315652fa37b33c2ed778ca84747d6a9bee9e8`

Pre-fix VERIFIED-REPO defect:
`canonical_run.py --run-id ... --resume-from-remote` called
`load_manifest()` before remote restore. On a truly fresh/reclaimed clone the
local manifest does not exist, so the documented CLI recovery path could not
start.

Change:
- adds `prepare_existing_run()`;
- when resume is requested and local manifest is absent, fetch durable branch
  and restore first;
- then load restored manifest;
- generation still goes through normal code-SHA identity verification later;
  this helper does not waive the pinned-science contract.

Tests:
- missing local manifest + successful remote fetch/restore -> manifest becomes
  loadable;
- failed durable fetch -> recovery aborts before restore.

# Verified branch scope so far

At the checkpoint immediately before this handoff was written, cumulative diff
from the exact base touched only:

- `backtest/canonical_durability.py`
- `backtest/canonical_run.py`
- `test_canonical_durability.py`
- `test_canonical_run.py`

plus this handoff once committed.

No scoring, probability, calibration, recommendation, settlement, live
publication, frontend, main-branch, or durable-data files were changed.

## 8. Resume script exact-target / idempotence hardening

Commits:
- `d62663b2378cd00140780b7ecd975d29a3380f19`
- `79bc066aa392a454631021c581ffc8377ccd151d`

Pre-fix VERIFIED-REPO defects:
- no-argument mode selected the "newest" durable run, which can be a proof/test
  run rather than the intended long-running artifact;
- script could launch a second generator while one already owned the run, then
  generic `pgrep` could find the original PID and falsely report the failed
  child as "resumed";
- start/end/weather/cache were partly hard-coded instead of derived from the
  durable run contract;
- the restore wrapper created the local checkpoint directory before calling the
  now fail-closed restore function;
- proxy durable push for an old pinned SHA could erase existing lineage/cache
  metadata by omitting them.

Changes:
- exact run_id is mandatory (argument or `FC_CANONICAL_RUN_ID`);
- preflight no-ops if the exact run is already alive in the container;
- post-launch verification checks the actual `$BGPID`, not a generic matching
  process;
- start/end/weather/cache/sleep/no-bullpen are derived from durable
  manifest/index evidence;
- no local directory is pre-created before restore identity verification;
- proxy push preserves existing durable lineage/cache metadata while recording
  the current environment.

Verification:
- VERIFIED-REPO: full script re-fetched after patch.
- SUPERCHAD runtime syntax check: exact branch script was copied byte-for-byte
  from GitHub into an isolated local container and `bash -n` returned
  `BASH_SYNTAX_OK`.
- UNKNOWN: full end-to-end recovery execution in the real Claude environment.
- This is **not** authorization to schedule auto-resume.

Review risks:
- Claude must still adversarially test simultaneous invocations, healthy-owner
  no-op, dead-owner exact single launch, and completed-run no-op.
- Current owner preflight is process/cmdline based; the canonical lock remains
  the generation-side authority.
- External scheduled auto-resume remains blocked until Claude independently
  approves these semantics.

## 9. CI-discovered mock-store provenance regression

Commits:
- `1fbe4dc7da74fd5730b59bafc86a5fe2baf80fbb`
- `40c650becc220014ddb09797f5ab1abf784adee6`

Exact-head CI at `908a95cdeee46dac4658650edc01f4c73813ea58`
failed 15 canonical-run tests with the same traceback:

`statcast_lineage_from_cache_report(...): int(year) -> TypeError on Mock`.

Cause:
many canonical-run tests intentionally use lightweight/mock stores. A Mock
`cache_report` attribute is truthy and supports `.get()`, so the new
provenance helper treated it like a real validation mapping and tried to coerce
mock scientific identity values.

Fix:
- provenance is now accepted only from a real `dict` validation report;
- non-mapping/mock-like reports mean provenance absent, not fabricated;
- regression check explicitly passes object-like report/year/through and
  requires None.

This was a real SUPERCHAD-caused compatibility defect caught by GitHub CI,
not hidden. Claude should verify the fix and exact-head CI independently.

# Important unresolved canonical findings

These remain unresolved unless later sections explicitly record fixes:

1. The real canonical CLI/run path still does not populate structured
   `source_lineage` into durable checkpoints. The active real durable run had
   empty lineage at last independent inspection.
2. A nonempty Statcast-only lineage would still be incomplete if other mutable
   pregame source inputs are not provenance-bound. Do not turn "nonempty" into
   "certified".
3. `cache_mode` is recorded separately and is not yet part of
   `assert_identity_compatible()`'s manifest comparison.
4. Concurrent durable writers / stale remote-tracking refs need an explicit
   audit before claiming multi-writer safety.
5. The active artifact remains an evidence artifact, not automatically a
   scientifically certified canonical dataset.

# Claude mandatory independent review

When Claude returns:

1. fetch this branch fresh;
2. verify merge-base against the then-current research branch;
3. inspect every cumulative diff;
4. run `test_canonical_durability.py` and `test_canonical_run.py` on the
   branch;
5. where practical, run new adversarial tests against the pre-fix base and prove
   they detect the original defect;
6. inspect all recovery callers for compatibility with the stricter environment
   default;
7. red-team path-existence/checksum logic for stale remote refs and concurrent
   pushes;
8. verify no change touched generation science;
9. classify each section:
   - ACCEPT
   - ACCEPT WITH FIX
   - REJECT
   - CANNOT VERIFY — exact reason
10. do not merge or use to resume the live run without explicit user
    authorization.

North Star reminder: this branch is an ACCURACY ENABLER. Once canonical
integrity is sufficient, stop building durability machinery and return to
equal-operational-volume realized winner experiments.
