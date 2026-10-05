# Environment-B runs (GitHub Actions, `.github/workflows/v3-a1-drill-replay.yml`)

Each run is a fresh GitHub-hosted VM with a shallow checkout, a fresh venv from the lock, and a read-only token. Results are in:
- the job summary;
- check-run annotations (`A1-B …`);
- the artifact `a1-drill-replay-b-<sha>` (retained 90 days).

| run | commit verified | result |
|---|---|---|
| [37352202211](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/37352202211) | `fc3a59d52f` | job **success** (tests + replay green). The result JSON was not readable through the builder's API client (blob-hosted logs), so annotations were added. |
| [37353256200](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/37353256200) | `b22da45369` | job green, but **INVALID AS A GATE** (see correction) |

## CORRECTION (Codex final audit, accepted)
Runs 37352202211, 37353256200 and 37353829066 are **not valid gate evidence**.
- Their test steps piped `unittest` through `tail` without `pipefail`.
- `test_a1.DrillArtifacts` ran from the workspace root, where it cannot import `test_a1`.
- Required tests could therefore be red while the job was green. Codex reproduced exactly that for 37353256200.
- The replay annotations below were real verifier output. However, that verifier's `exact_replay` was the timestamp-stripped `replay_equivalent`, **not** literal board identity.

The repaired gate (a1-2) is recorded on Issue #91, with run ids for the controlled mutant and for the clean exact head. It has:
- `bash -eo pipefail` everywhere, real exit codes, and no tail/tee on any test;
- DrillArtifacts run from `v3/` with `A1_REQUIRE_DRILL=1`;
- literal board identity;
- a `strace -f` process-tree trace;
- three always-run gate steps.

**Run 37353256200 in detail (historical; superseded):**
- Every check PASS:
  - sha256sums, artifact set, board / capture / schedule / manifest hashes, manifest reproducible;
  - overlay, tape identity, provenance identity, timestamps, exact replay.
- Replay: misses 0, unconsumed 0, guard violations 0, environment mismatch none.
- Replay environment: python 3.11.16, image `ubuntu24-20260927.320.1`, kernel `6.17.0-1022-azure`, glibc 2.39, shallow clone, host pybaseball cache ABSENT, HOME/cache/TMP empty at start.
- Hashes, identical to record A:

| artifact | sha256 |
|---|---|
| shadow board | `44f94b6aba474a5d1f5f42aa6d417aba7c478eb59701752ac7da90317b14deeb` |
| capture | `73c03fee7d4f3cfe167b3419f68147d01a3c2dc2cf2aa090af046f51d204a12a` |
| schedule | `51f48ae2b3963b8b272e9514d472dc82793349aebefc4808fc57072d23dd09f4` |
| manifest | `04f0b81043d62c6384d84a9d67abad2eb131e4c28aebe54c9272a38d35d094cf` |
| tape | `e2cf77b38acaa8f5c3a93b14db1dfec4d3636971031ef865ff45ce0b32d0c4f7` |

- TSA: freetsa and digicert 2026-10-05T17:49:21Z, before first pitch (21:00Z).
- The verifier code (`*.py`, `*.lock`) is byte-identical between the record commit `e6fdee0230` and the verified commit `b22da45369`.
