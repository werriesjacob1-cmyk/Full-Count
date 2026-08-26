---
name: fc-break-it
description: Adversarial red-team pass on a Full Count research conclusion, a piece of production logic, or a "safe" tooling change -- actively try to find the failure mode instead of confirming the happy path. Use before trusting a result or a safety claim.
---

# fc-break-it

## For a research conclusion

Delegate to the `fc-methodology-red-team` agent (read-only by design) and
require a direct verdict on:

- Leakage (point-in-time discipline actually checked, not assumed from a
  docstring).
- Mixed regimes (single verified `code_git_sha`, not blended pre/post-fix
  rows).
- Fit/test contamination and post-hoc definition changes.
- Cherry-picked subgroup / one-market / one-season dependence.
- Fake significance (does the win survive a real CI, or is it noise).
- Calibration-improvement quietly substituted for a hit-rate claim.

A red team pass that finds nothing wrong still has to show its work: which
checks were run, and what would have failed if the result were weak.

## For production logic or a "safe" tooling change

Don't just read the code and agree it looks right -- construct the specific
input/state that would break it, and check whether it actually does:

- What's the most adversarial realistic input this code could see?
- What happens on the boundary conditions (empty input, exactly-at-threshold,
  concurrent access, a network/push failure mid-operation)?
- If this is a hook/automation script: run it, don't just read it. A
  logic error that's silently swallowed by a suppressed stderr (`2>/dev/null`)
  will not show up on inspection -- it showed up once already this project,
  as a real race condition in the autosave hook that a read-through missed
  and an actual disposable-worktree test run caught.
- If someone is claiming a change is "safe": what specific test would fail
  if it weren't? If no such test exists yet, that's the finding.

## When NOT to use

Don't apply this to routine, low-stakes changes as a matter of course --
reserve it for results/claims that will be trusted, promoted, or merged.
