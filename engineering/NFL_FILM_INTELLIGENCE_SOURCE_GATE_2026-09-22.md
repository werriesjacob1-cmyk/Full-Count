# NFL film-intelligence source gate and synthetic prototype

Status: **REAL-SOURCE GATE BLOCKED** as of 2026-09-22. This work did not watch, download, copy, or analyze NFL footage. It did not purchase a subscription or accept third-party competition terms.

## Source evidence

| Candidate | Access and cost | Analysis/use rights found | Historical/current coverage | Decision |
|---|---|---|---|---|
| NFL+ Premium All-22 | Paid: NFL lists $14.99/month or $99.99/season, plus tax. | NFL subscription terms limit use to personal/non-commercial purposes and prohibit archiving, republishing, reproducing, and downloading. The general terms prohibit systematic retrieval absent written consent. | Full-game, condensed, and All-22 replays; current subscription product. | **Blocked.** It is paid, and the published terms do not establish rights for an automated FULL COUNT observation pipeline. |
| NFL Films licensing | Contract required; no no-purchase license was found. | NFL states that use of NFL-controlled footage requires express written consent in a contract. | Potentially broad, subject to a negotiated license. | **Blocked.** No contract or written analysis authorization is present. |
| NFL Big Data Bowl 2025 on Kaggle | Page is public, but the data page requires sign-in and acceptance of competition rules. No rules were accepted during this work. | Dataset license is “Subject to Competition Rules”; those terms were not available as an already accepted repository entitlement. | Historical competition dataset with game/play/player/tracking files; not a current-season production feed. | **Blocked for ingestion.** Access and downstream-use conditions have not been established for this project, and it cannot cover current games. |
| nflverse play-by-play | Free public release; repository is CC BY 4.0 and reports raw PBP 1–2 hours after games and processed data daily in season. | Reuse is supported by the published CC BY 4.0 license with attribution. | Historical and current play-by-play. | **Usable only as a binding/context source.** It does not supply the complete observed formation, personnel, motion, pressure, coverage, blocking, and matchup taxonomy required here. |
| nflverse participation data | Free public release; documented as CC BY-SA 4.0 with required attribution. | Reuse is supported under CC BY-SA 4.0. | Prior seasons; documentation says 2023 onward data is supplied after postseason completion, so it is not a current-week feed. | **Usable later for partial historical validation.** It is not current and does not independently establish every requested label. |

Primary evidence:

- [NFL+ price and All-22 inclusion](https://support.nfl.com/hc/en-us/articles/35869739723028-How-much-does-NFL-cost)
- [NFL subscription terms](https://www.nfl.com/legal/subscriptions_terms)
- [NFL general terms](https://www.nfl.com/legal/terms/)
- [NFL Films licensing disclaimer](https://www.nfl.com/news/nfl-films-licensing-disclaimer)
- [NFL Big Data Bowl 2025 data page](https://www.kaggle.com/c/nfl-big-data-bowl-2025/data)
- [nflverse-data repository and automation status](https://github.com/nflverse/nflverse-data)
- [nflverse participation loader license and release timing](https://github.com/nflverse/nflreadr/blob/main/R/load_participation.R)

The exact real-source blocker is the absence of a no-purchase source that both grants the intended analysis rights and supplies current, play-bound observations for the full label set. Public viewability is not treated as analysis authorization.

## Prototype contract

`nfl/research/film_observations.py` provides source-neutral plumbing while the source gate is blocked:

- a source manifest records access time, source type, rights status, license locator, cost, historical/current coverage, blocker, and SHA-256 content digest;
- real charting or footage fails closed unless analysis rights are verified and a license URL is recorded;
- observations bind season, week, game ID, play ID, quarter, game clock, and snap timestamp;
- every formation, personnel, motion, pressure, coverage, blocking, and matchup label records value, confidence, evidence basis, provenance locator, and observation timestamp;
- unknown labels remain explicit and are never converted to asserted labels;
- duplicate game/play/time keys fail validation;
- independent annotations are joined only on exact game/play/time keys, with per-field comparable counts, exact agreement, and concrete disagreements;
- the source artifact digest is verified before annotations are accepted.

The bundled files under `nfl/research/fixtures/` are conspicuously synthetic. Team/player identities are invented, the scenario text was authored for this test, and every evidence basis says that no game footage was viewed.

Run the prototype from the repository root:

```bash
python -m nfl.research.film_observations \
  --manifest nfl/research/fixtures/film_synthetic_manifest.json \
  --source-content nfl/research/fixtures/film_synthetic_source.json \
  --primary nfl/research/fixtures/film_synthetic_primary.jsonl \
  --independent nfl/research/fixtures/film_synthetic_independent.jsonl
python -m unittest nfl.tests.test_film_observations
```

A real-source adapter must remain disabled until its exact license/terms, source artifact, source digest, allowed analysis purpose, attribution requirements, cost, and coverage are recorded and reviewed. Clearing that gate would authorize ingestion only; it would not validate label accuracy or permit selector/model promotion.
