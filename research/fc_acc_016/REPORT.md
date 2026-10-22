# FC-ACC-016 — prospective portfolio-challenge protocol: readiness report (2026-10-22)

**Verdict: MODIFY.**
- **Ready now:** the protocol is frozen and mechanically checkable. The model is locked, the rules are fixed before any observation, and a reference checker with 19 labeled synthetic cases and 7 unit tests passes.
- **Problem with the mandated primary:** TB 2+ against the actual Top Pick champion is **structurally almost non-evaluable**. The frozen TB challenger essentially never reaches the champion's own 0.60 Top Pick floor.
- **What must happen first:** before capture starts, a governance choice (G0, PROTOCOL §9) and Codex's FC-OPS-002 interface additions (§8).
- **No new model:** nothing was fitted, recalibrated or tuned; the frozen weights are untouched.

## Deliverables (PROTOCOL.md sections)

1. **Model identity and feature contract (§1, `model_lock.json`):**
   - artifact `07115a54…` @ `84b294fc`;
   - TB entry `68f2e65d…` and hits entry `f64c9594…`, 49 inputs in fixed order;
   - 81 coefficients;
   - 18 critical inputs that fail closed if missing;
   - training seasons 2024–25;
   - all of it re-verified on every use.
2. **Hypotheses (§2):** H1-TB primary; H2-HITS, H3 market-only and H4 calibration secondary; strikeouts diagnostic only.
3. **Matched-count policy (§2–3):**
   - per decision unit (one final pregame export run), N_u = the champion's actual count, never re-derived;
   - N = 0 → no picks;
   - the challenger competes only by displacing the lowest-ordered pick, under identical gates.
4. **Market-only arm (§2):** raw implied probability over the same universe, plus a TB-only variant.
5. **Decision-time restrictions (§1, §3):**
   - slot only from a confirmed lineup; announced probable starter only; no weather;
   - inputs only from before the slate date;
   - authentic FanDuel offer, at most 45 minutes old at the cutoff, pregame;
   - production's own price and robustness gates, read-only and sha-pinned;
   - a fixed, documented rule borrowing the champion's interval width;
   - one selection per player;
   - sealed before first pitch.
6. **Uncertainty framework (§5):**
   - slate-clustered bootstrap, with game and player sensitivity analyses;
   - discordance-based information requirement (D/2 > 1.84/δ²) with a D < 200 not-evaluable floor;
   - fixed horizon, no optional stopping;
   - outcome-free training/serving parity check.
7. **Promotion standards (§6–7):** four separate gates (research, capture, shadow, promotion), each requiring Jacob's separate written authorization; ROI non-inferiority margin −0.02 per unit.
8. **Codex interface (§8):** bundle `fc-acc-016-bundle-1`. Fields are assigned to producers, the PR #230 export fields still missing are listed, and there is a no-edit rule both ways.
9. **Verdict:** MODIFY, as above.
10. **Highest-value next step:** below.

## Key finding: the mandated primary is structurally degenerate

Prediction-only diagnostic (`floor_reach.py`, `out/floor_reach.json`; outcomes deleted before scoring):

| Market | Season | Rows | Frozen challenger: max | Rows reaching ≥ 0.60 | Production raw max | Production ≥ 0.60 |
|---|---|---|---|---|---|---|
| TB 2+ | 2024 | 42,541 | 0.619 | **3** | 0.550 | 0 |
| TB 2+ | 2025 | 43,480 | 0.627 | **6** | 0.550 | 0 |
| TB 2+ | 2026 | 35,545 | 0.603 | **1** | 0.555 | 0 |
| Hits 1+ | 2024–26 | 121,554 | 0.830–0.838 | 55–58% of rows | 0.744–0.764 | 42–52% |

- **What follows:** under the champion's Top Pick standard (p ≥ 0.60, A/B evidence, confirmed lineup, a robust value test at a real price), a TB 2+ candidate can essentially never take a slot. The primary is expected to end `NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE`, which means untestable, not negative.
- **Consistent with the record:** production's own 24 frozen boards (2026-09-20 → 10-16) held no TB 2+ rows.
- **Hit rate vs ROI:** TB 2+ is a plus-money market where roughly 35–45% of picks win. It can serve the ROI half of the North Star, never the 72% hit-rate half. Putting TB picks into Top Pick slots would lower the hit rate even when it raises ROI.
- **Where TB can compete:** the only production standard under which TB 2+ can legitimately compete is the **value** lane (p < 0.60, the robust value test, not SUSPECT). The synthetic suite shows how narrow that lane is: at +200, it admits only p in about [0.35, 0.379] with an interval lower bound above 0.333.
- **Preregistration:** lane V is preregistered as the alternative primary, but adopting it requires G0.

## Readiness by gate

| Gate | Status |
|---|---|
| Research readiness | **Ready, except G0** (lock, protocol, checker and tests in place) |
| Live-data capture readiness | **Not ready.** FC-OPS-002 is inactive; PR #226/#230 are not certified together; the PR #230 export lacks `prob_ci`, `reliability`, `lineup_confirmed`, `data_fresh`, `raw_hit_probability`, `signals` and the announced starter; outcomes are not graded for all challenger-market rows. |
| Shadow-test readiness | **Not ready.** No decision-time challenger feature builder; no captured slates for the parity check. |
| Production promotion readiness | **Not ready.** It requires a full prospective horizon, `SUPPORTED_PENDING_AUTHORIZATION`, a SUPERCHAD audit and Jacob's final authorization. |

## Corrections and limitations

- **Superseded wording:** FC-ACC-015 §8 said a changed starter "voids" the row. This protocol separates the two: the decision stands and is settled as the book settles it, flagged, with a sensitivity analysis.
- **Untested end to end:** the checker has been exercised only on synthetic units. Real bundle shapes may expose gaps, and any fix must be a new interface or protocol version committed before the affected slates.
- **Borrowed interval:** the challenger interval rule borrows the champion's downside width. It is a predeclared convention, not an estimated uncertainty for the frozen model.
- **Retrospective numbers:** the floor-reach diagnostic uses retrospective universes (all lineup batters, no prices). The real prospective pool will be smaller.

## Highest-value next step

1. **Jacob/SUPERCHAD record G0.** Recommended: **(b) lane V as the TB primary, with H2-HITS kept as the separate lane-TP hit-rate test**, in writing on Issue #91 before any capture.
2. **Codex adds the §8 fields** to the inactive FC-OPS-002 same-unit replay fixture, so the checker can run on one real archived unit.
3. **Then, under separate authorization,** a research-only decision-time challenger feature builder and the outcome-free parity check.

Alligator
