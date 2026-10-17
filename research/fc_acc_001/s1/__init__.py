"""FC-ACC-001-S1 research-only evidence capture (NOT activated).

Minimal additive capture for the forward shadow protocol (../S1_PROTOCOL.md):
  raw_archive   content-addressed, write-once raw sportsbook/stat responses
  fanduel       raw-preserving FanDuel sweep + offers parsed ONLY from archived bytes
  workload      pregame pitcher workload from archived MLB statsapi game logs
  features      minimal point-in-time candidate feature snapshot (explicit UNKNOWN)
  manifest      one shared offer manifest per slate unit, arms at matched N, seal
  ledger        write-once, idempotent unit seals with integrity verification

No production code path calls this package. Nothing here schedules, publishes,
grades, or changes a selector, calibrator or published record.
"""
