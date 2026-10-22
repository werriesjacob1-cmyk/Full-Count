# MLB accuracy challenger: superseded versions (SUPERSEDED — NONCONFIRMATORY)

This register marks earlier versions without editing them. Their files stay byte-for-byte as committed, as a record of the research discipline.

| Version | Files | Boundary commit | Status | Why |
|---|---|---|---|---|
| v1 | `research/mlb_accuracy_challenger_prereg_20261001.md`, `harness.py`, `run_eval.py`, `test_harness.py` (+ amendment 1 at `96884cb968`) | `0ebd6152c2` | **SUPERSEDED — NONCONFIRMATORY**. Never evaluated. | Codex audit, #91 comment 5935759204: no operational universe and no quote provenance. Superseded by v2. |
| v2 | `research/mlb_accuracy_challenger_prereg_v2_20261001.md`, `manifest.py`, `harness_v2.py`, `seal_manifests.py`, `run_eval_v2.py`, `test_v2.py` | `9265935966c9d2150d2bccb1086b585bdd105169` | **SUPERSEDED — NONCONFIRMATORY**. Never evaluated, and no manifest was ever sealed. | Codex audit, #91 comment 5938236903: the quote join ignored game identity, `taken_at` marks a sweep's start, sealing happened after first pitch with self-asserted timestamps, and regimes were not enforced. Superseded by v3. |

**v2 seal routines.** `trig_01NJyVApkdSbMBK1ojRp9WY6` (23:23Z) and `trig_019HwkqAnwzySnTBmYnhVJU7` (01:13Z), with runner `session_019i5ReaXMnSDN3ZRBdYthZs`. Both were **disabled 2026-10-01T18:51Z, before their first firing**, because Codex found the v2 seal architecture insufficient for prospective evidence. Their history is kept.

**Rules for v1 and v2:**
- No v1 or v2 slate may ever be called confirmatory.
- The v1 and v2 harnesses must not be run on prospective data.
- `harness.py` (v1) remains in use **only** as the frozen arm, selector and C3 library that v3 imports unchanged.

**Current protocol:** `research/mlb_accuracy_challenger_prereg_v3_20261001.md`.

Alligator.
