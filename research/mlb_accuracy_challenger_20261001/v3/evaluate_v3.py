#!/usr/bin/env python3
"""Evaluation for preregistration v3 -- sections 9-13. Research only.

Input per slate unit: the seal record, its external receipts, the manifest, and
the graded file of the SHADOW board. A slate is used only if:
  * the regime check passes (regimes.py; no date override exists);
  * the seal chain is intact and the seal binds this manifest's hashes;
  * the GitHub receipt AND a verified RFC 3161 token both precede the earliest
    covered first pitch (seal.verify_receipts) -- otherwise
    SLATE_INVALID_NO_CONFIRMATORY_USE, never repaired;
  * the graded file is linked to the manifest's shadow board.
Selection reads no outcome. The frozen coefficients and v1 selector are reused unchanged.
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import harness as H  # noqa: E402  (v1: frozen arms, selector, C3)
import harness_v2 as H2  # noqa: E402  (v2: probability quality, clustered diff incl. week)
import manifest_v3 as M3  # noqa: E402
import regimes as RG  # noqa: E402
import seal as SL  # noqa: E402

V3_BOUNDARY_UTC = "2026-10-02T06:00:00Z"   # slates cut off at/before this are NONCONFIRMATORY / DRILL ONLY
PRACTICAL_DELTA = 0.05     # minimum practically meaningful hit-rate gain (C2 - shadow champion)
MIN_CHAMPION_SETTLED = 250  # coverage floors, NOT a power guarantee
MIN_SLATES = 60
CHALK_GUARD = 0.03
GRADE_CLASS = {"hit": "settled", "miss": "settled", "void": "void", "push": "push"}


def _rows(manifest, graded, coef):
    if graded.get("source_board_sha256") != manifest["shadow_board_sha256"]:
        raise ValueError(f"{manifest['date']}/{manifest['window']}: graded file not linked to the shadow board")
    grades = {g["candidate_id"]: g.get("grade") for g in graded.get("records") or []}
    out = []
    for m in manifest["rows"]:
        if not (m["eligible"] and m["family_status"] == "PRIMARY"):
            continue
        g = grades.get(m["candidate_id"])
        r = {"id": m["candidate_id"], "date": manifest["date"], "week": H2._week(manifest["date"]),
             "unit": f"{manifest['date']}/{manifest['window']}", "game_pk": m["game_pk"],
             "player_id": m["player_id"], "stat": m["stat"], "family": H.family(m["stat"]), "p0": float(m["p0"]),
             "odds": float(m["quote"]["american"]), "q": m["q_raw"], "q_devig": m["quote"].get("q_devig"),
             "champion": m["shadow_champion"], "y": H.y_of(g), "grade_class": GRADE_CLASS.get(g, "unresolved")}
        out.append(H.score(r, coef))
    return out


def arm_counts(picks):
    c = Counter(r["grade_class"] for r in picks)
    return {"selected": len(picks), "settled": c["settled"], "void": c["void"], "push": c["push"],
            "unresolved": c["unresolved"]}


def verdict_v3(champ, chall, game_ci, n_slates):
    if champ.get("n_scored", 0) < MIN_CHAMPION_SETTLED or n_slates < MIN_SLATES:
        return "INSUFFICIENT_N"
    diff = chall["hit_rate"] - champ["hit_rate"]
    if diff <= 0:
        return "REJECTED"
    lb = game_ci.get("one_sided_lower95")
    if lb is None or lb <= 0:
        return "INCONCLUSIVE"
    if chall["mean_q"] - champ["mean_q"] > CHALK_GUARD:
        return "INCONCLUSIVE_CHALK_GUARD"
    return "SUPPORTED_ADOPTABLE" if diff >= PRACTICAL_DELTA else "POSITIVE_BELOW_PRACTICAL_THRESHOLD"


def evaluate(slates, coef, *, regime, genesis_sha256=None, chain=None):
    """slates: [{"seal", "receipts", "manifest", "graded", "final_first_pitch_utc"(optional)}]."""
    spec = RG.check_regime(regime, [s["manifest"] for s in slates])
    if chain is not None:
        SL.verify_chain(chain, genesis_sha256)
        chained = {c["seal_sha256"] for c in chain}
    used, invalid = [], {}
    for s in slates:
        m, seal = M3.verify_manifest(s["manifest"]), s["seal"]
        unit = f"{m['date']}/{m['window']}"
        if not SL.utc(m["cutoff_utc"]) > SL.utc(V3_BOUNDARY_UTC):
            invalid[unit] = "PRE_V3_BOUNDARY_DRILL_ONLY"
            continue
        binds = all(seal.get(k) == m.get(k) for k in ("manifest_sha256", "shadow_board_sha256", "capture_sha256",
                                                     "schedule_sha256", "date", "window"))
        if not binds:
            invalid[unit] = "SEAL_DOES_NOT_BIND_MANIFEST"
            continue
        if chain is not None and seal["seal_sha256"] not in chained:
            invalid[unit] = "SEAL_NOT_IN_EVIDENCE_CHAIN"
            continue
        efp = min([m["earliest_first_pitch_utc"]] + ([s["final_first_pitch_utc"]] if s.get("final_first_pitch_utc") else []),
                  key=SL.utc) if m["earliest_first_pitch_utc"] else None
        if efp is None:
            invalid[unit] = "NO_COVERED_GAMES"
            continue
        status, detail = SL.verify_receipts(seal, s["receipts"], earliest_first_pitch_utc=efp)
        if status != "ON_TIME":
            invalid[unit] = f"SLATE_INVALID_NO_CONFIRMATORY_USE:{status}"
            continue
        used.append((m, s["graded"]))
    picks, prim_all, audit, n_champ = defaultdict(list), [], {}, 0
    for m, g in used:
        rows = _rows(m, g, coef)
        n, sel = H.select(rows)
        n_champ += bool(n)
        for k, v in sel.items():
            picks[k] += v
        prim_all += rows
        audit[f"{m['date']}/{m['window']}"] = {"manifest_sha256": m["manifest_sha256"], "n_d": n,
                                               "eligible_primary": len(rows)}
    out = {"regime": regime, "confirmatory": spec["confirmatory"], "n_slates_used": len(used),
           "n_slates_with_champion": n_champ, "zero_champion_slates": len(used) - n_champ,
           "invalid_slates": invalid, "slate_audit": audit, "picking": {}}
    champ = picks["CHAMPION"]
    cs = H2.picking_summary(champ, len(used))
    cs["counts"] = arm_counts(champ)
    out["picking"]["SHADOW_CHAMPION"] = cs
    for name in (H.PRIMARY, *H.SECONDARY):
        s = H2.picking_summary(picks[name], len(used))
        s["counts"] = arm_counts(picks[name])
        s["vs_champion"] = {u: H2.clustered_diff(champ, picks[name], u) for u in ("game", "player", "week")}
        s["overlap"] = H.overlap_table(champ, picks[name])
        out["picking"][name] = s
    prim = out["picking"][H.PRIMARY]
    out["primary_verdict"] = (verdict_v3(cs, prim, prim["vs_champion"]["game"], n_champ) if cs.get("n_scored")
                              else "INSUFFICIENT_N")
    sens = {u: (prim["vs_champion"][u]["one_sided_lower95"] or 0) > 0 for u in ("player", "week")}
    out["dependence_sensitivity"] = {"player_lb_positive": sens["player"], "week_lb_positive": sens["week"]}
    if not spec["confirmatory"]:
        out["primary_verdict"] = "NOT_APPLICABLE_DESCRIPTIVE_REGIME"
    out["probability_quality"] = H2.probability_quality(prim_all)
    out["c3_pitcher_outs_secondary"] = dict(H.c3_pitcher_outs(prim_all), role="SECONDARY_NO_PROMOTION_ON_ITS_OWN")
    return out
