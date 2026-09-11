# Coaching identity: what is now solved, and what genuinely is not

## The correction this document exists to record

NFL-01's first pass reported that offensive coordinator, defensive coordinator
and play-caller identity "do not exist in free structured data." That was wrong,
and the error was one of overreach rather than observation: `nfldata/games.csv`
was checked, found to carry only `away_coach`/`home_coach`, and the absence in
that one file was generalised into an absence everywhere. One file is not a
category.

## SOLVED: coordinator identity, free, licensed, and point-in-time

Wikipedia maintains `Template:<Team> staff` for all 32 clubs, listing the full
staff by role.

The FIRST per-page measurement on 2026-09-11 resolved only 30/32 because the
32-request burst was rate-limited. That result is retained as evidence of why
per-page live capture is the wrong acquisition pattern.

The corrected BATCHED live measurement on 2026-09-11 uses one MediaWiki query:

| | resolved |
|---|---|
| templates fetched | 32 / 32 |
| head coach | 32 / 32 |
| offensive coordinator | 32 / 32 |
| defensive coordinator | 31 / 32 |

The one missing defensive-coordinator role is Tampa Bay and is not a transport
gap: the staff structure has no separate DC. That absence must remain a real
coaching-state observation rather than being imputed.

**It is also point-in-time reconstructable**, which is the hardest of the
inventory's fifteen fields and the one most intelligence sources fail. The
MediaWiki API serves any page as of a timestamp. Verified against Cincinnati:

```
asked as of 2021-09-01 -> revision 2021-08-23  OC Brian Callahan  DC Lou Anarumo
asked as of 2023-09-01 -> revision 2023-07-22  OC Brian Callahan  DC Lou Anarumo
asked as of 2024-09-01 -> revision 2024-07-16  OC Dan Pitcher     DC Lou Anarumo
asked as of 2026-09-11 -> revision 2026-07-27  OC Dan Pitcher     DC Al Golden
```

That is real coaching history — Callahan leaving for a head-coaching job,
Anarumo replaced by Golden — recovered from revisions rather than from a
snapshot.

### The caveat, which is not small

**A revision timestamp is when Wikipedia was edited, not when the appointment
happened.** Wikipedia lags reality, usually by hours, sometimes much longer. So
this reconstructs *what was publicly recorded at time T*, not *what was true at
time T*.

For a pregame cutoff that is arguably the correct semantic — a model should only
know what was knowable — but it must never be described as an appointment date.
`nfl/archive/sources/coaching_staff.py` therefore archives `asked_as_of` and the
revision's own timestamp as separate fields. A capture asking for today can
legitimately return a six-week-old revision, so freshness is recorded, never
assumed.

### Two sources disagree, and the disagreement is evidence

Cross-validating Wikipedia's head coach against `nfldata`'s for 2026:
**22 agree, 5 differ.** The differences are instructive, and none should be
silently resolved:

- **Las Vegas** — `nfldata` "Klint **Kubliak**" vs Wikipedia "Klint **Kubiak**".
  A spelling error in one of them. Any name-keyed join across these two sources
  fails silently on this row, which is an argument for joining on team and
  season rather than on a person's name.
- **Buffalo** (`nfldata` Sean McDermott / Wikipedia Joe Brady) and **Arizona**
  (`nfldata` Jonathan Gannon / Wikipedia Mike LaFleur). Reading the raw wikitext
  settles it: the templates genuinely say `Head coach – Joe Brady` and
  `Head coach – Mike LaFleur`. `nfldata`'s coach column appears not to track
  in-season or late-offseason changes. **Wikipedia was right and the structured
  dataset was stale.**
- The remaining differences were fetch failures on the Wikipedia side, not
  disagreements.

This is exactly the CONTRADICTION / INFORMATION-RISK state the architecture
requires be preserved rather than collapsed to whichever source was read last.

## NOT SOLVED: who actually calls the plays

Coordinator identity is a **title**. Play-calling is a **duty**, and the two come
apart constantly: head coaches who call their own offense, coordinators who hold
the title without the headset, and mid-season handoffs that are announced in a
press conference and recorded in no database.

**No public machine-readable source for play-caller identity was established.**
Not in nflverse, not in `nfldata`, not in Wikipedia's structured fields. It is
stated in prose and in press conferences.

### What the data does support, honestly

Four signals, none of them ground truth, all subject to RECORD → MEASURE →
PROMOTE. None is implemented as a scorer, and none should be until measured.

1. **Structural absence — the strongest and cheapest.** Tampa Bay's template
   lists **no defensive coordinator**. That is not a parse failure: Todd Bowles
   is a defensive head coach who calls his own defense, so the role is not
   separately filled. A missing coordinator on one side of the ball, next to a
   head coach whose own background is on that side, is a genuine derivable
   signal — and it falls out of the source already captured.
2. **Head-coach background.** Whether the HC came up offensive or defensive
   shifts the prior on who calls that phase. Derivable from the same templates
   plus career history; weakly informative alone.
3. **Declared intent from media.** The NFL Intelligence Engine's proper job. Coach
   speech is not ground truth — that is why speaker/staff reliability profiles
   are designed rather than assumed — but "X will call plays" is a first-class
   claim type with a speaker, a timestamp and a quotable span.
4. **Statistical change detection — a research hypothesis, not a method.** A
   play-calling handover should perturb pass rate over expectation, personnel
   grouping, motion rate and tempo. `xpass`/`pass_oe` are populated in the 2026
   play-by-play (verified). Whether a changepoint is detectable with ~17 games a
   season, against normal week-to-week variance and opponent effects, is
   **unknown and probably underpowered**. It must be validated against known
   historical handovers before it is trusted to flag an unknown one.

### The honest position

Full Count can know, for every team and reconstructable historically, **who holds
each coaching title**. It cannot yet know **who calls the plays**, and no
free source confers that. The gap is narrowed — not closed — by structural
inference plus recorded coach statements, and closing it further is a
measurement problem, not an acquisition problem.

Claiming otherwise would be the same overreach this document was written to
correct.

## Maintenance reality

32 teams. Roughly 96 role assignments a season for HC/OC/DC, plus in-season
changes. Current-state capture is automated as ONE batched MediaWiki request;
point-in-time historical reconstruction remains per-page because MediaWiki does
not support the needed revision parameters across multiple titles. The residual
human judgement is confined to play-caller attribution and to adjudicating
source contradictions like Buffalo and Arizona above — neither of which is a
treadmill, and both of which are recorded rather than resolved.
