# Pre-Merge Certification Template — Permanent FULL COUNT Doctrine

Recorded per Jacob's directive, Issue #91 comment `5743926733`, 2026-09-19.
Effective immediately for every future FULL COUNT merge (MLB, NFL, data,
models, workflows, UX, docs, generated state).

## Why this exists

The independent audits of PRs #146-#148 (and follow-on audits #149-#151)
found real defects despite green CI: upstream hash-order tie-breaking
contaminating already-merged role-event evidence, a PMF normalization
failure, and news-claim identity/chronology gaps. The lesson is not that
every PR needs the same expensive test suite. The lesson is that **no PR is
merge-ready merely because an implementing agent reports success, unit
tests pass, or GitHub says mergeable.**

## Rule

Every future merge requires a documented, independent, risk-proportionate
adversarial audit of the exact proposed changes and their interaction with
current `main`. The reviewer must not be the authoring agent and must
inspect actual code/diff and evidence, not accept a summary. **If no
independent reviewer is available, hold the merge and report that fact —
never self-certify by repackaging the author's own tests.**

Automated CI is additive to this review, never a substitute for it.

## The seven certification sections

Fill in only the sections a change's risk classification actually touches
(see "Risk-proportionate scope" below) — this is a checklist to apply with
judgment, not a form to complete uniformly on every PR.

### 1. Scope and risk map
- Exact base SHA, exact head SHA, complete changed-file inventory.
- Risk classification (pick every category that applies): scientific/data,
  live-operations/customer, security/authorization, shared-library,
  schema/identity, generated/public-ledger, documentation-only.
- Shared callers, upstream builders, downstream consumers, caches,
  artifacts, workflows, model/selector interactions, cross-PR dependencies.
- Reject unrelated/unreviewed scope drift — a PR claiming one objective
  should not quietly touch unrelated files.

### 2. Adversarial failure search
- State at least one plausible way this change could silently produce
  wrong results or corrupt a downstream output, then attempt to falsify
  the change's correctness against that scenario.
- Run targeted negative/boundary tests relevant to the actual failure
  modes: missing/duplicate IDs, partial/stale sources, late/out-of-order
  timestamps, unexpected source/schema changes, tie/collision/order
  dependence, empty/zero/large values, push/void/line-moved cases where
  applicable.
- Docs-only changes get evidence/provenance and cross-reference checks,
  not irrelevant model tests. No fixed test count substitutes for real
  failure-mode coverage.

### 3. Reproducibility and data lineage
(Required for any change that constructs datasets, labels, eligible
populations, or forecasts.)
- Freeze or identify exact source bytes/digests/vintage and code SHA.
- Verify deterministic identity and row/event counts/ordering/serialized
  output under input permutations, and under independently varied
  `PYTHONHASHSEED` in fresh processes wherever sets/dicts/ties can matter.
- **Never "fix" order-dependence by pinning `PYTHONHASHSEED`** — fix the
  actual tie-break with an explicit, stable, documented total ordering.
  A hash-seed pin only hides nondeterminism; it doesn't remove it.
- Missing identity or uncertain historical as-of state must quarantine or
  fail closed — never guess.
- Verify point-in-time chronology, train/holdout separation, the
  historical/prospective/public-ledger distinction, and no
  outcome/closing-line/late-news leakage.

### 4. Statistical claims
(Required for any research/challenger result.)
- Independently reproduce claimed metrics from the exact source/eligible
  population — do not take the author's numbers on faith.
- Compare challengers on identical paired examples at equal legitimate
  volume; report N, season/subgroup stability, uncertainty with
  appropriate game/player/event clustering (not naive per-row resampling
  when rows share a common source of noise), selection overlap, and
  added/removed rows.
- Disclose alternative baselines and negative findings — preserving a
  negative result is not optional.
- Distinguish a numerical improvement from a defensible incremental
  signal, and distinguish historical predictive gain from real-price
  prospective profit. A "winner" label or a research-promotion tag is
  never proof of customer-pick readiness.

### 5. Live/product changes
(Required for anything touching a scheduled workflow, live capture, or
customer-facing output.)
- Test with deterministic fixtures AND representative real-source
  evidence where it's safe to do so.
- Verify exact game/player/market identity, source times, full discovery/
  accounting, eligibility transitions, stale/moved-line and
  unknown/inactive handling, sealed artifacts, grading, and recovery
  behavior.
- Preserve fail-closed semantics throughout.
- Audit any effect on customer-facing outputs, immutable ledgers,
  scheduled automation, and publish paths.

### 6. Combined-tree certification
- Verify the final candidate head, clean mergeability, and required
  exact-head CI.
- After any dependency reconciliation, re-verify focused integrations and
  required combined-tree tests against the CURRENT `main` — a green run
  on an obsolete PR head does not certify a new head produced by a rebase
  or a base-branch merge.
- Check generated-state drift (dashboards, manifests, caches) separately,
  without erasing published or scientific evidence.

### 7. Audit output and authorization
Record a concise, durable certificate on the PR itself or on Issue #91,
containing:
- Independent reviewer identity (must differ from the implementing agent).
- Exact code/source versions reviewed.
- The risk map from section 1.
- Adversarial tests run and their results (section 2).
- Data lineage/determinism findings where relevant (section 3).
- Downstream impact (section 1/3).
- Scientific/prospective limits, stated plainly (section 4).
- Explicit unresolved blockers.
- A **GO / HOLD** recommendation for *code integration only*.

**GO never implies model promotion, deployment/publication, purchase, or a
customer-pick-policy change.** Jacob remains final merge authority. A prior
scope-limited merge authorization never extends to a new PR.

## Hard HOLD conditions

Hold the merge (do not integrate) if any of the following is true:
- No independent reviewer available or performed.
- Unresolved upstream data corruption.
- Nondeterminism in a data/research builder.
- Invalid probabilities (a distribution that doesn't sum to 1, etc.).
- Unidentified source vintage.
- Point-in-time or outcome leakage.
- Incompatible schemas between old and new consumers.
- Stale exact-head CI (CI ran on a SHA that is no longer the head).
- A significant downstream effect that was not measured.
- Unresolved scientific claims presented as validated.
- Missing explicit merge authorization from Jacob.

Do not convert an audit failure into a cosmetic test change, and do not
quietly change a scientific threshold just to obtain green CI. Instead:
correct the actual upstream defect, quarantine the affected artifacts,
re-evaluate every downstream consumer, and have an independent reviewer
certify the repair before requesting merge again.

## Risk-proportionate scope

This doctrine is deliberately not one-size-fits-all. A documentation-only
PR needs section 1 (scope) and light provenance checks from section 2 —
not sections 3-5. A dashboard-generated-state commit needs section 6's
generated-state-drift check and little else. A new historical research
dataset or challenger needs sections 1-4 in full. A change touching a
scheduled live-capture workflow needs sections 1, 2, 5, and 6 in full.
Use judgment to scope the review to the actual risk — the goal is a real
adversarial check on what could actually break, not uniform overhead on
every change regardless of size.

## Worked precedent

The three audits that produced this doctrine are the reference examples:
- PR #149 (audit of #148): found a real PMF-normalization defect
  (`EmpiricalResidualPool.pmf` summed to 1.36, not 1.0) and a real
  `zero_probability` vs. ladder `under` coherence gap (~0.43 absolute),
  by constructing a hand-computable adversarial pool rather than trusting
  the original 83 passing tests.
- PR #150 (audit of #147): found the upstream hash-randomization defect by
  running the real builder 12 times from frozen bytes and diffing a
  668-event run against a 667-event run to isolate the exact flipping
  case — not by re-reading the code and assuming it was fine.
- PR #151 (audit of #146): found a real `claim_id`-collision risk (a
  position-change revision can collide to the same id with silently
  different content) by constructing two differently-shaped real report
  snapshots, not by trusting the schema's own validation alone.

Alligator
