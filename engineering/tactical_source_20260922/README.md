# Real tactical source prototype — 2026-09-22

Owner: Codex; base `d02b91e374925d2e6c026d5ccec29abb9df00ed3`.
Handoff supplement to shared ENGINEERING_HANDOFF.md (shared file deliberately not
edited during Claude ownership). Status: **PARTIAL, descriptive research only**.

This is genuine third-party play charting, not film viewing. No footage was
downloaded or independently annotated. No predictive gain, live feature, model
promotion, or current-week pick is claimed.

## Rights, sources and coverage

[nflreadr participation documentation](https://nflreadr.nflverse.com/reference/load_participation.html)
licenses its 2023+ FTN contribution under
[CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), requiring attribution
to **FTN Data via nflverse**. This evidence's charting excerpts and derived charting
data are offered under that same license; changes are joining to PBP, filtering,
and counting. These rights do not convey rights to underlying game video.

[FTN charting documentation](https://nflreadr.nflverse.com/reference/load_ftn_charting.html)
provides a separate current-season subset with the same attribution/license.
[Official availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html)
distinguishes postseason-only participation from in-season FTN charting.

Actual download and join evidence is in `evidence.json`, including exact SHA256,
capture time, source URL, bytes, sample game/play/receiver identities, exclusions,
and conditional denominators. Both sources join on exact game/play, never names.

| Source | Raw rows / games | Bound legal targeted passes | Useful field availability |
|---|---:|---:|---|
| 2024 participation | 45,919 / 285 | 17,848 | route 17,611 known / 237 unknown; coverage/man-zone 17,827 / 21; pressure and personnel present in all bound rows |
| 2026 FTN charting | 5,174 / 31 | 1,848 | motion, play-action, RPO, thrown-read, catchable/contested and rush/blitz counts present in all bound rows |

Present is not independently accurate. Targeted-pass filtering removes kick/run
rows whose charted zeros would distort passing features. 2,159 historical and 247
current pass rows lack a usable target GSIS ID; these remain excluded rather than
inventing player bindings. Source disagreements/annotation error still require
validation. Personnel labels are preserved as supplied; no assertion about
position accuracy is made. Current FTN lacks coverage and route fields; it cannot
pretend to refresh historical coverage/route tendencies.

## Interface and real consumer

`nfl.research.tactical_source_adapter.capture` hashes raw bytes;
`bind_pass_targets` joins PBP, rejects duplicate keys and team disagreement, and
excludes same-day/future games, penalties/non-pass plays and unknown receivers.
`summarize` consumes these rows into observed field denominators and
receiver/opponent/man-zone catch-count cells (6,169 observed historical cells).
This is a real descriptive consumer, **not yet a predictive consumer**. It does
not infer all-route target share from targets or fit a probability from sparse
cells. Capture timestamps are the conservative availability boundary for both
charting and PBP. No historical backtest may move that boundary to game date or
upstream `date_pulled`; revision timing is not proven by those dates.

Claude's receiving interface can take the resulting game/play/GSIS observations,
cutoff, source hashes and explicit unknowns; integration requires a separate
reviewed change. No shared B0, receiving module or PR #170 file was changed.

## Remaining film blockers and evaluation

[NFL confirms Coaches Film requires NFL+ Premium](https://support.nfl.com/hc/en-us/articles/35869780989332-How-do-I-watch-Coaches-Film).
[Subscription terms](https://www.nfl.com/legal/subscriptions_terms) restrict use to
personal, non-commercial use. No project entitlement or permission for automated
extraction/commercial model use was supplied. A subscription alone is not evidence
of those rights. Acquisition/processing remains BLOCKED pending a suitable rights
grant and accessible footage; no purchase or terms acceptance occurred. Exact
commercial analysis license price is unquoted, so no invented price is stated.

Routes, coverage and pressure are PARTIAL historical charting. Current motion,
RPO, read-thrown and contested/catchable signals are CAPTURED/BOUND. Disguise,
post-snap rotations, individual assignments, all-route participation, camera
visibility and independent real-film annotation remain BLOCKED/UNKNOWN.

Next evaluation: predeclare a future-game player-clustered paired B0/challenger
experiment using a frozen pregame snapshot; train/smooth only on earlier eligible
data. Assess calibration/log loss and actual-price return at matched eligible
volume, clustered by game/player. Compare current FTN context separately from
stale coverage priors and prevent same-target outcome fields from leaking into
that target's prediction. Do not tune to these descriptive counts. No measurable
incremental value is established yet.

Validation: 10 deterministic unit tests, including backdated capture, duplicate
keys, same-day games, no-play, missing identity, team mismatch and partial data.
Full exact-head CI and independent review are required before merge consideration.

Alligator

