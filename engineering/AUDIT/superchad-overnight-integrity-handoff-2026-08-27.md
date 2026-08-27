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

_No implementation from this safe branch has been recorded yet. Add entries
only after the corresponding commit exists._

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

