# AUDITS — isolated challenger verdicts
- Path: `AUDITS/<TASK_ID>/<agent>.md`. Header: `VERDICT=PASS|BLOCK  AUDITED_SHA=<sha>  SCOPE=full|delta:<from>..<to>`, then numbered findings.
- The builder does not read another agent's audit of the same task before it is posted; challengers do not read each other's.
- Never copy audit verdicts into CURRENT_STATE or FACTS until SUPERCHAD adjudicates (then record only the outcome + pointer).
- A PASS records `AUDITED_SHA`; later checks are delta-only from it (contract §5).
