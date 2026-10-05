# FC-MLB-001A drill attempt history (nothing hidden)

All attempts are FC-MLB-001A EVIDENCE-INTEGRITY DRILL runs, NOT prospective units. None was pushed to the evidence chain or posted as a receipt.

| # | Commit | Result | Cause |
|---|---|---|---|
| 1 | `00dc06956c` | aborted before any artifact | `ENOSPC` while checking out the pinned shadow tree (container disk allowance) |
| 2 | `00dc06956c` | **failed closed on the R5 guard** | matplotlib font-directory probes (`/usr/X11…/fonts`, `/usr/local/share/fonts`, …) and the interpreter's stdlib zip path entry (`/usr/lib/python311.zip`) |
| 3 | `e6fdee0230` | **recorded** → `A1_DRILL_20261005_NIGHT/` | — |

**After attempt 2:**
- Both surfaces from the guard failure were named explicitly as read-only OS/interpreter surfaces in `isolation.OS_RUNTIME_PREFIXES` and the stdlib zip entry; there is no blanket allowance.
- The complete tape (~257 MB) is stored as byte-exact parts, because GitHub rejects files over 100 MB.

**Checks on attempt 3:**
- `PRECHECK_SAME_CONTAINER_REPLAY.json` is a pre-check replay in a separate isolated root in the *recording* container. It is **not** environment B.
- Environment B is the GitHub Actions run (`.github/workflows/v3-a1-drill-replay.yml`).
