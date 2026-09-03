---
name: fc-prospective-audit
description: Audit the boundary between the three data estates — canonical historical model data, prospective full-candidate capture, and the immutable public Top Pick ledger. Use before any claim about what the product actually offered or published. Read-only.
allowed-tools: Read, Grep, Glob, Bash
context: fork
agent: fc-prospective-ledger-auditor
background: false
effort: high
---

# fc-prospective-audit

> **`background: false` requires Claude Code ≥ v2.1.218.** On an older client the
> key is ignored, the fork runs in the background, and the calling session
> continues past it — so a verdict meant to gate an action arrives after the
> action. Check `claude --version` before treating this skill as a blocking gate.

Forked into `fc-prospective-ledger-auditor`, read-only. Broken capture is a
finding and a specification for someone else, never a repair job here.

## The three estates — and why pooling them is the defect

| Estate | What it is | What it can support |
|---|---|---|
| **Canonical historical** (`backtest/`) | what the model *would have* computed | a claim about the model |
| **Prospective capture** | every candidate the pipeline saw that day, with live market state | a claim about opportunity |
| **Public ledger** (`dashboard/prediction_ledger.py`, hash-chained) | what was actually published as a wager | a claim about deployed performance |

Only the third supports a performance claim. A sentence saying "we hit X%"
without naming its estate is not a measurement — **flag the exact line**.

Canonical historical data is **confirmed-starting-lineup historical evidence**.
It is *not* an exact replay of what Full Count knew at a historical publication
timestamp: some market and live context inputs are unavailable historically.
Flag any text that overclaims it, and never fabricate the missing inputs.

## Field-by-field capture audit — open the record, do not assume

Candidate ID (stable across the day); capture timestamp (and that it **precedes
first pitch**); game; player; market, line, side; model probability **with the
model/calibrator version that produced it**; reliability/evidence band; lineup
state at capture (**confirmed vs projected — not the same thing**); live
sportsbook price **with its own timestamp**, not the capture timestamp; fetch
state (succeeded / degraded / absent — degraded must not read as succeeded);
QC and gate outcome with the **specific** block reason; alternatives at the same
thesis identity; rank; settlement and settlement authority; publication identity
where published.

## Flag loudly

- **Fabricated historical prices** — a price reconstructed, modelled or
  backfilled, stored in a field that reads as observed. The most damaging defect
  available here: it makes a backtest look like a track record. Absent provenance
  means **unverified**, not "probably fine".
- **Missing market state** — "it would have been a Top Pick" is unprovable
  without the price that was actually available.
- **Post-publication mutation** — **verify the hash chain, do not assume it**. A
  published entry that changed afterwards is P0 regardless of how benign it looks.
- **Funnel gaps** — candidates vanishing between capture and selection with no
  recorded rejection reason are indistinguishable from a silent filter.
- **Evidence-regime blending** — any report, chart or number pooling two estates.

## Market math lives here

De-vig, implied probability, ROI and market-agreement geometry are audited in
this skill rather than a separate one — the subject is price provenance either
way, and splitting the no-fabricated-prices rule across two documents is how
rules drift. Never treat MARKET DISAGREES as discriminative inside a population
where the formulas structurally force it.

## Verdict

**AUDIT PASSED** (with the counts and chain verification actually run) /
**DEFECTS FOUND** (most damaging first, with exact file and record) /
**CANNOT AUDIT — [exact missing evidence]**.
