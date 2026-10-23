# NFL scoped rules (Codex and Claude load this when working under nfl/)
- Tier 1 protocol-v1 seals are immutable once committed (Week 4: `claude/nfl-tier1-seal-2026w04` @ `693526aa5f`, inputs `claude/nfl-seal-inputs-2026w04` @ `b304820f7a`). Never back-fill a game after kickoff; seal only games > 45 min before kickoff.
- Exploratory rows are `EXPLORATORY_RESEARCH_ONLY_NOT_A_PICK` and never enter primary predictions.
- Point-in-time inputs only (injury/practice reports, weather, lines as of the seal time); never invent historical prices.
- `codex/` branches (#196, #199) are Codex-owned; do not edit without a recorded handoff.
- Orientation and task state: `fc.py orient` on `claude/full-count-ops-state` (see root AGENTS.md).
