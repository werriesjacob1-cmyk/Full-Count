# SUPERCHAD Overnight Integrity Handoff — 2026-08-27

Status: **LIVING HANDOFF — STOP BEFORE MERGE**

Purpose: give Claude Code an exact, adversarially useful record of everything
SUPERCHAD changed while Claude usage was unavailable, so Claude can independently
reproduce, challenge, test, and reject any part of the work before promotion.

## Authority and branch isolation

- Repository: `werriesjacob1-cmyk/Full-Count`
- Working branch: `superchad/overnight-integrity-safe-02`
- Exact base at branch creation:
  `a3017bce8a9dd41919f546a9e011818c3bf68c15`
- Base branch:
  `claude/canonical-rebuild-and-accuracy-foundation-01`
- No merge authorization is implied.
- No deployment authorization is implied.
- Claude must independently review this branch before any cherry-pick, PR, merge,
  promotion, or use as production-grade research evidence.

## Hard boundaries used by SUPERCHAD

SUPERCHAD may modify only research-evaluation / research-integrity tooling and
tests on this isolated branch.

SUPERCHAD must not:
- modify `main`;
- modify the live canonical pinned worktree;
- mutate `canonical-durable-checkpoints` or `canonical-run-manifests`;
- change probabilities, model features, fitted parameters, calibrators, scoring
  weights, recommendation thresholds, recommendation policy, grading,
  settlement, publication lifecycle, or live writer behavior;
- trigger production workflows or deployments;
- merge, rebase, force-push, delete branches, or rewrite history;
- call its own work merge-safe or promotion-safe.

## Why this branch exists

The overnight audit found that the research framework could still produce a
plausible-looking promotion result while leaving several integrity properties
under-enforced. The target is **not to improve a model**. The target is to make
future accuracy experiments harder to fool.

Candidate workstreams, each to be implemented and reviewed separately:

1. **Promotion-grade dataset identity**
   - Current concern: downstream code can accept checksum-shaped metadata without
     proving that it came from the actual locked manifest/artifact.
   - Desired property: promotion-grade mode verifies the real manifest and
     recomputes the artifact identity; exploratory/legacy replay remains
     backward-compatible.

2. **Candidate-content identity**
   - Current concern: identity-only population fingerprints can remain unchanged
     while scores/features/ranking inputs attached to the same candidate IDs
     change.
   - Desired property: preserve identity fingerprint and add a separate,
     deterministic content/ranking-input fingerprint.

3. **Ranking-input identity**
   - Current concern: an experiment manifest may record policy names/versions
     without binding the exact fields that determined ordering.
   - Desired property: promotion-grade selection policies explicitly declare and
     fingerprint ranking inputs.

4. **Operational equal volume**
   - Current concern: aggregate equal N across a multi-date corpus can let a
     challenger shift opportunity away from difficult slates and toward easier
     ones.
   - Desired property: promotion-grade selector tests preserve a predeclared
     per-date/slate allocation, not merely total N.

5. **Missing-outcome denominator integrity**
   - Current concern: outcome exclusion can create different effective
     denominators for champion and challenger.
   - Desired property: fail closed on asymmetric missing outcomes; never use
     outcome-aware refill to repair the comparison.

6. **Strict Best Expression**
   - Current concern: soft suppression may allow redundant expressions to flow
     back into top N when refill is impossible while still being described as a
     diversification test.
   - Desired property: exploratory soft behavior may remain available, but a
     promotion-grade diversification claim must fail or explicitly report that
     the strict same-slate refill contract was not satisfied.

## Canonical work deliberately NOT touched

The active canonical rebuild is a separate estate. At the time this handoff was
created, the durable artifact still showed:
- run id: `canonical-20260827T141713Z-d6a1050f`
- pinned scientific SHA:
  `68b663a38a1fa1ba8d3cd96a88613ce1e73483c6`
- durability operating, but
- `source_lineage: []`
- `source_lineage_fingerprint: null`

Therefore durability does not imply canonical scientific certification.
SUPERCHAD will not patch source-lineage/cache/resume behavior while the run is
active.

## Required evidence for every SUPERCHAD change

For each workstream, append below:
- exact pre-change defect;
- affected source/function;
- minimal patch;
- adversarial regression test;
- exact commit SHA;
- tests that actually ran;
- CI status if available;
- known compatibility risks;
- anything not independently verified.

### Truth labels

Use:
- VERIFIED-REPO
- VERIFIED-ARTIFACT
- VERIFIED-CI
- SUPERCHAD-IMPLEMENTED
- SUPERCHAD-ASSERTED
- UNKNOWN

"Implemented" is not "tested." "Tested locally" is not "CI verified."
"CI verified" is not "methodologically approved."

---

# Change log

## Workstream 1 — Promotion-grade dataset identity

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO at base `a3017bce8a9dd41919f546a9e011818c3bf68c15`:
`backtest/equal_volume.py::EqualVolumeExperiment._assert_promotion_grade_dataset()`
accepted promotion-grade evidence when `population.dataset_identity` merely
contained truthy `artifact_sha256` and `artifact_row_count` keys. It did not
load the Accuracy Lab manifest, verify that the metadata came from that
manifest, or recompute the artifact identity from the file on disk.

That meant a caller could construct a plausible-looking dict and satisfy
`promotion_grade=True` without proving which artifact was actually evaluated.

### Implementation

1. `9853ffc156cdf79496e06af95207ce83dec4358b`
   - `accuracy_lab.py`
   - added `verify_promotion_grade_dataset_identity(identity)`
   - promotion identity must contain `manifest_path`
   - verifier reloads the real manifest
   - applies the existing strong-manifest gate
   - rejects duplicated caller metadata that disagrees with the source manifest
   - resolves the manifest's recorded `rows_path`
   - calls the existing `lock_holdout(..., require_strong_dataset_identity=True)`
     path to recompute/compare real artifact SHA, row count, distinct-date count,
     and date range
   - returns the verified manifest identity plus a checksum of the manifest
   - existing-manifest path is read-only; no migration or rewrite

2. `49f8ae2689684a527bc64f0c9a17a64d69d9fc5c`
   - `test_accuracy_lab.py`
   - adversarial cases:
     - real manifest + unchanged artifact verifies
     - fake checksum metadata cannot piggyback on a real manifest
     - artifact changed after lock fails closed

3. `e44070a45254f32d517e6d8e3b6d98a59f0d272b`
   - `backtest/equal_volume.py`
   - promotion-grade mode now delegates to the Accuracy Lab real-artifact
     verifier instead of checking two arbitrary dict keys
   - verified identity is retained in the integrity report
   - verified identity is bound into `experiment_manifest_id`
   - exploratory mode remains permissive and does not require a manifest

4. `064260f1f99650fe0673c4bb8e9ee46e18004141`
   - `test_equal_volume.py`
   - replaces the old synthetic "strong identity" promotion fixture with a real
     temporary Accuracy Lab manifest + artifact
   - explicitly tests the pre-fix cheat: checksum + row count without a
     manifest must now fail
   - explicitly tests post-lock artifact drift

5. `55f2d3c825cde9002128bb154cbc60af45faf8e2`
   - `test_best_expression.py`
   - its integration fixture no longer falsely claims promotion-grade dataset
     provenance; it remains an exploratory exact-volume integration test
   - promotion-grade provenance itself is tested in `test_equal_volume.py`

### Scope audit

VERIFIED-REPO cumulative diff from the exact branch base through
`55f2d3c825cde9002128bb154cbc60af45faf8e2` touches only:
- `accuracy_lab.py`
- `backtest/equal_volume.py`
- `test_accuracy_lab.py`
- `test_equal_volume.py`
- `test_best_expression.py`
- this audit handoff

No model, recommendation, live, settlement, grading, canonical generation,
resume, durable checkpoint, workflow, or frontend source was changed.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: adversarial regression tests are committed.
- UNKNOWN: targeted Python tests have **not** been executed by SUPERCHAD in the
  real repository runtime; SUPERCHAD's local container cannot resolve GitHub to
  clone the repository, and no test workflow was triggered.
- UNKNOWN: no exact-SHA GitHub CI/status exists for this isolated branch at the
  time of this entry.
- VERIFIED-REPO: source was re-fetched after the commits and the resulting
  implementation/test blocks were re-read.
- Claude must run these tests independently before accepting the work.

### Compatibility / review risks

- **Intentional fail-closed break:** any caller using
  `promotion_grade=True` with checksum-shaped metadata but no
  `manifest_path` now fails. Claude should inventory all such callers and
  decide whether they should be upgraded to a real manifest or remain
  exploratory.
- The new verifier deliberately reuses `lock_holdout()` rather than creating a
  second artifact-validation implementation. Claude should verify there is no
  unexpected filesystem mutation on the existing-manifest path.
- Claude should independently verify that binding the verified manifest identity
  into `experiment_manifest_id` is sufficient and does not make the ID depend
  on irrelevant mutable presentation fields.
- No claim is made that this solves candidate-content identity, ranking-input
  identity, per-slate equal volume, missing outcomes, or Best Expression strict
  refill. Those remain separate workstreams.

## Workstream 2 — Candidate-content identity

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO at base and through Workstream 1:
`EligiblePopulation.fingerprint` hashed only the five candidate identity fields
(`date, game_pk, player_id, prop_type, line`). Two populations containing the
same candidate keys but different scores, probabilities, signals, or other
selector-relevant row content therefore produced the same population
fingerprint.

That is insufficient evidence for a selector comparison: candidate membership
can be unchanged while the information used to rank those candidates changes.

### Implementation

1. `e112f6185cdd20be24d23bb21e7e02d42ed5f10e`
   - `backtest/equal_volume.py`
   - preserves the existing identity-only fingerprint
   - adds deterministic `content_fingerprint` over candidate rows sorted by
     candidate identity
   - excludes only `outcome`, so the realized answer used for grading does not
     enter the preselection content identity
   - exposes the content fingerprint in population description and integrity
     output
   - binds it into `experiment_manifest_id`

2. `da49e1b6e6cdc2e624e0ffbb6188f49fdd7d73d8`
   - `test_equal_volume.py`
   - verifies identity/content fingerprints are input-order independent
   - verifies same candidate IDs + changed score keep the identity fingerprint
     but change the content fingerprint
   - verifies changing only realized `outcome` does not change preselection
     content identity

3. `1c6cfdf90501b8445f8e838386ed1f68d36cbef3`
   - `test_equal_volume.py`
   - verifies `experiment_manifest_id` changes when candidate content changes,
     so the new fingerprint is not merely decorative report metadata

### Scope / methodology notes

- This work does **not** yet claim that every field in the content fingerprint
  is a legitimate ranking input. It intentionally binds the candidate payload
  broadly, while Workstream 3 will separately bind the exact declared ranking
  inputs.
- `outcome` is excluded to avoid putting the answer used for grading into the
  preselection identity.
- Other historical/postgame fields that may exist on a row remain part of the
  broad content fingerprint. Claude should review whether that is desirable
  strictness or whether a narrower preselection-content contract is preferable.
- No selector ordering, probability, eligibility, or realized result was
  changed by this workstream.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: regression tests are committed.
- UNKNOWN: tests have not run in the real repository runtime.
- UNKNOWN: no exact-SHA CI is attached to the isolated branch.
- VERIFIED-REPO: implementation and test source were re-fetched and inspected
  after commit.

---

# Claude independent review checklist

When Claude usage returns, **do not trust this handoff as proof that the work is
correct.** Use it as a map.

Claude should:

1. Fetch `superchad/overnight-integrity-safe-02` fresh.
2. Resolve its exact base/merge-base against the then-current research branch.
3. Read the cumulative source diff, not only commit messages.
4. Reproduce each claimed pre-fix failure independently.
5. Run each new regression test against:
   - the pre-fix base, where practical, to prove the test actually detects the
     defect;
   - the SUPERCHAD branch, to prove the fix closes it.
6. Inspect for accidental changes to:
   - model/scoring/calibration/recommendation behavior;
   - production/live/settlement behavior;
   - canonical generation/resume behavior.
7. Challenge the methodology:
   - Does per-date equal volume preserve the real operational opportunity?
   - Are ranking-input fingerprints complete enough?
   - Is any outcome information entering selection or refill?
   - Does strict Best Expression test the intended claim rather than a stronger
     or different claim?
8. Run the relevant full research test suite in the real Claude environment.
9. Verify exact-SHA GitHub CI where available.
10. Return a verdict for each commit/workstream:
    - ACCEPT
    - ACCEPT WITH FIX
    - REJECT
    - CANNOT VERIFY — [exact reason]
11. Do not merge or promote without explicit user authorization.

## Final promotion standard

Even if every integrity fix is correct, none of this is itself evidence that a
challenger wins more props.

The eventual promotion question remains:

> At the same legitimate usable operational pick volume, on a certified
> point-in-time dataset, does the challenger produce more realized winners than
> the champion, with stable and dependence-aware evidence?

