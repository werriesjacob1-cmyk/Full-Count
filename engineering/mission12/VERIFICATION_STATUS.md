# Verification status at head `d3a7…` (branch `claude/prospective-hits-pa-lifecycle-closure-01`)

## Test suite

Local, full glob, this head: **PASSED=138, FAILED=2.**

GitHub Actions `test.yml` at head `2fe604db`: **failure** — and it is called
failure here, not green.

## The two failures, and why they are not this branch's

| suite | Mission 1 base `41369064` | current `main` | this branch |
|---|---|---|---|
| `test_board_first_paint.py` | FAIL | **FAIL** | FAIL |
| `test_browser_e2e.py` | FAIL | **PASS** | FAIL |

* **`test_board_first_paint.py`** — "an 11-hour-old board is classified stale on
  board age". Fails on current `main` too, re-verified at `a609866d`. It is a
  four-clock fixture defect that predates Mission 1 and is PR #75's subject. It
  must not be absorbed into this branch.
* **`test_browser_e2e.py`** — "at least one clickable pick-card/prop-row exists
  on Today". Fails on the Mission 1 base, **passes on current `main`**. The
  branch is 347 commits behind main, so its committed `docs/` fixture data has
  no clickable Top Pick card. Fixed on main already; this branch inherits the
  old data. It resolves by merging `main` in — step 2 of `PR85_INTEGRATION.md`.

Neither failure touches `backtest/prospective_*`, `pa_v1_compat`,
`generate_picks.py` or the capture tap. All 9 prospective suites pass, plus
the new `test_prospective_bootstrap.py`.

## What is verified by execution, not by reading

* the reference-clock parity defect, driven through the real production worker
* the adapter's exactness for every reachable D-k, and its fail-closed on the
  non-invertible same-day case
* a non-final grade is never sealed, and the real grade is recordable afterwards
* a champion deleted from the served payload fails the epoch closed
* a demoted pick does **not** fail closed, and the demotion is recorded
* a late all-`final` deployment does not kill the date
* the bootstrap implementation swap is refused while the values pin passes it
* per-arm concentration diverges from pooled by ~3x on the locked fixture
