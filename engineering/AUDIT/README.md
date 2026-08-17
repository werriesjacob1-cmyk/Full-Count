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

No audit finding has been finalized in this directory yet. Initial hypotheses
are listed in `engineering/ENGINEERING_HANDOFF.md`; promote one here only after
independent verification. Each future finding should include severity,
status, evidence, reproduction, impact, affected sources of truth, remediation
authorization, tests, and follow-up.
