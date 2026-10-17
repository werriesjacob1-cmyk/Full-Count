# FC-ACC-001: ranked falsifiable accuracy hypotheses (not yet tested)

Ground rules:
- Nothing below has been shown to improve accuracy.
- Each hypothesis must be tested inside S1, at equal pick volume, with factor
  values frozen **before** selection. Success means more wins **and** a
  positive hit − break-even and ROI at captured odds, against **both** the
  champion and the market-only control.
- No outcome was mined to produce this list.
- Ranking: (1) information plausibly not yet in the price at decision time;
  (2) available before selection with existing sources; (3) volume of affected
  picks.

Context: the public losses are concentrated in strikeouts (−10.3 pp against
break-even, n=96) and pitcher_outs (−8.5 pp, n=43), with hits_runs_rbis next
(−6.2 pp, n=360). That is where an advantage would matter most.

| # | Factor | Existing evidence / availability before selection | Hypothesis | Falsification test (S1) | Volume impact |
|---|---|---|---|---|---|
| 1 | **Pregame market movement** | `data/props` has 6 timestamped FanDuel snapshots a day, committed (normalized; no raw hash). Freezes do not store movement. | A price that has shortened toward our side before the decision carries information the opening price lacked; drifting prices are worse. | Freeze the first and decision-time offer per candidate. Within CH1 and CH2, compare hit − break-even for steamed vs drifted picks; add a movement tiebreak only if pre-registered. Reject if hit − break-even differs by < 3 pp (two-sided CI includes 0). | Applies to every priced candidate; reorders rather than shrinks volume. |
| 2 | **Confirmed lineup and batting slot → expected PA** | Freeze stores only `lineup_assumed`. The slot and projected PA are model signals and are not frozen for the pool. | For hits / HRR overs, confirmed slots 1–5 (more PA) beat break-even and slots 7–9 fall below it. The price may lag lineup confirmation. | Freeze the slot and lineup state at decision time. Compare hit − break-even by slot band at equal volume; test a restricted CH1 (slot ≤ 5 among batter props). | hits and HRR are 470/609 public picks: large. |
| 3 | **Pitcher workload / hook likelihood** | Signals (`avg_outs_per_start`, days of rest, bullpen fatigue) exist in the model, but are not frozen for the pool. | Outs and K overs on starters with short recent leashes (low pitch counts, short rest, a team quick to pull the starter) fall below break-even. | Freeze the last-3 pitch counts and outs, rest days and opponent; compare hit − break-even for short- vs long-leash overs; test excluding short-leash overs at equal volume. | Targets the two worst markets (139 public picks). |
| 4 | **Opponent strikeout / contact profile** | Not in frozen fields. Team K% is obtainable pregame from public stats. | K overs against high-contact lineups underperform the price. | Freeze the opponent lineup's K% (confirmed lineup); compare hit − break-even by tercile. | strikeouts: 96 public picks. |
| 5 | **Pitch-type and handedness matchups** | Already inside the model (platoon, pitch-type signals). The pool residual over price is d = +0.045 [−0.21, +0.34], i.e. not demonstrated. | Only a *new*, frozen matchup feature can add over price; the existing ones show no incremental signal. | Freeze the matchup score separately from p; residual test over break-even within S1. | Broad, but low prior. |
| 6 | **Injury and usage changes** | No verified pregame source in the repo. | Late scratches and role changes are mispriced. | Requires a timestamped source first. | Unknown. Lowest until a source exists. |

Not hypotheses: calibration, Brier and log-loss changes. They are diagnostics
and are never treated as pick improvements.

Alligator
