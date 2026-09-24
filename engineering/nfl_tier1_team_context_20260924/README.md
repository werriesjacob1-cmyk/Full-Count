# NFL Tier 1, Workstream C: team and game context (F1, F5, F6, F7, F10)

Branch `claude/nfl-tier1-team-context-20260924` (based on
`claude/nfl-tier1-foundation-20260924`). This is research only. It does not
change B0, the live workflows, any pick or any selector. The shared
`contract.py` and `harness.py` files are used as they are and are not edited.

## What was built

| Piece | File |
|---|---|
| Schedule context, stadium table, and per-team-game PBP summaries | `nfl/research/tier1/team_context_data.py` |
| Strictly-prior feature builders (F5 and team volume base, F10) and contract rows | `nfl/research/tier1/team_context_features.py` |
| Weather forecasts with a provable run time (GFS MOS via IEM) | `nfl/research/tier1/team_context_weather.py` |
| Consumer `team_context_challenger` (ablations, attribution) | `nfl/research/tier1/team_context_challenger.py` |
| Live week-N builder (feature rows and player-level research outputs) | `nfl/research/tier1/team_context_live.py` |
| Tests (19): known answers, leakage, UNKNOWN, mutation checks | `nfl/tests/test_tier1_team_context.py` |
| Evaluation in two stages (`--stage dev`, then `--stage final`) | `evaluate_team_context.py` |
| Historical MOS fetch, status lines, table printer, parity check | `fetch_mos_weather.py`, `status_records.py`, `summarize_report.py`, `pbp_tendency_parity_check.py` |

The consumer's form was declared in `team_context_challenger.py` before any scoring:

```
team:   E_S[y] = OLS_DEV(y ~ 1 + base_y_pg + features(S)),   S within {F1, F5, F6, F7}
        y = team dropbacks (receptions) | team gross passing yards (passing_yards, receiving_yards)
player: pred = k * B0 * clip(E_S / hist_y, 0.5, 2) ** alpha * F10_index ** beta
```

- `k` is the harness scale control fitted on DEV.
- `hist_y` is the team's actual volume in the games that formed the player's B0 window. B0 parity with the harness was checked on every scored row: 5,805 passing, 39,631 receptions and 39,631 receiving rows.
- `alpha`, `beta` and the F10 pseudo-games `m` come from a grid search on DEV MAE.
- When any input is UNKNOWN, the row uses the multiplier 1 (that is, `k*B0`) and the reason is recorded.

**Commit order is the proof that the holdout was not used for fitting.**
1. `f347e02868` froze `team_context_dev_params.json` (SHA-256 `34c803fb…049f`) before HOLDOUT or FRESH was scored.
2. `--stage final` then refits on DEV and aborts if anything differs from the frozen values.

## Factor status

Criteria are coded in `status_records.py` (output: `team_context_status.json`).
- The team factors F1, F5, F6 and F7 sit inside the team-volume ratio. Each is therefore judged on its increment over `VOLUME_BASE`, which is the same consumer with no Tier 1 factor.
- `VALIDATED` requires all of these:
  - vs the scale control, the confidence interval (CI) is below 0 on HOLDOUT and on DEV;
  - for team factors, the increment's CI is also below 0 on HOLDOUT and on DEV;
  - the FRESH point estimate is not worse.

Every delta is MAE(challenger) − MAE(reference); negative means better. HOLDOUT_2023_2025 is **exploratory** (previously inspected). FRESH_2026 covers weeks 1–2 only.

| Factor | Milestone | Consumer config | HOLDOUT activation | HOLDOUT delta vs scale control (CI) | HOLDOUT increment vs VOLUME_BASE (CI) | FRESH vs scale / vs VB |
|---|---|---|---|---|---|---|
| F1 game context (closing-line proxy) | **BUILT** | `F1` | 0.999 | pass yds −2.72 [−3.63, −1.72]; rec yds −0.128 [−0.166, −0.089]; rec −0.008 [−0.011, −0.005] | pass yds −0.85 [−1.35, −0.29]; rec yds −0.042 [−0.065, −0.017]; rec −0.000 [−0.001, +0.001] | pass −3.13 / −0.13 (n=71); rec yds −0.207 / −0.147 (n=436); rec −0.004 / −0.003 |
| F5 pass tendency and pace | **REJECTED** (incremental) | `F5` | 0.999 | pass −1.96; rec yds −0.089; rec −0.009 (all CI < 0, but carried by the volume base) | pass −0.085 [−0.205, +0.032]; rec yds −0.003 [−0.008, +0.002]; rec −0.001 [−0.002, +0.000] | pass +0.13 vs VB; rec yds −0.007; rec +0.003 |
| F6 environment (roof, surface, MOS forecast) | **VALIDATED for passing_yards and receiving_yards**: historically supported, not prospectively validated. Receptions not validated. | `F6` | 0.951 | pass −2.25 [−3.01, −1.50]; rec yds −0.107 [−0.141, −0.075]; rec −0.009 [−0.011, −0.006] | pass **−0.379 [−0.706, −0.059]**; rec yds **−0.021 [−0.035, −0.008]**; rec −0.001 [−0.001, −0.000] | pass −3.41 / −0.42; rec yds −0.067 / −0.007; rec −0.001 / **+0.0001** (fails "not worse") |
| F7 rest and travel | **REJECTED** | `F7` | 0.999 | pass −1.85; rec yds −0.088; rec −0.008 (volume base) | pass +0.023 [−0.086, +0.126]; rec yds −0.002 [−0.007, +0.003]; rec +0.000 | null |
| F10 opponent allowed-by-position | **BUILT** | `F10` (no volume ratio) | 0.974 to 0.998 | pass **−0.421 [−0.754, −0.097]**; rec yds −0.015 [−0.033, +0.004]; rec −0.002 [−0.003, −0.000] | n/a | pass −0.05; rec yds −0.029; rec **+0.005** |

Notes on the table:
- **F1** is not VALIDATED by doctrine. Its historical values are closing lines (`CLOSING_LINE_PROXY_RETROSPECTIVE`), which no pre-kickoff prediction had.
- **F10** is not VALIDATED. For passing yards the DEV in-sample CI crosses 0 (−0.204 [−0.430, +0.018]), which fails the consistency condition. For receptions the FRESH point estimate is worse.

Combined configs and the volume base (HOLDOUT delta vs the scale control):

| Config | uses_market_input | passing_yards | receiving_yards | receptions |
|---|---|---|---|---|
| VOLUME_BASE (team-volume ratio, no factor) | False | −1.87 [−2.67, −1.13] | −0.086 [−0.116, −0.056] | −0.008 [−0.011, −0.005] |
| ALL_NO_MARKET (F5+F6+F7+F10) | False | −2.62 [−3.46, −1.73] | −0.124 [−0.165, −0.087] | −0.011 [−0.014, −0.007] |
| ALL (F1+F5+F6+F7+F10) | **True** | −3.09 [−4.02, −2.14] | −0.158 [−0.202, −0.112] | −0.010 [−0.013, −0.007] |

On FRESH_2026, ALL_NO_MARKET is −3.90 for passing yards (n=71), −0.166 for receiving yards and +0.008 for receptions (n=436); the CIs are wide.

Full per-partition tables are in `summarize_report.py` output and `team_context_report.json`. These include vs B0, vs the scale control, vs VOLUME_BASE, activation, bias, fallback counts and per-factor attribution counts. Row-level attribution for every FRESH_2026 row under `ALL` is in `fresh_2026_attribution_rows`.

- **Bias:** every config under-predicts on average (for example, passing yards on HOLDOUT is about −3). The cause is the MAE-optimal scale `k < 1` (0.96, 0.87 and 0.78), which is inherited from the scale control.
- **Grid edges:** the ALL / ALL_NO_MARKET passing F10 parameters hit grid edges (`m`=128, `beta`=1.5). This reflects a flat ridge between `beta` and `m`. It was recorded, and the grid was not re-tuned after HOLDOUT.

## Market and model separation (read before using F1)

- Every config that contains F1 has `uses_market_input=True`.
- Historical F1 is the nflverse **closing** spread and total. It is a retrospective proxy and could not have been known before kickoff.
- Live F1 is FanDuel's own timestamped line.
- A config that uses F1 must **never** be used as independent evidence of value against the same book's player prices. `ALL_NO_MARKET` is the market-independent consumer.

## Sources and SHA-256

- **Shared data** (verified against `MANIFEST.sha256`): `schedules/games.csv` `7fdc123e…4c4e`; `play_by_play_2016..2026.csv.gz` (all 11 hashes are in `team_context_report.json → sources_sha256`, for example 2026 `6643f82a…ece8`); `stats_player_week_2026.csv` `736bdddef…8e67`; the weekly 2015–2025 files are audit-verified by the harness.
- **Stadium coordinates:** English Wikipedia coordinates API, raw response SHA-256 `f0d6a57e…c4ba1d45`. Stored in `data/`. There are 46 titles and none is missing. The IANA time zones are listed in `STADIUMS`.
- **Weather:** GFS MOS (MAV), taken from the IEM archive API `mesonet.agron.iastate.edu/api/1/mos.json`.
  - The pre-declared run is **12Z on the day before the ET game day**. Every row includes its `runtime`, and MOS is issued about 4 hours after its runtime, so the forecast is at least about 20 hours before kickoff.
  - 1,816 outdoor REG games were fetched. The table is `data/mos_weather_table.json.gz`, whose uncompressed SHA-256 is `1ac5c922…88fd`. Each entry keeps its raw-response SHA.
  - games.csv observed `temp`/`wind` are **never** used.
- **Live FanDuel:** the root and 16 event payloads were captured on 2026-09-24 at about 21:17Z and again at about 21:20Z. Their SHA-256s are in the live JSON.

## Live path, 2026 week 3

`live/team_context_live_2026_w03.json` (SHA-256 `c3bb9569…d4ed`) contains:
- **160 validated contract rows** (16 games × 2 teams × 5 factors). Each row's `information_cutoff` is the newest source time, 2026-09-24T21:20Z, which is before every kickoff.
- **544 player-level research outputs** from the frozen parameters, under the configs VOLUME_BASE, F10, ALL_NO_MARKET and ALL.

Example, ATL@GB (`2026_03_ATL_GB`, kickoff 00:15Z):
- **F1 live:** GB −4.5 and total 43.5, so GB's implied total is 24.0 and ATL's is 19.5.
- **F6:** outdoors on grass. The MOS run from 2026-09-23 12Z forecasts 4 kt wind, a 5% 6-hour probability of precipitation (PoP) and 60°F, with a 36.25 h lead.

Status of the live parts:
- **F1:** captured live for all 16 games.
- **F6 weather for Sunday and Monday outdoor games: PARTIAL.** Their pre-declared MOS run has not been issued yet, so those rows say `NOT_YET_ISSUED` together with the re-run time (Sunday games: 2026-09-26T17:00Z). ALL and ALL_NO_MARKET fall back to `k*B0` on those rows until then; VOLUME_BASE and F10 are active.
- **F7, F5 and F10:** complete from the weeks 1–2 PBP.

To refresh:
```
PYTHONPATH=. python3 engineering/nfl_tier1_team_context_20260924/evaluate_team_context.py --stage dev   # only if /tmp caches are missing (creates PBP summaries)
PYTHONPATH=. python3 -m nfl.research.tier1.team_context_live --season 2026 --week 3 --out <dir>
```
Stadium coordinates: `data/stadium_coordinates.json` must be at `/tmp/claude-0/nfl_tier1_c/stadiums/`.

## Negative findings and contradictions (kept on purpose)

- **F5 and F7 add nothing at the player level** beyond the prior team volume, even though they slightly improve the team dropback and pass-yard intermediate (team-level tables in the report).
- **Static F6 (roof and surface only) gave almost no increment.** The prelim DEV-only run (`team_context_dev_report_prelim_f6_static_only.json`) was about −0.02 passing yards vs VOLUME_BASE, compared with −0.19 once the MOS forecast was added.
- **F10 is not a WR-vs-CB matchup** and has no slot/wide split, because there is no alignment data. Its receiving-market effects are tiny: HOLDOUT −0.002 receptions and −0.015 yards.
- **Open-Meteo was blocked.** The historical-forecast, previous-runs and forecast APIs all answered `{"reason":"Daily API request limit exceeded..."}` from this egress on 2026-09-24. MOS (runtime proven) replaced them. Open-Meteo's historical-forecast archive would also not have proven the run time.
- **games.csv roof semantics:** `open`/`closed` is the observed state of a retractable roof on game day, so only "retractable" is used. Rows for 2026 games not yet played contain contradictory placeholders, for example `dome` for the Melbourne Cricket Ground and Stade de France. A stadium with no completed game therefore stays UNKNOWN. One JAX00 row is named Tottenham Hotspur Stadium, so venues are keyed by name, not by `stadium_id`.
- **`pbp_prior_tendencies.build_prior_pbp_tendencies` could be reused only in part.**
  - It raises an error on 2-point tries, where `down` is NA.
  - With those tries excluded, its neutral dropback rate matches this workstream's exactly: 216 team-games in 2025 weeks 12 and later, max |diff| 0.0.
  - `defense_prior_features` works from team-stat rows with no position split, so F10 is built from PBP.
- **Coverage gaps:** there is no 2015 PBP, so early-2016 rows fall back (TEAM_VOLUME_UNKNOWN is about 6% of DEV rows). A receiver's position is the modal position in the weekly corpus, a documented simplification.
