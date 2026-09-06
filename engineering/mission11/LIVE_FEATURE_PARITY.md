# Mission 1.1 §15 — live/historical PA-v1 feature parity

> # ⚠ SUPERSEDED — THIS CONCLUSION WAS WRONG
>
> **Original conclusion (2026-09-02): "PARITY ESTABLISHED. The prospective
> experiment may proceed."** That was incorrect.
>
> **Corrected verdict: PARITY DEFECT CONFIRMED, then remedied by an adapter.**
> See `engineering/mission12/REST_SEMANTICS_PARITY.md`.
>
> **Why "same function, same call site" was insufficient.** This document
> argued structural identity: both regimes call the same `_sig()` site and the
> same decoding function, so the encoding cannot drift. That is true and it is
> not enough. Both paths read `m.TODAY` as their reference clock, and
> `PointInTime.__enter__` **repoints `m.TODAY` to D-1** during replay
> (`backtest/engine.py:420,476`) while live it is D. Identical code over a
> different clock produces a different feature. I checked that the code was
> the same and never checked what the code was measured against.
>
> Consequence: the same real circumstance lands in different PA-v1 fitted
> cells for a last game at D-2 or D-3. D-2 — a single off day — is among the
> most common circumstances in baseball.
>
> The measured evidence below (feature presence, decode domains, cell
> resolution) remains accurate as far as it goes. It measured that the live
> features are PRESENT and WELL-FORMED; it did not measure that they MEAN the
> same thing.

**ORIGINAL (SUPERSEDED) VERDICT: PARITY ESTABLISHED.**

PA-v1 is frozen on three features: batting order, days-rest group, getaway-day
group. §15 requires proving the LIVE features have the same semantic decoding
and fallback rules as the historical training representation, and STOPPING the
experiment if they do not.

Two independent lines of evidence, structural and measured.

---

## 1. Structural identity — the same code writes and reads both regimes

This is stronger than a statistical comparison, because it is not an inference
from a sample.

**Both regimes read `signals[name]` written by the SAME `generate_picks._sig()`
calls.** `_sig(bag, name, raw, scaled)` stores the **scaled** value and omits
the key entirely when the raw input is absent (`generate_picks.py`, `_sig`'s
own docstring: "ABSENT IS NOT ZERO AND NOT NEUTRAL").

| feature | the single `_sig` call that writes it | encoding |
|---------|----------------------------------------|----------|
| `lineup_slot` | `generate_picks.py:1942` `_sig(signals, "lineup_slot", order, lineup_context)` | `lineup_context = scale(10 - order, 1, 9)` → 0–100 |
| `days_rest` | `generate_picks.py:2088` `_sig(signals, "days_rest", rs["days_since_last_game"], clamp((n - 1) * 2, -3, 4))` | clamped scaled integer |
| `getaway_day` | `generate_picks.py:2173` `_sig(signals, "getaway_day", 1 if is_getaway else 0, -2 if is_getaway else 0)` | `-2` / `0` |

**Historical rows carry the same bag verbatim.** `backtest/engine.py:1355` calls
`gp.build_candidates(...)` — the same function the live pipeline uses — and
`backtest/engine.py:1111` writes `"signals": pick.get("signals") or {}` straight
onto the row. There is no separate historical feature builder to drift from.

**Both regimes decode through the same functions**, `backtest/pa_v1_fit.py`:
`derive_batting_order`, `days_rest_group`, `getaway_day_group`, `joint_key`.
The live scorer and the fitter import the identical implementations.

`rest` is supplied in both regimes: live via
`generate_picks.py:3763 ("rest", lambda: _src.rest_and_usage(game_meta))`,
historical via `backtest/engine.py:977 "rest": rest`. Both are consumed at the
same line, `generate_picks.py:2085`.

**The batting-order decode is exact and lossless.**
`lineup_context = scale(10 - order, 1, 9)` maps order 1→100 and order 9→0;
`derive_batting_order(v) = round(9.0 - v*8.0/100.0)` inverts it exactly for
every order 1–9 (verified: 1→100→1, 5→50→5, 9→0→9).

---

## 2. Measured on a real live slate — 270 Hits candidates

`engineering/mission11/live_feature_parity_probe.py`, run against a real
`_build_and_score()` pass on 2026-09-02. Raw output preserved at
`live_feature_parity_result.json`.

| feature | present | observed live domain | historical domain |
|---------|---------|----------------------|-------------------|
| `lineup_slot` | **270/270 (100%)** | 9 distinct values, 0.0–100.0 | same scale |
| `getaway_day` | **270/270 (100%)** | `{-2.0, 0.0}` | `{-2.0, 0.0}` — **identical** |
| `days_rest` | 268/270 (99.3%) | `{0.0, 2.0}` | `{-2, 0, 2, 4}` — live is a **subset** |

Decoded batting orders: exactly **30 candidates in each of orders 1–9** — a
perfectly uniform 1-through-9 decode across 30 lineups, which is what a lossless
decode of real batting orders must look like.

Cell resolution against the frozen artifact:

```
joint_cell_present        267   (98.9%)
joint_key_but_cell_absent   1   -> order-marginal fallback
no_joint_key                2   -> order-marginal fallback
scored                    270   (100%)
```

Every candidate scored. The declared fallback chain (joint cell → order
marginal → unscorable) behaves live exactly as the artifact's
`fallback_semantics` block describes.

The live `days_rest` domain being `{0.0, 2.0}` rather than the full
`{-2, 0, 2, 4}` is a property of one early-September slate (nearly everyone
played the previous day), not a semantic difference: the values observed are
drawn from the identical encoding, and the two absent values are simply not
represented on this one date.

---

## 3. A permanent caveat found while doing this — NOT a parity failure

`days_rest_group()` buckets on `0 / 1 / <=3 / else`, which reads as if the
stored value were a raw day count. It is not: `_sig` stores the **scaled**
value `clamp((days_since_last_game - 1) * 2, -3, 4)`. For integer day counts
that value can only ever be:

| days since last game | stored value | bucket it lands in |
|----------------------|--------------|--------------------|
| 0 (doubleheader) | −2 | `0_days_rest` |
| 1 (played yesterday) | 0 | `0_days_rest` |
| 2 | 2 | `2-3_days_rest` |
| 3 or more | 4 (clamped) | `4plus_days_rest` |

**`1_day_rest` is structurally unreachable.** It would require a stored value of
exactly 1, i.e. 1.5 days since the last game. Confirmed two ways: exhaustively
over integer day counts, and empirically — the frozen artifact's 41 joint cells
contain `0_days_rest` (18), `2-3_days_rest` (11) and `4plus_days_rest` (12), and
**zero** `1_day_rest` cells.

So the bucket **labels do not describe their contents**: `0_days_rest` actually
means "played today or yesterday", `2-3_days_rest` means "exactly 2 days", and
`4plus_days_rest` means "3 or more days".

**This is not a parity defect.** The mapping is deterministic and *identical*
in both regimes, because both apply the same function to the same encoding — so
the live challenger is the same model that was fitted. It is a naming and
interpretability defect, recorded here as a **permanent caveat** so that nobody
later reads "4plus_days_rest" as meaning four-plus days of rest.

It is **not fixed**, and deliberately so: PA-v1 is frozen, this mission forbids
any refit or feature change, and renaming the buckets would change the artifact
hash. If PA-v1 is ever refit, this is the first thing to correct.

---

## 4. Conclusion

| requirement | result |
|-------------|--------|
| same live and historical source | yes — the same `_sig` call sites, the same `build_candidates` |
| same transformation | yes — one implementation, no parallel feature builder |
| same missing handling | yes — `_sig` omits absent inputs in both regimes; `joint_key` returns None on any missing component in both |
| same units/domain | yes — live domains are identical (`getaway_day`) or subsets (`days_rest`, `lineup_slot`) of the historical ones |
| same boundary values | yes — batting-order decode verified lossless at both ends and mid-range |
| fallback rules identical | yes — measured live: 267 joint / 3 order-marginal / 0 unscorable |

**Parity holds. No STOP condition is triggered by §15.**
