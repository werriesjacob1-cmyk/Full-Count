# Preregistration: MLB accuracy challenger program, v2 (2026-10-01)

**This version supersedes v1** (`research/mlb_accuracy_challenger_prereg_20261001.md`, commit `0ebd6152c2`, plus amendment 1 at `96884cb968`).

**The commit that adds this file is the v2 evidence boundary.** The locked boundary timestamp is **2026-10-02T05:00:00Z**. Only slates whose board cutoff is strictly later than that are evaluated.

**Research only.**
- Nothing here changes the production model, selector, calibration, Top Picks, the public ledger, or any customer surface.
- Promoting anything requires Jacob's explicit, separate decision.
- Nothing here is merged or deployed.

## 0. Why v2 exists, and what happened to v1
Codex's audit (Issue #91, comment 5935759204) required changes before the lock:
- one common operational candidate universe;
- a hashed same-cutoff manifest per slate;
- explicit player-prop quote provenance;
- separating probability quality from picking performance;
- an operational equal-volume rule;
- dependence sensitivity;
- market-family discipline.

v1 met none of these at the required standard. In particular, v1 used the board's `market_odds`, which has no per-quote timestamp, and it did not apply the published path's freshness and public-eligibility gates.

**What happened to v1:**
- v1 is **withdrawn**. It never produced an evaluation.
- No slate was ever scored under it.
- No graded file dated after 2026-09-30 has been read by this program.
- The v1 boundary, 2026-10-01T18:00:00Z, passed during this revision. No board sealed after it had a graded file when v2 was written.
- v1's files stay in the repository unchanged, as history.

v2 reuses v1's frozen challenger probabilities and decision table **unchanged**, as described in §6 and §10.

## 1. Required answers (lock checklist)
| Question | Answer |
|---|---|
| Historical confirmatory holdout | **NONE** |
| Primary confirmatory path | **PROSPECTIVE ONLY** |
| Common operational candidate universe | **YES** (§5; `manifest.py`) |
| Same cutoff across all arms | **YES**: one cutoff per slate, the frozen board's `sealed_at` (§4) |
| Actual player-prop quote provenance required | **YES**: an exact captured FanDuel quote from `data/props`, taken at or before the cutoff, no older than 45 minutes (§5.3) |
| Equal eligible volume rule locked | **YES** (§7) |
| Outcomes unseen at lock | **YES** (§12) |

The prospective experiment is ready to seal.

The supply risk and the external dependencies are stated plainly in §13. They do not change any answer above.

## 2. Research question
Within one common operational candidate universe, frozen at one cutoff per slate:
- the champion is the production Top Pick selector as it actually ran;
- C2 ranks candidates by the model's information **beyond the captured price**;
- C2 takes exactly as many picks as the champion.

Does C2 produce a higher realized hit rate without buying it with chalk?

## 3. What is already known (hypothesis generation only, never evidence)
**PR #219 is exploratory and stays exploratory.** Its forward-chained 2026 results:
- price-only recalibration beats the raw model by −0.0082 log loss;
- model + price adds +0.0006 over price;
- 75 Top Picks at a stated 64.2% realized 53.3%;
- pitcher outs is the one family where model + price helped;
- combined starter strikeouts runs high.

This branch's `exploratory/` folder reproduces #219 exactly.

**The equal-volume selector comparison is not promotion-grade.** This covers both #219's comparison and this branch's exploratory diagnosis, where a residual selector hit 61.3% against the champion's 53.3% on 75 picks inside the price band. The reasons:
- it uses development outcomes that were already inspected;
- its prices have no quote timestamp;
- there was no common operational universe (no freshness or public-eligibility gates);
- the price band was chosen after looking.

It only motivated choosing C2 as the primary arm. #219 is not revised, weakened, or closed by this document.

**Other prior use of 2026 outcomes.** Both disclosures from v1 §2 still stand:
- the pitcher-outs shrinkage experiment of 2026-09-20;
- the aggregate counts of the 2026-09-29 graded board.

**Seen while writing v2 (non-outcome fields only).** I read the following from frozen boards and `data/props` on main, without opening any graded file:
- 2026-08-04 to 2026-10-01: record fields, `sealed_at`, and recommendation status;
- capture timestamps, prices and in-play flags, used to measure supply under the v2 rules (§13).

The 2026-10-01 board (sealed 02:27Z, before the v2 boundary) was also run through the manifest builder into a scratch folder. It had 0 eligible rows. That board is not evaluable.

## 4. Evidence class, cutoff, and boundary
**Evidence class.** Prospective full-candidate data only:
- frozen boards `output/board_freeze_{date}.json`;
- the linked graded file `output/board_freeze_graded_{date}.json`;
- the per-slate manifest built from them.

Canonical 2026 regular-season data is development only. The public Top Pick ledger is never read for scoring and never modified.

**Common cutoff `T_d`.** The frozen board's `sealed_at`.
- `seal_board` refuses any seal at or after the earliest referenced first pitch, so `T_d` is always pregame.
- Every arm, the champion included, sees exactly the board records and captures available at `T_d`.

**Boundary.** A slate is evaluable only if `T_d` is strictly later than 2026-10-02T05:00:00Z, compared as parsed UTC datetimes. The development window 2026-08-04..2026-09-27 is refused outright. Both rules are enforced in `harness_v2.evaluate`.

## 5. Common operational candidate universe: the same-cutoff manifest (`manifest.py`)
One manifest per slate. It is built from **only** the frozen board and the `data/props` files for the cutoff's UTC date and the day before; a slate's captures can sit in either UTC-dated file. The builder has no outcome input, and a test checks its signature.

Every board record appears in the manifest with:
- `candidate_id`, `game_pk`, `player_id`, `player_name` and `player_norm`;
- `stat`, family status, `needs`, `line` and side;
- the game's start time;
- the board price, plus the captured quote's odds, `taken_at`, age and source;
- `in_play` status;
- `q_raw`, and `q_devig` where a real two-sided quote exists;
- `p0`, reliability, recommendation status and champion flag;
- `eligible`, plus exactly one exclusion reason.

The manifest is hashed: `manifest_sha256` is the sha256 of its canonical JSON. The harness refuses a manifest whose hash does not rebuild, and a graded file not linked to the manifest's `board_sha256`.

### 5.1 Eligibility
These are the same operational gates as the published Top Pick path, short of the champion's own selection thresholds. The first rule that fails is recorded as the exclusion reason.

1. **Family status** is PRIMARY or EXPLORATORY (§9). Otherwise `FAMILY_RED_FLAG_SEPARATE` or `FAMILY_NOT_A_PLAYER_PROP`.
2. **Unique identity** (game, player, stat, needs, side). Otherwise `DUPLICATE_IDENTITY`, and every copy is excluded.
3. **Supported settlement mapping.** Side is `over` and `needs` is an integer threshold. This is what `board_freeze_grader` settles for these stats. Otherwise `SETTLEMENT_UNSUPPORTED`.
4. **QC:** `qc_status == "kept"`. Otherwise `QC_NOT_KEPT`.
5. **Lineup policy / starter confirmation:** `lineup_assumed is False`. This is production's rule. Otherwise `LINEUP_OR_STARTER_NOT_CONFIRMED`.
6. **Public eligibility**, which is `recommendation.py`'s non-selection gates:
   - a probability exists;
   - `sample_n != 0`;
   - reliability is A or B (`TOP_PICK_MIN_RELIABILITY`).

   Otherwise `NO_PROBABILITY`, `NO_TRACK_RECORD` or `RELIABILITY_NOT_PUBLIC_ELIGIBLE`.
7. A **recommendation status** exists, and **`model_version == 2026.08.15`**.
8. **Board price present** and **board fresh:** `T_d − board_generated_at` is at most 4 h (`MAX_BOARD_AGE_SECONDS`).
9. **Not started:** the game's first pitch is after `T_d`.
10. **Quote provenance** (§5.3) holds.
11. **Price band:** the captured price's raw implied probability is in **[0.40, 0.70]**, unchanged from v1.

### 5.2 Champion
The champion is a record with `recommendation_status == "top_pick"` on the frozen board, which is the production selector as it ran at `T_d`.
- Its picks count toward the comparison only if they pass §5.1 in a PRIMARY family.
- Every champion pick excluded from the comparison is counted by reason in the manifest (`champion_excluded_by_reason`) and reported.

### 5.3 Price provenance (locked)
| Item | Rule |
|---|---|
| Source | FanDuel player-prop offers captured by `prop_snapshot.py` into `data/props/props_{UTC date}.json`. Hits and hits_runs_rbis (one-sided Yes/Over) come from `snapshots[].rows`. Pitcher strikeouts and pitcher outs (two-sided Over/Under) come from `two_sided_snapshots[].rows`. |
| Never used | `data/odds` (game lines only), inferred prices, closing prices, consensus prices, and prices reconstructed after the cutoff. |
| Quote timestamp | The capture's `taken_at`, which is the start of the sweep. A sweep is bounded at 240 s, so measured age is conservative. |
| Which capture | The single latest capture of the relevant type with `taken_at ≤ T_d`. Captures after `T_d` are invisible. |
| Stale-quote policy | `T_d − taken_at` must be at most 45 min (`MAX_PRICE_AGE_SECONDS`, inclusive). Otherwise `QUOTE_STALE`. |
| Suspended / pulled quote | Absent from that latest capture means `NOT_QUOTED_AT_CUTOFF`; an earlier capture never stands in. An in-play flag means `IN_PLAY`. A one-sided row whose `start_time ≤ T_d` means `GAME_STARTED`. |
| Ambiguity | Several matching rows with different prices mean `AMBIGUOUS_QUOTE`. |
| Exact price match | The captured American price must equal the board's `market_odds`, which is the price the champion acted on. Otherwise `PRICE_MISMATCH_BOARD_VS_CAPTURE`. The board's own live fetch is not persisted per quote, so the capture is the only timestamped evidence that the price existed. |
| Matching key | `player_norm` uses the byte-for-byte rule of `odds_fanduel.normalize_name`, plus stat (with alias `stolen_base→stolen_bases`) and integer `needs`. Two-sided rows match on `market` instead of `stat`. |
| Odds to implied | American odds o: if o < 0, q = −o/(−o+100); otherwise q = 100/(o+100). This `q_raw` is used by every arm and every primary metric. |
| De-vig | Possible **only** where the same two-sided capture holds both Over and Under prices: `q_devig = q_side / (q_over + q_under)`. Recorded and reported descriptively only. |
| Missing opposite side | No de-vig (`ONE_SIDED_NO_DEVIG`). The opposite side is **never invented**. One-sided batter props never have one. A two-sided row missing the requested side is `SIDE_NOT_OFFERED`. |

### 5.4 Sealing manifests before outcomes
`seal_manifests.py` builds manifests from `origin/main` at a given commit, with these rules:
- It **refuses** a date whose graded file already exists at that commit.
- It **refuses** a date whose board is not yet final: the commit time is before the earliest first pitch, so a reseal is still possible.
- It never rewrites an existing manifest.

Written manifests are committed to this branch under `research/mlb_accuracy_challenger_20261001/manifests/`.

`run_eval_v2.py` admits a slate **only if** all of the following hold:
- the manifest's first commit is strictly earlier than the graded file's first commit on main;
- the hash rebuilds;
- the graded file links to the manifest's board.

Late, missing or unlinked slates are listed and excluded, never repaired.

The commit timestamps are corroborated by the push timeline of draft PR #220. Manifests are sealed by a scheduled runner (§13).

## 6. Arms (frozen, unchanged from v1)
The coefficients are in `frozen_coefficients.json`, sha256 `3c9e2c01…2009`, fitted once on development rows by `fit_frozen.py` and never refitted. They apply per family, with a pooled fallback.

| Arm | Model |
|---|---|
| C0 (market) | p1 = logistic(a + c·logit q) |
| C1 (calibrated model) | pcal = logistic(a + b·logit p0) |
| C2 (market + model) | p2 = logistic(a + b·logit p0 + c·logit q) |
| C3 (pitcher outs) | C2 restricted to pitcher_outs, tested on proper score only (§10) |

Here q is the captured `q_raw`.

**Selector keys:**

| Arm | Status | Ranks by |
|---|---|---|
| **C2_RESIDUAL** | primary | p2 − p1 |
| C1_CALIBRATED_EDGE | secondary | pcal − q |
| C0_MARKET | secondary | p1 |
| RAW_MODEL | secondary | p0 |

## 7. Equal eligible volume (locked)
- On slate d, **N_d** is the number of champion picks in the PRIMARY eligible universe of that slate's manifest.
- Each challenger takes **exactly N_d** rows from the same PRIMARY eligible universe, by its key. Ties break by `candidate_id`.
- No backfill, no carry-over between slates, and no outcome in selection.
- The universe always holds at least N_d rows, because the champion's picks are in it.

**Zero-champion-pick slate rule.** If N_d = 0, every arm takes 0 picks on that slate. The slate is still counted (`zero_champion_slates`) and contributes nothing to any hit rate.

**Settlement.** Picks graded neither hit nor miss (void, push, ungraded) are reported per arm and leave the hit-rate denominator. They are never re-picked.

## 8. Metrics: probability quality and picking performance are reported separately
**Probability quality.** Computed on the same rows: every settled PRIMARY eligible row, whether picked or not.
- Log loss and Brier score for p0, q_raw, p1, pcal and p2.
- Calibration tables in bins [0, .4), [.4, .5), [.5, .6), [.6, .7), [.7, 1].
- Residual information, LL(p2) − LL(p1), with a game-clustered CI.
- The same pieces per primary family.
- On the two-sided subset only: LL of q_raw versus q_devig versus p2.

All descriptive except C3.

**Picking performance.** Per arm:
- picks, settled picks, wins, hit rate;
- the mean implied probability of the captured price, and hit rate minus that mean;
- 1-unit ROI at the captured price;
- picks per slate, slates with picks, zero-champion slates;
- market mix, maximum picks in one game, maximum picks on one player, and the share of repeated-player picks;
- the overlap, champion-only and challenger-only tables.

**ROI** is reported because every price is an exact, timestamped, pre-cutoff captured quote equal to the price the champion acted on. No ROI is computed anywhere a price was not observed this way.

## 9. Market-family discipline (locked)
| Family | Status | Role |
|---|---|---|
| hits, hits_runs_rbis, strikeouts (pitcher), pitcher_outs | **PRIMARY** | The primary overall endpoint universe |
| pitcher_outs | also **C3** | A separate proper-score hypothesis, never pooled into the primary claim |
| combined_strikeouts | **RED_FLAG_SEPARATE** | Never in the primary universe. It has no captured quote: FanDuel's market is a one-sided ladder that `prop_snapshot.py` deliberately does not capture. Champion picks in it are counted and their hit rate reported, descriptively. Excluding it from production Top Picks is a prospective production recommendation for Jacob, not applied here. |
| nrfi_combined, first_inning_run | **NOT_A_PLAYER_PROP** | Excluded, counted |
| every other stat (total_bases, stolen_base, hard_hit_105, moonshot_420, rbis, runs, singles, doubles, triples, home_runs, …) | **EXPLORATORY** | Same manifest rules. Reported **by named stat**, never as one bucket. Eligible rows and champion picks are counted. No claims, no promotion. |

**Primary-family sensitivity (descriptive, predeclared).** The equal-volume comparison is rerun on the PRIMARY universe without pitcher_outs.

**No promotion from a post-hoc subgroup.** This covers any family, any month, and any slice not named here.

## 10. Decision rules (locked; v1's `harness.verdict` and `c3_pitcher_outs`, unchanged)
**Primary.** Regime B only. The metric is C2 hit rate minus champion hit rate, at equal volume.

| Verdict | Condition |
|---|---|
| INSUFFICIENT_N | Fewer than 250 champion settled picks, or fewer than 60 slates with N_d > 0 |
| REJECTED | Difference ≤ 0 |
| SUPPORTED | The one-sided 95% game-clustered bootstrap lower bound is > 0 (B = 2000, seed 20261001), **and** the chalk guard holds: C2 mean q − champion mean q ≤ 0.03 |
| INCONCLUSIVE | A positive difference that fails the bound |
| INCONCLUSIVE_CHALK_GUARD | A positive difference that passes the bound but fails the chalk guard |

**Dependence.**
- The game cluster, (date, game_pk), is decisive.
- Player-clustered and ISO-week-clustered bootstraps (same B and seed) are reported as sensitivity. If either lower bound is ≤ 0 while the game bound is > 0, the report says so beside the verdict.
- Repeated-player exposure is reported per arm (§8).

**C3 (pitcher outs)**, the mean paired LL(p2) − LL(p1) over settled eligible pitcher_outs rows, game-clustered:

| Verdict | Condition |
|---|---|
| INSUFFICIENT_N | Fewer than 150 rows |
| SUPPORTED | CI upper bound < 0 |
| REJECTED | Mean ≥ 0 |
| INCONCLUSIVE | Otherwise |

A SUPPORTED verdict is a **promotion candidate only**. Promotion needs Jacob's explicit decision, reported alongside ROI, supply and the sensitivity results.

## 11. Evaluation regimes and looks
**Regime A: POSTSEASON_2026_SHADOW**
- Covers slates with cutoff after the boundary through the end of the 2026 World Series.
- **Descriptive only.** It never gets a primary verdict and is never pooled.
- One report after the Series.

**Regime B: CONFIRMATORY_2027_REGULAR**
- **Count-only checks** on 2027-07-13 and 2027-09-01. They read manifests only, counting champion picks in the primary universe and slates with N_d > 0. No graded file is opened.
- The single analysis runs at the first check where the minimum (250 / 60) is met.
- Otherwise it runs once on 2027-10-05, after the regular season, with whatever exists.
- No other looks.

## 12. Leakage prohibitions
1. No refit, re-band, re-threshold, re-key or reselection after the boundary.
2. No graded file is opened before an analysis date. Count-only checks read manifests only.
3. No development-window slate and no slate at or before the boundary; the harness enforces both. Only on-time manifests are admitted; the runner enforces that.
4. No inferred, closing, consensus or `data/odds` price, and no invented opposite side.
5. The public ledger and settled grades are untouched.
6. Postseason is never pooled with Regime B.
7. **Any change after this commit is a new version with a new boundary,** committed before the affected data exist.

Changes to capture cadence or board-run timing in production do not change any rule here. They change supply only, and are recorded in the report.

**Outcomes unseen at lock.** Since #219 (2026-09-27), this program has read graded data only from the development window. The single exception is the disclosed 2026-09-29 aggregate counts. No graded file for any date after 2026-09-30 exists in this program's reads.

## 13. Supply, dependencies, and known limitations (stated, not hidden)
**Measured supply under these exact rules.** Pre-boundary boards 2026-09-20..09-30, 10 slates, non-outcome fields only:
- **22 of 48** champion Top Picks fall in the primary universe.
- On **6 of 10** slates the latest capture before the cutoff was more than 45 minutes old, so the slate had an empty universe.
- Where a fresh capture existed, 29 rows had a board price that differed from the capture.

`odds-snapshot.yml` is scheduled hourly but in practice ran about 4–6 times per day. **Risk:** at about 2 champion picks per slate, the 250-pick minimum may be reached only late in 2027, and the verdict may be INSUFFICIENT_N.

Making capture more reliable, or capturing at board seal, is a **production pipeline change for Jacob**, not made here. It would not change any rule in this document.

**Dependencies:**
- The production model stays at `2026.08.15`. A re-version removes later slates from the universe, and continuing would then need v3.
- The board-freeze grader and the `data/props` schema stay as they are.
- The manifest-sealing runner keeps running. A missed seal makes that slate late, and late slates are excluded.

**Limitations:**
- The board has no dedicated starter-confirmation field. The rule is production's `lineup_assumed is False`, plus a live, non-in-play pitcher market in the cutoff capture.
- A capture's `taken_at` is snapshot-level, not per quote.
- Commit timestamps are self-asserted. They are corroborated by the PR push timeline.

## 14. Provenance
| Item | Value |
|---|---|
| `manifest.py` sha256 | `0b6f9178674c571df87a7cddb98b26c9d5635838f1a5a89d4bd8e53efffa6bdc` |
| `harness_v2.py` sha256 | `30d60d883a9f115eb336ab2b98f4b409c1304168c4de041ff2525bf9d49a8c40` |
| `seal_manifests.py` sha256 | `60208c96ab8f2c4ec12bccfab0214cafca0a3c6bdee3ce9ca7d9ac76595821f1` |
| `run_eval_v2.py` sha256 | `9120d4bdc2470d9be6a647cedfdf847c5713a222a33be35d855aee611432cb04` |
| `test_v2.py` sha256 | `946e478e66bdae3f7f2102d03f3041806be24fab3415bc5f18b278bd5f3eb671` (16 tests; mutation-checked: 15 rule mutants, each killed) |
| `harness.py` (v1, reused) sha256 | `d355a12b597556341b1f7f8133fbbfc4d2ccb667057f370890edad2dd3631857` |
| `frozen_coefficients.json` sha256 | `3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009` |
| `fit_frozen.py` sha256 | `3c326f527f2d3520bdc249f727d71e5fc852f40b9a144f70547dda5d339a9789` |
| Development data snapshot | `58abe9f2e5a2b69d0f8bc98c22c8bafe7b00a224` |
| #219 head | `68fa5d06384691e4a32b968a92eefb99820f76e1` (unchanged) |
| Main read for the supply measurement | `a191eb720390982dce32487c4170c509533794c8` |
| Production constants mirrored | `recommendation.py` on main: `MAX_PRICE_AGE_SECONDS = 45*60`, `MAX_BOARD_AGE_SECONDS = 4*3600`, `TOP_PICK_MIN_RELIABILITY = ("A","B")` |

Alligator.
