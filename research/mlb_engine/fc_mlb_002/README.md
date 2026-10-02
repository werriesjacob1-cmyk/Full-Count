# FC-MLB-002 — Hits 1+ batter×pitcher interaction challenger (research only)
Criteria: `engineering/ops/TASKS/FC-MLB-002.md` on `claude/full-count-ops-state` (frozen at cb1a338b28).
Run: `fetch_statcast.py` → `fc_mlb_002.py pa` → `fit` (2025 only; writes out/FROZEN_FIT_2025.json, committed BEFORE evaluation) → `evaluate` (ONE run on 2026) → `make_report.py`.
Data: Baseball Savant pitch-level CSVs (STATCAST_MANIFEST.jsonl: per-day sha256 + fetch time; raw files kept outside git).
DEVELOPMENT evidence only. No production/selector/pick/ledger/V3 change.
