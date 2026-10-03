# Preregistration: MLB accuracy challenger program, v3 (2026-10-01)

**Status: CURRENT PROPOSED PROTOCOL, READY FOR INDEPENDENT REVIEW. NOT ACTIVATED.**

**Research only.**
- Nothing here changes the production model, selector, calibration, Top Picks, published picks, the public ledger, or any customer surface.
- Nothing is merged or deployed.
- Prospective collection starts only after Jacob separately authorizes activation (§16).

**Boundary.** The commit that adds this file is the v3 research boundary. It is externally anchored by an Issue #91 comment and RFC 3161 timestamps over this file's sha256 (§17).
- The locked boundary timestamp is **2026-10-02T06:00:00Z**, enforced as `evaluate_v3.V3_BOUNDARY_UTC`.
- Any slate unit cut off at or before it is **NONCONFIRMATORY / DRILL ONLY**.

**Implementation:** `research/mlb_accuracy_challenger_20261001/v3/`. Files:
- `capture.py`
- `shadow.py`
- `manifest_v3.py`
- `seal.py`
- `regimes.py`
- `evaluate_v3.py`
- `runner.py`
- `test_v3.py`
- `tsa_certs/`

The challengers are reused **unchanged** from v1: `../frozen_coefficients.json` and `../harness.py`.

## 0. Why v3 exists
Codex's independent audit of v2 at `9265935966` (Issue #91, comment 5938236903) found that v2 failed on five counts:
1. **Common universe.** The price join ignored game and event identity. It treated frozen `recommendation_status` as if it proved customer publication.
2. **Price provenance.** A snapshot's `taken_at` marks the *start* of a sweep. A partial sweep could make a game it never fetched look "pulled".
3. **Seal integrity.** The sealer waited until after first pitch on purpose. The runner trusted self-asserted git timestamps.
4. **Postseason separation.** This was written in prose only and was not enforced.
5. **Runner architecture.** Seals went to the mutable PR branch.

Codex also raised three concerns:
- freezing the live production model version;
- 250/60 is a coverage floor, not a power justification;
- the C2/C3 hierarchy.

Jacob directed V3 before any prospective slate.

**v1 and v2 are SUPERSEDED, NONCONFIRMATORY** (`research/mlb_accuracy_challenger_SUPERSEDED.md`).
- No v1 or v2 slate was ever scored.
- Both v2 seal routines were disabled at 2026-10-01T18:51Z before their first firing. They were `trig_01NJyVApkdSbMBK1ojRp9WY6` and `trig_019HwkqAnwzySnTBmYnhVJU7`, with runner `session_019i5ReaXMnSDN3ZRBdYthZs`.
- No v2 manifest exists on any branch.
- No v2 slate may ever be called confirmatory.

## 1. Lock checklist
| Item | Answer |
|---|---|
| Historical confirmatory holdout | **NONE** |
| Primary confirmatory path | **PROSPECTIVE ONLY** (2027 regular season) |
| Common universe | **YES** (§6) |
| Exact quote identity | **YES** (§5) |
| Capture complete before cutoff | **YES** (§4, §5) |
| External pregame seal | **YES**: Issue #91 server receipt + RFC 3161 token, both before the earliest covered first pitch (§7) |
| Regime enforcement | **YES**, in code (§11) |
| Shadow champion independent of production | **YES** (§8) |
| Equal volume locked | **YES** (§12) |
| Outcomes unseen at V3 lock | **YES** (§18) |

## 2. Research question and the single promotion decision
**The question.** On one common, pregame-sealed operational candidate universe, frozen per slate unit, compare:
- **C2**, a selector ranking candidates by the frozen model's information beyond the exact captured price, p2 − p1;
- **the frozen shadow champion**.

C2 takes exactly as many picks as the champion. Does C2 achieve a practically meaningful higher realized hit rate without buying it with chalk?

**There is exactly one promotion decision:** the primary C2 verdict in Regime B (§13).
- **C3**, pitcher outs on proper score, is **secondary**. It is reported with its interval and can never by itself trigger promotion. Because only the C2 verdict can promote, no multiplicity adjustment is applied to it.
- C0, C1 and RAW are descriptive secondary arms.

## 3. What is already known (hypothesis generation only)
**Exploratory evidence.**
- #219 and this branch's `exploratory/` reproduction and diagnosis are exploratory and stay exploratory.
- Their equal-volume selector comparison is **not promotion-grade**. The outcomes had already been inspected, the quotes had no timestamps, there was no operational universe, and the band was chosen after looking.

**Non-outcome inputs seen during v1–v3 work.** These are disclosed in v1 §2 and v2 §3. During v3:
- the drill and trace of the pinned pipeline on 2026-10-01;
- the end-to-end runner drill on the 2026-10-01 NIGHT unit (§17);
- live TSA tests.

No graded file of any date after 2026-09-30 has been opened by this program.

## 4. Sequence and cutoff (per slate unit)
**Slate unit.** A slate unit is (date, window).
- **DAY:** games whose scheduled first pitch is before 17:00 America/New_York.
- **NIGHT:** games at or after 17:00 America/New_York.

Each game belongs to exactly one unit. Each unit is sealed at most once. Supersession is rejected (`verify_chain`).

**Runner sequence.** `runner.py` runs these steps. Every one must finish before the earliest covered first pitch.
1. **Frozen shadow champion run** (§8). This produces the shadow board.
2. **Dedicated FanDuel capture** (`capture.py`), recording `CAPTURE_STARTED_AT`, per-event fetch start and completion, per-tab success, and `CAPTURE_COMPLETED_AT`.
3. **MLB statsapi schedule snapshot**, covering game_pk, teams, start time, gameType, doubleheader flag and probable pitchers.
4. **Manifest build.** `MANIFEST_CUTOFF` is the time of building, at or after capture completion.
5. **External seal and receipt verification** (§7).
6. **Stop.**

**Quote freshness.** `MANIFEST_CUTOFF − CAPTURE_COMPLETED_AT ≤ 45 min` is a ceiling. On the normal path it is about 1–3 minutes: the drill measured the pipeline at 164 s.

## 5. Quote identity (`manifest_v3.resolve_quote`)
A board candidate binds only to the exact offer. Every field below must hold.

| Field | Rule |
|---|---|
| Book | `fanduel` |
| Game/event | The board's `game_pk` maps one-to-one to a captured FanDuel event. The match needs normalized away and home teams equal to the statsapi schedule, and \|openDate − scheduled start\| ≤ 90 min. Zero matches gives `EVENT_NOT_MAPPED`. Several matches, or an event claimed by two games, gives `EVENT_MAPPING_AMBIGUOUS`. **Doubleheaders fail closed** unless the times separate them. |
| Market | One-sided: the exact `marketType` for (stat, threshold), from a frozen one-to-one table copied from `odds_fanduel.MARKET_MAP` at the pin. Pitcher Ks: `PITCHER_[A-F]_(TOTAL_)STRIKEOUTS`. Pitcher outs: `*_OUTS_RECORDED_SB`. |
| Player | The normalized runner name equals the normalized board `player_name`. |
| Team | When FanDuel shows a team slug, it must equal the board team's slug. Otherwise `QUOTE_TEAM_MISMATCH`. |
| Side | Over/Yes. Two-sided markets also require the runner side. |
| Line | Pitcher markets require the runner handicap or embedded line to exactly equal the board `line`. One-sided markets require the exact threshold. |
| Odds | The captured `americanOddsInt` must equal the board `market_odds`. Only representation is normalized (`+100` = `100`); prices are never substituted. |
| Capture identifier | `capture_sha256` + `event_id` + `market_id` + `selection_id`, all recorded. |
| Status | `marketStatus == OPEN` and `runnerStatus == ACTIVE`, otherwise `MARKET_SUSPENDED`. In-play gives `IN_PLAY`. |
| Uniqueness | More than one matching offer gives `QUOTE_AMBIGUOUS`. |

**Capture states. These are never conflated.**
- `CAPTURE_INCOMPLETE`: no completion time.
- `CAPTURE_COMPLETED_AFTER_CUTOFF`.
- `QUOTE_STALE`: older than 45 min.
- `EVENT_NOT_OBSERVED`: a required tab failed, the event was not fetched, or the event was not completed by the cutoff.
- `MARKET_ABSENT`: only when the event was fully observed and the offer was not posted.
- `QUOTE_NOT_PROVEN`.

No later capture can repair an earlier gap. There is no backfill.

**Never used:** `data/odds` for prop prices, `data/props` sparse snapshots, inferred prices, closing prices and consensus prices.

**De-vig.** Computed only when the same captured market holds a real, active opposite runner at the same line. It is descriptive only, and no opposite side is ever invented.

## 6. Common universe (`manifest_v3.build_manifest`)
**Gates.** Every shadow-board record of the unit is recorded with pass or fail for each gate, in this order. The first failure is its exclusion reason.

| # | Gate | Rule |
|---|---|---|
| 1 | Family | PRIMARY or EXPLORATORY (§9) |
| 2 | Schedule | The game is in the statsapi snapshot |
| 3 | Window | The game belongs to this unit |
| 4 | Duplicate identity | (game, player, stat, needs, side) is unique; otherwise every copy fails |
| 5 | Settlement | Side is over and the threshold is an integer, as the board grader settles |
| 6 | QC | `qc_status == "kept"` |
| 7 | Lineup | `lineup_assumed is False` |
| 8 | Starter | Pitcher markets only: the player is the MLB-listed probable pitcher for that game. This is recorded as `MLB_PROBABLE_LISTED_NOT_INDEPENDENTLY_CONFIRMED` and **never labelled confirmed**. |
| 9 | Probability | A probability is present |
| 10 | sample_n | `sample_n ≠ 0` |
| 11 | Reliability | A or B |
| 12 | Recommendation status | Present |
| 13 | Shadow version | Record provenance equals the shadow pin and its labels |
| 14 | Board price | Present |
| 15 | Board freshness | Sealed at or before the cutoff, and generated at most 4 h before it |
| 16 | Game not started | First pitch is after the cutoff |
| 17 | Event mapping | §5 |
| 18 | Quote | §5 |
| 19 | Exact price match | §5 |
| 20 | Price band | Captured raw implied probability in **[0.40, 0.70]**, unchanged |

**Hashing.** The manifest is hashed (`manifest_sha256`). It records:
- the shadow board, capture and schedule hashes;
- the covered games;
- the earliest covered first pitch;
- the event mapping and its failures;
- counts by exclusion reason, including the shadow champion's exclusions.

The builder has no outcome input, and a test checks its signature.

**Intentional difference from production, stated plainly.** The champion's `p ≥ 0.60` and pessimistic-CI value test decide which records it selects. Challengers instead rank every record that passes the non-selection gates above. Challenger picks are therefore **not** claimed to meet current public Top Pick policy.

## 7. External pregame seal (`seal.py`, `runner.publish_seal`)
**Seal record.** The seal binds:
- prereg version v3 and this file's sha256;
- the challenger version, which is the sha256 of `frozen_coefficients.json`;
- the frozen shadow champion id;
- the date, window and cutoff;
- the manifest, shadow-board, capture and schedule sha256s;
- the covered games and the earliest first pitch;
- `prev_seal_sha256`, a hash chain from the genesis record.

**Evidence ref.** `claude/mlb-challenger-v3-evidence` is a dedicated branch, separate from the development branch.
- It is append-only: there is one directory per slate unit, plus `CHAIN.json`.
- It is never force-pushed, and existing directories are never rewritten.
- GitHub branch protection cannot be set from this environment. That limitation is covered by the hash chain and by independent receipts that list every seal hash.

**A seal counts only with both receipts, each strictly before the earliest covered first pitch.**
- **R1 — GitHub Issue #91 comment.** The comment body contains `seal_sha256`. The evaluator re-fetches it by id and uses GitHub's server `created_at`.
- **R2 — RFC 3161 token.** At least one token over the `seal_sha256` bytes from FreeTSA or DigiCert, verified cryptographically by the evaluator. The FreeTSA chain is pinned in `tsa_certs/`; DigiCert uses the system store. Both TSAs are requested, for two independent timestamps.

**Git times are ignored.** A pregame local commit with a postgame receipt is `LATE_SEAL`.

**If any of the following happens, the slate unit is `SLATE_INVALID_NO_CONFIRMATORY_USE`:**
- a missing or late receipt;
- a hash that does not rebuild;
- a seal that does not bind its manifest;
- a seal that is not in the chain;
- a chain break (rewrite, deletion or reorder);
- a second seal for the same unit.

Such a unit is lost. It is never repaired, never re-sealed and never backfilled.

## 8. Frozen shadow champion (`shadow.py`)
**Identity.** `FROZEN_SHADOW_POLICY_2026.08.15@7d3ebacd55`.
- The production pipeline at commit `7d3ebacd55c34c6ec78030bdeca70799e56d4764`, tree `02526a74f6669069127d5a72d771a437cb5561bf`.
- Labels: model 2026.08.15, selection policy 1.0.0, calibration 1.0.0, feature 1.0.0.

**Pinned:** the entire tree, including the learned artifacts:
- `backtest/calibrators_by_market.json`;
- `backtest/reliability_bands.json`;
- `results/signal_measurement.json`;
- `recommendation.py`, `generate_picks.py` and `board_freeze.py`.

The sha256 of each is checked before every run.

**Live overlay.** Exactly one observational file: `data/odds/odds_{date}.json`, production's game-line snapshot, read by the pinned `line_movement()` feature. It is copied from main at run time and hashed. If it is absent, the feature returns {} by its own design.

**Why that is the only overlay.** A traced drill on 2026-10-01 (164 s) recorded every file the pipeline opened. Other non-pinned reads were `data/players/*.json`, used only by the post-freeze parlay and HTML renderers.

**Runs contemporaneously.** The champion runs inside the runner, at the unit's cutoff, on live statsapi, FanGraphs, Savant, Rotowire, weather and FanDuel data. Its selections, `recommendation_status == "top_pick"` on the shadow board, are fixed in the sealed manifest before outcomes.

**Live production is free to evolve.** Model, selector and calibration can all change without touching the experiment. A production board can never stand in for the champion: `verify_shadow_board` requires the pinned git_sha and labels.

**What "champion" means.**
- **A. Shadow champion classification:** the frozen policy above, used consistently for the primary experiment.
- **B. Actual published Top Picks:** read from `data/public_top_picks/registry.json`, reported **separately and descriptively**. They are never the experimental champion.

`recommendation_status` on a board is **not** proof of customer publication.

## 9. Market families
| Family | Status |
|---|---|
| hits, hits_runs_rbis, strikeouts (pitcher), pitcher_outs | **PRIMARY** |
| pitcher_outs | also **C3**, secondary |
| combined_strikeouts | **RED_FLAG_SEPARATE**: never in the primary universe; descriptive |
| nrfi_combined, first_inning_run | **NOT_A_PLAYER_PROP** |
| every other stat | **EXPLORATORY**, reported by named stat, with no claims and no promotion |

**Predeclared sensitivity:** the primary comparison without pitcher_outs.

No promotion is ever made from a post-hoc subgroup.

## 10. Arms (unchanged from v1; not refitted)
The frozen coefficients are `frozen_coefficients.json`, sha256 `3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009`, fitted on development rows only.

| Arm | Model |
|---|---|
| C0 | p1 = logistic(a + c·logit q) |
| C1 | pcal = logistic(a + b·logit p0) |
| C2 | p2 = logistic(a + b·logit p0 + c·logit q) |

Here q is the exact captured raw implied probability, and p0 is the shadow champion's probability.

**Selector keys:**

| Arm | Status | Ranks by |
|---|---|---|
| **C2** | primary | p2 − p1 |
| C1 | secondary | pcal − q |
| C0 | secondary | p1 |
| RAW | secondary | p0 |

V3 is an evidence-integrity repair. No formula, coefficient, key or band changed.

## 11. Regimes (`regimes.py`, enforced in code)
| Regime | Allowed gameType | Calendar | Use |
|---|---|---|---|
| `2026_POSTSEASON_SHADOW` | F, D, L, W | 2026-09-29..2026-11-15 | descriptive only |
| `2027_REGULAR_CONFIRMATORY` | R | 2027-03-01..2027-10-10 | the only confirmatory regime |

**How the code enforces this:**
- A regime is selected by name. No date override exists.
- Every slate unit must fall inside the regime's calendar.
- Every covered game must carry an allowed gameType. That gameType comes from the pregame schedule snapshot inside the hashed manifest.
- One violation rejects the whole call. That covers a 2026 postseason slate in the 2027 regime, a 2027 postseason game pooled with regular-season slates, and a 2026 regular-season slate passed into 2027.
- Negative tests cover each case.

## 12. Equal volume
- **N_d** is the shadow champion's selections inside the unit's eligible PRIMARY universe. Each challenger selects exactly N_d from that same universe by its key, with ties broken by `candidate_id`.
- If N_d = 0, every arm selects zero, and the unit counts as a zero-pick slate.
- No backfill, no carry-forward.

**Reported per arm:** selected, settled (hit or miss), void, push and unresolved. The hit rate uses settled picks, and every denominator is shown.

## 13. Decision rule, practical threshold, and power
**Minimum practically meaningful improvement: δ = +5.0 percentage points** of hit rate (C2 − shadow champion), set before any v3 outcome exists.
- The typical price is q ≈ 0.55, about −122. There, 1 pp of hit rate is worth about +1.8% ROI, so 5 pp is about +9% ROI, roughly twice the typical prop hold.
- Smaller gains are within the noise of vig, model drift and price slippage, and would not justify a production change.

**Primary verdict.** Regime B only. One analysis runs after the 2027 regular season, on or after 2027-10-05, once every covered unit is graded or 7 days have passed. Undecided picks then count as unresolved. There are no interim outcome looks. Count-only status (seals and manifests) may be checked at any time.

| Verdict | Condition |
|---|---|
| INSUFFICIENT_N | Fewer than 250 champion settled picks, or fewer than 60 units with N_d > 0. These are coverage floors only. |
| REJECTED | Difference ≤ 0 |
| INCONCLUSIVE | Positive difference, but the one-sided 95% game-clustered bootstrap lower bound is ≤ 0 (B = 2000, seed 20261001) |
| INCONCLUSIVE_CHALK_GUARD | The lower bound is > 0, but C2 mean q − champion mean q > 0.03 |
| POSITIVE_BELOW_PRACTICAL_THRESHOLD | The lower bound is > 0 and the chalk guard holds, but the difference is < 5.0 pp. This is **not adoptable**. |
| **SUPPORTED_ADOPTABLE** | The lower bound is > 0, the chalk guard holds, **and** the difference is ≥ 5.0 pp. This makes C2 a promotion candidate only; Jacob decides. |

The player-clustered and ISO-week-clustered lower bounds are reported beside the verdict as dependence sensitivity. If either is ≤ 0, that is stated.

**C3 (secondary).** The mean LL(p2) − LL(p1) on settled eligible pitcher_outs rows, with a game-clustered CI. The v1 rule applies (n ≥ 150; SUPPORTED if the upper bound is < 0). It informs research only.

### Power note (assumptions stated, not a guarantee)
| Settled picks per arm | p | Overlap | SE(diff) | One-sided 95% bar | 80%-power detectable effect |
|---|---|---|---|---|---|
| 250 | 0.55 | none (independent) | 4.45 pp | 7.3 pp | 11.1 pp |
| 250 | 0.55 | 30% shared picks | 3.7 pp | — | 9.3 pp |
| 600 | 0.55 | none | 2.9 pp | — | 7.1 pp |
| 600 | 0.55 | 30% shared picks | 2.4 pp | — | 6.0 pp |

- **Clustering.** About 1.3 picks per game and a small within-game correlation give a design effect of roughly 1.0–1.1. Week-level dependence is unknown and is reported as sensitivity.
- **Expected supply.** Frozen boards 09-20..09-30 averaged 4.8 champion picks per date in band. The v3 losses are price mismatch, starter not listed, team-slug mismatch and capture failures. Expect roughly 3–4 eligible champion picks per date, two units per date, about 26 dates per month. That gives about **80–110 picks/month** and about **45–55 units/month**, or roughly **550–700 settled champion picks** over the 2027 regular season.
- **Honest reading.** Only effects of about 6–7 pp or more are likely to be detected. A true effect of exactly 5 pp will often come out INCONCLUSIVE. The floors of 250 / 60 do **not** guarantee power.

## 14. Probability quality vs picking performance (reported separately)
**Probability quality.** Computed on all settled eligible PRIMARY rows, whether picked or not:
- log loss and Brier score for p0, q, p1, pcal and p2;
- calibration bins;
- residual LL(p2) − LL(p1) with a game-clustered CI;
- the de-vig subset.

**Picking performance.** Per arm:
- the counts from §12;
- hit rate and mean captured q;
- ROI at the exact captured price, which is valid because the price is an exact, timestamped, pregame offer;
- picks per unit, zero-pick units and market mix;
- repeated-player share and maximum picks per player and per game;
- the overlap tables.

## 15. Leakage prohibitions
1. No refit, re-band, re-threshold, re-key or reselection.
2. No graded file is opened before the analysis date.
3. No slate at or before the boundary, and no late, unsealed or unlinked unit; this is enforced.
4. No inferred, closing, consensus or `data/odds` prop price, and no invented opposite side.
5. The public ledger and published picks are untouched.
6. Postseason is never pooled with Regime B; this is enforced.
7. Any change after this commit is **v4 with a new boundary**.

## 16. Runner and activation (built, NOT activated)
`runner.py --mode drill` is local only: no push, no #91 post, labelled `DRILL_NONCONFIRMATORY`. It is permitted now.

`runner.py --mode prospective` refuses (exit 2) unless **both** of these exist:
- `v3/ACTIVATION.json`, a Jacob authorization record naming the exact #91 comment and the prereg commit;
- `MLB_V3_ACTIVATION=JACOB_AUTHORIZED`.

Neither exists, and a test checks this.

**What an activated runner does:**
- shadow run;
- capture;
- schedule snapshot;
- manifest;
- one append to the evidence ref (`git push`, never force);
- the #91 receipt;
- two TSA tokens;
- re-fetch and verify;
- stop.

**What it never does:** read outcomes, grade, or edit the prereg, the challengers or old seals.

Scheduling (two daily firings per date, timed before the earliest DAY and NIGHT first pitches) is part of the activation decision. It is **not** created now.

## 17. Anchoring this preregistration
After this commit is pushed:
- an Issue #91 `V3 PREREG ANCHOR` comment records the commit SHA and this file's sha256 (GitHub server time);
- RFC 3161 tokens from FreeTSA and DigiCert over this file's sha256 are committed in a follow-up anchor record (`v3/PREREG_ANCHOR.json`).

Both happen before 2026-10-02T06:00:00Z.

The **evidence ref genesis** is created from this commit. Its genesis hash is the sha256 of this file.

**End-to-end drill (nonconfirmatory).** On 2026-10-01, `runner.py --mode drill` ran on the 2026-10-01 NIGHT unit: shadow run, live capture, schedule snapshot, manifest, and TSA tokens over the manifest hash. Its result is recorded in `v3/DRILL_2026-10-01_NIGHT.json`. It is DRILL ONLY.

## 18. Outcomes unseen at V3 lock
**YES.** This program has opened graded data only from the development window (through 2026-09-27), plus the disclosed aggregate counts for 2026-09-29. No graded file for any date after 2026-09-30 has been read, and no v1 or v2 slate was scored.

## 19. Provenance
| Item | Value |
|---|---|
| Frozen coefficients | `3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009` |
| `harness.py` (v1, reused) | `d355a12b597556341b1f7f8133fbbfc4d2ccb667057f370890edad2dd3631857` |
| Shadow pin / tree | `7d3ebacd55c34c6ec78030bdeca70799e56d4764` / `02526a74f6669069127d5a72d771a437cb5561bf` |
| FreeTSA certificates | `2151b611…4438` (cacert), `8bfb0305…3467` (tsa.crt) |
| v3 file sha256 | capture.py `79533a1b79099712063f6e63e682d34afbcbeef9d8d8c2ae5153ceed39784fab`; shadow.py `8e86fb55fbedc4c34c81c70e85394405fad234aa59351307ca653e1516365b74`; manifest_v3.py `95d335ccd7832d81b788eb00d8fd39556afc50595613a35ce599f4cdf0ef16af`; seal.py `901cdb12862f3f6b7874871e8fd4832194896e521a6feedede09682009260a7e`; regimes.py `0d21248a7d68d45b9bfb37fbba24bd5b89026369a5310016a73f290112e1dd92`; evaluate_v3.py `75e9b07ebf58ff0defdf90d87f4ecda7cd8a33bafa611970473675803786af35`; runner.py `dcdbf93b71c130904460e1e080eacde8e40950a7b25a232799ec2a0b60281473`; test_v3.py `1f00f540e5fed26885b0ac3ce493c3810bc23b2c01398daf0a6156db9359f118` (34 tests; 29 integrity mutants, all killed) |
| v2 audited head | `9265935966c9d2150d2bccb1086b585bdd105169` (superseded) |

Alligator.
