"""Protocol §12 reporting over the durable prospective ledger. READ ONLY.

An audit found no implementation of §12 existed: grep for `overlap`, `pa_only`
or `champion_only` across the prospective modules returned zero. (Note
`backtest/prospective_reporting.py` is NOT this reporter -- it belongs to the
2026-08-25 candidate-funnel track, and wiring it in here would blend two
evidence estates.)

This module only reads. It never writes a ledger event, never settles, and
never selects. It is the one place allowed to compute a hit rate, and it
computes every denominator the protocol requires -- not just the flattering
one.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import prospective_bootstrap as pb  # noqa: E402
from backtest import prospective_ledger as pl  # noqa: E402
from backtest import prospective_receipt as pr  # noqa: E402

# Protocol §13's promotion FLOOR. Not a stopping rule and not a success test:
# below either number the answer is INCONCLUSIVE, which is never a PA failure.
MIN_PRIMARY_DATES = 30
MIN_DECIDED_PER_ARM = 100


def _events(ledger_dir):
    """Every event across every slate-date partition, oldest date first."""
    root = os.path.join(ledger_dir, pl.LEDGER_DIR)
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".jsonl"):
            out.extend(pl.read_events(os.path.join(root, name)))
    return out


def _rate(hit, decided):
    return (hit / decided) if decided else None


def build_report(ledger_dir):
    """The §12 checkpoint report."""
    events = _events(ledger_dir)

    designated, no_primary, failed = {}, [], []
    receipts, settlements = {}, {}
    for e in events:
        t, body = e.get("event_type"), e.get("body") or {}
        if t == pl.EVENT_DECISIVE_EPOCH_DESIGNATED:
            designated[body.get("slate_date")] = body.get("decisive_epoch_id")
        elif t == pl.EVENT_NO_PRIMARY_EPOCH:
            no_primary.append({"slate_date": body.get("slate_date"),
                               "reason": body.get("reason")})
        elif t == pl.EVENT_EPOCH_FAILED_CLOSED:
            failed.append({"slate_date": body.get("slate_date"),
                           "epoch_id": body.get("epoch_id"),
                           "stage": body.get("stage"),
                           "reason": body.get("reason")})
        elif t == pl.EVENT_PREGAME_RECEIPT:
            receipts[body.get("receipt_id")] = body
        elif t == pl.EVENT_SETTLEMENT:
            settlements[body.get("receipt_id")] = body

    primary_epochs = set(designated.values())
    primary = [r for r in receipts.values()
               if r.get("decisive_epoch_id") in primary_epochs]

    rows, integrity_mismatch = [], []
    for r in primary:
        s = settlements.get(r["receipt_id"])
        if s and s.get("receipt_content_sha256") != r.get("receipt_content_sha256"):
            # A settlement that does not pair to the sealed receipt is not
            # counted. Evidence that cannot be proven to describe the wager it
            # claims to describe is not evidence.
            integrity_mismatch.append(r["receipt_id"])
            continue
        rows.append({
            "slate_date": r.get("slate_date"),
            "canonical_prop_id": r.get("canonical_prop_id"),
            "game_pk": r.get("game_pk"),
            "player_id": r.get("player_id"),
            "champion_member": r.get("champion_member"),
            "pa_v1_member": r.get("pa_v1_member"),
            "outcome": (s or {}).get("outcome", "ungraded"),
            "pa_v1_fallback_state": r.get("pa_v1_fallback_state"),
            "lineup_confirmed": r.get("lineup_confirmed"),
            "source_integrity_state": r.get("source_integrity_state"),
            "odds_american": r.get("odds_american"),
            "model_version": r.get("model_version"),
            "selection_policy_version": r.get("selection_policy_version"),
            "calibration_version": r.get("calibration_version"),
            "feature_version": r.get("feature_version"),
            "git_sha": r.get("git_sha"),
        })

    def arm(key):
        sel = [r for r in rows if r[key]]
        c = {o: sum(1 for r in sel if r["outcome"] == o)
             for o in ("hit", "miss", "void", "ungraded")}
        decided = c["hit"] + c["miss"]
        n = len(sel)
        return {"selected_n": n, "decided_n": decided, **c,
                "hit_rate": _rate(c["hit"], decided),
                "void_rate": (c["void"] / n) if n else None,
                "ungraded_rate": (c["ungraded"] / n) if n else None}

    champ, pa = arm("champion_member"), arm("pa_v1_member")

    # Exact per-date volume equality, reported rather than assumed.
    per_date = {}
    for r in rows:
        d = per_date.setdefault(r["slate_date"], {"champion": 0, "pa_v1": 0})
        if r["champion_member"]:
            d["champion"] += 1
        if r["pa_v1_member"]:
            d["pa_v1"] += 1
    volume_mismatches = {d: v for d, v in per_date.items()
                         if v["champion"] != v["pa_v1"]}

    # Overlap decomposition: where the two arms actually differ is the whole
    # question, and a headline delta hides it.
    both = [r for r in rows if r["champion_member"] and r["pa_v1_member"]]
    pa_only = [r for r in rows if r["pa_v1_member"] and not r["champion_member"]]
    ch_only = [r for r in rows if r["champion_member"] and not r["pa_v1_member"]]

    def sub(sel):
        h = sum(1 for r in sel if r["outcome"] == "hit")
        d = sum(1 for r in sel if r["outcome"] in ("hit", "miss"))
        return {"n": len(sel), "decided_n": d, "hit": h, "hit_rate": _rate(h, d)}

    # Per-date contribution direction, so one date cannot silently carry the
    # entire effect unnoticed.
    contribution = {}
    for date in sorted(per_date):
        dr = [r for r in rows if r["slate_date"] == date]
        c = sub([r for r in dr if r["champion_member"]])
        p = sub([r for r in dr if r["pa_v1_member"]])
        contribution[date] = {
            "champion_hit_rate": c["hit_rate"], "pa_v1_hit_rate": p["hit_rate"],
            "delta": (None if c["hit_rate"] is None or p["hit_rate"] is None
                      else round(p["hit_rate"] - c["hit_rate"], 6)),
            "n": max(c["n"], p["n"]),
        }

    # Verify the frozen contract BEFORE using it. Recording a hash that
    # nothing checks is not a pin.
    try:
        pb.verify_contract_unmodified()
        contract_verified = True
        contract_error = None
    except pb.ContractModified as exc:
        contract_verified = False
        contract_error = str(exc)

    settle_rows = [{"slate_date": r["slate_date"], "outcome": r["outcome"],
                    "champion_member": r["champion_member"],
                    "pa_v1_member": r["pa_v1_member"]} for r in rows]
    boot = pb.run(settle_rows)
    # Secondary clustering needs the unit keys on each row.
    clus_rows = [dict(r, game_pk=r.get("game_pk"), player_id=r.get("player_id"))
                 for r in rows]
    psb_game = pb.secondary_clustering(clus_rows, "game_pk")
    psb_player = pb.secondary_clustering(clus_rows, "player_id")
    conc_game = pb.concentration(clus_rows, "game_pk")
    conc_player = pb.concentration(clus_rows, "player_id")

    def dist(field):
        out = {}
        for r in rows:
            out[str(r.get(field))] = out.get(str(r.get(field)), 0) + 1
        return out

    # Version strata. A production change mid-window must not silently turn one
    # prospective experiment into several incomparable regimes.
    strata = {}
    for r in rows:
        k = "|".join(str(r.get(f)) for f in
                     ("model_version", "selection_policy_version",
                      "calibration_version", "feature_version"))
        strata.setdefault(k, {"n": 0, "dates": set()})
        strata[k]["n"] += 1
        strata[k]["dates"].add(r["slate_date"])
    strata = {k: {"n": v["n"], "dates": sorted(v["dates"])}
              for k, v in strata.items()}

    delta = (None if champ["hit_rate"] is None or pa["hit_rate"] is None
             else round(pa["hit_rate"] - champ["hit_rate"], 6))
    enough = (contract_verified
              and len(designated) >= MIN_PRIMARY_DATES
              and champ["decided_n"] >= MIN_DECIDED_PER_ARM
              and pa["decided_n"] >= MIN_DECIDED_PER_ARM)

    return {
        "ok": True,
        "protocol_version": pr.PROTOCOL_VERSION,
        "protocol_sha256": pr.PROTOCOL_SHA256,
        "receipt_schema_version": pr.RECEIPT_SCHEMA_VERSION,
        "pa_v1_artifact_scientific_sha256": pr.PA_V1_SCIENTIFIC_SHA256,
        "bootstrap_contract": dict(pb.CONTRACT),
        "bootstrap_contract_verified": contract_verified,
        "bootstrap_contract_error": contract_error,

        "primary_slate_dates": len(designated),
        "primary_dates": sorted(designated),
        "missing_primary_epoch_dates": no_primary,
        "epochs_failed_closed": failed,

        "champion": champ,
        "pa_v1": pa,
        "delta_hit_rate": delta,
        "exact_volume_equality_by_date": per_date,
        "volume_mismatches": volume_mismatches,

        "overlap_n": len(both),
        "overlap": sub(both),
        "pa_v1_only": sub(pa_only),
        "champion_only": sub(ch_only),
        "date_contribution": contribution,

        "date_cluster_bootstrap": boot,
        # SECONDARY, diagnostic only -- never a promotion criterion.
        "secondary_clustering": {
            "primary_unit": "slate_date",
            "game_pk": psb_game, "player_id": psb_player,
            "concentration": {"game_pk": conc_game, "player_id": conc_player},
            "note": "Player is a CROSSED cluster, not nested in date: PA-v1 "
                    "ranks on batting order and reselects the same "
                    "top-of-order hitters, which a date-level resample does "
                    "not absorb. Reported so the understatement is visible; "
                    "the promotion rule is unchanged.",
        },
        "lineup_confirmed_rate": dist("lineup_confirmed"),
        "pa_v1_fallback_rates": dist("pa_v1_fallback_state"),
        "source_integrity_states": dist("source_integrity_state"),
        "odds_distribution": dist("odds_american"),
        "version_strata": strata,

        "settlement_pairing_mismatches": integrity_mismatch,
        "promotion_floor": {
            "min_primary_dates": MIN_PRIMARY_DATES,
            "min_decided_per_arm": MIN_DECIDED_PER_ARM,
            "met": enough,
            "verdict": ("ELIGIBLE FOR REVIEW" if enough
                        else "INCONCLUSIVE / NOT YET PROMOTABLE"),
            "note": "Below the floor the answer is INCONCLUSIVE. That is never "
                    "a PA-v1 failure, and a wide interval is not evidence "
                    "against the challenger.",
        },
    }
