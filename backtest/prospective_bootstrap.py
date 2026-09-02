"""Frozen bootstrap contract for the prospective Hits PA-v1 comparison.

THIS CONTRACT IS PREREGISTERED AND FROZEN. It was fixed before any prospective
outcome existed, and the constants below are not tuning knobs. Every clause
exists to close a specific way a favourable interval could be manufactured
after the fact:

  UNIT = SLATE DATE (cluster). A date contains many games, and picks within a
  date share weather, umpires, schedule context and a common market state.
  Resampling individual picks would treat those as independent observations
  and produce an interval far narrower than the evidence supports. Game and
  player clustering are SECONDARY diagnostics only; they may be reported, and
  they may not replace this primary unit.

  WITH REPLACEMENT, CARRYING MULTIPLICITY. A date drawn twice contributes ALL
  of its selections twice. Deduplicating a repeated date would silently shrink
  the resampled volume and break the equal-volume property the whole
  comparison rests on.

  REPLICATES = 5000, SEED = 20260901, FIXED. NO SEED REDRAW, EVER. Re-running
  with a new seed until an interval excludes zero is p-hacking with extra
  steps; the seed is therefore a module constant, and run() does not accept a
  seed argument at all.

  RNG = random.Random, a deterministic Mersenne Twister seeded by an integer.
  Reproducible from the constants alone, on any machine, with no numpy version
  in the trust path.

  PRIMARY STATISTIC = PA-v1 hit rate MINUS champion hit rate, at exact matched
  operational volume, on the DECIDED denominator. Both arms are recomputed
  inside each replicate from the same resampled dates, so the pairing survives
  resampling.

  INTERVAL = 95% percentile (2.5th, 97.5th).

  A replicate whose resampled dates yield ZERO decided picks in either arm is
  UNDEFINED, not zero. It is skipped and counted, and the successful replicate
  count is reported. Coercing it to zero would pull the interval toward no
  effect and quietly overstate precision.
"""

from __future__ import annotations

import hashlib
import os
import random

# ── FROZEN CONSTANTS. Do not change these to obtain a different interval. ──
BOOTSTRAP_UNIT = "slate_date"
BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 20260901
BOOTSTRAP_CI = 0.95
BOOTSTRAP_RNG = "python_random_Random_mersenne_twister"
BOOTSTRAP_STATISTIC = "pa_v1_hit_rate_minus_champion_hit_rate_at_matched_volume"
BOOTSTRAP_DENOMINATOR = "decided_only_hit_plus_miss"
SECONDARY_UNITS = ("game_pk", "player_id")

def contract_file_sha256():
    """Hash of THIS FILE, so the frozen contract is pinned in the evidence.

    The red team's finding: `run()` correctly exposes no seed, replicate or CI
    argument, so there is no API through which to move an interval -- but the
    constants above live in an unpinned file, and the LOCKED protocol never
    states a seed, a replicate count or an RNG. A one-line edit to
    BOOTSTRAP_SEED would therefore have been undetectable in the evidence
    record, unlike the PA-v1 artifact, which is hash-verified before every
    capture.

    This closes that asymmetry: every §12 report carries the hash of the file
    that produced its interval.
    """
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


CONTRACT = {
    "unit": BOOTSTRAP_UNIT,
    "replicates": BOOTSTRAP_REPLICATES,
    "seed": BOOTSTRAP_SEED,
    "ci": BOOTSTRAP_CI,
    "rng": BOOTSTRAP_RNG,
    "statistic": BOOTSTRAP_STATISTIC,
    "denominator": BOOTSTRAP_DENOMINATOR,
    "resample": "with_replacement_carrying_all_selections_with_multiplicity",
    "seed_redraw_permitted": False,
    "unit_change_after_outcomes_permitted": False,
    "secondary_units": list(SECONDARY_UNITS),
}


def _rate(rows, key):
    """Decided hit rate for one arm, or None when nothing was decided."""
    decided = [r for r in rows if r.get(key) and r.get("outcome") in ("hit", "miss")]
    if not decided:
        return None
    return sum(1 for r in decided if r["outcome"] == "hit") / len(decided)


def point_estimate(settlements):
    """Observed PA-v1 minus champion decided hit rate. None if either is None."""
    champ = _rate(settlements, "champion_member")
    pa = _rate(settlements, "pa_v1_member")
    if champ is None or pa is None:
        return None
    return pa - champ


def group_by_date(settlements):
    """Cluster settlements by slate date -- the frozen resampling unit."""
    clusters = {}
    for row in settlements:
        clusters.setdefault(row.get("slate_date"), []).append(row)
    return clusters


def run(settlements):
    """Run the frozen bootstrap. Takes no seed and no replicate-count argument.

    The absent parameters are the point: a caller cannot redraw a seed or trim
    replicates to move an interval, because there is no argument through which
    to do it.
    """
    clusters = group_by_date(settlements)
    dates = sorted(d for d in clusters if d is not None)
    observed = point_estimate(settlements)
    if not dates:
        return {"contract": dict(CONTRACT),
                "contract_file_sha256": contract_file_sha256(),
                "observed": observed,
                "successful_replicates": 0, "attempted_replicates": 0,
                "ci_low": None, "ci_high": None, "n_dates": 0}

    rng = random.Random(BOOTSTRAP_SEED)
    diffs = []
    for _ in range(BOOTSTRAP_REPLICATES):
        # Draw len(dates) dates WITH REPLACEMENT and carry every selection on
        # each drawn date, with multiplicity.
        drawn = [rng.choice(dates) for _ in range(len(dates))]
        resampled = []
        for date in drawn:
            resampled.extend(clusters[date])
        value = point_estimate(resampled)
        if value is not None:          # undefined replicates are skipped, not zeroed
            diffs.append(value)

    result = {
        "contract": dict(CONTRACT),
        "contract_file_sha256": contract_file_sha256(),
        "observed": observed,
        "n_dates": len(dates),
        "attempted_replicates": BOOTSTRAP_REPLICATES,
        "successful_replicates": len(diffs),
        "undefined_replicates": BOOTSTRAP_REPLICATES - len(diffs),
        "ci_low": None,
        "ci_high": None,
    }
    if diffs:
        diffs.sort()
        tail = (1.0 - BOOTSTRAP_CI) / 2.0
        lo = int(tail * (len(diffs) - 1))
        hi = int((1.0 - tail) * (len(diffs) - 1))
        result["ci_low"] = diffs[lo]
        result["ci_high"] = diffs[hi]
    return result
