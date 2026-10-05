# FC-MLB-001A — V3 evidence-integrity amendment A1 (reproducibility / infrastructure only)

## ACCEPTANCE_CRITERIA
Frozen 2026-10-05, before any amendment code. Authority: SUPERCHAD disposition, with Jacob as final authority. Later changes need SUPERCHAD.

### 0. Problem (evidence on #91: 5971140470, 5983051660, 5985109220, 5998885845)
- The frozen shadow pin (`7d3ebacd55`) calls `pybaseball.cache.enable()` (`mlb_daily.py:35`, `grading_sources.py:76`).
- In record runs, responses served from the container's `~/.pybaseball/cache` never reach the sealed tape.
- Replay therefore depends on unsealed cache state:
  - with a virgin cache it misses 219–225 requests per unit;
  - with a warm cache it leaves recorded exchanges unconsumed.
- Secondary defects:
  - the board's `provenance.git_sha` depends on clone depth / `core.abbrev`;
  - the shadow pin's runtime packages are not installed deterministically.

### 1. Scope (ONLY these)
- **R1 Cache isolation (record):** every record run of the pinned pipeline gets a fresh, empty, per-run `HOME` and `XDG_CACHE_HOME`. No pybaseball (or any other home-directory) cache can pre-exist. The pinned tree is NOT modified; its tree id stays `02526a74f6`.
- **R2 Cache isolation (replay):** every replay, including in `verify_evidence`, starts from its own fresh, empty `HOME`/`XDG_CACHE_HOME`, independent of the recording container.
- **R3 Deterministic dependencies:**
  - The shadow pin's runtime packages come from a committed, exact-version, `--require-hashes` lock (`shadow-requirements.lock`).
  - They are installed into an isolated environment. Before every record and every replay, the installed set must equal the lock exactly, otherwise fail closed.
  - The interpreter version is recorded.
- **R4 Provenance independent of clone shape:**
  - The pinned pipeline's git calls run with an injected `core.abbrev=10` (environment `GIT_CONFIG_COUNT`/`KEY`/`VALUE`). No repo or user config is relied on.
  - The board must carry `git_sha == SHADOW_PIN[:10]` from both shallow and full clones.
- **R5 No hidden filesystem state:**
  - The record run's environment fingerprint is sealed in the unit as new provenance fields:
    - lock sha256;
    - installed-set sha256;
    - python version;
    - the fact that the HOME/XDG cache dirs were empty at start;
    - the injected git config.
  - Any file the pipeline reads outside the pinned tree, the sealed overlay and the isolated HOME is a failure. This is checked by the existing drill trace method.
- **R6 Cross-container independence:** record in container A, then replay exactly in a different virgin container B (§3).

### 2. Must NOT change
- Challenger model, coefficients, ranking policy, selection logic, equal-volume rules.
- V3 hypotheses and the prereg (`15fb1d539c`; the prereg blob stays byte-identical).
- The confirmatory 2027 regime, the shadow pin or its tree, the shadow labels, the gates.
- Existing seals, `CHAIN.json`, receipts and evidence history: no rewrite, rerun or backfill.
- The four existing units keep their #91/5998885845 classification. The amended verifier must still report them as not reproducible; it may not "repair" them.
- No outcome reads or scoring.

### 3. Acceptance bar (all required before any reactivation)
1. Unit tests for R1–R5, including mutation tests:
   - a pre-populated cache is detected or ignored;
   - a lock mismatch fails closed;
   - abbrev-7 shallow clone provenance is rejected without R4 and accepted with it;
   - legacy (pre-A1) units still verify as before.
2. **Drill:** a fresh container A records one drill/shadow unit with the amended code (not on the confirmatory chain).
3. A **different virgin container B** replays it from sealed artifacts alone with:
   - 0 replay misses and 0 unconsumed exchanges;
   - an exact shadow-board hash;
   - exact manifest and capture validation (`verify_unit` = VERIFIED);
   - exact provenance identity;
   - a valid receipt and timestamps;
   - no dependence on prior cache state (B's HOME empty at start).
4. **Codex** independently executes or reproduces the container-B verification and reports it on #91.
5. The amended implementation is a new commit and tree. It needs a **new Jacob activation record** bound to it.
   - The old activation (`504ca9cdb9` / `a7c9443`) does not carry over.
   - The trigger stays disabled until Jacob re-enables it explicitly.
   - Local tests passing never reactivate it.

### 4. Evidence
- Amendment code goes on a new branch from `504ca9cdb9` (V3 implementation lineage): `claude/mlb-v3-amendment-a1-20261005`, with `AMENDMENT_A1.md` in `v3/` documenting R1–R6.
- Drill artifacts and the B-container verification go in that branch's drill directory.
- `v3_dispatch.py` changes (dependency bootstrap, abbrev) go on `claude/mlb-v3-ops` only after Codex's challenge and Jacob's go.

### 5. Stop
- If any R requires changing the pinned tree, the prereg, or challenger science, STOP and report.
- No V3 reactivation and no new MLB predictive experiment under this task.

## BUILDER_NOTES
(owner only)
- Plan:
  - (a) `shadow.run_pipeline` gains an isolated env (per-run temp HOME/XDG, injected git config), used identically for record and replay;
  - (b) `shadow-requirements.lock` (pip-compile with hashes from the pin's `requirements.txt` + scipy) and a verify-installed-set check;
  - (c) seal provenance fields + verifier checks (versioned; legacy units keep the old path);
  - (d) tests + mutants;
  - (e) container A record drill;
  - (f) container B virgin replay;
  - (g) Codex challenge;
  - (h) Jacob activation.

## LOG
- 2026-10-05 criteria frozen (V3 hold 16:39:32Z; #91/5998885845).
- 2026-10-05 implemented @ ca08763f6d; drill record A (e6fdee0230) + env B GitHub Actions 37353256200 PASS; legacy units unchanged/not reproducible; -> READY_FOR_CHALLENGE. V3 held.
- 2026-10-05 a1-2 repair @ 0478658601 (Codex final audit): CI gate fixed (mutant run 37370057443 FAIL; clean run 37370084563 integrity PASS, frozen_conformance FAIL); R4 frozen injection; R5 STOP (frozen criterion overbroad); literal board identity STOP (4 replay-clock fields); storage design only -> BLOCKED on SUPERCHAD/Jacob criterion decisions. V3 held.
