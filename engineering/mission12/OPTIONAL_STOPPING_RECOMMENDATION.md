# Mission 1.2 §6A — optional stopping: recommendation for Jacob

**Nothing here is implemented. This is a recommendation requiring your decision.**

## The gap

Protocol §13 sets a **floor** (≥30 primary slate dates AND ≥100 decided
selections per arm) and permits monitoring. It sets **no ceiling, no
alpha-spending rule and no preregistered analysis schedule**, while §12 requires
the CI at *every* reporting checkpoint.

So after date 30, the interval can be re-read on every subsequent date and
promotion proposed at the first look where the lower bound clears 0. The floor
stops a hot early streak; it does nothing about **repeated looks after the
floor**, which is the real multiplicity risk in a season-long forward test. At a
nominal 95% interval, ~40 sequential looks turn a true null into a >50% chance
of at least one favourable crossing.

`prospective_scoreboard.build_report()` is callable at any moment via
`python3 -m backtest.prospective_lifecycle report` and **records nothing about
how many times it has been run**.

## Recommendation: a separately locked ANALYSIS CHARTER, not a V1 amendment

Amending the frozen protocol mid-experiment is itself a degree of freedom. The
cleaner instrument is a **separate, separately-hashed Analysis Charter v1**,
locked before the first countable receipt, that governs *when the interval may
be read for a promotion decision*. The experiment protocol stays frozen and
untouched; the charter constrains only the decision procedure.

### Proposed charter (my recommendation — your call)

1. **One interim look and one final look.** The interim look occurs the first
   time the floor is met (≥30 dates AND ≥100 decided per arm). The final look
   occurs at a preregistered stopping point — I suggest the earlier of 90
   primary slate dates or the end of the regular season.

2. **O'Brien–Fleming-style alpha split**, so the interim look is conservative
   and the final look retains almost the full nominal level:
   - interim: two-sided α = 0.005 (99.5% interval)
   - final: two-sided α = 0.048 (95.2% interval)
   These are the standard two-look OBF boundaries and keep the family-wise rate
   at 0.05. The date-cluster bootstrap already produces percentile intervals, so
   this is a change of percentile only — no new machinery.

3. **Monitoring vs deciding are different acts.** Operational monitoring (are
   receipts being sealed, are dates dropping, is the funnel sane) may continue
   freely and is *not* a look. A **look** is any computation of the
   champion-vs-PA-v1 interval that is reported to or acted on by a
   decision-maker. Only looks are counted.

4. **Mechanical enforcement.** `build_report()` gains a `--look` flag. Without
   it, the report is produced with the arm comparison and interval
   **suppressed** (funnel, volumes, integrity states, version strata still
   shown). With it, the look is appended to the ledger as an immutable
   `analysis_look` event carrying the timestamp, the head SHA, the counts and
   the interval — so the number of looks is *evidence*, not a memory. A third
   look is refused unless the charter is re-locked.

5. **No re-locking after seeing a result.** The charter may be amended only
   before the interim look, or after the experiment concludes for a *future*
   experiment.

### Why not the alternatives

- **Do nothing** — leaves an uncontrolled multiplicity hole that a competent
  critic will find immediately, and it is the one hole that cannot be patched
  retrospectively.
- **Amend protocol V1** — mixes the experiment definition with the decision
  procedure and sets a precedent for mid-flight protocol edits.
- **Bonferroni over N looks** — requires knowing N in advance and is needlessly
  conservative at the final look.

### What I need from you

1. Approve or modify the two-look schedule and the stopping point.
2. Approve the α split (or state a preferred spending function).
3. Confirm the monitoring/look distinction in point 3 matches your intent.

Until this is settled, the honest reporting posture is unchanged:
**INCONCLUSIVE / NOT YET PROMOTABLE**, which is what the scoreboard already
returns below the floor.
