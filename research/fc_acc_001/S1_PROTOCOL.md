# FC-ACC-001-S1: forward shadow protocol (DRAFT, NOT ACTIVATED)

Status: draft protocol plus a feasibility assessment. **It is not activated and
must not be activated before the blocker in §9 is resolved and Jacob
authorizes it.** When it is finalized, the final text is anchored in Issue #91
before the first covered slate.

## 1. Question

On a common, pregame-frozen candidate-and-offer universe, does the
market-anchored blend CH1 produce **more realized winners at the same pick
count**, and **profit at the captured offered odds**, compared with:
- (a) the existing-policy champion;
- (b) a market-only control?

## 2. Evidence regimes

| Regime | Use |
|---|---|
| Everything dated ≤ 2026-10-12 (public ledger, 20 freezes, PR #219/#220 exploration) | **Exploratory only.** Never pooled with S1. |
| S1 descriptive: 2026 postseason dates after anchoring | Descriptive only. |
| S1 confirmatory: the first regular-season period after anchoring (2027) | Confirmatory, with one analysis at the end. No interim outcome looks. |

## 3. Slate units and universe

- **Unit:** (date, window). DAY is first pitch before 17:00 America/New_York;
  NIGHT is the rest. One seal per unit.
- **Freeze:** before the unit's earliest first pitch, freeze the full
  candidate-and-offer universe **U_u**. Every candidate the model scored that
  has a captured offer is included, together with the frozen factor values in
  §8.
- **Seal:** canonical hash over the universe, the offers, the code SHA and the
  model/calibration version. The seal is committed or uploaded before the first
  pitch.

## 4. Offer provenance (required per offer)

Each offer must carry:
- `sportsbook`;
- `sportsbook_event_id`;
- `game_pk`;
- player id;
- `market`;
- `side`;
- `line`;
- `american_odds`;
- `observation_completed_at`, which must be at or before the unit's decision
  cutoff;
- `raw_response_sha256`, the hash of the archived raw sportsbook response the
  offer was parsed from.

`offer_provenance.validate_offer()` enforces all of this. An offer that fails
any check is **excluded and counted** as MISSING_QUOTE or INCOMPLETE_CAPTURE.
It is never imputed.

## 5. Arms

All arms draw from the same U_u. N_u is the champion count.

| Arm | Policy label | Eligibility | Rank key |
|---|---|---|---|
| CHAMPION | existing policy | `recommendation_status == top_pick` under the frozen production policy | as produced |
| CH1 | **alternative policy** | priced, QC-kept, lineup not assumed, reliability A/B/C | **0.5·p + 0.5·break_even_raw** (weight FIXED now; never tuned on any ≤ 2026-10-12 outcome) |
| CH2 | alternative policy (control) | same as CH1 | break_even_raw (market only) |
| RAW | descriptive | same as CH1 | p |

Further rules:
- One line per (player, stat, side) per arm per unit.
- Ties are broken by candidate_id.
- `fair_novig` is reported where it exists. It is **never** used as a
  substitute for break-even.

## 6. Matched counts and exceptions

Each arm takes exactly N_u picks. Exceptions are recorded per unit:

| Code | Meaning |
|---|---|
| ZERO_CHAMPION | N_u = 0; all arms take 0, and the unit is counted |
| SHORTFALL(arm, k) | U_u has too few eligible entries; the arm takes what exists and is marked non-comparable for that unit |
| MISSING_SLATE | no seal before first pitch; excluded and counted |
| MISSING_QUOTE / INCOMPLETE_CAPTURE | counted per offer |
| VOID, PUSH, UNSETTLED | counted; never in the hit-rate denominator |

## 7. Outcomes and reporting

**Per arm:**
- selected, settled, wins, losses, void, push and unsettled;
- **wins**;
- hit rate on settled picks;
- **flat 1-unit profit and ROI at the captured offered odds**;
- mean break_even_raw;
- hit − break-even.

**Comparisons:**
- vs CHAMPION: wins added and removed, winners retained, losers removed, and
  overlap;
- vs CH2: whether CH1 beats price-only ranking at all.

**Uncertainty:** game-cluster and unit-cluster bootstraps (B = 10,000, seed
fixed at anchor).

**Stratification:** by market, by odds band (≤ −250, −249..−200, −199..−150,
−149..−110, −109..+100, > +100), by date/unit and by game/player cluster. These
are descriptive.

**Promotion candidate (Jacob decides), all required at once:**
1. the CH1 − CHAMPION wins difference has a one-sided 95% lower bound > 0;
2. the CH1 − CH2 wins difference has a lower bound > 0, so the model adds
   something beyond the price;
3. CH1's ROI difference has a lower bound > 0 against both.

A gain over the champion that CH2 matches is reported as **PRICE-ONLY**. It is
not evidence for the model.

**Sample floor:** ≥ 250 settled champion picks and ≥ 60 units.

## 8. Pregame factor capture (needed for §10 hypotheses)

For every candidate in U_u, freeze the values that existed at the freeze:
- lineup confirmed or assumed, and batting slot;
- projected PA;
- probable starter, days of rest, recent pitch counts and outs;
- opponent team K% and contact%;
- pitcher handedness, and the batter's platoon split;
- the offer's earlier observed prices for the day (movement).

## 9. Feasibility on current main (measured, `amendment_a1.py`): BLOCKED

| Requirement | Current evidence | Status |
|---|---|---|
| Common pregame universe | `output/board_freeze_*`: one seal per day, sealed late (about 19:40–01:50 UTC), so it covers only the day's remaining games | **Partial.** Needs per-unit seals before first pitch. |
| Existing-policy champion | freeze `recommendation_status` | OK |
| Authentic offer provenance | Freeze records carry odds only. There is no sportsbook, event id, observation time or raw-response hash per offer. `data/props` keeps 6 normalized FanDuel snapshots a day with `taken_at`, but **no raw response bytes or hash**. | **Blocked.** 0/91 champion, 0/91 CH1u and 0/91 CH2u selections yield a fully valid offer. Only 55, 50 and 53 respectively can even be matched to a pre-seal snapshot row. |
| Frozen factor values (§8) | Freeze records carry none. Model signals survive only for the ~24 selected picks per run. | **Blocked** for §10 tests. |
| PR #220 V3 capture / seal | Built, unmerged, not activated | **Cannot be assumed.** Reusing it needs its own integration review and Jacob's authorization. |

**Smallest unblock (needs Jacob's decision; not done here):**
- **Option 1.** Authorize a research-only capture step that archives the raw
  FanDuel responses with sha256, and binds every frozen offer to (raw hash,
  event id, observation time). Either extend board freezes, or integrate PR
  #220's `capture.py`, after its own review.
- **Option 2.** Seal per DAY/NIGHT unit before first pitch, and add the §8
  factor values to the frozen record.

Until then S1 **cannot** produce authentic forward evidence. Running it on
today's freezes would reproduce the same provenance gap.

## 10. Hypotheses

Ranked falsifiable hypotheses that would use §8 are in `HYPOTHESES.md`.

Alligator
