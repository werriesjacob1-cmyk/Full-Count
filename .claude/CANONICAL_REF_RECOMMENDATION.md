# Canonical scientific code — reachability, and the one action recommended

## The historical concern

The canonical runner was described as reachable only by bare SHA
`fc589447ec157bff9a96071edc3ceb6c7dc734eb`. A commit reachable from no ref is
eligible for garbage collection: the scientific identity of every canonical run
would then be unverifiable, permanently and silently.

## Current reachability — MEASURED 2026-09-02

**It is durably referenced.** Not by a tag, but by a real branch ref:

```
$ git ls-remote --heads origin claude/canonical-source-identity-01
fc589447ec157bff9a96071edc3ceb6c7dc734eb    refs/heads/claude/canonical-source-identity-01
```

The ref points at *exactly* the pinned SHA — not at a descendant — and both
expected files exist in that tree:

```
$ git cat-file -e fc589447...:backtest/canonical_run.py         -> present
$ git cat-file -e fc589447...:backtest/canonical_durability.py  -> present
$ git log -1 --format='%cI %s' fc589447...
2026-08-28T15:18:51+00:00 preflight: reuse one fixed scratch ref instead of a timestamped one
```

`.claude/tests/test_superclaude_acceptance.sh` checks all three conditions —
remote ref resolves to the exact SHA, and both files exist at it — and fails,
not warns, if any of them stops holding. So the earlier claim that no such ref
existed was wrong, and the correction is already load-bearing in the suite.

## The residual exposure

A **branch** ref is mutable by design. Three ordinary actions would silently
un-pin the canonical identity:

1. someone commits on `claude/canonical-source-identity-01`, advancing the ref
   past `fc589447` (the acceptance test would then FAIL — good — but the pin is
   already lost at that moment);
2. someone deletes the branch after it looks merged or stale;
3. a branch-cleanup policy or a force-push moves it.

None requires malice. A branch that exists solely to anchor a SHA does not
announce that purpose to anyone reading a branch list.

## RECOMMENDED ACTION — NOT TAKEN HERE

Create **one annotated, immutable tag** at that exact commit, and leave the
branch in place:

```
git tag -a canonical-source-identity-v1 fc589447ec157bff9a96071edc3ceb6c7dc734eb \
  -m "Canonical scientific runner identity for every certified canonical run.
Immutable anchor: backtest/canonical_run.py + backtest/canonical_durability.py
at the exact fitted-artifact-producing commit. Do not move, do not delete."
git push origin canonical-source-identity-v1
```

Then, in a follow-up tooling change, teach the acceptance test to prefer the
tag and treat the branch as a secondary anchor.

**Why this is recommended rather than done.** Creating and pushing a tag writes
a new ref to the shared repository, outside the tooling branch this mission is
scoped to. That is a repository-level authority this mission does not carry,
and a tag named as the canonical scientific identity is exactly the kind of ref
that should be created deliberately by the human who owns the record — once,
with intent — rather than as a side effect of a tooling PR.

Nothing is blocked in the meantime: the branch ref is real today and the suite
checks it.
