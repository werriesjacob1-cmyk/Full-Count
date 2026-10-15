# FC-ACC-001-S1: full-candidate export, design (not implemented)

> **Revision A2 (2026-10-15).** This revision follows Codex re-audit
> 6194240381 and `../AMENDMENT_A2.md`.
>
> **Superseded rule.** The original §5 rule said: "for each game, the last
> run with a sealed capture before **that game's** first pitch". It also used a
> fixed 22:00 UTC DAY/NIGHT split.
>
> **Why it was wrong.** A later game's last run can postdate an earlier game's
> start, so the combined DAY/NIGHT unit could not be sealed before its first
> pitch. The fixed UTC split misassigns games under daylight saving time.
>
> **What replaces it.** Every unit now uses ONE common cutoff:
> - the unit's earliest scheduled first pitch from the archived schedule,
>   minus 30 minutes;
> - DAY/NIGHT taken at 17:00 America/New_York.
>
> The superseded text is kept in git history at `73e4f601`.

**Status: DESIGN ONLY.** No production file has been changed. Implementation
waits on independent Codex review and Jacob's explicit authorization.

Base: PR #226 @ `d316593c`. The production paths below were read at that commit
(`generate_picks.py`, `board_freeze.py`, `odds_fanduel.py`,
`mlb_daily.fetch_lineups`, `.github/workflows/mlb-daily.yml`).

## 1. Where the full scored universe exists today

`generate_picks.main()`, in order. Line numbers are at `d316593c`.

| Step | Line | State |
|---|---|---|
| `_build_and_score()` | 3622 | `fetch_lineups`, then `lineup_basis` is recorded with `lineups_observed_at`. `bettable_games()` **drops started/final games before scoring**. Scoring, then `attach_hit_probabilities` (which keeps every threshold in `line_options`), then `apply_calibration`, then `attach_reliability`. |
| `quality_control()` | 4654 | Splits the pool into `candidates` (kept), `_qc_rejected` (with `qc_reason`) and `assumed_lineup` (holdout). |
| `apply_signal_weights` | 4664 | Adjusts the score using the 0–100 `signals` bag. |
| `attach_market_prices` | 4753 | Production FanDuel prices, **keyed by normalized player name only**: no event, no observation time, no raw bytes. |
| select moonshots / by_category / shadow | 4760–4810 | Fresh copied dicts. |
| `attach_recommendations` | 4827 | `status` and `status_reasons` (`top_pick`/`lean`/…) on every pool. |
| quality gate, then positive-read floor, then `rank_for_board`, then `select_main_board` | 4846–4928 | `gated`, `with_read`/`no_read`, `ranked`, `top10`. |
| `write_markdown` / `write_json` | 4931 | **Customer-facing artifacts written here.** |
| `board_freeze.freeze_board` + `seal_board` + `write_frozen_board` | 4950–4970 | **Already the complete universe**: kept, QC-rejected and assumed holdout, each with a rejection reason, rank and selection-surface membership. Wrapped in `try/except` after `write_json`. |

**Conclusion.** The right insertion point is the existing freeze block, which is
already post-publication, already fail-isolated, and already has every pool in
scope. A second scoring engine or a new candidate source is not needed.

### What the freeze record already carries

- Identity: candidate_id v2, game_pk, player_id or combo ids, stat, needs, side, line.
- Prediction: p, raw p, calibrated_by, CI, reliability, sample_n, lift and score.
- Market: production `market_odds` and `price_clears`.
- Eligibility: `qc_status`, `qc_reason`, `lineup_assumed`, the quality gate, the positive-read floor and `rejection_reason`.
- Selector: `final_rank`, `recommendation_status`, `status_reasons` and every `selected_*` flag.
- Provenance: model, selection-policy, calibration and feature versions, plus `git_sha`.

### What the freeze record drops (the gaps this export closes)

1. **`signals`.** The per-candidate feature bag (scaled 0–100 only; see §6),
   plus `projected_pa`, `cat_*` components and `signal_weight_adjustment`.
2. **`line_options` / `alternatives`.** Every other threshold the model priced
   for the same player and market. The freeze keeps only the chosen line, so
   the universe the market prices is under-represented.
3. **Lineup context.** `lineup_basis` (slot, provenance and `observed_at` per
   game side) is computed but not frozen. The opposing starter id is not recorded.
4. **Run identity.** No `GITHUB_RUN_ID`/attempt or cron slot, and no list of
   games excluded as already started.
5. **Offer provenance.** Production prices are name-keyed and unbound. They stay
   that way, because authentication comes from the separate S1 capture (§4),
   never from production.

### Defect found in the existing freeze (partial-slate bias)

`board_freeze_{date}.json` is **overwritten by every run** (`atomic_write_json`).
There are 6 cron slots: 14:30, 15:30, 17:00, 20:00, 22:30 and 23:30 UTC. Each run
scores only `bettable_games()`. A late run therefore replaces a full-slate
freeze with one that holds only the evening games. Evidence:
`board_freeze_2026-09-20.json` holds **1 game / 72 records**, generated at
22:33 UTC on a regular-season Sunday.

Codex should confirm against that day's real schedule. The export must
therefore be **per run and write-once**, never per date.

## 2. Recommended insertion point and call flow

```
generate_picks.main()
  ... write_json(...)                       # customer artifacts (unchanged)
  try: board_freeze ... write_frozen_board  # unchanged
  try:                                      # NEW, separate try, after the freeze
      import fc_acc_export                  # NEW module, pure / no network
      fc_acc_export.write_run_export(
          frozen_board=_frozen_board,       # sealed freeze, already verified
          pools={"kept": candidates, "qc_rejected": _qc_rejected,
                 "assumed_lineup": assumed_lineup},
          lineup_basis=ctx["lineup_basis"],
          lineups_observed_at=ctx["lineups_observed_at"],
          game_meta=game_meta,
          run={"run_id": $GITHUB_RUN_ID, "attempt": $GITHUB_RUN_ATTEMPT,
               "event": $GITHUB_EVENT_NAME, "git_sha": provenance.git_sha},
          out_dir=OUTPUT_DIR/"fc_acc_export")
  except Exception as e: m.warn(...)        # never fatal, never re-raised
```

**Gating.** Run only when `FC_ACC_EXPORT=1` (default off), which needs Jacob's
authorization to set. With the flag off, the call is a no-op.

### `write_run_export` behaviour

1. Recompute `canonical_prop_id` for every pooled candidate. Require the id set
   to **equal** the sealed freeze's record ids exactly, with no extras, no gaps
   and no duplicates. Otherwise raise (fail closed). This is the
   missing/duplicate guard, and it means the freeze stays the single
   source of membership.
2. For each candidate, emit one row of extension fields only (§3). Rows join to
   the freeze by `candidate_id`; nothing the freeze already holds is copied.
3. Seal: `export_sha256` over canonical JSON, binding `freeze_sha256` (the
   sealed board's own hash) and `board_generated_at`. Require
   `exported_at < min(first pitch)` of the games it covers, the same rule
   `seal_board` uses.
4. Write once to
   `fc_acc_export/{date}/{board_generated_at}_{run_id}-{attempt}.json.gz`
   (gzip `mtime=0`), using `O_EXCL`. If the file exists and has the same sha,
   that is a no-op; a different sha raises.
5. No network, no model call and no mutation of any input dict. Every pool is
   only iterated, and `copy.deepcopy` is used for values that are serialized.

### Which customer-visible state could change?

None. The call runs after `write_json`/`write_markdown`/freeze. It reads only,
in its own `try`, and is test-enforced (§7).

**Delay.** It is pure serialization of about 1,000 rows. Expected well under 1 s,
to be measured in the dry run.

## 3. Minimal export schema (`fc-acc-001-export-1`)

```jsonc
{
  "schema_version": "fc-acc-001-export-1",
  "research_only": true, "public_eligible": false,
  "date": "YYYY-MM-DD",
  "run": {"run_id": "...", "attempt": 1, "event": "schedule|workflow_dispatch",
          "git_sha": "...", "board_generated_at": "...", "exported_at": "..."},
  "freeze_binding": {"freeze_sha256": "...", "record_count": 969},
  "versions": {"model_version": "...", "selection_policy_version": "...",
               "calibration_version": "...", "feature_version": "..."},
  "games": [{"game_pk": "...", "matchup": "...", "game_start_utc": "...",
             "status_at_run": "Pre-Game", "away_sp_id": 123, "home_sp_id": 456}],
  "games_excluded_started": [{"game_pk": "...", "status_at_run": "In Progress"}],
  "lineup_basis": [/* verbatim build_lineup_basis output, incl. provenance + observed_at */],
  "lineups_observed_at": "...",
  "rows": [{
    "candidate_id": "fc2:...",                // join key to the sealed freeze record
    "type": "batter|pitcher|combo",
    "opp_sp_id": 456,                          // null for pitchers/combos
    "lineup_slot": 3,                          // from lineup_basis, null if absent
    "projected_pa": 4.31, "projected_value": 1.2,
    "signals_scaled_0_100": {"lineup_slot": 71.0, "...": 0},   // verbatim bag
    "signals_absent": ["platoon_xwoba"],       // names in the family schema not present
    "cat_components": {"matchup": 61.2, "...": 0},
    "signal_weight_adjustment": 0.8,
    "options": [                               // EVERY priced threshold, chosen one included
      {"option_id": "fc2:...", "stat": "hits", "needs": 2, "side": "over",
       "line": 1.5, "p": 0.31, "raw_p": 0.33, "calibrated_by": "...",
       "chosen": false, "production_market_odds": 290,
       "production_price_status": "UNAUTHENTICATED_PRODUCTION_NAME_KEYED"}
    ]
  }],
  "export_sha256": "..."
}
```

**Fields deliberately excluded.** These are already in the freeze and reached
through `candidate_id`:
- identity;
- p, reliability, lift;
- eligibility and exclusion reasons;
- recommendation status and reasons;
- rank and selection flags.

Display text (`why`, `watchouts`) is also excluded.

The research loader joins the two and fails closed when
`freeze_binding.freeze_sha256` does not verify.

## 4. Authentication bindings (before first pitch)

None of these require touching production fetchers.

**Sequence.**
1. Production run: export is written, and picks are already pushed.
2. S1 capture step (same job, after the push step; §5): it
   - (a) fetches the statsapi `schedule?hydrate=lineups,probablePitcher`
     **raw bytes** into `RawArchive`. This is the same endpoint as
     `mlb_daily.fetch_lineups`;
   - (b) runs `fanduel.sweep(...)`, which archives the raw bytes.
3. Build the manifest (exists at `d316593c`) from the export plus the freeze rows
   as candidates. Seal it in the PROSPECTIVE ledger when
   `decision_at = capture completion < first pitch`.

| Binding | Mechanism | Fail-closed result |
|---|---|---|
| **Offer** | Offers are re-derived from archived FanDuel bytes and joined on (game_pk via a unique event map, player, stat, side, needs). Every `options[]` entry is joinable, not only the chosen line. The production price is never used as the decision price; any difference is flagged. | `MISSING_QUOTE`, `CONFLICTING_OFFERS`, `STALE_QUOTE`, `OBSERVED_AFTER_DECISION`, `INCOMPLETE_CAPTURE` (already implemented) |
| **Schedule** | Each export `game_pk`/`game_start_utc` must match the archived raw schedule. The status must be pregame at capture. | `SCHEDULE_UNVERIFIED` / `GAME_NOT_PREGAME_IN_RAW` (the game stays in the unit; the unit becomes NON_COMPARABLE) |
| **Lineup** | For batter rows, the export's `lineup_basis` slot and provenance must equal the archived raw lineup (player id at slot, both sides). Production's own lineup bytes stay unarchived; this is **independent corroboration**, not a tee, and the doc says so. | `LINEUP_UNCONFIRMED_AT_CAPTURE` (not confirmed in raw), `LINEUP_CHANGED_AFTER_RUN` (slot or player differs, or scratched). The row is excluded from alternative eligibility and the champion's exposure is recorded. |
| **Starter** | `opp_sp_id` and pitcher candidates must match `probablePitcher` in the raw schedule. | `STARTER_CHANGED_AFTER_RUN` |
| **Freeze** | `freeze_sha256` must verify with `board_freeze.verify_board_seal`. | Whole run rejected |

**Chronology rule (A2).** A run is usable for a unit only when the
following holds, where `unit_cutoff` is the unit's earliest scheduled first
pitch in the archived schedule minus 30 minutes:

`board_generated_at ≤ exported_at < capture_started ≤ capture_completed ≤ unit_cutoff`

Every included candidate, offer, schedule observation and feature observation
must be available by `unit_cutoff`. Nothing observed later can enter the unit.

**A3.** `unit_cutoff` is the **committed** cutoff (`../AMENDMENT_A3.md`). It is
fixed from a schedule observed by 09:00 (DAY) or 15:00 (NIGHT) New York time,
recorded write-once, and never moved later by a newer schedule. The decision
run is the last run completed by that committed cutoff. A missing commitment
gives `MISSING_CUTOFF_EVIDENCE`, so the unit is `NON_COMPARABLE`. This design
adds a cutoff-commitment step: a schedule fetch before the commitment deadline,
recorded and anchored. That step is not implemented as a workflow.

## 5. When it executes, and the risks

| Risk | Control |
|---|---|
| Production delay | The export is in-process after all customer writes, with no network and under 1 s. The capture is a **new step placed after "commit and push picks"** in `mlb-daily.yml`, with `continue-on-error: true` and `timeout-minutes: 12`. Picks are already pushed before it starts. *Alternative:* a separate `workflow_run` workflow with its own concurrency group. It keeps the job's 35-minute budget untouched but adds queue latency (a risk to pregame cutoffs). |
| Changed customer selections | The export is read-only after selection. A wiring test diffs `rank_for_board`/`select_main_board`/`write_json` output with the export forced on, forced off and forced to raise; all must be identical. A source test asserts the call site comes after `write_json` and sits inside its own `try`. |
| Postgame leakage | Started games are already removed by `bettable_games()` and recorded in `games_excluded_started`. The export seal requires `exported_at < first pitch`. The capture rejects any game not pregame in the raw schedule. Grading never reads the export until the ledger seal predates first pitch. |
| Missing or duplicate candidates | The id set must equal the sealed freeze's id set. `options[]` ids must be unique within a run. The count is recorded. Any mismatch raises. |
| Partial-slate selection bias | Exports are per run, append-only and write-once; they are never overwritten, and every run is kept for completeness.<br><br>The evaluation unit is (date, window): DAY is first pitch before 17:00 America/New_York (IANA conversion), NIGHT otherwise.<br><br>Each unit has **one** decision run: the last run whose export **and** capture completed at or before the common `unit_cutoff`. All unit games and all challenger selections are frozen from that single run. The rule is fixed in advance, so neither outcomes nor per-game timing can choose it.<br><br>Runs after the cutoff are retained but can **never** be selected into, or combined with, a sealed unit. A unit game absent from that run, or not pregame in the raw schedule, stays in the unit, counts toward the champion's N, and makes the unit `NON_COMPARABLE`. A unit with no qualifying run is `NO_PREGAME_RUN`. |
| Synthetic or unverifiable offers | Offers come only from archived raw bytes. The production price is labelled `UNAUTHENTICATED_PRODUCTION_NAME_KEYED`. A dry-run manifest can never enter the PROSPECTIVE ledger (already enforced). |
| Late-scratch step | The existing "Re-check the board for late scratches" step can alter the board after generation. The export reflects the generation-time decision. The capture's raw lineup check catches scratches (`LINEUP_CHANGED_AFTER_RUN`). |

## 6. Missing-data handling

- **Signals.** The production `_sig` stores only **scaled** values and omits
  absent inputs. The export copies the bag verbatim and lists the absent names
  against a fixed family schema, so an absent value is never imputed or
  zero-filled. Raw (unscaled) values stay `UNAVAILABLE_IN_PRODUCTION`.
  Capturing them would need `_sig` to also write a parallel `signals_raw` bag.
  That is a scoring-module touch, deferred and separately authorized. It
  cannot change any score.
- **Workload.** Prior-start outs, pitches and rest come from the S1 archived
  game log (`workload.py`, implemented). The pitch limit is
  `UNKNOWN_NO_VERIFIED_SOURCE`.
- **Market absent.** `production_market_odds: null` is kept as is. Authentic
  offer status always comes from the capture.
- **Export failure.** No export file means the run is `NO_EXPORT` for the
  research unit only. Picks are unaffected and there is no backfill.
- **Capture failure.** The manifest raises (root failure) or marks the
  candidate `INCOMPLETE_CAPTURE`. The unit is recorded as such, never re-tried
  after first pitch.

## 7. Tests (required before activation)

Extend the existing `test_mlb_fullboard_snapshot_wiring.py` pattern; no new
harness is needed.

1. **Completeness.** Pools with kept, QC-rejected and assumed rows give an export id set equal to the freeze id set. Removing one row, or duplicating one, raises.
2. **Options coverage.** Every `line_options`/`alternatives` threshold appears once with a unique `option_id`, and `chosen` is true exactly once per row.
3. **Non-mutation.** Pools are deep-equal before and after `write_run_export`.
4. **Selector invariance.** `rank_for_board`/`select_main_board`/`write_json` bytes are identical with the export on, off and forced to raise.
5. **Call-site.** An AST test asserts the call comes after `write_json` and the freeze block, sits in its own `try/except`, and is gated by `FC_ACC_EXPORT`.
6. **Chronology.** `exported_at ≥` first pitch raises, and a naive timestamp raises.
7. **Freeze binding.** A tampered freeze or a sha mismatch raises.
8. **Write-once.** The same content is a no-op; different content at the same path raises. Two runs on one date give two files, and the earlier file is unchanged.
9. **Absent ≠ zero.** An absent signal goes into `signals_absent`, never 0 or 50.
10. **No network.** `socket.socket` patched to raise during export: it passes.
11. **Schedule/lineup binding** (in the S1 package):
    - matching raw gives VERIFIED;
    - a changed slot gives `LINEUP_CHANGED_AFTER_RUN`;
    - a projected raw lineup gives `LINEUP_UNCONFIRMED_AT_CAPTURE`;
    - a changed starter gives `STARTER_CHANGED_AFTER_RUN`;
    - a started game gives `GAME_STARTED_BEFORE_DECISION`.
12. **Unit run choice (A2).** Runs at 15:30, 20:00 and 22:30 UTC, with EDT NIGHT games at 23:10 and 23:40 UTC, give a NIGHT cutoff of 22:40. The decision run is 22:30, provided its capture completed by 22:40. The run at 23:20 (after 23:10) is retained but rejected for the unit. With a DAY game at 17:05 UTC (13:05 EDT), the DAY cutoff is 16:35, so the decision run is 15:30. A unit with no run by its cutoff gives `NO_PREGAME_RUN`.
13. **Bounded growth.** Repeating an identical run adds zero bytes. The export never embeds `prior/` or earlier exports.

### Exact non-live verification procedure

1. `FC_ACC_EXPORT` unset, then run the full root test suite. Every
   pre-existing test result is unchanged (the 2 known MLB calibration failures
   are handled by #225).
2. Run `python3 -m unittest test_fc_acc_export test_mlb_fullboard_snapshot_wiring test_fc_acc_001_s1_capture`.
3. Replay a real sealed freeze. Load `output/board_freeze_2026-09-22.json`
   (985 records), rebuild minimal pool dicts from its records, export to a temp
   directory, and verify that:
   - the id sets are equal;
   - the size is recorded;
   - the sha is stable across two runs.

   This is labelled `DRY_RUN_NONCONFIRMATORY`, and its rows carry no options or
   signals, because the freeze never stored them.
4. Run the synthetic S1 dry run with the export as the candidate source. It
   must seal into the DRY_RUN ledger only.
5. Codex audits the diff, which must touch only:
   - the new module;
   - the single call site;
   - the tests;
   - optionally the workflow step.

## 8. Storage and retention

**Measured.** The freeze is 1.48 MB raw / 65 KB gzip for 985 records
(`board_freeze_2026-09-22.json`).

**Estimated export size.** The export adds roughly 1.5–2.5 KB per row raw
(options and signals), so about 1.5–2.5 MB raw and **about 0.1–0.25 MB gzip**
per full-slate run.

**Per day.** Up to 6 runs, with later runs smaller, gives about 0.4–1.0 MB
gzip a day. A 180-day season comes to **about 70–180 MB**.

**FanDuel raw archive.** Unmeasured at 1–5 MB gzip per sweep. That is the
dominant cost: with captures only at the 2 runs closest to the DAY and NIGHT
cutoffs, it is about 0.4–1.8 GB a season.

**Plan.**
- **Where.** Do not store this on `main`; it bloats the repo and per-date
  overwrites would recur. Use a dedicated append-only evidence branch,
  `research/fc-acc-001-evidence`, holding:
  - exports;
  - manifests;
  - the ledger;
  - a `raw/` content-addressed object store, with files at most 100 MB each.

  Every commit only **adds** new content-addressed files. There is no `prior/`,
  no nesting of earlier artifacts and no rewriting, which follows the lesson of
  the NFL link-7 growth.
- **Capture frequency.** Only for the last run before each unit's A2 cutoff, not
  all 6 runs. Exports are tiny and are kept for every run.
- **Retention.**
  - Ledger, manifests and exports: permanent (small).
  - Raw FanDuel and statsapi objects: kept until the pre-registered evaluation
    window is graded and Codex-certified, plus one season.
  - After that, pruning needs Jacob's approval. The ledger keeps every
    `raw_sha256`, so pruned evidence degrades to hash-only, and that downgrade
    is recorded explicitly.
- **Guard.** A CI check fails if the evidence branch grows by more than
  25 MB in a day or contains a nested archive.

## 9. Implementation complexity, and the smallest change after authorization

**Smallest change.**
1. New `fc_acc_export.py` at the repo root, next to `board_freeze.py`: about
   150 lines, pure.
2. **One** new `try/except` block of about 12 lines in `generate_picks.main()`,
   immediately after the freeze block, gated by `FC_ACC_EXPORT`.
3. `test_fc_acc_export.py` (tests 1–10 and 13) plus 2 additions to
   `test_mlb_fullboard_snapshot_wiring.py`.
4. In `research/fc_acc_001/s1/`:
   - `schedule.py` (archive raw schedule; verify the lineup, starter and start
     time), about 120 lines;
   - `manifest.build` accepting export rows plus `options[]`;
   - the A2 unit run-selection rule (one run per unit, completed by the unit cutoff).

**Separately authorized activation, later:**
- one workflow step;
- the evidence-branch writer;
- setting `FC_ACC_EXPORT=1`.

**Complexity: low to moderate.** About 300 production-adjacent lines plus about
200 research lines. Production behaviour is unchanged when the flag is off and
also when it is on. The real risk is wiring the workflow step and the evidence
branch, not the export itself.

**Not proposed:**
- changes to `_sig`, fetchers, the selector, the calibrators or the freeze schema;
- a database;
- a second scoring engine;
- any backfill of past slates. Historical freezes lack options and signals, and
  that negative result is preserved.

Alligator
