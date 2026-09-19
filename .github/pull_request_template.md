<!--
Pre-merge certification checklist. Doctrine: engineering/PRE_MERGE_CERTIFICATION_TEMPLATE.md
(Issue #91 comment 5743926733). Fill in only the sections your change's risk
classification actually touches — see "Risk-proportionate scope" in that doc.
Delete this comment block once filled in.
-->

## 1. Scope and risk map

- Base SHA:
- Head SHA:
- Changed files:
- Risk classification (check all that apply): [ ] scientific/data [ ] live-operations/customer [ ] security/authorization [ ] shared-library [ ] schema/identity [ ] generated/public-ledger [ ] documentation-only
- Shared callers / upstream builders / downstream consumers touched:

## 2. Adversarial failure search

- Plausible way this change could silently produce wrong results or corrupt a downstream output:
- Targeted negative/boundary tests run (missing/duplicate IDs, stale sources, out-of-order timestamps, ties/collisions, empty/zero/large values, etc.):

## 3. Reproducibility and data lineage (if this builds a dataset, label, eligible population, or forecast)

- Exact source bytes/digests/vintage:
- Determinism verified under input permutation / varied `PYTHONHASHSEED`? (Never "fix" by pinning the seed — fix the actual tie-break.)
- Point-in-time chronology / train-holdout separation / leakage check:

## 4. Statistical claims (if this reports a research/challenger result)

- Independently reproduced metrics? From what population, what N?
- Paired comparison, season/subgroup stability, appropriately clustered uncertainty:
- Negative findings / alternative baselines disclosed:

## 5. Live/product changes (if this touches a scheduled workflow or customer-facing output)

- Identity, source timing, eligibility, stale/unknown handling, sealed artifact, grading path verified:
- Fail-closed semantics preserved:

## 6. Combined-tree certification

- Clean mergeability + exact-head CI on the CURRENT head (not a stale SHA):
- Generated-state drift checked separately from scientific/published evidence:

## 7. Audit output and authorization

- Independent reviewer (must not be the implementing agent):
- Unresolved blockers:
- **Recommendation: GO / HOLD** (code integration only — never implies model promotion, deployment, or a customer-pick-policy change)
- Jacob's merge authorization: reference the exact Issue #91 comment, or state "not yet authorized"
