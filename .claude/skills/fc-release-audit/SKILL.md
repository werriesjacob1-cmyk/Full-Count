---
name: fc-release-audit
description: Independent pre-merge safety check for a Full Count branch/PR. Use before authorizing a merge to main, especially anything touching predictive/live/publication code. Does not implement fixes.
---

# fc-release-audit

## Steps

1. Delegate to the `fc-release-auditor` agent (read-only by design).
2. It verifies, every time:
   - The actual diff (`git diff <base>..<head>` or the GitHub PR diff) --
     never the branch's own commit messages or self-report.
   - Current `main`, fetched fresh, to confirm the PR's stated base is
     actually current.
   - Generated-state drift is separated from real source changes
     (`docs/data.json`, `docs/live.json`, `data/props/*`, `output/*`,
     `results/grades_*.json`, dashboard state files).
   - Whether the diff touches any score weight, calibrator, probability
     formula, recommendation threshold/gate, stable-lift policy, or locked
     experiment definition -- explicit yes/no with file:line evidence.
   - CI actually ran against the exact head commit under review.
   - Frontend source/generated parity (`dashboard/static/*` byte-identical
     to `docs/` mirrors) if `test_build_dashboard.py` covers this diff.
   - Publication/settlement contracts are unchanged unless the PR
     explicitly and legitimately changes them.
   - There is an actual explicit merge authorization from Jacob for this
     specific branch/PR -- never an inferred "probably fine."
3. Output is a verdict + evidence, not a fix. If a defect is found, name it
   precisely enough to act on without further investigation.

## When NOT to use

Mid-development iteration on a branch that isn't close to merge yet -- this
is the final check, not a running lint pass.
