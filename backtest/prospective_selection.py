"""Champion set, PA-v1 selection, and the fail-closed equal-volume contract.

Locked protocol section 6.

THE CHAMPION IS NOT RECONSTRUCTED. It is the exact set of Hits Top Picks that
the corresponding Pages artifact actually admitted for public exposure --
build_publication_manifest()'s own `candidates` list, which has already applied
the real production gates (recommendation_status == "top_pick", structured
public settlement support, pregame game_state, and the publication cutoff). A
historical proxy such as `predicted_prob >= 0.60` is explicitly NOT acceptable
here and is not used anywhere in this module.

EQUAL VOLUME IS PER EPOCH, NOT GLOBAL. PA-v1 selects exactly N(date) picks from
the same frozen pool the champion drew from, for each date independently. A
global top-N budget would let a challenger win by silently moving its picks
onto easier days -- identical headline volume, different slate mix, invisible
in the aggregate. That failure mode is why assert_equal_volume() checks per
epoch and raises rather than warns.

N(date) = 0 produces no comparison for that date. Nothing is manufactured.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHADOW_STAT = "hits"


class EpochFailedClosed(Exception):
    """The epoch cannot produce a sound comparison and yields nothing.

    Raised rather than degraded on purpose. Every alternative -- dropping the
    unmatched champion, shrinking N, substituting a later candidate state --
    silently changes what is being compared, and each one moves the result in
    the challenger's favour.
    """


def champion_hits_picks(manifest, *, stat=SHADOW_STAT):
    """The champion arm: Hits Top Picks the artifact actually exposed.

    Reads the publication manifest's own candidate list. The manifest already
    encodes production's exposure decision, so this function filters by market
    only; it must never add or relax a gate of its own.
    """
    out = []
    for candidate in (manifest or {}).get("candidates") or []:
        identity = candidate.get("settlement_identity") or {}
        snapshot = candidate.get("snapshot") or {}
        candidate_stat = (identity.get("stat")
                          or (snapshot.get("projection") or {}).get("stat")
                          or snapshot.get("stat"))
        if candidate_stat != stat:
            continue
        out.append(candidate)
    return out


def _pool_index(pool):
    """canonical_prop_id -> (row, verdict) for the gated eligible pool."""
    index = {}
    for row, verdict in pool:
        pid = verdict.get("canonical_prop_id")
        if pid is not None:
            index[pid] = (row, verdict)
    return index


def _universe_index(universe):
    """canonical_prop_id -> (row, verdict) for the FROZEN RAW capture.

    Distinct from the pool on purpose. The raw universe is every Hits row the
    build produced; the pool is the subset that passed the section 5 gates.
    Section 6 uses both, and conflating them changes what fails closed.
    """
    index = {}
    for row, verdict in universe:
        pid = verdict.get("canonical_prop_id")
        if pid is not None:
            index[pid] = (row, verdict)
    return index


def resolve_champions(champions, universe, pool):
    """Match every champion to the frozen capture, failing closed if any misses.

    Two DIFFERENT memberships, and the difference is the whole rule:

      * Every champion MUST resolve against the frozen raw universe. A champion
        the shadow never saw means the snapshot is not the build that shipped,
        so the epoch's identity binding is broken and nothing it produces can
        be trusted. That raises.

      * N(date) counts champions that are ALSO in the gated eligible pool. A
        champion the site published but which fails a policy-independent
        operational gate is a real fact about that pick, not a bug -- it is
        recorded and excluded from the matched volume, not silently kept.
    """
    uni = _universe_index(universe)
    idx = _pool_index(pool)
    unmatched = [c.get("canonical_id") for c in champions
                 if c.get("canonical_id") not in uni]
    if unmatched:
        raise EpochFailedClosed(
            f"{len(unmatched)} champion Hits Top Pick(s) could not be matched "
            f"to the frozen shadow universe by canonical identity: "
            f"{unmatched}. The snapshot is not the build that shipped; this "
            f"epoch fails closed rather than comparing a different universe.")
    in_pool, out_of_pool = [], []
    for champion in champions:
        pid = champion["canonical_id"]
        if pid in idx:
            in_pool.append((pid, idx[pid]))
        else:
            out_of_pool.append((pid, uni[pid][1].get("failed_gates")))
    return {"in_pool": in_pool, "out_of_pool": out_of_pool,
            "n": len(in_pool)}


def rank_pa_v1(pool, pa_scores):
    """Protocol section 6's exact ranking, in its exact stated order.

    1. higher frozen PA-v1 score
    2. higher current Full Count hit probability
    3. stable canonical candidate identity

    Rule 3 is a real tiebreak, not decoration: PA-v1 reads a small number of
    discrete cells, so exact ties are common rather than rare, and without a
    deterministic final key the selected set would depend on dict ordering.
    Rows PA-v1 cannot score are ranked LAST but never dropped -- dropping them
    would let the challenger quietly shrink its own opportunity set to the
    cases it happens to be confident about.
    """
    ranked = []
    for row, verdict in pool:
        pid = verdict.get("canonical_prop_id")
        pa = pa_scores.get(pid)
        champ = row.get("hit_probability")
        ranked.append({
            "canonical_prop_id": pid,
            "pa_v1_probability": pa,
            "champion_probability": champ,
            "row": row,
            "verdict": verdict,
            "pa_v1_scored": pa is not None,
        })
    ranked.sort(key=lambda r: (
        0 if r["pa_v1_scored"] else 1,           # unscored last, never dropped
        -(r["pa_v1_probability"] or 0.0),
        -(r["champion_probability"] or 0.0),
        str(r["canonical_prop_id"]),
    ))
    for rank, item in enumerate(ranked, 1):
        item["pa_v1_rank"] = rank
    return ranked


def select_pa_v1(ranked, n):
    """Take exactly n. Never more, never fewer, never a different n."""
    if n < 0:
        raise EpochFailedClosed(f"negative selection volume {n}")
    if n > len(ranked):
        raise EpochFailedClosed(
            f"cannot select {n} PA-v1 picks from a pool of {len(ranked)}; "
            f"the challenger may not select outside the frozen pool the "
            f"champion drew from")
    return ranked[:n]


def assert_equal_volume(epoch_id, champion_selected, pa_selected):
    """Fail closed on ANY per-epoch volume inequality.

    Raises, never warns. A challenger evaluated at even slightly different
    volume is not being compared at equal volume, and the North Star of this
    experiment is realized hit rate AT THE SAME operational pick volume.
    """
    n_champ, n_pa = len(champion_selected), len(pa_selected)
    if n_champ != n_pa:
        raise EpochFailedClosed(
            f"epoch {epoch_id}: champion selected {n_champ} but PA-v1 selected "
            f"{n_pa}. Equal volume is the comparison, not a nicety.")
    return n_champ


def build_epoch_selection(*, epoch, manifest, universe, pool, pa_scores):
    """Full per-epoch selection. Returns None when N(date) == 0.

    None is a real, correct answer: a date on which the site published no
    exposable Hits Top Pick has no comparison to make, and manufacturing one
    would invent volume that never existed.
    """
    champions = champion_hits_picks(manifest)
    resolved = resolve_champions(champions, universe, pool)
    n = resolved["n"]
    if n == 0:
        return None
    ranked = rank_pa_v1(pool, pa_scores)
    pa_selected = select_pa_v1(ranked, n)
    assert_equal_volume(epoch.get("decisive_epoch_id"),
                        resolved["in_pool"], pa_selected)
    champion_ranks = {pid: i for i, (pid, _) in enumerate(resolved["in_pool"], 1)}
    return {
        "decisive_epoch_id": epoch.get("decisive_epoch_id"),
        "slate_date": epoch.get("slate_date"),
        "n": n,
        "champion_selected": resolved["in_pool"],
        "champion_ranks": champion_ranks,
        "champion_published_but_ineligible": resolved["out_of_pool"],
        "pa_v1_selected": pa_selected,
        "pa_v1_ranked": ranked,
    }
