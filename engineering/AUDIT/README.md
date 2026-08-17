# Pre-Phase-V Full-System Audit

This directory indexes the evidence-backed audit performed before Phase V.
Phase V has **not** begun. The audit's purpose is to establish correct,
reproducible measurement and safe production behavior before feature or model
expansion.

## Finding rules

- Every finding must cite reproducible evidence: affected files/functions,
  data examples, workflow runs, test output, or an explicit reproduction.
- Classify every finding as **CRITICAL**, **HIGH**, **MEDIUM**, or **LOW**.
- Finding a problem does not automatically authorize changing it. Record the
  scope and obtain task authority before implementing a remediation.
- Model, feature, weight, threshold, or calibration changes require a separate
  validation task, held-out evidence, explicit versioning, and a clear
  distinction between backtest and forward performance.
- Preserve prediction history and distinguish missing/stale data from
  favorable evidence.

| Severity | Use when |
|---|---|
| CRITICAL | Historical integrity, production safety, or system-wide user trust is actively compromised and immediate containment is justified. |
| HIGH | Recommendations, grading, pricing, deployment, or persisted state can be materially wrong for a meaningful share of users or events. |
| MEDIUM | Correctness, reliability, reproducibility, or maintainability is meaningfully impaired but impact is bounded. |
| LOW | The issue is minor, localized, or primarily hygiene/documentation with little immediate behavioral impact. |

## Index

- [`live-lifecycle-2026-08-17.md`](live-lifecycle-2026-08-17.md) — **HIGH / MEDIUM**:
  includes **CRITICAL / HIGH / MEDIUM** publication, grading, identity,
  settlement-authority, odds-observation, retention, workflow, Pages-delivery,
  and frontend findings. It defines the corrected lifecycle contract, official
  settlement-rule evidence, and post-merge verification checklist. Remediation
  is implemented on draft PR #51 and remains unmerged.

Initial hypotheses not indexed here remain provisional in
`engineering/ENGINEERING_HANDOFF.md`. Each future finding should include
severity, status, evidence, reproduction, impact, affected sources of truth,
remediation authorization, tests, and follow-up.
