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

## Workstream 3 — Ranking-input identity

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO: `SelectionPolicy.identity()` recorded policy name/version/
description, but not the candidate fields that determined ordering. The
experiment therefore could not distinguish "same policy label, different
ranking inputs" without reading implementation code and row payloads manually.

### Implementation

1. `ca6e608fe81b1eebec63769cf952b4047031297d`
   - `backtest/equal_volume.py`
   - `SelectionPolicy` now accepts optional `ranking_input_fields`
   - field names are normalized to a deterministic unique sorted tuple
   - policy identity reports the declaration
   - promotion-grade mode fails closed if champion or challenger does not
     declare ranking inputs
   - promotion-grade mode explicitly rejects `outcome` as a ranking input
   - experiment computes deterministic fingerprints of the declared input
     values for every candidate
   - fingerprints are present in integrity output and bound into
     `experiment_manifest_id`
   - exploratory policies remain backward-compatible; declarations are optional

2. `69540742aa91820f80ca408a1ee61f9b3a423502`
   - `test_equal_volume.py`
   - champion fixture declares `score`
   - challenger fixture declares `predicted_prob`
   - promotion-grade policy with no declaration is rejected
   - explicit realized-outcome leakage declaration is rejected
   - changed champion score changes champion input fingerprint while leaving the
     challenger's predicted-probability fingerprint unchanged
   - declaration ordering/duplicates normalize deterministically
   - promotion-grade report asserts both ranking-input fingerprints are present

### Methodology limitation — important

This mechanism is an **explicit contract**, not runtime introspection of
arbitrary Python. A dishonest or buggy `rank_fn` could theoretically use a
field that its `ranking_input_fields` declaration omits. The framework cannot
prove the declaration is complete from arbitrary Python execution.

Therefore Claude's independent review must compare each promotion policy's
declared fields against the actual rank implementation. For nested structures,
declaring the containing top-level object (for example `signals`) is safer
than declaring an incomplete subset.

The existing candidate-content fingerprint from Workstream 2 remains a broader
backstop: even an undeclared row-content change changes the population-content
identity, while Workstream 3 makes the exact claimed ranking inputs visible and
auditable.

### Compatibility / scan status

- Intentional fail-closed behavior: promotion-grade policies without declared
  ranking inputs now fail.
- SUPERCHAD scanned the obvious equal-volume / selector / experiment / accuracy
  files and found `promotion_grade` usage only in the Accuracy Lab /
  equal-volume test surface already being modified. A repository-wide scan was
  attempted but the connector's per-call tool limit prevented completing all
  Python blobs in one orchestration call. Claude must perform a normal
  repository-wide grep before accepting this API change.
- Exploratory `SelectionPolicy` construction remains valid without a
  declaration.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: tests committed.
- UNKNOWN: real-runtime test execution.
- UNKNOWN: exact-SHA CI.
- VERIFIED-REPO: changed source/test blocks re-fetched after commit.

## Workstream 4 — Operational equal volume by slate/date

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO: `EqualVolumeExperiment` enforced only one aggregate `volume`
across the entire eligible population. A champion and challenger could each
select the same total N while allocating those picks to different dates/slates.
That can manufacture an apparent selector advantage by moving exposure away
from difficult slates and toward easier ones.

Aggregate equal N is therefore not sufficient for FULL COUNT's operational
promotion standard.

### Implementation

1. `fefab31353c144ac5c94c6c54aa7263f1acd5641`
   - `backtest/equal_volume.py`
   - adds optional `volume_by_date`
   - validates non-negative integer quotas
   - requires schedule sum to equal requested total volume
   - refuses quotas larger than the eligible population available on a date
   - when a schedule is present, each policy fills the exact quota independently
     from its deterministic ranking
   - promotion-grade mode requires a schedule
   - promotion-grade schedule keys must cover the **exact eligible date set**,
     including explicit zero-pick dates
   - report records requested schedule, allocation mode, and actual selected
     counts by date for each policy
   - experiment manifest binds the schedule

2. `78605d16712011f089bc399fdb0ff74067c028e4`
   - `test_equal_volume.py`
   - promotion-grade aggregate-only N is rejected
   - incomplete date coverage is rejected
   - accepted promotion fixture uses explicit schedule including zero-pick dates
   - two-slate adversarial fixture proves champion/challenger cannot shift
     volume between dates even when their global preferences strongly favor
     opposite slates
   - schedule sum mismatch and overfilled slate are rejected

3. `2359f0b16a95b4cb8e324a9c53b5ca91e854969f`
   - `backtest/equal_volume.py`
   - unenforced aggregate mode now reports
     `same_operational_volume_by_date=None`, not a misleading `False`
   - human-readable report explicitly labels per-date locked allocation versus
     aggregate top-N

4. `8932dc1acedf40c95787c0c403dd62d6dc09b4b8`
   - `test_equal_volume.py`
   - locks the "unknown when not enforced" semantics

### Methodology notes

- Date/slate is the enforced unit in this first operational-volume contract.
  This does not yet freeze market-family allocation within each date.
- Relative ordering within each date is preserved by filtering each policy's
  full deterministic ranking to the locked quota for that date.
- Zero-pick dates must be stated explicitly in promotion mode so a caller cannot
  silently omit hard/no-selection dates from the schedule.
- The schedule itself must come from a legitimate predeclared operational
  benchmark (for example the champion's real/intended slate volume), not be
  optimized after seeing outcomes. The framework structurally enforces the
  supplied schedule but cannot prove how a caller chose it.
- Claude should challenge whether date-level locking is sufficient for the
  locked disagreement experiment or whether a stricter market-family allocation
  is necessary for that specific claim.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: implementation and adversarial tests committed.
- UNKNOWN: real-runtime execution.
- UNKNOWN: exact-SHA CI.
- VERIFIED-REPO: source/test changes re-fetched and reviewed after commit.

## Workstream 5 — Missing-outcome denominator integrity

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO: under `OUTCOME_EXCLUDE_PAIRWISE`, the framework formed the
union of graded identities, removed every missing identity from whichever
selection contained it, and then continued without rechecking effective
denominator equality.

If a missing-outcome candidate appeared only in the champion selection, the
champion could finish with one fewer scored wager than the challenger despite
the original selected N being equal. That violates the comparison contract.
Outcome-aware refill would be an invalid repair because missingness is known
only after selection.

### Implementation

1. `8f23affed1229ddf7fa1563e1fe3932ef25d1dc3`
   - `backtest/equal_volume.py`
   - computes the exact missing selected identity set independently for each arm
   - `exclude_pairwise` now proceeds only if the two missing identity sets are
     exactly equal
   - otherwise fails closed and explicitly forbids outcome-aware refill
   - symmetric common missing overlap may still be removed from both sides
   - adds a final structural assertion that champion/challenger
     `n_scored` are equal after outcome handling

2. `5962e9223c977ede624362a47747ccf943a9fea3`
   - `test_equal_volume.py`
   - missing candidate selected only by one arm must fail
   - equal missing **counts** with different missing identities must still fail
   - the exact same missing overlap selected by both arms may be excluded
     symmetrically and leaves equal scored denominators

### Methodology note

The safest promotion policy remains `OUTCOME_REQUIRED` when complete grading
is available. This workstream does not encourage exclusion; it prevents the
explicit exclusion mode from silently changing one side's denominator.

No outcome-aware selection or refill was added.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: adversarial tests committed.
- UNKNOWN: real-runtime execution.
- UNKNOWN: exact-SHA CI.
- VERIFIED-REPO: implementation/test source inspected after commit.

## Workstream 6 — Strict Best Expression refill

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO: Best Expression historically used soft demotion. When an
eligible population did not contain enough independent theses to refill the
requested top-N, redundant expressions flowed back into the selected set. That
is honest exploratory behavior because volume is preserved, but it cannot
support a promotion claim that specifically says the portfolio remained
thesis-diversified.

A second subtle risk is schedule mismatch: a strict-refill check is meaningless
if it proves capacity against one per-date quota and the experiment executes a
different quota.

### Implementation

1. `ef4b4effe91a97c4e0855c20962316fe4614b120`
   - `backtest/best_expression.py`
   - adds `StrictRefillViolation`
   - adds optional `strict_volume_by_date` to
     `best_expression_rank_fn()`
   - strict mode computes same-slate thesis-distinct capacity and fails if any
     date cannot satisfy its quota without exceeding `max_per_thesis`
   - adds `strict_refill_capacity()` and `assert_strict_refillable()`
   - soft mode is preserved when no strict schedule is supplied

2. `1f0f0279fa3341755c6f1c30e6bedfd5a0d656f2`
   - `test_best_expression.py`
   - insufficient same-slate independent capacity must raise
   - sufficient same-slate refill produces distinct theses
   - strict schedule must cover every eligible date
   - strict Best Expression integrates with EqualVolumeExperiment's locked
     per-date allocation

3. `2b284f04c736217836135b2ad42596f54426124e`
   - module documentation now distinguishes soft/exploratory re-entry from
     strict/promotion behavior

4. `fe7ee13b02230594c1afd7cab0fb6148488f821c`
   - strict rank function exposes machine-readable required-volume and selection
     contract metadata

5. `b4956cb368258e3d533a631dd85490013094e268`
   - `SelectionPolicy` captures selector-specific required volume metadata
   - `EqualVolumeExperiment` refuses to execute a selector whose strict refill
     schedule differs from the experiment's own `volume_by_date`
   - the contract is included in policy identity, so the experiment manifest
     binds it

6. `cd95078cb8ecc22b96c559d761e0d21152bbb4df`
   - adversarial test proves a strict Best Expression policy validated against
     one schedule cannot be executed under a different schedule

### Methodology notes

- Strict mode proves **capacity**, not accuracy. It does not claim diversified
  picks win more often.
- The default thesis remains `player_game`; stronger groupings such as
  game-wide suppression remain separate hypotheses.
- Strict failure is preferable to silently changing the experimental thesis.
  If a slate cannot refill, that is evidence about operational feasibility, not
  permission to lower N or re-admit redundancy under a promotion label.
- A future promotion runner must also declare the Best Expression ranking input
  fields (normally the underlying score plus candidate identity fields already
  structurally bound). Claude should verify the exact declaration.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: code and adversarial tests committed.
- UNKNOWN: real-runtime test execution.
- UNKNOWN: exact-SHA CI.
- VERIFIED-REPO: cumulative source paths remain research/evaluation + tests +
  this handoff only.

## Workstream 6 — Strict Best Expression refill

**Status:** SUPERCHAD-IMPLEMENTED / NOT YET RUNTIME-VERIFIED / NOT CI-VERIFIED

### Pre-change defect

VERIFIED-REPO: Best Expression historically used soft demotion. When an
eligible population did not contain enough independent theses to refill the
requested top-N, redundant expressions flowed back into the selected set. That
is honest exploratory behavior because volume is preserved, but it cannot
support a promotion claim that specifically says the portfolio remained
thesis-diversified.

A second subtle risk is schedule mismatch: a strict-refill check is meaningless
if it proves capacity against one per-date quota and the experiment executes a
different quota.

### Implementation

1. `ef4b4effe91a97c4e0855c20962316fe4614b120`
   - `backtest/best_expression.py`
   - adds `StrictRefillViolation`
   - adds optional `strict_volume_by_date` to
     `best_expression_rank_fn()`
   - strict mode computes same-slate thesis-distinct capacity and fails if any
     date cannot satisfy its quota without exceeding `max_per_thesis`
   - adds `strict_refill_capacity()` and `assert_strict_refillable()`
   - soft mode is preserved when no strict schedule is supplied

2. `1f0f0279fa3341755c6f1c30e6bedfd5a0d656f2`
   - `test_best_expression.py`
   - insufficient same-slate independent capacity must raise
   - sufficient same-slate refill produces distinct theses
   - strict schedule must cover every eligible date
   - strict Best Expression integrates with EqualVolumeExperiment's locked
     per-date allocation

3. `2b284f04c736217836135b2ad42596f54426124e`
   - module documentation now distinguishes soft/exploratory re-entry from
     strict/promotion behavior

4. `fe7ee13b02230594c1afd7cab0fb6148488f821c`
   - strict rank function exposes machine-readable required-volume and selection
     contract metadata

5. `b4956cb368258e3d533a631dd85490013094e268`
   - `SelectionPolicy` captures selector-specific required volume metadata
   - `EqualVolumeExperiment` refuses to execute a selector whose strict refill
     schedule differs from the experiment's own `volume_by_date`
   - the contract is included in policy identity, so the experiment manifest
     binds it

6. `cd95078cb8ecc22b96c559d761e0d21152bbb4df`
   - adversarial test proves a strict Best Expression policy validated against
     one schedule cannot be executed under a different schedule

### Methodology notes

- Strict mode proves **capacity**, not accuracy. It does not claim diversified
  picks win more often.
- The default thesis remains `player_game`; stronger groupings such as
  game-wide suppression remain separate hypotheses.
- Strict failure is preferable to silently changing the experimental thesis.
  If a slate cannot refill, that is evidence about operational feasibility, not
  permission to lower N or re-admit redundancy under a promotion label.
- A future promotion runner must also declare the Best Expression ranking input
  fields (normally the underlying score plus candidate identity fields already
  structurally bound). Claude should verify the exact declaration.

### Test / CI truth

- SUPERCHAD-IMPLEMENTED: code and adversarial tests committed.
- UNKNOWN: real-runtime test execution.
- UNKNOWN: exact-SHA CI.
- VERIFIED-REPO: cumulative source paths remain research/evaluation + tests +
  this handoff only.

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

