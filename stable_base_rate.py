"""stable_base_rate.py -- a real, point-in-time, season-to-date LIFT
REFERENCE for hits_runs_rbis (live) and runs/rbis (shadow-only). Deliberately
NOT part of probability computation.

CONCEPTUAL SEPARATION, stated once here because conflating these two was the
real risk this module exists to avoid:

  - SHRINKAGE PRIOR (mlb_sources._apply_shrinkage's `league_p`, exposed as
    generate_picks.py's `base_rate`): pooled across TONIGHT'S SLATE ROSTER
    ONLY, and it feeds directly into `p_hat` -- the actual predicted
    probability a bet is priced against. Untouched by this module. It keeps
    doing exactly what it does today.

  - LIFT REFERENCE RATE (this module's `stable_base_rate()`): pooled across
    the WHOLE POPULATION OF PAST REAL GRADED CANDIDATES, season-to-date,
    used ONLY to answer "is this player's already-computed probability
    meaningfully above or below a stable historical reference" -- i.e. it
    feeds `stable_lift`, which recommendation.py reads for the Lean gate
    (hits_runs_rbis only; see LEAN_STABLE_LIFT_STATS there). It never
    touches `p_hat`/`predicted_prob`/`hit_probability`.

EVIDENCE: backtest/stable_baseline_challenger.py replayed real historical
outcomes and found the CURRENT slate-scoped reference gives hits_runs_rbis a
BACKWARDS Lean-gate separation (-2.5pp: candidates it calls "Lean-eligible"
hit LESS often than the ones it doesn't), while a season-to-date reference
like this one gives +5.1 to +5.3pp, stable across every year/season-phase
bucket with enough data to check. See that script and
backtest/replay_stable_lift_change.py (the exact-change replay) for the
full evidence trail.

DATA SOURCE: data/stable_base_rates/{stat}.json, a daily (date, needs) ->
(hit, n) ledger built by backtest/build_stable_base_rates.py from real
graded backtest/rows.jsonl outcomes. Re-run that builder periodically to
extend coverage; this module never fetches anything live and never sees a
future date relative to the `asof` it's asked about.

STRICT POINT-IN-TIME: only ledger entries with date < asof ever contribute.
No same-day outcomes, ever, by construction (`<`, not `<=`).

FAIL-SAFE: returns None when the season-to-date sample is smaller than
MIN_STABLE_SAMPLE (30, the same validity gate the validating research
script itself used) or when no ledger exists for the stat/needs at all.
Callers MUST treat None as "stable lift unavailable" and fall back to
existing behavior -- never invent a value."""
import json
import os
from datetime import datetime, timedelta

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "stable_base_rates")

MIN_STABLE_SAMPLE = 30

# Stats this module has a real, built ledger for. Anything else always
# returns None -- there is no fallback fabrication path.
SUPPORTED_STATS = ("hits_runs_rbis", "runs", "rbis")

_TABLE_CACHE = {}
_RESULT_CACHE = {}


def _season_start(dt):
    return datetime(dt.year, 3, 20)


def _load_table(stat):
    if stat in _TABLE_CACHE:
        return _TABLE_CACHE[stat]
    path = os.path.join(_DATA_DIR, f"{stat}.json")
    table = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                raw = json.load(f)
            by_needs = {}
            for entry in raw.get("daily") or []:
                by_needs.setdefault(entry["needs"], []).append(
                    (entry["date"], entry["hit"], entry["n"]))
            for needs in by_needs:
                by_needs[needs].sort()
            table = by_needs
        except (json.JSONDecodeError, OSError, KeyError):
            table = None
    _TABLE_CACHE[stat] = table
    return table


def stable_base_rate(stat, needs, asof):
    """Point-in-time season-to-date reference rate for (stat, needs) as of
    `asof` ("YYYY-MM-DD"). Returns (rate: float, n: int) or (None, 0) if
    unavailable/insufficient -- callers must handle None as "not usable",
    never substitute a guess.

    Season-to-date, matching the validated research: entries from
    _season_start(asof) (March 20 of the relevant year, prior season's
    window if `asof` itself is before that date) through the day BEFORE
    asof, inclusive. Strictly excludes `asof` itself -- no same-day
    leakage."""
    if stat not in SUPPORTED_STATS:
        return None, 0
    cache_key = (stat, needs, asof)
    if cache_key in _RESULT_CACHE:
        return _RESULT_CACHE[cache_key]
    table = _load_table(stat)
    if not table or needs not in table:
        _RESULT_CACHE[cache_key] = (None, 0)
        return None, 0
    try:
        asof_dt = datetime.strptime(asof, "%Y-%m-%d")
    except (ValueError, TypeError):
        _RESULT_CACHE[cache_key] = (None, 0)
        return None, 0
    start = _season_start(asof_dt)
    if asof_dt < start:
        start = _season_start(asof_dt - timedelta(days=200))
    start_s, asof_s = start.strftime("%Y-%m-%d"), asof
    hit_sum, n_sum = 0, 0
    for date, hit, n in table[needs]:
        if start_s <= date < asof_s:
            hit_sum += hit
            n_sum += n
    if n_sum < MIN_STABLE_SAMPLE:
        result = (None, 0)
    else:
        result = (round(hit_sum / n_sum, 4), n_sum)
    _RESULT_CACHE[cache_key] = result
    return result


def clear_cache():
    """Test/backtest hook: forces a fresh table+result load on next call.
    Backtest replay across many dates should call this once per dataset
    load (not per date -- the table itself doesn't change per date, only
    the asof window does, which _RESULT_CACHE already keys on)."""
    _TABLE_CACHE.clear()
    _RESULT_CACHE.clear()
