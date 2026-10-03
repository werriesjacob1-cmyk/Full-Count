# Preregistration amendment 1 (2026-10-01, before the boundary)

Amends `research/mlb_accuracy_challenger_prereg_20261001.md` (commit `0ebd6152c2`). This amendment was committed before 2026-10-01T18:00:00Z, so no evaluation slate existed when it was made.

## Change
- An empty-input smoke run of the new runner (`run_eval.py`, commit `e66abc64b8`) crashed in `harness.clustered_diff`: a window with no scored picks produced zero bootstrap draws.
- The fix returns `ci95: null` and `one_sided_lower95: null` when no draws exist. `verdict` then treats a missing bound as not passing.
- Results on any non-empty data are unchanged. No metric, threshold, band, selector, coefficient, regime or decision rule changed.
- A new test covers the empty window, giving 17 tests.

## Updated provenance (supersedes the §14 rows for these files)
| File | sha256 |
|---|---|
| `harness.py` | `d355a12b597556341b1f7f8133fbbfc4d2ccb667057f370890edad2dd3631857` |
| `test_harness.py` | `0c65a94ce6ed31b05c10b12895c16f2633a0cddb8f428eb4244e128293b90f97` (17 tests) |
| `run_eval.py` | `191d8cdc65b3660985c22da3dec76bc25a4767674b3b8ada7ee8c1f4643f5635` (I/O only) |
| `frozen_coefficients.json` | unchanged: `3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009` |

Alligator.
