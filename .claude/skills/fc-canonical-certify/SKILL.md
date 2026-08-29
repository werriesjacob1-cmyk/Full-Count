---
name: fc-canonical-certify
description: Independently certify whether a canonical backtest artifact may be called canonical. Use before any research result is allowed to rest on a dataset. Read-only — never repairs, regenerates, or re-runs anything.
allowed-tools: Read, Grep, Glob, Bash
context: fork
agent: fc-canonical-certifier
effort: high
---

# fc-canonical-certify

Runs in a **forked context** via `fc-canonical-certifier`, so the evidence
sweep stays out of the main conversation and the verdict comes back compact —
and so the reviewer is genuinely separate from whoever built the artifact.

**The producer may not certify.** If the session that generated the artifact is
the one asking, say so and decline.

## What certification is not

Durability is not certification. A run can push every date with zero failures
and still be **NOT CANONICAL**. Never let a green health check stand in for a
verdict.

## The checklist — every item, no skipping

**Coverage** — full date range, no gaps (enumerate missing); `ok`/`no_games`/
error tally; partial or stale tail (a truncated tail silently biases the most
recent season).

**Integrity** — per-checkpoint sha256 **recomputed**, never read back from the
meta; assembled byte checksum and row count; **order-independent** logical
fingerprint, because identical code produces different row order per run;
duplicate thesis identities within a date; schema conformance and version.

**Provenance** — repository identity must be `werriesjacob1-cmyk/Full-Count`,
or an alias with a correction record present; correction records explain what
and why; runtime options (weather mode, sleep, range) recorded and consistent
with the checkpoints; model/calibration/feature versions per row; original
checkpoint SHAs preserved for salvaged data.

**Environment** — Python version, platform, and critical package versions
recorded. Same git SHA is **not** the same scientific environment: a pybaseball
or pandas upgrade changes rows produced by byte-identical code.

**Source lineage** — structured per input, not a bare `"mlb_statsapi"`. Source,
request identity, retrieval timestamp, library version, row count, schema
fingerprint, content checksum, date coverage. Cache mode declared
`fresh_source` or `frozen_cache`. A Statcast cache accepted on verified schema,
checksum, row count and retrieval timestamp — never on a filename.

**Generation regime** — run `backtest/generation_regime.py`.
`MIXED_NON_EQUIVALENT` → NOT CANONICAL, no discussion. `MIXED_UNPROVEN` →
BLOCKED, name the missing proof. `MIXED_EQUIVALENT` → the equivalence record
must exist **and the overlap replay must actually have been run**; open it,
confirm the verdict is order-independent, and confirm any row difference was
attributed by a controlled 2×2 rather than argued away.

**Dataset identity** — `accuracy_lab.manifest_identity_strength()` at or above
promotion grade.

## Verdict — exactly one, with numbers

- **CANONICAL CERTIFIED** — listing the fingerprints and counts actually
  verified. A certification containing no numbers is worthless.
- **NOT CANONICAL** — which check failed, first, with evidence.
- **CERTIFICATION BLOCKED — [exact missing evidence]** — name the exact file or
  record needed. Never infer a pass from an absent artifact, and never soften a
  BLOCKED into a CERTIFIED because everything else looked fine.

## Standing blocker

Runs generated so far record `source_lineage: []` and
`source_lineage_fingerprint: null`. Until a run populates real lineage records,
the honest verdict is **CERTIFICATION BLOCKED — source lineage absent**, no
matter how clean coverage and integrity are.
