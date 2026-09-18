# FULL COUNT nflverse quarantine ledger

Date: 2026-09-14

Scope: deterministic row-level disposition for source defects already measured in
the 1999–2025 nflverse weekly player-stat audit. This contract is research
infrastructure. It does not authorize a warehouse load, model use, grading,
selection, or production publication.

## Safety contract

`nfl/research/nflverse_quarantine.py` accepts only the committed full-audit
manifest and an external raw cache. Before reading rows, it requires every
seasonal asset's byte length and SHA-256 to match the audit. Missing files,
source drift, malformed CSV rows, blank or invalid audited numeric values, and
per-season count drift stop generation.

Every emitted entry carries:

- a stable `nflq_` identifier derived from a version tag, full source digest,
  CSV line, sorted reason set, and canonical row digest;
- the logical source asset and full source digest, with no machine-local path;
- the CSV line and canonical full-row SHA-256;
- explicit reasons, disposition, and allowed uses;
- compact identity/game context, plus the observed nonzero offensive fields
  when present.

The ledger never guesses an identity, team, opponent, name, position, or
statistic.

## Dispositions

`EXCLUDED_STRUCTURAL_ZERO` applies only when the stable ID is blank or literal
`0`, display name and position are blank, all eleven tracked offensive fields
are zero, and no other critical-location reason is present. Its only allowed
use is `source_completeness_accounting`; it is excluded from player history and
model rows.

Every other emitted row is `QUARANTINED_RESEARCH_BLOCKER` with an empty
`allowed_uses` list. Blocking reasons are:

- `MISSING_IDENTITY_WITH_OFFENSE`
- `MISSING_STABLE_IDENTITY`
- `IDENTIFIED_ROW_MISSING_DISPLAY_AND_POSITION`
- `MISSING_OPPONENT`
- `MISSING_TEAM`

A row can carry multiple sorted reasons. Any additional critical-location
defect on a structural-zero row upgrades the row to a research blocker.

## Reproduction

After the audited external files are present:

```powershell
python -m nfl.research.nflverse_quarantine \\
  --audit engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json \\
  --cache <external-cache> \\
  --output engineering/evidence/nflverse_weekly_stats_quarantine_2026-09-14.json
```

The compact output may be committed only after all 27 source digests and the
476,159-row total reproduce exactly. Raw nflverse CSVs remain outside Git.

The prior cache directory was found empty during this implementation.
Re-acquisition is recorded as approval request `5670896965` on Issue #91.
Until exact approval and native network access are available, full-corpus
ledger generation remains intentionally incomplete; aggregate audit counts are
not expanded into invented row evidence.

Alligator
