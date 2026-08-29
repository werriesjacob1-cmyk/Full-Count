# HR Contact-State Locked Execution Runbook

> **STATUS: READY FOR REVIEW — NOT AUTHORIZED FOR REAL HOLDOUT EXECUTION.**
>
> This runbook documents how to execute the preregistered HR experiment after
> all prerequisite gates pass. Its existence is not permission to run it.

## Purpose

Turn the frozen HR contact-state protocol into a mechanically reproducible
sequence:

1. certify the canonical artifact;
2. bind explicit user authorization to that exact artifact;
3. freeze 2026 venue identity;
4. fit B/C/D on <=2025 and freeze 2026 predictions without outcomes;
5. reveal B/C/D outcomes once;
6. conditionally run E only if D survives;
7. never promote from historical evidence alone.

The primary decision quantity remains realized HR winners at the same
five-per-slate historical ranking volume.

## Required code/artifact prerequisites

Before any real 2026 experiment command:

- canonical generation is complete;
- independent `backtest/canonical_certification.py` returns
  `CANONICAL CERTIFIED`;
- the certification's canonical byte SHA matches the assembled rows file;
- the certification's source content SHA matches the exact Statcast parquet;
- HR execution prereg v2 has passed independent methodology review;
- this runner's exact Git SHA is recorded;
- explicit user authorization exists for the exact canonical artifact SHA;
- no production/model/scoring/calibration/recommendation change is bundled
  into the experiment execution.

Do not use an old certification report against new bytes.

## 1. Persist the certification report

Run the read-only certifier against the completed durable run and persist its
JSON output as an immutable experiment input.

The report must contain:

- `verdict == "CANONICAL CERTIFIED"`;
- zero failures;
- zero blockers;
- promotion-grade dataset identity;
- one observed code SHA;
- exact canonical assembled byte SHA;
- exact source parquet content SHA and schema attestation.

The locked execution wrapper re-hashes both files itself. A report alone is
not trusted.

## 2. Explicit authorization record

Copy the repository's authorization template to a NEW file outside the code
path only after explicit user authorization.

The runner requires:

```json
{
  "authorized": true,
  "authorization_type": "explicit_user_authorization",
  "scope": "hr_contact_state_2026_holdout",
  "allowed_stages": ["venue-map", "stage1"],
  "canonical_artifact_sha256": "<EXACT CERTIFIED ROWS SHA256>",
  "authorization_reference": "<where the explicit approval is recorded>"
}
```

Recommended irreversibility discipline:

- first authorization: `venue-map` + `stage1`;
- inspect the immutable Stage-1 freeze;
- second explicit authorization: add `stage2` before revealing outcomes.

If the user explicitly authorizes the full experiment in one instruction,
the record may list all intended stages. Do not infer that authorization from
code readiness, a prior merge, or a green CI run.

Conditional E requires `stage1-e` / `stage2-e` and is still impossible
unless D's immutable Stage-2 report actually survives.

## 3. Freeze venue identity

After authorization and certification:

```bash
python backtest/hr_contact_state_locked_run.py venue-map \
  --certification <certification.json> \
  --authorization <authorization.json> \
  --canonical-rows <rows.jsonl> \
  --source-parquet <exact-source.parquet> \
  --output <venue-map.json>
```

This request reads only:

- `gamePk`;
- `venue.id`;
- `venue.name`.

It does not persist linescore, result, status, or other outcome fields.

Every holdout game must resolve. Missing/conflicting venue identity aborts.

The output is write-once and bound to the exact canonical rows SHA.

## 4. Stage 1 — B/C/D prediction freeze

```bash
python backtest/hr_contact_state_locked_run.py stage1 \
  --certification <certification.json> \
  --authorization <authorization.json> \
  --canonical-rows <rows.jsonl> \
  --source-parquet <exact-source.parquet> \
  --venue-map <venue-map.json> \
  --output <stage1-bcd.json>
```

Stage 1:

- retains <=2025 training outcomes;
- immediately masks all 2026 outcome/postgame fields;
- uses only canonical `home_run` rows with `outcome`, `predicted_prob`,
  and `score` present;
- fits B/C/D only;
- uses strict `game_date < D` features;
- preserves unsupported candidates via exact champion fallback;
- freezes top five independently per date;
- freezes champion/challenger probabilities;
- freezes overlap/added/removed identities;
- freezes transforms, betas, optimizer diagnostics, coverage, source/canonical
  identity, venue identity, and exact runner SHA;
- writes one whole-bundle hash;
- refuses overwrite.

### Stage-1 review before outcome reveal

Before Stage 2, independently inspect:

- exact Git SHA;
- certification and source hashes;
- population count;
- excluded-row counts by predeclared reason;
- B/C/D support counts;
- B support >=500 or note that the preregistered coverage gate will kill the
  thread after reveal;
- per-date selected N is exactly `min(5, eligible_n_D)`;
- champion selections identical in B/C/D;
- every unsupported row has challenger probability exactly equal to champion;
- no `outcome`, `actual`, `actual_pa`, `fair_test`, result, or grade
  field exists in the frozen 2026 prediction population;
- whole-bundle SHA verifies.

Do not change anything after this review.

If Stage 1 is defective, STOP WITHOUT OUTCOME REVEAL.

## 5. Stage 2 — B/C/D outcome reveal

Only with authorization that includes `stage2`:

```bash
python backtest/hr_contact_state_locked_run.py stage2 \
  --certification <certification.json> \
  --authorization <authorization.json> \
  --canonical-rows <rows.jsonl> \
  --source-parquet <exact-source.parquet> \
  --stage1-bundle <stage1-bcd.json> \
  --output <stage2-bcd.json>
```

Stage 2 contains no fitting, feature extraction, ranking, or selection code.

Before consuming truth it verifies:

- whole Stage-1 bundle hash;
- each arm prediction-freeze hash;
- freeze-set hash;
- exact frozen population;
- exact frozen pre-outcome overlap/added/removed identities;
- exact canonical/source/venue identities;
- exact score-defined eligibility population.

It then reports for B/C/D:

- N, hits, misses, hit rate;
- realized-winner delta;
- overlap / added / removed anatomy;
- added-minus-removed;
- 5,000-replicate paired game-cluster bootstrap, seed 20260828;
- valid/invalid changed-set bootstrap count;
- player/team/park/month frozen removal audit;
- descriptive month table;
- descriptive fixed champion-probability bands;
- per-arm frozen survival verdict.

### Per-arm continuation

An initial arm survives only when:

- B-supported holdout count >=500;
- added and removed sets are non-empty;
- >=4,750/5,000 changed-set replicates are valid;
- added-minus-removed 95% CI lower bound is strictly >0;
- every dependency axis is resolvable;
- no largest-contributor removal flips positive effect to non-positive.

B/C/D are evaluated independently.

Only D survival permits E.

No B/C/D survivor is deployed from this result.

## 6. Conditional Arm E

If and only if Stage 2 says both:

- `arms.D.survival.earns_continuation == true`;
- `arm_e_permitted == true`;

and the runner remains at the EXACT SAME Git SHA as initial Stage 1:

```bash
python backtest/hr_contact_state_locked_run.py stage1-e \
  --certification <certification.json> \
  --authorization <authorization.json> \
  --canonical-rows <rows.jsonl> \
  --source-parquet <exact-source.parquet> \
  --stage1-bundle <stage1-bcd.json> \
  --stage2-report <stage2-bcd.json> \
  --output <stage1-e.json>
```

Arm E's holdout population is reconstructed from the already-frozen B
prediction population, not from revealed 2026 eligibility.

B/C/D results are used only to verify the boolean D-survival trigger and report
hash.

Then evaluate E once:

```bash
python backtest/hr_contact_state_locked_run.py stage2-e \
  --certification <certification.json> \
  --authorization <authorization.json> \
  --canonical-rows <rows.jsonl> \
  --source-parquet <exact-source.parquet> \
  --stage1-bundle <stage1-bcd.json> \
  --stage2-report <stage2-bcd.json> \
  --e-bundle <stage1-e.json> \
  --output <stage2-e.json>
```

E uses the same K, bootstrap, robustness, and descriptive stability rules.

E failure does not erase a B/C/D arm that already survived.

## 7. After historical evaluation

A surviving arm means only:

**historical world-model/ranking evidence improved at matched five-per-slate
volume on the untouched 2026 holdout.**

It does NOT establish:

- exact historical production eligibility;
- historical sportsbook edge;
- ROI;
- production promotion;
- merge/deploy authorization.

The next evidence regime is prospective full-candidate shadow with real:

- lineup state;
- market price;
- availability;
- selector volume;
- operational eligibility.

Only prospective evidence can close the historical-to-live gap.

## Hard stops

Stop and report immediately on:

- certification no longer green;
- canonical/source hash mismatch;
- authorization mismatch;
- runner SHA drift;
- missing/duplicate candidate identity;
- score/population mismatch;
- venue unresolved/conflicting;
- same-day/future feature leakage;
- per-date volume mismatch;
- outcome appearing in Stage-1 freeze;
- failed optimizer/transform;
- freeze hash mismatch;
- overwrite attempt;
- any pressure to adjust feature/K/seed/band/grouping/penalty after result
  visibility.

No repair-and-rerun on the same revealed holdout.
