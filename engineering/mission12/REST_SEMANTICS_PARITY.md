# Mission 1.2 §2–4 — PA-v1 reference-clock parity

**VERDICT: PARITY DEFECT CONFIRMED, then remedied by an exact adapter.**

## The defect

Both regimes read `m.TODAY` as the reference clock:

```
mlb_sources.py:891   today = _dt.datetime.strptime(m.TODAY, "%Y-%m-%d").date()
mlb_sources.py:914   "days_since_last_game": (today - last).days
```

`PointInTime` repoints it during replay:

```
backtest/engine.py:420   self.cutoff = shift(self.date, -1)   # D-1
backtest/engine.py:476   ("TODAY", self.cutoff)               # m.TODAY = D-1
```

Live, `m.TODAY` is D. **Identical code over a different clock is a different
feature.** Mission 1.1 verified the code was the same and never verified what
it was measured against.

## Evidence table

Produced by executing the real production worker `mlb_sources._rest_batter_one`
under both clocks with a stubbed game log — no network, no invented fixtures
beyond the game dates (`rest_semantics_probe.py`, output in
`rest_semantics_result.json`).

| circumstance | H.raw | H.signal | H.bucket | L.raw | L.signal | L.bucket | match |
|---|---|---|---|---|---|---|---|
| last game D-1 | 0 | −2 | `0_days_rest` | 1 | 0 | `0_days_rest` | OK |
| **last game D-2** | 1 | 0 | `0_days_rest` | 2 | 2 | `2-3_days_rest` | **DIFFER** |
| **last game D-3** | 2 | 2 | `2-3_days_rest` | 3 | 4 | `4plus_days_rest` | **DIFFER** |
| last game D-4 | 3 | 4 | `4plus_days_rest` | 4 | 4 | `4plus_days_rest` | OK |
| last game D-5 | 4 | 4 | `4plus_days_rest` | 5 | 4 | `4plus_days_rest` | OK |
| last game D-7 | 6 | 4 | `4plus_days_rest` | 7 | 4 | `4plus_days_rest` | OK |
| doubleheader on D-1 | 0 | −2 | `0_days_rest` | 1 | 0 | `0_days_rest` | OK |
| same-day game (DH g1) | 1 | 0 | `0_days_rest` | 0 | −2 | `0_days_rest` | OK (see below) |

**2 of 8 circumstances map to different fitted cells.** D-2 — a single off day
— is among the most common circumstances in baseball.

## What the frozen artifact actually learned

Proven from the path, not assumed. Historical value = `(D-1) − last_game_date`,
which is exactly the count of **off days** between the last game and the slate:

| last game | off days | historical value |
|---|---|---|
| D−1 | 0 | 0 |
| D−2 | 1 (D−1) | 1 |
| D−3 | 2 (D−2, D−1) | 2 |
| D−k | k−1 | k−1 |

The frozen feature is **`off_days_since_last_game`**, not live calendar
`days_since_last_game`.

## The remedy

`backtest/pa_v1_compat.py`, version `pa-v1-rest-semantics-compat-v1`.

```
n_live is None -> None (absent, as today)
n_live == 0    -> None (doubleheader rule, below)
n_live >= 1    -> n_hist = n_live - 1, then the production clamp
```

**The raw value is required.** The stored signal is `clamp((n−1)*2, −3, 4)` and
the clamp is lossy: `v_live == 4` means `n_live ∈ {3,4,5,…}`, whose historical
images span **both** `2-3_days_rest` (n=3) and `4plus_days_rest` (n≥4). Not
invertible — proven by enumeration in `test_pa_v1_compat.py` check 2. So
`generate_picks._build_and_score`'s ctx now exposes `extras["rest"]`: purely
additive, read by nothing in the scoring or recommendation path, changing no
score, no candidate dict and no receipt signal hash.

**Doubleheader rule, explicit.** `n_live == 0` means the most recent game is on
the slate date itself. The historical path could never observe it — its
`asof = D−1` filter (`mlb_sources.py:906-907`) drops it — and the prior game's
date is not recoverable from a bare day count. The historical equivalent is
therefore **underivable**: the adapter returns None, the `days_rest` key is
removed (matching `_sig()`'s absent-is-an-absent-key contract), and PA-v1 falls
back to the batting-order marginal, its own declared fallback. Likewise with no
rest data at all. **Fail closed rather than score a wrong-clock cell.**

## Properties

| requirement | status |
|---|---|
| deterministic | yes — pure arithmetic, no RNG, no I/O |
| outcome-blind | yes — reads only a pregame day count |
| pregame-only | yes — `rest_and_usage` is fetched during the build |
| exact historical equivalence | yes — verified for D−1…D−11 |
| does not modify fitted tables | yes — asserted in test check 7 |
| does not change the training artifact | yes — both hashes re-verified |
| versioned and receipt-recorded | yes — `pa_v1_compat` provenance per candidate, carrying both clocks, the live raw, the historical equivalent and the version |
| regression-tested across boundaries | yes — `test_pa_v1_compat.py` |
| doubleheader behaviour defined | yes — above, fail-closed |

**Frozen PA-v1 identity unchanged:** scientific `a4f598bd…`, file
`112517321e56…`. No refit, no table edit, no new feature.

## Correction to the record

`engineering/mission11/LIVE_FEATURE_PARITY.md` and `FINAL_REPORT.md` §L now
carry a superseding header stating the original conclusion, why "same function,
same call site" was insufficient, the actual reference-clock semantics, and the
corrected verdict. Nothing was silently rewritten.
