# PRE-REGISTRATION — HR Contact-State Challenger, v1

**LOCKED on authorship, before any result from canonical run
`canonical-20260828T153143Z-2b79304f` was read.** That run was still
generating when this was written (verified: `[21/877]`). The commit
introducing this file is the timestamp. Changing a rule later requires a
NEW commit stating what moved and why.

Labels: `VERIFIED` measured here · `INFERRED` · `HYPOTHESIS` · `UNKNOWN`.

---

## 1. Why this experiment exists

HR probability today is built from ONE rate: `hr_rate = homeRuns / PA`,
read from the MLB StatsAPI season line (`mlb_sources.py:1069`), fed into
`pp.pa_outcome_distribution(...)` and combined with `projected_pa` by
`pp.p_at_least_home_runs(k, dist, pa)` (`generate_picks.py:5520`).
**VERIFIED.**

No Statcast bat-tracking or contact-geometry field reaches HR probability.
`bat_speed` is the only one used anywhere, and it enters as
`bat_speed_bonus` → `form` → RECENT FORM category → `score`
(`generate_picks.py:1475-1479`) — **ranking only, never probability.**
`swing_length`, `attack_angle`, `swing_path_tilt`, `attack_direction`,
`hit_distance_sc` and `arm_angle` appear nowhere in scoring or
probability. **VERIFIED.**

So the champion prices home runs from a season-long outcome rate and a
lineup-slot PA estimate. Whether the *physical* state of a hitter's swing
adds anything beyond that is unmeasured, and as of the current canonical
run it is measurable for the first time.

## 2. THE QUESTION (one)

> Do strictly-pregame bat-tracking / contact-geometry state features
> improve out-of-sample HR **ranking** and **realized HR winners** beyond
> the existing champion, at the same legitimate selection volume?

## 3. ABLATION LADDER (locked, mechanism-driven)

| arm | definition |
|---|---|
| **A** | champion: existing `predicted_prob` for `home_run`, unmodified |
| **B** | A + **bat-speed state** — trailing mean and trailing 90th-pct `bat_speed` |
| **C** | A + **swing-geometry state** — trailing means of `attack_angle`, `swing_length`, `swing_path_tilt`, `attack_direction` |
| **D** | B + C |
| **E** | D + trailing `hit_distance_sc` state — **runs only if D earns continuation under §7** |

Ladder order is fixed. Arms are not reordered, added to, or re-cut after
results.

**`arm_angle` is EXCLUDED from this experiment.** It is a property of the
*pitcher's* release, not the batter's swing. Including it would silently
convert a batter contact-state test into a matchup test, confounding the
mechanism under study and making a null result uninterpretable. It is
retained in the artifact and reserved as a separate pitcher/matchup-state
hypothesis. **This is a scientific decision, not an oversight.**

Explicitly NOT in v1: pitch arsenal interactions, park geometry, bullpen
exposure, weather/air density, squared-up/blast (verified absent from the
pitch frame), market prices, player archetypes, any interaction search.
Those are later hypotheses conditional on v1 earning continuation.

## 4. FEATURES — strictly pregame, by construction

Every feature for a candidate on date `D` is computed from Statcast
pitches with `game_date < D`, for that `batter` only. Never `<= D`.

- Trailing window: last **100 tracked swings** strictly before `D`
  (bat-tracking is populated only on swings — 1264/2735 pitches in a real
  frame, **VERIFIED**), with a minimum of **30**. Below 30 the feature is
  `None` and the row falls back to the champion arm for that feature.
- No league/season aggregate that includes any date `>= D`.
- No feature derived from the candidate's own game outcome.
- Missingness is a first-class value, never imputed to a mean — per this
  project's standing "absent is not zero and not neutral" rule.

**Coverage caveat (`UNKNOWN` until run):** bat tracking begins in 2023.
2024-onward canonical dates should be covered, but the realized fraction
of HR candidates meeting the 30-swing minimum is not yet known and will be
reported before any effect size.

## 5. EVALUATION (locked)

- **Market:** `home_run` only.
- **Split:** chronological. Fit on dates **through 2025**; **2026 is an
  untouched holdout**. No random splitting.
- **Population:** identical eligible candidate set for every arm — the
  certified canonical `home_run` rows with `outcome`, `predicted_prob` and
  `score` present. No arm may shrink the population.
- **Volume:** equal selection count at every comparison point, matched to
  the champion's own selected N. Slate/date opportunity preserved.
- **Primary result:** realized HR hit rate at matched volume.
- **Also reported, always together:** exact overlap, added-pick hit rate,
  removed-pick hit rate, **added-minus-removed**, all with n.
- **Uncertainty:** paired bootstrap clustered on `game_pk`, 5,000
  resamples, 95% CI. Clustering is not optional — it previously widened an
  interval ~35% and flipped a conclusion.
- **Stability:** by month and by predicted-probability band, with n shown.

**No historical sportsbook prices exist for this population**
(`VERIFIED`: canonical rows carry no odds; the price archive is
2026-08-06→26 and does not overlap the training range). This experiment is
therefore **WORLD-MODEL / RANKING evidence only**. It is not, and will not
be reported as, a claim of price-relative betting edge. No price will be
fabricated or inferred from model probability.

## 6. ADVERSARIAL INTEGRITY REQUIREMENTS

1. No pitch from the candidate's own game may enter its features —
   enforced by a strict `<` date comparison and asserted by test.
2. No future-season or global aggregates.
3. No result-conditioned filtering of any kind.
4. No post-hoc change to selection count.
5. No hidden reduction of the eligible population; every arm reports the
   same n.
6. Market mix is not adjusted because an arm performed better somewhere.
7. The 2026 holdout is evaluated **once per arm**. It is not re-read and
   redefined.

## 7. STOP RULE (pre-committed)

The HR contact-state thread is **CLOSED** if any of these holds on the
holdout:

- equal-volume improvement is small or noisy — specifically, the
  **added-minus-removed CI includes zero**;
- the effect depends materially on one player, team, park or month —
  operationalised as: removing the single largest contributor of either
  flips the sign of added-minus-removed;
- coverage is so thin that fewer than **500** holdout HR candidates carry
  a real trailing-state feature.

On closure: document and move to the next distinct mechanism. Do **not**
iterate further on this same feature family. A negative result here is a
real and useful finding — it would say HR pricing is not limited by
swing-state observability.

## 8. WHAT PROMOTION WOULD REQUIRE (not decided here)

Nothing in this document authorises a production change. Even a positive
result is ranking evidence on one market in one holdout season. Promotion
remains governed by the existing bar: more realized winners at the same
legitimate usable volume, season-stable, with ROI as a veto and never a
driver.
