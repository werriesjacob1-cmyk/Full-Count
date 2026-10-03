# FULL COUNT — CURRENT STATE
as_of: 2026-10-02T15:05Z · main `8b68985234` · branch `claude/full-count-ops-state` (never merged to main)

This file is the default orientation. Do **not** read `ENGINEERING_HANDOFF.md` or Issue #91 history unless the task is historical reconstruction.

## Authority (always)
- **Roles:** Jacob is final authority. SUPERCHAD is the control plane and adjudicator. Codex is the independent challenger.
- **Jacob's exact-SHA approval is required for:**
  - merge, deploy, model promotion;
  - production model, selector, pick or ledger change;
  - activation or grading activation;
  - evidence or prereg change;
  - purchase or wager;
  - access or security change.
- No self-approval, no merging your own PR, no backfill of prospective evidence, no outcome reads past a prospective boundary.
- Acceptance criteria are written before any consequential build (contract §3).

## Working rules (summary; full text in OPERATING_CONTRACT.md)
- **Checkpoint** (#91; produced by `fc.py checkpoint TASK_ID`):
  `CHECKPOINT / TASK= HEAD= STATUS= / TESTS= BLOCKERS= / EVIDENCE= / NEXT= AUTHORITY= / Alligator`
- **Lanes:** A = Claude builds → Codex challenges → minimum repair → delta check. B = Codex researches → Claude challenges. Both end at SUPERCHAD.
- **WIP:** 1 active build per sport and 1 active challenge. Claims are made in WORK_QUEUE (`fc.py move`) and expire after 12 h.
- **Challenger** receives `fc.py challenge-capsule` only: never BUILDER_NOTES, LOG, verdicts or builder conclusions.
- **Efficiency:** batch independent commands; wait with `fc.py status --until-change`, never model polling; rotate sessions at about 150–200k.

## Split
MLB 60% / NFL 40%. NFL may take the majority inside T−48h of an NFL slate lock.

## MLB
- **V3 accuracy challenger:** ACTIVATED 2026-10-02 (Jacob, #91/5953838152). Collection is automated. **Do not touch.**
  - Implementation `504ca9cdb9` (tree `a7c9443`), prereg `15fb1d539c`. PR #220 is draft and must never be merged without Jacob.
  - Evidence ref `claude/mlb-challenger-v3-evidence` is append-only.
  - Runner session `session_016RxnYjrtP3stCp3XRSa2Ux`, woken by `trig_011u98uXVuFEipPfbTT6KGur` (hourly at :07, 13–01Z), running the dispatcher on `claude/mlb-v3-ops`.
  - Current regime is `2026_POSTSEASON_SHADOW` (descriptive). The confirmatory regime is the 2027 regular season.
  - No V3 outcome reads or scoring; one-look rules apply.
- **Focus:** predictive-engine development (probabilistic prop engine): **FC-MLB-002** (repaired; zero-match fix 7c331c266b) ; **FC-MLB-004** DONE (negative); **FC-MLB-005** DONE (NO_GAIN); **FC-MLB-006** (PA event world model) evaluated @ a555d4dd59, awaiting SUPERCHAD/Jacob.
- **Other MLB drafts** (pointers only; read on demand): #219 forward-chain study, #217 market-anchor prereg, #198 slate-date contract audit.

## NFL
- **Week 4 Tier 1 protocol-v1 seals:**
  - Thursday all-games seal: `claude/nfl-tier1-seal-2026w04` @ `693526aa5f`. **IMMUTABLE.**
  - Inputs: `claude/nfl-seal-inputs-2026w04` @ `b304820f7a`. **IMMUTABLE.**
  - The Saturday sun_mon seal runs automatically at 2026-10-03T18:30Z (`trig_01SthdubqRKWd3N28PRZjzvc` into `session_01YFgmGN4y6vJjiDc8rbZze2`), with a backstop at 20:00Z (`trig_01VarHYCdiEZ41YBBfFu9kNJ`).
  - First Sunday kickoff is 2026-10-04T13:30Z. Never backfill.
- **Focus:** predictive engine for props plus spreads/totals, task **FC-NFL-002**.
- **Open NFL drafts** (pointers only):
  - #202–#205 and #207: Tier 1/Tier 2 stack;
  - #193: receptions scale;
  - #196 and #199: Codex-owned; do not edit.

## Active work
Run `fc.py queue` for the live board. WIP: at most 1 active build per sport and 1 active challenge.

## Blockers / risks
- **Engineering blockers:** none recorded.
- **Ops risk:** a V3 NIGHT unit whose latest start falls after 00:00Z may fail closed. The unit is lost; no backfill.

## Pointers
- Facts: `engineering/ops/FACTS/{REPO,MLB,NFL}.json`
- Contract: `engineering/ops/OPERATING_CONTRACT.md`
- Task capsules: `engineering/ops/TASKS/<TASK_ID>.md` and `WORK_QUEUE.json`
- History (reconstruction only): `engineering/ENGINEERING_HANDOFF.md` (FROZEN 2026-10-02, see `ARCHIVE/README.md`); Issue #91; `engineering/PROJECT_STATE.md` (system map, last verified 2026-08-17)
