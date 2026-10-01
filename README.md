# MLB accuracy challenger v3: append-only evidence ref

This branch holds only pregame seal records for preregistration v3 (`research/mlb_accuracy_challenger_prereg_v3_20261001.md`, commit `15fb1d539c16d368bd8835254d93bb8f0ccd4609`, sha256 `5eb56f2837e25d29c5821043955eefe52c0d5f0513e9b6099ac5facb2ff63442`).

**Layout**
- One directory per slate unit: `seals/{date}_{window}/`. Each holds `seal.json`, `manifest.json.gz`, `capture.json.gz`, `schedule.json`, `shadow_board.json.gz` and `receipts.json`.
- `CHAIN.json` is the hash chain. Index 0 is GENESIS, whose `seal_sha256` is the prereg file sha256.

**Rules**
- **Append-only.** Never force-push, never rewrite or delete a directory, and never add a second seal for a unit.
- **Validity.** A seal is valid only with an Issue #91 receipt carrying its `seal_sha256`, plus a verified RFC 3161 token, both before the earliest covered first pitch. `evaluate_v3` verifies this. Git timestamps are ignored.
- **Status.** Prospective collection is **not activated** until Jacob separately authorizes it.

Alligator.
