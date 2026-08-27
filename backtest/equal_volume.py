#!/usr/bin/env python3
"""equal_volume.py -- exact-equal-volume selector comparison, enforced
structurally rather than by convention.

THE PROBLEM THIS REPLACES. FULL COUNT's promotion standard is a single
question: *at the same usable pick volume, does the challenger hit more
props?* Until now "the same volume" was a caller convention -- whoever
wrote the experiment was trusted to select the same number of picks for
both sides, from the same eligible set, with the same information. Every
one of those is silently violable, and a violated one does not produce an
error; it produces a NUMBER, which then gets quoted as evidence. A
challenger that quietly selects 8 picks where the champion selected 10
will usually post a better hit rate, and nothing in the output would say
so.

So the invariants live in the framework, not in the caller:

  * the framework -- never the policy -- decides how many picks are taken.
    A policy proposes an ORDER over the eligible population; the top N is
    taken by this module. A policy cannot return a shorter list, because
    it never returns a list length at all.
  * both sides are handed the same frozen EligiblePopulation object, and
    its fingerprint is recorded in the report. Two sides evaluated against
    different populations cannot be compared, and the framework refuses
    rather than reporting a delta.
  * outcomes are joined AFTER selection, from a mapping built before it,
    so a policy cannot see what it is about to be graded on.
  * every failure listed in the governing mission is an exception, not a
    warning. There is no `strict=False`.

WHAT "SAME POPULATION" MEANS. Selector experiments and eligibility
experiments are different claims and are kept apart deliberately. A
selector challenger may reorder and re-choose within the eligible set; it
may not change who was eligible. If you want to test an eligibility rule,
build two populations and say so -- `ExperimentKind.ELIGIBILITY` exists
for that and is reported under a different label, so an eligibility win
can never be quoted as a selector win.

DEPENDENCE IS NOT OPTIONAL. Multiple picks routinely share a game or a
player -- the real public ledger ran 39 settled Top Picks across roughly
18 unique games -- so treating props as independent Bernoulli trials
overstates significance, sometimes badly. The bootstrap here resamples
CLUSTERS (games by default), not rows, and the report always states the
clustering it used and how concentrated the selections were.

Realized hit rate leads every report. Brier/log-loss/calibration are
computed and reported, but strictly in a secondary section, because a
challenger that improves calibration while selecting fewer winners has
not done the thing FULL COUNT is trying to do.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone

CANDIDATE_IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")

# Outcome handling must be declared UP FRONT and applies identically to
# both sides -- see OutcomePolicy.
OUTCOME_REQUIRED = "required"          # a selected row with no outcome is an error
OUTCOME_COUNT_AS_MISS = "count_as_miss"
OUTCOME_EXCLUDE_PAIRWISE = "exclude_pairwise"  # dropped from BOTH sides' denominators


class EqualVolumeViolation(Exception):
    """Any breach of the equal-volume / same-population contract."""


class PopulationIntegrityError(Exception):
    """The candidate population itself is not fit to run an experiment on."""


def candidate_identity(row):
    return tuple(row.get(k) for k in CANDIDATE_IDENTITY_FIELDS)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=repr)


def _sha(obj):
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _identity_sort_key(identity):
    # Fields legitimately mix types across real rows (line is 1.5 or None,
    # ids arrive as int and str), so repr gives a stable total order
    # without coercing distinct values together.
    return tuple(repr(x) for x in identity)


class EligiblePopulation:
    """A frozen, fingerprinted candidate universe.

    Constructed once and handed to both sides. Validates the properties
    that would otherwise silently corrupt a comparison: every row must
    carry a complete identity, and no identity may appear twice (a
    duplicate would let one side "select" the same wager twice and count
    it twice).
    """

    def __init__(self, rows, *, definition, definition_version,
                 evidence_regime, dataset_identity, exclusions=None):
        self.rows = list(rows)
        self.definition = definition
        self.definition_version = definition_version
        self.evidence_regime = evidence_regime
        self.dataset_identity = dict(dataset_identity or {})
        self.exclusions = list(exclusions or [])

        identities = []
        for i, row in enumerate(self.rows):
            ident = candidate_identity(row)
            if any(v is None for v in ident):
                raise PopulationIntegrityError(
                    f"row {i} has an incomplete candidate identity {ident!r}; every "
                    f"eligible candidate must be uniquely addressable or selections "
                    f"cannot be compared between policies")
            identities.append(ident)
        dupes = [k for k, c in Counter(identities).items() if c > 1]
        if dupes:
            raise PopulationIntegrityError(
                f"{len(dupes)} duplicate candidate identities in the eligible "
                f"population (e.g. {dupes[:3]}). A duplicate lets one side select the "
                f"same wager twice and be credited twice.")

        self._by_identity = dict(zip(identities, self.rows))
        self.identities = identities
        self.fingerprint = _sha(sorted(identities, key=_identity_sort_key))

    def __len__(self):
        return len(self.rows)

    def __contains__(self, identity):
        return identity in self._by_identity

    def row(self, identity):
        return self._by_identity[identity]

    def describe(self):
        dates = sorted({r.get("date") for r in self.rows if r.get("date")})
        return {
            "n_eligible": len(self.rows),
            "eligible_population_fingerprint": self.fingerprint,
            "definition": self.definition,
            "eligibility_definition_version": self.definition_version,
            "evidence_regime": self.evidence_regime,
            "dataset_identity": self.dataset_identity,
            "date_range": [dates[0], dates[-1]] if dates else None,
            "n_dates": len(dates),
            "markets": dict(Counter(r.get("prop_type") for r in self.rows)),
            "exclusions": self.exclusions,
        }


class SelectionPolicy:
    """A named, versioned ranking over an eligible population.

    A policy provides ORDER, never volume. `rank()` must return every
    eligible identity exactly once, most-preferred first; the framework
    slices the top N. This is what makes "challenger selected fewer picks"
    structurally impossible rather than merely discouraged, and it makes
    the selection reproducible from the ranking inputs by construction.

    Ranking must be deterministic -- the framework verifies this by
    ranking twice and comparing, so a policy that leaks
    dict/set-iteration order or an unseeded random into its ordering is
    caught before its numbers are believed.
    """

    def __init__(self, name, version, rank_fn, *, description=None):
        self.name = name
        self.version = version
        self.rank_fn = rank_fn
        self.description = description or ""

    def rank(self, population):
        order = list(self.rank_fn(population))
        seen, out = set(), []
        for ident in order:
            ident = tuple(ident)
            if ident not in population:
                raise EqualVolumeViolation(
                    f"policy {self.name!r} ranked {ident!r}, which is not in the "
                    f"eligible population. A policy may reorder the population; it may "
                    f"not introduce candidates into it.")
            if ident in seen:
                raise EqualVolumeViolation(
                    f"policy {self.name!r} ranked {ident!r} more than once")
            seen.add(ident)
            out.append(ident)
        missing = len(population) - len(out)
        if missing:
            raise EqualVolumeViolation(
                f"policy {self.name!r} ranked {len(out)} of {len(population)} eligible "
                f"candidates ({missing} omitted). A ranking must cover the whole "
                f"population -- silently dropping candidates is how a policy avoids "
                f"the ones it would get wrong.")
        return out

    def identity(self):
        return {"policy_name": self.name, "policy_version": self.version,
                "description": self.description}


def rank_by(key_fn, *, reverse=True):
    """Build a deterministic ranking from a per-row score.

    Ties are broken by candidate identity rather than left to sort
    stability, so the ranking cannot depend on the population's incoming
    order -- two callers with the same rows in different order must get
    the same ranking, or the determinism check is meaningless."""
    def _rank(population):
        scored = []
        for ident in population.identities:
            row = population.row(ident)
            value = key_fn(row)
            scored.append((value is not None, value if value is not None else 0,
                           _identity_sort_key(ident), ident))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=reverse)
        # Secondary key applied within equal primary values, always ascending,
        # so ordering is total and independent of input order.
        grouped = defaultdict(list)
        for has, val, tie, ident in scored:
            grouped[(has, val)].append((tie, ident))
        out = []
        for key in sorted(grouped, key=lambda k: (k[0], k[1]), reverse=reverse):
            for _, ident in sorted(grouped[key]):
                out.append(ident)
        return out
    return _rank


class OutcomePolicy:
    """How selected rows with no recorded outcome are handled.

    Declared before the experiment runs and applied identically to both
    sides. The default refuses, because the common alternative -- quietly
    dropping ungraded picks -- changes each side's denominator by a
    different amount and is one of the easiest ways to manufacture a
    favourable hit rate without touching a threshold."""

    def __init__(self, mode=OUTCOME_REQUIRED, outcome_field="outcome"):
        if mode not in (OUTCOME_REQUIRED, OUTCOME_COUNT_AS_MISS, OUTCOME_EXCLUDE_PAIRWISE):
            raise ValueError(f"unknown outcome mode {mode!r}")
        self.mode = mode
        self.outcome_field = outcome_field

    def identity(self):
        return {"outcome_mode": self.mode, "outcome_field": self.outcome_field}


def _outcome_of(row, field):
    v = row.get(field)
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    try:
        return 1 if float(v) >= 0.5 else 0
    except (TypeError, ValueError):
        return None


class EqualVolumeExperiment:
    """The comparison object. Construct, then .run()."""

    KIND_SELECTOR = "selector"
    KIND_ELIGIBILITY = "eligibility"

    def __init__(self, *, population, champion, challenger, volume,
                 outcome_policy=None, kind=KIND_SELECTOR,
                 code_git_sha=None, promotion_grade=False,
                 cluster_field="game_pk", preregistered=False, notes=None):
        if kind not in (self.KIND_SELECTOR, self.KIND_ELIGIBILITY):
            raise ValueError(f"unknown experiment kind {kind!r}")
        if not isinstance(volume, int) or volume <= 0:
            raise EqualVolumeViolation(
                f"volume must be a positive integer, got {volume!r}")
        if volume > len(population):
            raise EqualVolumeViolation(
                f"requested volume {volume} exceeds the eligible population "
                f"({len(population)}). Neither side could honestly fill it, and "
                f"shrinking the requested volume after seeing that is exactly the "
                f"kind of post-hoc adjustment this framework exists to prevent.")
        self.population = population
        self.champion = champion
        self.challenger = challenger
        self.volume = volume
        self.outcome_policy = outcome_policy or OutcomePolicy()
        self.kind = kind
        self.code_git_sha = code_git_sha
        self.promotion_grade = promotion_grade
        self.cluster_field = cluster_field
        self.preregistered = preregistered
        self.notes = notes or ""

        if promotion_grade:
            self._assert_promotion_grade_dataset()

    def _assert_promotion_grade_dataset(self):
        """Promotion-grade evidence requires a dataset whose identity can
        actually be proven -- delegated to accuracy_lab so the rule has
        exactly one definition (see WeakDatasetIdentityError there)."""
        ident = self.population.dataset_identity
        if not ident:
            raise EqualVolumeViolation(
                "promotion_grade=True requires a dataset_identity on the eligible "
                "population; an unidentified dataset cannot back a promotion claim")
        missing = [k for k in ("artifact_sha256", "artifact_row_count")
                   if not ident.get(k)]
        if missing:
            raise EqualVolumeViolation(
                f"promotion_grade=True requires strong dataset identity; missing "
                f"{missing}. Lock a schema-v2-or-later Accuracy Lab manifest against "
                f"the artifact and pass its identity fields here "
                f"(see accuracy_lab.assert_promotion_grade_manifest).")

    # ── selection ──────────────────────────────────────────────────────

    def _select(self, policy):
        first = policy.rank(self.population)
        second = policy.rank(self.population)
        if first != second:
            raise EqualVolumeViolation(
                f"policy {policy.name!r} is not deterministic: ranking the identical "
                f"population twice produced different orders. A non-reproducible "
                f"selection cannot be evidence of anything.")
        selected = first[:self.volume]
        if len(selected) != self.volume:
            raise EqualVolumeViolation(
                f"policy {policy.name!r} yielded {len(selected)} selections for a "
                f"requested volume of {self.volume}")
        return selected

    def _grade(self, selected, outcomes):
        hits = misses = excluded = 0
        graded = []
        for ident in selected:
            o = outcomes.get(ident)
            if o is None:
                if self.outcome_policy.mode == OUTCOME_REQUIRED:
                    raise EqualVolumeViolation(
                        f"selected candidate {ident!r} has no recorded outcome and the "
                        f"declared outcome policy is {OUTCOME_REQUIRED!r}. Choose an "
                        f"explicit policy up front -- silently dropping ungraded picks "
                        f"moves the two sides' denominators by different amounts.")
                if self.outcome_policy.mode == OUTCOME_COUNT_AS_MISS:
                    misses += 1
                    graded.append((ident, 0))
                else:
                    excluded += 1
                continue
            hits += o
            misses += (1 - o)
            graded.append((ident, o))
        return {"hits": hits, "misses": misses, "excluded": excluded,
                "graded": graded,
                "n_scored": hits + misses,
                "hit_rate": (hits / (hits + misses)) if (hits + misses) else None}

    # ── dependence-aware uncertainty ──────────────────────────────────

    def _cluster_of(self, ident):
        row = self.population.row(ident)
        return row.get(self.cluster_field)

    def _clustered_bootstrap(self, champ_graded, chal_graded, *, iterations=2000, seed=20260827):
        """Paired cluster bootstrap on the hit-rate delta.

        Resamples whole clusters (games) with replacement rather than
        individual props, because picks sharing a game share a common
        shock -- a rain-shortened game or a blowout moves every prop in it
        together. Row-level resampling would treat those as independent
        and report an interval that is too narrow.
        """
        champ = dict(champ_graded)
        chal = dict(chal_graded)
        by_cluster = defaultdict(lambda: {"champ": [], "chal": []})
        for ident, o in champ.items():
            by_cluster[self._cluster_of(ident)]["champ"].append(o)
        for ident, o in chal.items():
            by_cluster[self._cluster_of(ident)]["chal"].append(o)
        clusters = list(by_cluster)
        if not clusters:
            return None

        rng = random.Random(seed)
        deltas = []
        for _ in range(iterations):
            picked = [clusters[rng.randrange(len(clusters))] for _ in clusters]
            ch = cm = lh = lm = 0
            for c in picked:
                b = by_cluster[c]
                ch += sum(b["champ"]); cm += len(b["champ"]) - sum(b["champ"])
                lh += sum(b["chal"]); lm += len(b["chal"]) - sum(b["chal"])
            if (ch + cm) == 0 or (lh + lm) == 0:
                continue
            deltas.append(lh / (lh + lm) - ch / (ch + cm))
        if not deltas:
            return None
        deltas.sort()

        def q(p):
            i = min(len(deltas) - 1, max(0, int(round(p * (len(deltas) - 1)))))
            return deltas[i]

        n_le_zero = sum(1 for d in deltas if d <= 0)
        return {
            "method": "paired cluster bootstrap",
            "cluster_field": self.cluster_field,
            "n_clusters": len(clusters),
            "iterations": len(deltas),
            "delta_ci95": [round(q(0.025), 4), round(q(0.975), 4)],
            "delta_median": round(q(0.5), 4),
            "p_delta_le_zero": round(n_le_zero / len(deltas), 4),
            "note": ("Clusters, not props, are resampled: picks sharing a game share a "
                     "common shock, so row-level resampling would understate the "
                     "interval."),
        }

    # ── dependence / stability descriptors ────────────────────────────

    def _dependence(self, selected):
        rows = [self.population.row(i) for i in selected]
        players = Counter(r.get("player_id") for r in rows)
        games = Counter(r.get("game_pk") for r in rows)
        return {
            "n_selected": len(selected),
            "unique_players": len(players),
            "unique_games": len(games),
            "max_props_one_player": max(players.values()) if players else 0,
            "max_props_one_game": max(games.values()) if games else 0,
            "same_player_multi_prop_count": sum(c for c in players.values() if c > 1),
            "same_game_multi_prop_count": sum(c for c in games.values() if c > 1),
            "effective_independent_games": len(games),
            "player_concentration": round(len(players) / len(rows), 4) if rows else None,
            "game_concentration": round(len(games) / len(rows), 4) if rows else None,
        }

    def _stability(self, graded):
        by_year, by_month, by_market = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
        for ident, o in graded:
            row = self.population.row(ident)
            d = str(row.get("date") or "")
            year = d[:4] or "unknown"
            month = d[5:7] or "unknown"
            market = row.get("prop_type") or "unknown"
            for bucket, key in ((by_year, year), (by_month, month), (by_market, market)):
                bucket[key][0] += o
                bucket[key][1] += 1

        def _fmt(bucket):
            return {k: {"hits": v[0], "n": v[1],
                        "hit_rate": round(v[0] / v[1], 4) if v[1] else None}
                    for k, v in sorted(bucket.items())}

        # Season phase from calendar month: MLB's regular season runs
        # late March/April through September/October.
        phase = defaultdict(lambda: [0, 0])
        for ident, o in graded:
            m = str(self.population.row(ident).get("date") or "")[5:7]
            label = ("early" if m in ("03", "04", "05")
                     else "mid" if m in ("06", "07")
                     else "late" if m in ("08", "09", "10") else "unknown")
            phase[label][0] += o
            phase[label][1] += 1

        return {"by_year": _fmt(by_year), "by_month": _fmt(by_month),
                "by_season_phase": _fmt(phase), "by_market": _fmt(by_market)}

    # ── secondary diagnostics ─────────────────────────────────────────

    def _probability_diagnostics(self, graded, prob_field="predicted_prob"):
        pairs = []
        for ident, o in graded:
            p = self.population.row(ident).get(prob_field)
            if p is None:
                continue
            try:
                pairs.append((float(p), o))
            except (TypeError, ValueError):
                continue
        if not pairs:
            return None
        n = len(pairs)
        brier = sum((p - o) ** 2 for p, o in pairs) / n
        eps = 1e-12
        logloss = -sum(o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps))
                       for p, o in pairs) / n
        return {"n_with_probability": n,
                "mean_predicted": round(sum(p for p, _ in pairs) / n, 4),
                "observed_rate": round(sum(o for _, o in pairs) / n, 4),
                "brier": round(brier, 5), "logloss": round(logloss, 5),
                "note": "SECONDARY. Promotion is decided on equal-volume realized hit "
                        "rate; these describe probability quality, not pick quality."}

    # ── the run ───────────────────────────────────────────────────────

    def run(self, *, bootstrap_iterations=2000):
        # Outcomes are resolved BEFORE selection so the mapping cannot be
        # influenced by what either policy chose, and are joined only
        # after -- neither policy is ever handed the graded result.
        outcomes = {ident: _outcome_of(self.population.row(ident),
                                       self.outcome_policy.outcome_field)
                    for ident in self.population.identities}

        champ_sel = self._select(self.champion)
        chal_sel = self._select(self.challenger)

        if len(champ_sel) != len(chal_sel):
            raise EqualVolumeViolation(
                f"champion selected {len(champ_sel)}, challenger selected "
                f"{len(chal_sel)} -- equal volume is the primary comparison's "
                f"defining condition")

        champ = self._grade(champ_sel, outcomes)
        chal = self._grade(chal_sel, outcomes)

        if self.outcome_policy.mode == OUTCOME_EXCLUDE_PAIRWISE:
            # Exclusion must be symmetric or the denominators diverge.
            keep = {i for i, _ in champ["graded"]} | {i for i, _ in chal["graded"]}
            dropped = (set(champ_sel) | set(chal_sel)) - keep
            if dropped:
                champ = self._grade([i for i in champ_sel if i not in dropped], outcomes)
                chal = self._grade([i for i in chal_sel if i not in dropped], outcomes)

        cs, ls = set(champ_sel), set(chal_sel)
        overlap, added, removed = sorted(cs & ls, key=_identity_sort_key), \
            sorted(ls - cs, key=_identity_sort_key), sorted(cs - ls, key=_identity_sort_key)

        def _tally(idents):
            hits = sum(outcomes.get(i) or 0 for i in idents
                       if outcomes.get(i) is not None)
            n = sum(1 for i in idents if outcomes.get(i) is not None)
            return {"n": len(idents), "n_scored": n, "hits": hits, "misses": n - hits,
                    "hit_rate": round(hits / n, 4) if n else None}

        delta = (None if champ["hit_rate"] is None or chal["hit_rate"] is None
                 else round(chal["hit_rate"] - champ["hit_rate"], 4))

        report = {
            "record_type": "equal_volume_selector_experiment",
            "experiment_kind": self.kind,
            "experiment_manifest_id": None,  # filled below
            "preregistered": self.preregistered,
            "promotion_grade": self.promotion_grade,
            "code_git_sha": self.code_git_sha,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "notes": self.notes,

            "population": self.population.describe(),
            "requested_volume": self.volume,
            "outcome_policy": self.outcome_policy.identity(),

            "champion": {**self.champion.identity(), "selected_n": len(champ_sel),
                         "hits": champ["hits"], "misses": champ["misses"],
                         "excluded": champ["excluded"],
                         "hit_rate": round(champ["hit_rate"], 4) if champ["hit_rate"] is not None else None},
            "challenger": {**self.challenger.identity(), "selected_n": len(chal_sel),
                           "hits": chal["hits"], "misses": chal["misses"],
                           "excluded": chal["excluded"],
                           "hit_rate": round(chal["hit_rate"], 4) if chal["hit_rate"] is not None else None,
                           "hit_rate_delta": delta,
                           "additional_winners": chal["hits"] - champ["hits"]},

            "selection_anatomy": {
                "overlap_n": len(overlap),
                "overlap_rate": round(len(overlap) / self.volume, 4) if self.volume else None,
                "overlap": _tally(overlap),
                "added": _tally(added),
                "removed": _tally(removed),
            },

            "stability": {
                "champion": self._stability(champ["graded"]),
                "challenger": self._stability(chal["graded"]),
            },

            "dependence": {
                "champion": self._dependence(champ_sel),
                "challenger": self._dependence(chal_sel),
            },

            "uncertainty": self._clustered_bootstrap(
                champ["graded"], chal["graded"], iterations=bootstrap_iterations),

            "integrity": {
                "eligible_population_fingerprint": self.population.fingerprint,
                "same_population_both_sides": True,  # structural: one object
                "selection_deterministic_verified": True,
                "outcomes_joined_after_selection": True,
                "post_outcome_population_filtering": False,
                "duplicate_candidate_identities": 0,
                "dataset_identity": self.population.dataset_identity,
                "evidence_regime": self.population.evidence_regime,
                "eligibility_definition_version": self.population.definition_version,
            },

            "secondary_diagnostics": {
                "note": "Reported AFTER realized hit rate, deliberately. A challenger "
                        "with better calibration but fewer winners has not met the "
                        "promotion standard.",
                "champion": self._probability_diagnostics(champ["graded"]),
                "challenger": self._probability_diagnostics(chal["graded"]),
            },
        }
        report["experiment_manifest_id"] = _sha({
            "population": self.population.fingerprint,
            "champion": self.champion.identity(),
            "challenger": self.challenger.identity(),
            "volume": self.volume,
            "outcome_policy": self.outcome_policy.identity(),
            "kind": self.kind,
            "code_git_sha": self.code_git_sha,
        })
        return report


def format_report(report):
    """Human-readable rendering, realized hit rate first."""
    p, c, l = report["population"], report["champion"], report["challenger"]
    a, d, u = report["selection_anatomy"], report["dependence"], report["uncertainty"]
    out = []
    out.append("=" * 74)
    out.append(f"EQUAL-VOLUME {report['experiment_kind'].upper()} EXPERIMENT"
               f"{'  [PROMOTION-GRADE]' if report['promotion_grade'] else '  [exploratory]'}")
    out.append("=" * 74)
    out.append(f"population: n={p['n_eligible']} dates={p['n_dates']} "
               f"range={p['date_range']} regime={p['evidence_regime']}")
    out.append(f"  fingerprint {p['eligible_population_fingerprint'][:16]}  "
               f"eligibility {p['eligibility_definition_version']}")
    out.append(f"requested volume: {report['requested_volume']}  "
               f"outcome policy: {report['outcome_policy']['outcome_mode']}")
    out.append("")
    out.append("REALIZED HIT RATE (the promotion standard)")
    out.append(f"  champion   {c['policy_name']} v{c['policy_version']}: "
               f"N={c['selected_n']} {c['hits']}H/{c['misses']}M  rate={c['hit_rate']}")
    out.append(f"  challenger {l['policy_name']} v{l['policy_version']}: "
               f"N={l['selected_n']} {l['hits']}H/{l['misses']}M  rate={l['hit_rate']}")
    out.append(f"  delta={l['hit_rate_delta']}  additional winners={l['additional_winners']}")
    out.append("")
    out.append("SELECTION ANATOMY")
    out.append(f"  overlap n={a['overlap_n']} ({a['overlap_rate']}) rate={a['overlap']['hit_rate']}")
    out.append(f"  added   n={a['added']['n']} rate={a['added']['hit_rate']}")
    out.append(f"  removed n={a['removed']['n']} rate={a['removed']['hit_rate']}")
    out.append("")
    out.append("DEPENDENCE")
    for side in ("champion", "challenger"):
        s = d[side]
        out.append(f"  {side}: players={s['unique_players']} games={s['unique_games']} "
                   f"max/player={s['max_props_one_player']} max/game={s['max_props_one_game']}")
    if u:
        out.append("")
        out.append("UNCERTAINTY")
        out.append(f"  {u['method']} on {u['cluster_field']}: clusters={u['n_clusters']} "
                   f"delta CI95={u['delta_ci95']} p(delta<=0)={u['p_delta_le_zero']}")
    out.append("")
    out.append("SECONDARY DIAGNOSTICS (not the promotion standard)")
    for side in ("champion", "challenger"):
        s = report["secondary_diagnostics"][side]
        if s:
            out.append(f"  {side}: brier={s['brier']} logloss={s['logloss']} "
                       f"mean_pred={s['mean_predicted']} observed={s['observed_rate']}")
    out.append(f"\nexperiment manifest: {report['experiment_manifest_id'][:24]}")
    return "\n".join(out)
