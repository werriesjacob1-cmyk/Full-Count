---
name: fc-release-audit
description: Independent pre-merge safety check for a branch or PR. Use before authorizing a merge to main, especially anything touching predictive, live, settlement or publication code. Does not implement fixes and never authorizes the merge.
allowed-tools: Read, Grep, Glob, Bash
context: fork
agent: fc-release-auditor
background: false
effort: high
---

# fc-release-audit

> **`background: false` requires Claude Code ≥ v2.1.218.** On an older client the
> key is ignored, the fork runs in the background, and the calling session
> continues past it — so a verdict meant to gate an action arrives after the
> action. Check `claude --version` before treating this skill as a blocking gate.

Forked into `fc-release-auditor` — **read-only**, clean context, so the verdict
is a direct check of the diff rather than a rewording of the author's summary.

## The order matters

1. **Fresh fetch, then resolve the real base.** `git fetch origin main`, then
   `git merge-base`. Never trust a stale local ref, never quote a SHA you have
   not just resolved. A previous report on this project was factually wrong
   because a merge parent was quoted from memory instead of re-resolved. If the
   author's stated base and the actual parent disagree, **that is the headline
   finding**.
2. **Read the actual diff.** `git diff <base>..<head>`, or the PR diff via
   `mcp__github__pull_request_read`. Never commit messages. **Enumerate every
   changed file** — an incomplete file list has already produced a wrong report here.
3. **Generated vs source, decided by path.** `data/`, `docs/`, `output/`,
   `results/` are generated; everything else is source. Never conclude
   "generated-only" from a commit message that says "Dashboard live update".
4. **CI at the exact head SHA.** Resolve the head, then check the run for *that*
   SHA. A green run on an earlier commit says nothing about this one.
   - **Local test sweeps are not CI.** A full local suite passed on this project
     while CI had been red for ten hours, because CI's shallow checkout could not
     run a test that reads git history. Check CI itself.
5. **Freeze detection.** Does the diff touch probabilities, scoring, calibrators,
   thresholds, Top Pick policy, selector behavior, market rules, settlement,
   grading, or public lifecycle semantics? Those need **visible** explicit human
   authorization — not assumed.
6. **Generated state.** Was `docs/live.json` or similar resolved by replacing
   live output with branch-era output? That is a silent regression.
7. **Settlement writers.** If settlement or grading changed, enumerate every path
   that can write `hit`/`miss` and confirm each is gated.
8. **Browser E2E** where the frontend changed: `test_browser_e2e.py`, plus
   static↔docs parity.

## Connector

GitHub MCP for diff, PR, checks and logs — **using owner/repo
`werriesjacob1-cmyk/project-gridiron`**, not the provenance name `Full-Count`,
which this session's MCP scope denies. Both names are correct for different
purposes; see `.claude/CAPABILITY_MATRIX.md`.

**Fallback if MCP is unavailable:** `git fetch` + `git diff` + `git merge-base`
cover diff and base. CI status then becomes **UNAVAILABLE — say that explicitly**.
Never let "I could not check CI" render as "CI is fine".

## Verdict

**SAFE TO MERGE** / **NOT SAFE** / **CANNOT AUDIT — [exact missing evidence]**,
naming the checks actually performed.

A verdict is **not** merge authorization. You report; a human decides.
