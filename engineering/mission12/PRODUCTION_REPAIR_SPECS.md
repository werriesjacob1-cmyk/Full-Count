# Mission 1.2 §7 — production repair specs (NOT applied here)

Two pre-existing production defects, evidence preserved. **Neither is fixed in
this branch**: both edit the live publication path and each deserves its own
authorization and PR.

---

## P1 — the Prediction Ledger has recorded nothing in production

**Evidence.** `dashboard/confirm_publication.py` appends one hash-chained event
per new registry entry to `data/prediction_ledger/events.jsonl`. But
`.github/workflows/dashboard-deploy.yml` stages **only** `registry.json`, and
`git commit` without `-a` commits only staged paths; the next retry's
`git checkout --detach origin/main` discards the rest and the runner is torn
down.

Measured: `registry.json` holds **108** entries; `events.jsonl` holds **6**, and
`git log -- data/prediction_ledger/events.jsonl` shows only two development
commits and **not one workflow-produced commit**, while `registry.json` has a
long run of `Record deployed Top Pick exposure` commits.

**Minimal repair.** In `dashboard-deploy.yml`, the step `Confirm durable public
exposure`, add the path to the existing `git add`:

```
git add data/public_top_picks/registry.json data/prediction_ledger/events.jsonl
```

**Risk:** low, additive. **Verification:** after one deploy that admits a new
Top Pick, `events.jsonl` gains an entry in a workflow-authored commit, and the
hash chain verifies.

---

## P2 — reconciliation never populates `live.json`

**Evidence.** `dashboard/live_state.py:382` initialises `"reconciliation": None`
and only `dashboard/run_reconciliation.py:157` ever replaces it with a
`checked_at`-bearing object. Measured across **twelve consecutive**
`docs/live.json` commits on `origin/main` spanning 04:26→05:16 UTC on
2026-09-02: **NULL in every one.** `run_reconciliation.py` is invoked from
`dashboard-live.yml` every 5 minutes.

**Why it matters here.** The prospective source-integrity contract can evaluate
schedule availability and the required freshness channels, but **not**
reconciliation-derived holds (board-age and lineup mismatches). Those are
recorded as `unevaluated_signals` rather than silently ignored — but the gate is
weaker than designed until this is fixed.

**Minimal repair.** Determine whether `run_reconciliation.py` is (a) not
running, (b) running and failing before the write, or (c) running and writing to
a payload that is then overwritten by a later writer in the same 5-minute cycle.
Start by checking whether `dashboard-live.yml` stages `docs/live.json` **after**
the reconciliation step rather than before it.

**Do not** work around this by having the shadow compute its own reconciliation
— that would create a duplicate source of truth, which the pipeline's
one-semantic-writer discipline forbids.
