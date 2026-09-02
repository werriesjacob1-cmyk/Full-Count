# SuperClaude activation checklist — for the NEXT mission, not this one

Native `fc-*` remains **NOT active**, re-confirmed this mission in one check:
the project root `.claude/` has no `agents/` and no `skills/` directory, so
nothing can load at session start. Definitions live only on
`tooling/superclaude-activation-01` (`79f1109f`).

Do not call SuperClaude active until a fresh-session runtime proof exists.

1. **Refresh** `tooling/superclaude-activation-01` onto current `main`.
2. **Independent tooling diff audit** — a reviewer who did not write the branch
   reads the full diff for anything beyond agent/skill definitions and settings.
3. **Configuration acceptance** — confirm the 9 agents and 10 skills are the
   intended set, and that `.claude/settings*.json` grants no capability beyond
   what each role needs.
4. **Jacob merge authorization** — explicit, for that branch specifically.
5. **Fresh Claude session** — project agents/skills load at session start, so a
   session already running when the merge lands does not count.
6. **Prove loading** — enumerate available agent types and `ListSkills`; all 9
   `fc-*` agents and all 10 `fc-*` skills must appear.
7. **Invoke each critical specialist at least once** — at minimum
   `fc-prospective-ledger-auditor`, `fc-methodology-red-team`, `fc-live-sre`,
   `fc-release-auditor` — and confirm each returns in its own voice and
   respects its declared tool grants.
8. **Verify hooks / settings / connectors / runtime capabilities** actually take
   effect, not merely that files exist.
9. **Record `SUPERCLAUDE_RUNTIME_CERTIFIED`** with the session id, the head SHA,
   and the enumerated agent/skill list as evidence.
