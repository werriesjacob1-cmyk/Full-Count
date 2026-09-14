# PA-v1 AUTHORITATIVE FITTED ARTIFACT — scientific identity

**This is the authoritative PA-v1 prospective scientific identity.**

| field | value |
|---|---|
| `scientific_content_sha256` | **`a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a`** |
| `serialized_file_sha256` | `112517321e562ee25f46140cf8ce52e2ef48b40447235cf9b22e50dec9870750` |
| protocol | `prospective-hits-pa-v1` |
| model | `residual_order_days_rest_getaway` |
| certified rows sha256 | `8ca010641d08008044c8c3b609162d6e5d69f07bb79be6705b2690a51ab2cb34` |
| certified rows / dates | 1,186,300 / 555 |
| train cutoff (inclusive) | `2026-08-25` |
| MIN_CELL_N | 200 |
| joint / order cells fit | 41 / 9 |
| training player-games | 121,590 |
| fitter file sha256 | `82bf9b2faf27ed2c76a9a49753a737784a6b2f5fa8608e55ae79f88b6af6ff90` |
| repo HEAD at fit | `030e03d33af29917be59300bdcd1d0e546524ee1` |
| certified input verified | **true** (fail-closed gate passed) |
| fitter worktree clean | **true** (fail-closed gate passed) |
| `effective_from` | **`2026-09-02T00:00:00+00:00`** |

## effective_from is deliberate

Set to 2026-09-02T00:00:00Z, which is **after** this freeze (2026-09-01 ~20:56Z)
and therefore after any receipt produced by today's dry run. That is the point:
the Mission 1K dry run happens strictly before `effective_from`, so its receipts
are structurally non-countable rather than merely labelled as such. The first
countable prospective receipt can only exist from 2026-09-02 onward.

Locked protocol section 3: PA-v1 is frozen once the first eligible prospective
receipt exists. It is never backdated after observing a result.

## Reproducibility

Two authoritative invocations over byte-identical certified input produced the
identical `scientific_content_sha256`, while `created_at` and
`repo_worktree_fully_clean` differed between them — proving those siblings are
correctly outside the hash. `--verify` recomputes the hash from the artifact's
own contents and reports VERIFIED.

`serialized_file_sha256` differs between runs by design: the file bytes contain
`created_at`. Use `scientific_content_sha256` for scientific identity and
`serialized_file_sha256` for byte custody of one specific file.

## SUPERSEDED

`a57f4362b86be85c9c4f9a8ff63380002fbf845dd7e803454d20237c23fbedfd` was a
fitting **smoke test** committed earlier on this branch. It is **NOT
authoritative** and must never be cited as PA-v1's identity. It was produced
before the four integrity corrections: incomplete hash coverage, certified
input described rather than required, code identity not binding the bytes that
ran, and dedupe able to silently resolve a player-game conflict. Its history is
preserved deliberately; it is superseded.
