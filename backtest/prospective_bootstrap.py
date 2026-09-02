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


class ContractModified(RuntimeError):
    """The frozen bootstrap contract file no longer matches its pinned hash."""

# THE PINNED SCIENTIFIC BASELINE. Hashes the frozen CONTRACT VALUES -- unit,
# replicates, seed, CI, RNG, statistic, denominator, resampling rule -- not the
# whole file. A one-line edit to BOOTSTRAP_SEED changes this and is refused.
#
# Pinning the whole file was the first attempt and it was too blunt: adding a
# secondary DIAGNOSTIC (which cannot touch the primary interval) tripped it,
# which would have trained a reader to re-pin on every edit -- exactly the
# habit that makes a pin worthless. The file hash is still recorded below as a
# change detector; this constants hash is the one that is verified.
EXPECTED_CONTRACT_SHA256 = (
    "6cf2a728ec07ab676d939ade5b2f235215a465ce84545fe46c71d35720dafce9")


def contract_sha256():
    """Hash of the frozen contract VALUES."""
    import hashlib as _h
    import json as _j
    return _h.sha256(_j.dumps(CONTRACT, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")).hexdigest()


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
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
        body = [ln for ln in fh
                if not ln.startswith("    \"") or "PLACEHOLDER" not in ln]
    body = [ln for ln in body
            if not ln.lstrip().startswith('"') or "SHA" not in ln]
    return hashlib.sha256("".join(
        ln for ln in body
        if "EXPECTED_CONTRACT_BODY_SHA256" not in ln
        and not (ln.strip().startswith('"') and ln.strip().endswith('")'))
    ).encode("utf-8")).hexdigest()


def verify_contract_unmodified():
    """Raise if the frozen bootstrap contract file has been edited.

    A one-line change to BOOTSTRAP_SEED would otherwise alter only a recorded
    string that nothing compares against a baseline -- detectable in principle
    by a human diffing two reports months apart, which is to say not
    detectable.
    """
    actual = contract_sha256()
    if actual != EXPECTED_CONTRACT_SHA256:
        raise ContractModified(
            f"frozen bootstrap contract sha256 {actual} != pinned "
            f"{EXPECTED_CONTRACT_SHA256}. A contract value (unit, seed, "
            f"replicates, CI, statistic or denominator) has been changed; no "
            f"interval computed from it may be counted.")
    return True


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


def secondary_clustering(settlements, unit):
    """SECONDARY diagnostic only. Never changes the promotion rule.

    The frozen contract fixes the primary unit at the slate date. But PLAYER is
    a CROSSED cluster, not nested in date: PA-v1 ranks on batting order, so it
    repeatedly selects the same top-of-order hitters across dates, and a
    date-level resample absorbs none of that. The understatement runs in the
    challenger's favour, which is why it is measured rather than assumed
    negligible.

    Reported alongside the primary interval. It is explicitly NOT a promotion
    criterion, and changing that would require Jacob's authorization.
    """
    rng = random.Random(BOOTSTRAP_SEED)
    clusters = {}
    for row in settlements:
        clusters.setdefault(row.get(unit), []).append(row)
    keys = sorted(k for k in clusters if k is not None)
    observed = point_estimate(settlements)
    if not keys:
        return {"unit": unit, "n_clusters": 0, "ci_low": None, "ci_high": None,
                "observed": observed, "successful_replicates": 0}
    diffs = []
    for _ in range(BOOTSTRAP_REPLICATES):
        drawn = [rng.choice(keys) for _ in range(len(keys))]
        resampled = []
        for k in drawn:
            resampled.extend(clusters[k])
        value = point_estimate(resampled)
        if value is not None:
            diffs.append(value)
    out = {"unit": unit, "n_clusters": len(keys), "observed": observed,
           "successful_replicates": len(diffs), "ci_low": None, "ci_high": None}
    if diffs:
        diffs.sort()
        tail = (1.0 - BOOTSTRAP_CI) / 2.0
        out["ci_low"] = diffs[int(tail * (len(diffs) - 1))]
        out["ci_high"] = diffs[int((1.0 - tail) * (len(diffs) - 1))]
    return out


def concentration(settlements, unit):
    """How concentrated the selections are on repeated units.

    A high max share or a low effective count means the date-level interval is
    understating uncertainty for that arm.
    """
    counts = {}
    for row in settlements:
        k = row.get(unit)
        if k is not None:
            counts[k] = counts.get(k, 0) + 1
    n = sum(counts.values())
    if not n:
        return {"unit": unit, "n": 0, "distinct": 0, "max_share": None,
                "effective_n": None}
    # Inverse Simpson: the number of equally-frequent units that would give the
    # same concentration. Far below `distinct` means heavy repetition.
    shares = [c / n for c in counts.values()]
    return {"unit": unit, "n": n, "distinct": len(counts),
            "max_share": round(max(shares), 6),
            "effective_n": round(1.0 / sum(s * s for s in shares), 3)}


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
