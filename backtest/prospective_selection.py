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


def champion_hits_picks(payload, *, publication_cutoff_at, converged_at=None,
                        stat=SHADOW_STAT):
    """The champion arm: Hits Top Picks the decisive artifact ACTUALLY EXPOSED.

    ═══════════════════════════════════════════════════════════════════════
    THE DEFECT THIS REPLACES — found independently by two reviewers
    ═══════════════════════════════════════════════════════════════════════

    Mission 1 read `manifest["candidates"]`. That list is built by
    dashboard/publication_registry.py, whose FIRST filter is:

        if prop_id in registry["entries"]:
            continue

    `registry["entries"]` is a PERMANENT, CUMULATIVE, CROSS-DATE store. So
    `candidates` is the set of props achieving FIRST PUBLIC EXPOSURE at that
    exact artifact -- not the set the artifact displays.

    Protocol §7 then mandates the LATEST converged deployment as decisive. By
    then almost everything is already registered, so the champion list is
    near-empty. Measured on real committed state: docs/data.json for
    2026-09-01 displays 2 Hits Top Picks; both were already registered; the
    manifest would emit ZERO. N(date) would be 0 while the site showed two
    Hits Top Picks all evening. Across real dates a day's Hits Top Picks are
    spread over 10-12 distinct publishing artifacts.

    The consequences were all silent and all favoured the challenger: PA-v1
    ranked the whole gated pool while the champion was confined to
    first-exposure residue; the denominator collapsed for reasons unrelated to
    pick quality; and dates dropped non-randomly.

    The registry membership test is LIFECYCLE BOOKKEEPING -- "do not record a
    first publication twice" -- not an exposure predicate. So the champion set
    is now read from the artifact's own SERVED PAYLOAD, applying the manifest's
    real exposure gates MINUS that filter.

    Gates applied here, each matching production:
      * recommendation_status == "top_pick"   (production's own exposure bar)
      * stat == hits                          (this experiment's market)
      * game_state is pregame                 (never a live/final game)
      * before_betting_cutoff(row, publication_cutoff_at)
                                              (production's strict admission
                                               rule, at the artifact's real
                                               preparation clock)
      * publicly usable before first pitch    (a prop nobody could see until
                                               after the game started was
                                               never a wager -- symmetric,
                                               applied to the pool too)

    No probability proxy. No ROI/value gate. Those are champion selection
    policy, and the historical `predicted_prob >= 0.60` reconstruction is
    explicitly forbidden by the protocol.
    """
    from dashboard.live_state import (before_betting_cutoff, canonical_prop_id,
                                      game_state)

    if not publication_cutoff_at:
        # NEVER fall back to prepared_at. before_betting_cutoff() is
        # `now < game_start`, NOT the 15-minute rule, so falling back would
        # make the champion arm LOOSER than production and admit picks the
        # site could not have published.
        raise EpochFailedClosed(
            "the bound deployment carries no publication_cutoff_at; the "
            "champion arm cannot be resolved against production's real "
            "admission rule and this epoch fails closed")

    out, dropped = [], []
    for row in (payload or {}).get("props") or []:
        row_stat = (row.get("projection") or {}).get("stat") or row.get("stat")
        if row_stat != stat:
            continue                      # a different market entirely
        # From here on the row IS an exposed Hits prop. EVERY exclusion below
        # is RECORDED, never a bare `continue`. An independent red team found
        # that silently dropping an exposed champion here reintroduces exactly
        # the asymmetric replacement resolve_champions exists to prevent --
        # one function earlier, where resolve_champions can never see it.
        if row.get("recommendation_status") != "top_pick":
            continue                      # never exposed as a Top Pick
        state = row.get("game_state")
        if state is None:
            state = game_state(row.get("status") or {}, row=row)
        try:
            cid = canonical_prop_id(row)
        except (ValueError, KeyError, TypeError):
            cid = None
        if state not in (None, "pregame"):
            dropped.append((cid, "not pregame in the served payload"))
            continue
        if not before_betting_cutoff(row, publication_cutoff_at):
            dropped.append((cid, "inside the publication cutoff"))
            continue
        if converged_at is not None:
            from backtest.prospective_epoch import _parse, publicly_usable
            if not publicly_usable(_parse(row.get("game_start")),
                                   _parse(converged_at)):
                dropped.append((cid, "not publicly usable before first pitch"))
                continue
        out.append({"canonical_id": cid, "row": row,
                    "identity_error": cid is None})
    return out, dropped


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
    """Match every exposed champion to the frozen capture. Fail closed twice.

    ═══════════════════════════════════════════════════════════════════════
    THE ESCALATED QUESTION, RESOLVED
    ═══════════════════════════════════════════════════════════════════════

    Mission 1 applied two different memberships: a champion missing from the
    frozen universe raised, but a champion that failed a policy-independent
    OPERATIONAL gate was silently dropped from N, with NO BACKFILL, while PA-v1
    still took its own best N-1.

    The red team's finding: that is ASYMMETRIC REPLACEMENT. The champion is
    scored on a gate-selected residue of its own picks; the challenger is
    scored on its own optimum. Worse, WHICH champions fall out is steerable
    after the fact through the source-integrity verdict and the evaluation
    clock -- a post-outcome lever pointed at the champion's measured set.

    Resolution adopted: a published-but-ineligible champion now FAILS THE EPOCH
    CLOSED, exactly as an unresolvable one already did. A pick the site exposed
    for public wagering, which the shadow's own usability gates say a human
    could not have placed, is a contradiction between two claims that both
    purport to describe operational usability. One of them is wrong. Silently
    deleting the pick resolves that contradiction in the direction that shrinks
    the champion. Failing closed makes it visible and un-exploitable, and
    MISSING EVIDENCE is already the protocol's accepted answer for a date that
    cannot produce a sound comparison.

    It is also self-correcting: if this fires constantly, that is evidence the
    gates are miscalibrated against production's own exposure bar -- a finding
    that must surface BEFORE any evidence counts, not be absorbed silently.
    """
    unidentifiable = [c for c in champions if c.get("identity_error")]
    if unidentifiable:
        raise EpochFailedClosed(
            f"{len(unidentifiable)} exposed Hits Top Pick(s) have no derivable "
            f"canonical identity; the champion set cannot be resolved and this "
            f"epoch fails closed rather than comparing a partial champion.")

    uni = _universe_index(universe)
    idx = _pool_index(pool)

    missing = [c["canonical_id"] for c in champions
               if c["canonical_id"] not in uni]
    if missing:
        raise EpochFailedClosed(
            f"{len(missing)} champion Hits Top Pick(s) could not be matched to "
            f"the frozen shadow universe by canonical identity: {missing}. The "
            f"snapshot is not the build that shipped; this epoch fails closed "
            f"rather than comparing a different universe.")

    in_pool, out_of_pool = [], []
    for champion in champions:
        pid = champion["canonical_id"]
        if pid in idx:
            in_pool.append((pid, idx[pid]))
        else:
            out_of_pool.append((pid, uni[pid][1].get("failed_gates")))

    if out_of_pool:
        raise EpochFailedClosed(
            f"{len(out_of_pool)} champion Hits Top Pick(s) were publicly "
            f"exposed but fail a policy-independent operational gate: "
            f"{out_of_pool}. Dropping them would score the champion on a "
            f"gate-selected residue of its own picks while the challenger "
            f"keeps its optimum, and WHICH ones drop is steerable after the "
            f"fact. This epoch fails closed instead.")

    return {"in_pool": in_pool, "out_of_pool": out_of_pool, "n": len(in_pool)}


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


def verify_payload_binding(payload, epoch):
    """The champion arm's basis must be PROVEN to be this deployment's payload.

    ═══════════════════════════════════════════════════════════════════════
    THE DEFECT THIS CLOSES
    ═══════════════════════════════════════════════════════════════════════

    An independent red team demonstrated by execution that the payload was an
    unauthenticated, caller-supplied file. Nothing tied it to the bound
    deployment -- not `generated_at`, not a content digest. So an arbitrary
    JSON, chosen after outcomes were known, could define the entire champion
    arm.

    The celebrated `generated_at` hash join binds the SNAPSHOT to the
    deployment. It said nothing whatsoever about the champion's basis. This
    closes that half.

    `public_generated_at` is the value the deploy workflow polls the PUBLIC url
    until it matches, so requiring equality here means the champion arm is read
    from the same board the public actually served.
    """
    expected = epoch.get("public_generated_at")
    actual = (payload or {}).get("generated_at")
    if not expected:
        raise EpochFailedClosed(
            "bound epoch carries no public_generated_at; the champion payload "
            "cannot be proven to belong to this deployment")
    if actual != expected:
        raise EpochFailedClosed(
            f"champion payload generated_at {actual!r} != the deployment's "
            f"proven public generated_at {expected!r}. The champion arm must "
            f"be read from the board that actually went public, not from a "
            f"file handed in alongside it.")
    return True


def build_epoch_selection(*, epoch, payload, universe, pool, pa_scores,
                          schedule=None):
    """Full per-epoch selection, sealed from ONE bound state. None when N == 0.

    Order matters and every step is required:
      0. PROVE the champion payload belongs to this deployment;
      1. re-gate the captured pool against the BOUND DEPLOYMENT's real clocks;
      2. read the champion set from that proven payload;
      3. fail closed if ANY exposed champion was dropped by a shadow gate;
      4. resolve every champion, failing closed on any mismatch;
      5. rank PA-v1 over the SAME re-gated pool;
      6. take exactly N;
      7. assert per-epoch equal volume.
    """
    from backtest.prospective_epoch import regate_pool

    verify_payload_binding(payload, epoch)

    regated, dropped = regate_pool(pool, epoch, schedule=schedule or {})

    champions, champ_dropped = champion_hits_picks(
        payload,
        publication_cutoff_at=epoch.get("deployment_publication_cutoff_at"),
        converged_at=epoch.get("deployment_converged_at"))

    if champ_dropped:
        # An exposed Hits Top Pick that a SHADOW gate removed is the same
        # contradiction resolve_champions fails closed on, and it must not be
        # resolved silently one function earlier.
        raise EpochFailedClosed(
            f"{len(champ_dropped)} publicly exposed Hits Top Pick(s) were "
            f"removed by shadow gates before champion resolution: "
            f"{champ_dropped}. Dropping them would score the champion on a "
            f"gate-selected residue of its own picks while the challenger "
            f"keeps its optimum. This epoch fails closed instead.")

    resolved = resolve_champions(champions, universe, regated)
    n = resolved["n"]
    if n == 0:
        return None

    ranked = rank_pa_v1(regated, pa_scores)
    pa_selected = select_pa_v1(ranked, n)
    assert_equal_volume(epoch.get("decisive_epoch_id"),
                        resolved["in_pool"], pa_selected)
    champion_ranks = {pid: i for i, (pid, _) in enumerate(resolved["in_pool"], 1)}
    return {
        "decisive_epoch_id": epoch.get("decisive_epoch_id"),
        "slate_date": epoch.get("slate_date"),
        "n": n,
        "payload_generated_at": (payload or {}).get("generated_at"),
        "regated_pool_size": len(regated),
        "regate_dropped": len(dropped),
        "regate_drop_reasons": sorted({r for _row, r in dropped}),
        "champion_exposed_n": len(champions),
        "champion_selected": resolved["in_pool"],
        "champion_ranks": champion_ranks,
        "pa_v1_selected": pa_selected,
        "pa_v1_ranked": ranked,
    }
