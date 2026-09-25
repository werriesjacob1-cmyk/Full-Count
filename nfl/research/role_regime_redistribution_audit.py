#!/usr/bin/env python3
"""Scientific-integrity audit of draft PR #147
(`role_regime_redistribution.py`, branch
`claude/nfl-role-redistribution-experiment-20260919`).

This module does NOT re-derive PR #147's experiment. It reuses PR #143's
(`role_intelligence_features.py`, `role_intelligence_baselines.py`) and
PR #142's (`coach_regime_registry.py`) and PR #147's own
(`role_regime_redistribution.py`) functions unchanged, and adds exactly two
things those modules do not provide: (1) a frozen-bytes reproducibility
harness for the WR/RB teammate-absence event build, and (2) an exact
PAIRED-population evaluator that scores every baseline and the challenger on
the identical (event, candidate) row set, replacing PR #147's own
independently-populated-per-predictor comparison.

## Finding 1 -- root cause of the 668-vs-667 event-count discrepancy

PR #147's own disclosure (`engineering/ENGINEERING_HANDOFF.md`, "2026-09-19
-- NFL HC-regime x redistribution-baseline join...") reports a first run
producing 668 events (324 WR_ABSENCE/344 RB_ABSENCE) and an immediate rerun
producing 667 (323/344), plus that PR #143's pinned `players.csv` digest had
already drifted from the live asset by the time of that run (pinned
7,259,734 bytes / `801d5fec...`, live 7,291,736 bytes / `12c126bb...`).

This audit traced `players.csv`'s actual code path
(`role_intelligence_data_prep.parse_players_crosswalk_csv`/
`fetch_snap_count_rows`) and found it is used ONLY as a `pfr_id -> gsis_id`
crosswalk for `snap_counts_<season>.csv` rows, which feed `offense_snap_share`
only. Event construction
(`role_intelligence_features.build_teammate_absence_trigger_events`) reads
only `weekly_rows` (from `stats_player_week_<season>.csv`, already keyed on
stable `player_id`/gsis) and `injury_rows` (from `injuries_<season>.csv`,
also gsis-keyed) -- `players.csv` never enters this path, and `target_share`/
`carry_share` (`compute_dimension_shares`) are computed from
`targets`/`team_targets`/`carries`/`team_carries` alone, also independent of
`players.csv`. **`players.csv` drift is therefore ruled out as a cause of the
668-vs-667 discrepancy by direct code trace, independent of the digest
findings below.**

This audit then re-ran the REAL 2012-2025 event build from FROZEN local
bytes (downloaded once, saved to disk, never re-fetched -- see
`build_usage_rows_from_frozen_sources`/`frozen_injury_source`) TWICE, in two
separate Python process invocations with the default (unset) `PYTHONHASHSEED`
this repo's CI and this environment both run under. **Both counts recurred
from byte-identical input**: repeated runs alternate between 668 and 667
events with no source re-fetch at all (12 consecutive runs of
`frozen_repro.py`/`frozen_repro_dump.py` in the audit branch's PR body/Issue
#91 status produced 668 five times and 667 seven times). Diffing the sorted
event-key sets between a 668-run and a 667-run isolates the EXACT flipping
event: `("WR_ABSENCE", 2012, 2, "GB", "00-0024267")` is present in every
668-count run and absent in every 667-count run observed.

**Root cause, localized to exact code**:
`role_intelligence_features._top_usage_player_per_team_week` (module-level,
not touched by this audit) ranks each team-week's top-usage WR/RB via
`max(candidates, key=lambda pid: running_mean[pid])`, where `candidates` is
built by iterating `roster_by_team[team]` -- a plain `set`, not a list or an
(insertion-ordered) dict. When two or more players tie EXACTLY on
`running_mean[pid]` (very plausible early in a team's own history window,
e.g. week 2 of a season where every candidate has exactly one prior game),
`max()` deterministically returns the FIRST element of `candidates`'s
iteration order -- and a Python `set`'s string-key iteration order depends
on hash values, which are randomized per-process by default
(`PYTHONHASHSEED` unset, this repo's and this environment's default). The
tie-break winner therefore differs from process to process even given
byte-identical inputs, and if the winning player is the one with an `OUT`/
`DOUBTFUL` injury status that week, an event appears; if the losing player
wins instead, it does not. Pinning `PYTHONHASHSEED` to any fixed value (0 and
42 both tested) makes the result perfectly stable across repeated runs
under that seed, confirming hash-randomized set iteration -- not any source
byte drift, network timing, or race -- as the causal mechanism. This is a
REAL BUG in `role_intelligence_features.py` (order-dependent tie-breaking
over an unordered set), not a source-provenance issue; per this audit's
scope this file is NOT patched here, only documented precisely, and no new
digest is pinned anywhere.

## Finding 2 -- `players.csv` digest, independently re-verified

This audit independently re-fetched `players.csv` live (2026-09-19) and
found it BYTE-IDENTICAL to PR #147's own disclosed live-fetch digest --
`PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST` below -- and NOT the digest PR
#143 pinned in `role_intelligence_source_digests.PLAYERS_CROSSWALK_SOURCE`.
In other words: the asset has not drifted again since PR #147's run earlier
today, but PR #143's pin remains stale relative to the live asset. This
audit does NOT re-pin it anywhere; a human (Jacob) should decide whether
`role_intelligence_source_digests.PLAYERS_CROSSWALK_SOURCE` should be
updated. See `compare_players_crosswalk_digest`.

## Finding 3 -- the paired-population defect (PR #147's disclosed n=449 vs
n=441 for `target_share`, held-out 2022-2025)

`role_regime_redistribution.evaluate_predictors` (and
`role_intelligence_baselines.evaluate_baselines`, which uses the identical
pattern) score each predictor independently: for every event, it calls
`predictor(event, teammates, history, dimension)`, and for every player_id
key the returned dict happens to contain, it looks up a realized share and
appends an error IF one exists. A (event, candidate) row's presence in a
given predictor's error population therefore depends only on whether THAT
predictor happened to emit a numeric prediction for that player -- not on
any shared, predeclared eligibility rule -- so different predictors end up
scored on silently different populations while being reported side by side
as if directly comparable.

Concretely: `NO_ADJUSTMENT`/`PROPORTIONAL_TEAMMATE_REDISTRIBUTION` include a
teammate ONLY if he has a numeric last-5 strictly-prior share
(`_teammate_prior_share_last5_mean` returns non-`None`) -- a teammate with
zero recent role-state history (e.g. a rookie, a practice-squad call-up, a
long-injured returner) is silently OMITTED from their prediction dicts
entirely. `DEPTH_CHART_NEXT_MAN`/`RECENT_USAGE_NEXT_MAN` start from
`NO_ADJUSTMENT`'s dict and then unconditionally add one more entry (the
next-man-up candidate), even if that specific candidate lacked a prior share
(`predictions.get(next_man_id, 0.0) + removed_prior`). The challenger
(`predict_committee_model`) goes further still: after copying
`NO_ADJUSTMENT`'s predictions, it ALWAYS adds a probability-weighted share
for EVERY teammate in the event's candidate list, defaulting a missing prior
to `has_prior=0, prior_value=0.0` in its own feature vector rather than
omitting the candidate -- so it is structurally guaranteed to have a
super-set of `NO_ADJUSTMENT`'s scored population whenever ANY candidate
lacks recent history. This is exactly why PR #147 observed the challenger's
own `n` (449 target_share / 261 carry_share) exceeding the baselines' `n`
(441 / 250) on the identical held-out event set -- disclosed by PR #147
itself, not a new discovery of this audit, but never corrected there.

`compute_paired_evaluation` below fixes this by scoring every predictor
passed to it on the INTERSECTION of rows for which every one of them
produces a real numeric prediction AND a realized target-game share exists
-- one shared denominator, used for every predictor's MAE, `n`, and
season-by-season breakdown reported together.

## Scope of the frozen-bytes reproduction (disclosed, not hidden)

Event construction and `target_share`/`carry_share` computation depend only
on `stats_player_week_<season>.csv` (weekly targets/carries) and
`injuries_<season>.csv` (pregame OUT/DOUBTFUL). `candidate_depth_team` (used
by `DEPTH_CHART_NEXT_MAN` and as a committee feature) additionally needs
`depth_charts_<season>.csv`. `snap_counts_<season>.csv` and
`play_by_play_<season>.csv.gz` feed ONLY `offense_snap_share`,
`red_zone_opportunity_share`, `third_down_snap_share`, and
`two_minute_snap_share` -- dimensions this audit's task scope (task 2) does
not evaluate at all, and the paired-evaluation instructions explicitly keep
`target_share`/`carry_share` separate from every other dimension. This
audit's frozen-bytes download therefore intentionally excludes
`snap_counts`/PBP (many times larger, ~19MB/season gzipped PBP alone across
14 seasons) and passes empty snap/PBP inputs into
`build_player_game_usage_rows` -- a disclosed, bounded scope, not a silent
omission. `players.csv` is fetched and digest-compared (Finding 2) but is
never used to build usage rows in this module, per Finding 1's trace.
"""
from __future__ import annotations

import hashlib
import random
import urllib.request
from collections import Counter, defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Iterable

from nfl.research.role_intelligence_baselines import (
    BASELINE_PREDICTORS,
    DIMENSION_RELEVANT_EVENT_TYPES,
)
from nfl.research.role_intelligence_data_prep import (
    build_player_game_usage_rows,
    parse_depth_chart_csv,
    parse_weekly_stats_csv,
    fetch_injury_rows,
)
from nfl.research.role_intelligence_features import (
    build_player_dimension_history,
    build_replacement_candidate_rows,
    build_role_state_rows,
    build_teammate_absence_trigger_events,
    compute_dimension_shares,
)
from nfl.research.role_intelligence_source_digests import PLAYERS_CROSSWALK_SOURCE
from nfl.research.role_regime_redistribution import (
    CHALLENGER_HELD_OUT_SEASONS,
    CHALLENGER_NAME,
    CHALLENGER_TRAIN_SEASONS,
    MIN_EVENTS_FOR_NAMED_REGIME,
    attach_hc_regime_to_events,
    build_hc_registry,
    predict_committee_model,
    train_committee_model,
)

# --------------------------------------------------------------------------
# Finding 2: players.csv three-way digest comparison (diagnosis only, never
# re-pins anything)
# --------------------------------------------------------------------------

# PR #143's pin, as recorded today in `role_intelligence_source_digests.py`.
PR143_PINNED_PLAYERS_CROSSWALK_DIGEST = {
    "bytes": PLAYERS_CROSSWALK_SOURCE["bytes"],
    "sha256": PLAYERS_CROSSWALK_SOURCE["sha256"],
}

# Recorded verbatim from PR #147's own disclosed run
# (`engineering/ENGINEERING_HANDOFF.md`, "2026-09-19 -- NFL HC-regime x
# redistribution-baseline join, first hierarchical challenger", branch
# `claude/nfl-role-redistribution-experiment-20260919`). A citation of
# already-disclosed evidence, not re-derived here.
PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST = {
    "bytes": 7291736,
    "sha256": "12c126bb35ddf015a929a8db7c019fd32693aa2b47bae7fad6fcc8132a3b7719",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_players_crosswalk_digest(fresh_bytes: bytes) -> dict[str, Any]:
    """Three-way digest comparison for `players.csv`: PR #143's pin, PR
    #147's own disclosed live-fetch digest, and `fresh_bytes` (an
    independently fetched copy, e.g. from a fresh live download run right
    now). Returns a structured report. Never re-pins anything -- diagnosis
    only; a human decides whether to update the pin.
    """
    fresh = {"bytes": len(fresh_bytes), "sha256": sha256_bytes(fresh_bytes)}
    matches_pr143_pin = fresh == PR143_PINNED_PLAYERS_CROSSWALK_DIGEST
    matches_pr147_observed = fresh == PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST
    return {
        "fresh": fresh,
        "pr143_pin": dict(PR143_PINNED_PLAYERS_CROSSWALK_DIGEST),
        "pr147_observed": dict(PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST),
        "fresh_matches_pr143_pin": matches_pr143_pin,
        "fresh_matches_pr147_observed": matches_pr147_observed,
        "all_three_distinct": not matches_pr143_pin and not matches_pr147_observed,
        "pr143_pin_is_stale_relative_to_fresh": not matches_pr143_pin,
    }


# --------------------------------------------------------------------------
# Finding 1: frozen-bytes reproduction harness
# --------------------------------------------------------------------------

class _FrozenBytesResponse:
    """Minimal stand-in for `http.client.HTTPResponse` used only by this
    audit's own `frozen_injury_source` context manager."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FrozenBytesResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@contextmanager
def frozen_injury_source(injuries_dir: str):
    """Temporarily route `role_intelligence_data_prep.fetch_injury_rows`'s
    `urllib.request.urlopen` call to local frozen files named
    `injuries_<season>.csv` under `injuries_dir`, restoring the real
    `urllib.request.urlopen` on exit (even on error). This exercises the
    exact unmodified production parsing code in `fetch_injury_rows` against
    frozen bytes -- it does not re-implement injury-row parsing, and it never
    edits `role_intelligence_data_prep.py`.
    """
    real_urlopen = urllib.request.urlopen

    def _frozen_urlopen(request, timeout=None, *a, **kw):  # noqa: ANN001, ARG001
        url = request.full_url if hasattr(request, "full_url") else str(request)
        for token in url.split("/"):
            if token.startswith("injuries_") and token.endswith(".csv"):
                with open(f"{injuries_dir}/{token}", "rb") as f:
                    return _FrozenBytesResponse(f.read())
        raise RuntimeError(
            f"frozen_injury_source: no local fixture for {url!r} -- "
            "live fetch is intentionally blocked inside this context manager"
        )

    urllib.request.urlopen = _frozen_urlopen
    try:
        yield
    finally:
        urllib.request.urlopen = real_urlopen


def build_usage_rows_from_frozen_sources(
    frozen_dir: str,
    seasons: Iterable[int],
    *,
    depth_chart_seasons: frozenset[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build WR/RB usage rows for `target_share`/`carry_share` scope from
    LOCAL frozen bytes under `frozen_dir` (expects `weekly_stats/`,
    `injuries/`, and `depth_charts/` subdirectories laid out exactly as this
    audit's own download step wrote them). No live network fetch occurs
    inside this function (see module docstring's "Scope" section for why
    `snap_counts`/PBP are intentionally excluded).

    Reuses `role_intelligence_data_prep.parse_weekly_stats_csv`,
    `parse_depth_chart_csv`, `fetch_injury_rows` (via `frozen_injury_source`),
    and `build_player_game_usage_rows` completely unmodified. Returns
    `(usage_rows, injury_rows)`.
    """
    if depth_chart_seasons is None:
        depth_chart_seasons = frozenset(range(2012, 2025))

    all_usage_rows: list[dict[str, Any]] = []
    all_injury_rows: list[dict[str, Any]] = []
    with frozen_injury_source(f"{frozen_dir}/injuries"):
        for season in seasons:
            with open(f"{frozen_dir}/weekly_stats/stats_player_week_{season}.csv", encoding="utf-8") as f:
                weekly_rows = parse_weekly_stats_csv(f.read(), season)
            injury_rows = fetch_injury_rows(season)
            all_injury_rows.extend(injury_rows)
            if season in depth_chart_seasons:
                with open(f"{frozen_dir}/depth_charts/depth_charts_{season}.csv", encoding="utf-8") as f:
                    depth_rows = parse_depth_chart_csv(f.read(), season)
            else:
                depth_rows = []
            usage_rows = build_player_game_usage_rows(weekly_rows, [], depth_rows, injury_rows, [], {})
            all_usage_rows.extend(usage_rows)
    return all_usage_rows, all_injury_rows


def build_events_candidates_role_state(
    usage_rows: list[dict[str, Any]], injury_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Thin wrapper: `(events, candidates, role_state_rows)`, all built by
    PR #143's own unmodified functions."""
    events = build_teammate_absence_trigger_events(usage_rows, injury_rows)
    candidates = build_replacement_candidate_rows(usage_rows, events)
    role_state_rows = build_role_state_rows(usage_rows)
    return events, candidates, role_state_rows


def event_set_digest(events: list[dict[str, Any]]) -> tuple[str, list[tuple]]:
    """A stable content digest over an event population's identity keys,
    independent of list order -- used to compare two runs' event sets."""
    import json

    keys = sorted(
        (e["event_type"], e["season"], e["week"], e["team"], e["removed_player_id"])
        for e in events
    )
    digest = hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()
    return digest, keys


# --------------------------------------------------------------------------
# Finding 3: exact paired-population evaluator
# --------------------------------------------------------------------------

def _realized_share(
    usage_index: dict[tuple, dict], season: int, week: int, team: str, player_id: str, dimension: str
):
    row = usage_index.get((season, week, team, player_id))
    if row is None:
        return None
    value = compute_dimension_shares(row)[dimension]
    return value if isinstance(value, (int, float)) else None


def compute_paired_evaluation(
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
    predictors: dict[str, Callable],
    *,
    seasons: frozenset[int] | None = None,
) -> dict[str, Any]:
    """Score every predictor in `predictors` on the exact SAME (event,
    candidate) rows: the intersection of rows for which EVERY predictor
    produces a real numeric prediction AND a realized target-game share
    exists. One shared denominator (`paired_n`) for every predictor's MAE --
    this is what PR #147's own `evaluate_predictors` does not do (see module
    docstring, Finding 3).

    `seasons`, when given, restricts to events in those seasons (e.g. the
    predeclared held-out 2022-2025 set) -- the same restriction
    `evaluate_challenger_vs_baselines` applies, reused here as a plain
    filter rather than duplicated logic.
    """
    relevant_event_types = DIMENSION_RELEVANT_EVENT_TYPES.get(dimension, frozenset())
    scoped_events = [
        e for e in events
        if e["event_type"] in relevant_event_types and (seasons is None or e["season"] in seasons)
    ]

    history = build_player_dimension_history(role_state_rows)
    usage_index = {(r["season"], r["week"], r["team"], r["player_id"]): r for r in usage_rows}
    candidates_by_event: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        key = (c["season"], c["week"], c["team"], c["removed_player_id"])
        candidates_by_event[key].append(c)

    predictor_names = list(predictors.keys())
    rows: list[dict[str, Any]] = []
    drop_reasons: Counter = Counter()

    for event in scoped_events:
        key = (event["season"], event["week"], event["team"], event["removed_player_id"])
        teammates = candidates_by_event.get(key, [])
        if not teammates:
            drop_reasons["event_has_no_teammate_candidates"] += 1
            continue
        predictions_by_name = {
            name: fn(event, teammates, history, dimension) for name, fn in predictors.items()
        }
        for teammate in teammates:
            player_id = teammate["candidate_player_id"]
            realized = _realized_share(
                usage_index, event["season"], event["week"], event["team"], player_id, dimension
            )
            if realized is None:
                drop_reasons["no_realized_target_game_share"] += 1
                continue
            preds: dict[str, float] = {}
            missing_predictor = None
            for name in predictor_names:
                value = predictions_by_name[name].get(player_id)
                if not isinstance(value, (int, float)):
                    missing_predictor = name
                    break
                preds[name] = value
            if missing_predictor is not None:
                drop_reasons[f"missing_prediction:{missing_predictor}"] += 1
                continue
            rows.append({
                "season": event["season"],
                "week": event["week"],
                "team": event["team"],
                "removed_player_id": event["removed_player_id"],
                "candidate_player_id": player_id,
                "event_key": key,
                "realized": realized,
                "abs_errors": {name: abs(preds[name] - realized) for name in predictor_names},
            })

    n = len(rows)
    result: dict[str, Any] = {
        "dimension": dimension,
        "paired_n": n,
        "predictor_names": predictor_names,
        "drop_reasons": dict(drop_reasons),
        "rows": rows,
    }
    if n == 0:
        result["mae_by_predictor"] = {name: None for name in predictor_names}
        return result

    result["mae_by_predictor"] = {
        name: sum(r["abs_errors"][name] for r in rows) / n for name in predictor_names
    }
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_season[r["season"]].append(r)
    result["n_by_season"] = {season: len(srows) for season, srows in sorted(by_season.items())}
    result["mae_by_season"] = {
        season: {name: sum(r["abs_errors"][name] for r in srows) / len(srows) for name in predictor_names}
        for season, srows in sorted(by_season.items())
    }
    return result


def bootstrap_mae_ci_by_event(
    rows: list[dict[str, Any]],
    predictor_name: str,
    *,
    n_resamples: int = 2000,
    seed: int = 20260919,
) -> dict[str, Any]:
    """Percentile bootstrap 95% CI for one predictor's MAE on the paired
    `rows` `compute_paired_evaluation` returns, CLUSTERED BY EVENT
    (`season, week, team, removed_player_id`) rather than by individual row
    or by player.

    Chosen over per-row resampling because multiple candidate rows inside
    the same event share one removed player's vacated opportunity budget
    and one game's own shared noise (weather, game script, blowout), so they
    are not independent draws -- resampling whole events preserves that
    within-event correlation. Chosen over per-player resampling because a
    single event's mass-balance interdependence (candidates split ONE
    budget) is a same-event effect, not a same-player-across-events effect;
    a player who appears as a candidate in several different events across
    seasons is not, on that account alone, more correlated with himself than
    with a same-event teammate. Deterministic given `seed` (a local
    `random.Random` instance, never global `random` state, so this never
    affects any other code's random draws).
    """
    events: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        events[r["event_key"]].append(r["abs_errors"][predictor_name])
    event_keys = list(events.keys())
    if not event_keys:
        return {"n_events": 0, "point_estimate": None, "ci_low": None, "ci_high": None, "n_resamples": n_resamples}

    all_values = [v for vs in events.values() for v in vs]
    point_estimate = sum(all_values) / len(all_values)

    rng = random.Random(seed)
    estimates: list[float] = []
    n_events = len(event_keys)
    for _ in range(n_resamples):
        sample_values = []
        for _ in range(n_events):
            key = event_keys[rng.randrange(n_events)]
            sample_values.extend(events[key])
        if sample_values:
            estimates.append(sum(sample_values) / len(sample_values))
    estimates.sort()
    lo_idx = int(0.025 * len(estimates))
    hi_idx = min(int(0.975 * len(estimates)), len(estimates) - 1)
    return {
        "n_events": n_events,
        "point_estimate": point_estimate,
        "ci_low": estimates[lo_idx],
        "ci_high": estimates[hi_idx],
        "n_resamples": n_resamples,
    }


def build_challenger_predictor(model: dict[str, Any]) -> Callable:
    return lambda event, teammates, history, dimension: predict_committee_model(
        event, teammates, history, dimension, model
    )


def paired_challenger_vs_baselines(
    events_with_regime: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    role_state_rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    dimension: str,
    model: dict[str, Any],
    *,
    held_out_seasons: frozenset[int] = CHALLENGER_HELD_OUT_SEASONS,
    include_bootstrap: bool = True,
) -> dict[str, Any]:
    """The exact paired comparison this audit's task requires: the
    challenger AND all four existing baselines scored on the identical
    held-out (event, candidate) row set. Reuses
    `role_regime_redistribution.predict_committee_model`/`train_committee_model`
    and `role_intelligence_baselines.BASELINE_PREDICTORS` unmodified.
    """
    predictors = dict(BASELINE_PREDICTORS)
    predictors[CHALLENGER_NAME] = build_challenger_predictor(model)
    result = compute_paired_evaluation(
        events_with_regime, candidates, role_state_rows, usage_rows, dimension, predictors,
        seasons=held_out_seasons,
    )
    if include_bootstrap:
        result["bootstrap_by_event"] = {
            name: bootstrap_mae_ci_by_event(result["rows"], name) for name in predictors
        }
    return result


def paired_named_regime_coverage(
    rows: list[dict[str, Any]], events_with_regime: list[dict[str, Any]]
) -> dict[str, Any]:
    """Check whether the PAIRED population (a strict subset of the full
    held-out event set) can still support any per-named-HC-regime report at
    `MIN_EVENTS_FOR_NAMED_REGIME` (predeclared by PR #147; NOT lowered here).

    Counts DISTINCT EVENTS per regime (not candidate rows -- one event can
    contribute several teammate-candidate rows to `rows`, and PR #147's own
    `MIN_EVENTS_FOR_NAMED_REGIME` threshold is declared in event units, via
    `evaluate_baselines_by_hc_regime`'s `regime_counts = Counter(e[...] for e
    in events_with_regime ...)`; counting rows instead would overstate
    coverage and is not the same claim). Returns, per regime key with >=1
    paired event, its paired EVENT count, and whether it clears the
    threshold -- and the max count observed, so a "cannot support per-regime
    reporting" conclusion is falsifiable rather than asserted.
    """
    regime_by_event_key = {
        (e["season"], e["week"], e["team"], e["removed_player_id"]): e.get("hc_regime_key")
        for e in events_with_regime
    }
    paired_event_keys = {r["event_key"] for r in rows}
    counts: Counter = Counter()
    for event_key in paired_event_keys:
        regime_key = regime_by_event_key.get(event_key)
        if regime_key is not None:
            counts[regime_key] += 1
    return {
        "min_events_for_named_regime": MIN_EVENTS_FOR_NAMED_REGIME,
        "paired_events_total": len(paired_event_keys),
        "regimes_observed": len(counts),
        "max_paired_n_any_regime": max(counts.values()) if counts else 0,
        "any_regime_clears_threshold": any(v >= MIN_EVENTS_FOR_NAMED_REGIME for v in counts.values()),
        "counts": dict(counts),
    }


__all__ = [
    "PR143_PINNED_PLAYERS_CROSSWALK_DIGEST",
    "PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST",
    "sha256_bytes",
    "compare_players_crosswalk_digest",
    "frozen_injury_source",
    "build_usage_rows_from_frozen_sources",
    "build_events_candidates_role_state",
    "event_set_digest",
    "compute_paired_evaluation",
    "bootstrap_mae_ci_by_event",
    "build_challenger_predictor",
    "paired_challenger_vs_baselines",
    "paired_named_regime_coverage",
    "CHALLENGER_TRAIN_SEASONS",
    "CHALLENGER_HELD_OUT_SEASONS",
]
