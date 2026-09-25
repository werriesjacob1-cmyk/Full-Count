# F16: historical defensive-box matchup proxy for rushing yards

**Decision: exploratory, no demonstrated incremental value; research-only and not promoted.** Predictive code and focused tests were pushed to `codex/nfl-tier2-rushing-box-f16-20260925` at `a8f0d944ef0399e7442566a52337852fc05ca844` before the held evaluation. This branch depends on unmerged draft PR #186 (`a24dfe45d682689fdcec2ee2148c7de504f66efb`) for the existing rushing-yards B0 and its already rejected carries×YPC research comparator. PR #186 code was imported read-only; F16 adds new files only.

## Real source and classification

FTN Data via nflverse, CC BY-SA 4.0: 2024 `ftn_charting_2024.csv` SHA-256 `6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb`, 8,254,908 bytes, released 2025-09-01 01:29:37 UTC; 2024 `pbp_participation_2024.csv` SHA-256 `b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9`, 49,688,308 bytes, released 2025-09-04 10:24:49 UTC. Both precede the modeled 2025 week-2 feature cutoff. Game/play IDs join exactly. The offense and defense derive from the game ID and `possession_team`; the offense must have 11 unique GSIS IDs, one QB, five offensive linemen, and a consistent observed position composition. FTN `n_defense_box` must equal participation `defenders_in_box` and be between 1 and 11. Zero and disagreements are unknown, never light-box evidence.

Of 48,031 charted plays, 35,265 (73.42%) pass these conditions. There are 10,640 joined but unknown/contradictory observations, 2,126 charted plays without participation, and 14 participation-only plays outside the chart denominator. Accepted game/play IDs are sealed by SHA-256 `8183a6a1ed0b17848dcc295d6f805842537d7fbd733699a0a1aaa1e86ff185f8` in the evaluation artifact. Heavy box is defined as at least seven observed defenders. Offensive 11 personnel is one RB/FB, one TE, three WR; every other complete grouping is OTHER. These are observed positions and counts, not formation, rushing concept, blocking scheme, defensive-front alignment, or player assignment.

The locally available `play_by_play_2024.csv.gz` has `run_gap`, `run_location`, runner IDs, and play yards, but its pinned release asset was created in August 2026. That exact revision cannot be certified as information available before 2025 games, so F16 does **not** use it for prior player box-versus-rush efficiency or label its observed run location as a 2025-available scheme. No authenticated pregame run-concept, front-alignment, blocking-assignment, or individual matchup source is captured here. These remain source-blocked, not imputed from short gains, formations, or personnel.

## Connected prediction

For each 2024 defense, F16 estimates its heavy-box rate separately against verified 11 and OTHER offensive personnel. For each 2024 offense, it measures the prior 11-personnel share. Their mixture is a historical opponent-response proxy for an upcoming offense; it is **not** a current-game defensive plan or a run-only box rate. A separate ridge rushing-yards challenger consumes that mixture through `B0 × (expected heavy-box rate − 0.25)`. The intercept-free scale-only control and F16 coefficients are fitted on identical 2025 regular-season weeks 2–8 development rows with fixed ridge strength 100. The one-coefficient PR #186 B0 remains unchanged and read-only.

F16 activates for RB/FB carrying rows only when B0 exists, the player has a valid GSIS ID, player/team/game identity matches, 2024 offense has at least 300 verified plays, opponent defense has at least 100 verified plays in each grouping, and the source predates the feature cutoff. Otherwise it returns a named `NO_ADJUSTMENT`. Current-game carry-positive membership is known only after the game; this is a **retrospectively selected research cohort**, not pregame pick eligibility. The 2023–25 weekly stat files are SHA-pinned in code but the copies used were retrieved in 2026. Their exact historical revisions are not proven point-in-time. HC/OC changes, current injuries, personnel availability, and game-state-specific or run-only defense are not inputs.

## Frozen held result

On 2025 weeks 9–18, 727 RB/FB carry-positive candidates existed; 694 activated, covering 115 players, 151 games, and 32 teams. The 33 abstentions lacked a prior B0. Development had 433 activated rows. Every metric below is on the identical 694 held rows.

| Model | MAE rushing yards | RMSE | Bias (prediction − actual) |
| --- | ---: | ---: | ---: |
| PR #186 unmodified B0 | 22.4460 | 31.7948 | −0.5313 |
| Development-fitted scale only | **21.9805** | **31.6256** | −3.7628 |
| F16 defensive-box proxy | 22.0361 | 31.7623 | −3.8402 |
| PR #186 carries×YPC research comparator | 23.3985 | 32.6524 | −0.1927 |

F16 improves MAE versus unscaled B0 by 0.4100 yards, but **worsens** it versus the more relevant scale-only control by 0.0555 yards. Fixed-seed 1,000-resample 95% intervals for F16 minus scale-only MAE are [−0.1019, +0.2576] by player cluster and [−0.1257, +0.2461] by game cluster. Neither supports incremental predictive value. All 694 projections changed relative to scale only, by mean absolute 1.491 yards. An authentic counterexample is Derrick Henry at Cleveland, week 11: prior matchup proxy expected heavy box 48.65%, B0 84.0 yards, scale-only 77.41, F16 63.78 (box term −14.26), actual 103. The box adjustment made that prediction much worse. There is no historical sportsbook line, price, EV, or equal-volume pick test; lower MAE versus B0 is not a winning-pick claim. The 2025 season has been inspected by other FULL COUNT research, so this is exploratory rather than pristine held validation.

The deterministic `evaluation.json.gz` stores source identities, source play-ID seal, counts, fixed fit, all matched rows, metrics, intervals, and examples. Its canonical uncompressed JSON SHA-256 is `192fa76189997e12560b0dfb320608f15a01039217d505d2c7a1e413128f6e96`; gzip SHA-256 is `f92b85a71e4a46047367f8a4715f91f9a701d22bc7a68bf3dfbfec8708b97d59` with gzip mtime zero. Independent review found a missing fail-closed player-ID gate; that gate and adversarial tests were added after the initial code checkpoint. The complete evaluation reproduced byte-identically because no evaluated row had an invalid ID. Reproduce with `python -m nfl.research.tier2.rushing_scheme_f16 --participation PATH --ftn PATH --stats-dir PATH --output PATH` using the pinned exact inputs. Four focused adversarial `unittest` cases pass. No live workflow, selector, B0, customer pick, or grading integration exists.

The next defensible F16 study would require a verified pregame-available play-level runner/outcome and scheme or run-location source, plus current defensive personnel context. This checkpoint intentionally does not synthesize those labels.

