"""FC-ACC-016 labeled SYNTHETIC cases for the protocol checker.

Every object here is synthetic: invented games, players, prices and outcomes, labelled
`"synthetic": true`. They exercise the protocol rules through the real frozen model
transform and the real production gate functions. They are NOT historical offers and
carry no evidence about any model.
"""
from __future__ import annotations

import copy
import math

import numpy as np

import checker as C

FP = "2027-05-01T23:10:00Z"          # first pitch
BASE_UNIT = {"unit_id": "U", "slate_date": "2027-05-01", "run_id": "synthetic-run",
             "prepared_at": "2027-05-01T22:30:00Z", "decision_cutoff_at": "2027-05-01T22:40:00Z",
             "sealed_at": "2027-05-01T22:45:00Z", "coverage_status": "COMPLETE",
             "model_artifact_sha256": C.LOCK["artifact_sha256"],
             "games": [{"game_pk": 1, "first_pitch_at": FP}, {"game_pk": 2, "first_pitch_at": FP}], "rows": []}


def features_for(entry, target_p, key="skill_tb"):
    """Synthetic feature vector: every input at its training median, then one critical
    input moved so the frozen transform returns target_p (deterministic algebra, no fit)."""
    keys = entry["keys"]
    f = {k: float(entry["median"][i]) for i, k in enumerate(keys)}
    p0, _ = C.challenger_probability(entry, f)
    j = keys.index(key)
    c, sd = entry["coef"][1 + j], entry["sd"][j]
    f[key] += (math.log(target_p / (1 - target_p)) - math.log(p0 / (1 - p0))) * sd / c
    return f


def offer(market, needs, line, american, observed="2027-05-01T22:20:00Z", **kw):
    o = {"join_label": "MATCHED_RESEARCH_ONLY", "authenticated": True, "sportsbook": "fanduel",
         "market": market, "needs": needs, "line": line, "side": "OVER", "american": american,
         "observed_at": observed, "raw_sha256": "0" * 64, "synthetic": True}
    o.update(kw)
    return o


def row(cid, pid, market, needs, line, status, p, ci, american, game=1, ch_p=None, entry=None, **kw):
    r = {"canonical_id": cid, "game_pk": game, "player_id": pid, "team": 1, "market": market, "needs": needs,
         "line": line, "wager_side": "OVER", "recommendation_status": status, "hit_probability": p,
         "prob_ci": ci, "reliability": "A", "lineup_confirmed": True, "data_fresh": True,
         "offer": offer(market, needs, line, american) if american is not None else None, "synthetic": True}
    if ch_p is not None:
        r["challenger"] = {"features": features_for(entry, ch_p), "features_built_at": "2027-05-01T22:35:00Z",
                           "features_data_through": "2027-04-30", "slot_source": "CONFIRMED_LINEUP_AT_DECISION",
                           "sp_source": "ANNOUNCED_PROBABLE_AT_DECISION"}
    r.update(kw)
    return r


def unit(rows, **kw):
    u = copy.deepcopy(BASE_UNIT)
    u["rows"] = rows
    u.update(kw)
    return u


def cases():
    m = C.load_frozen_models()
    tb, hits = m["total_bases"], m["hits"]
    champ = lambda cid, pid, lo, game=1: row(cid, pid, "hits", 1, 0.5, "top_pick", lo + 0.05, [lo, lo + 0.10], -140, game)  # noqa: E731
    tbr = lambda cid, pid, chp, american=-110, **kw: row(cid, pid, "total_bases", 2, 1.5, "lean", 0.50, [0.45, 0.55],  # noqa: E731
                                                          american, ch_p=chp, entry=tb, **kw)
    out = {}
    # 1. champion selects zero: nothing may be invented, even for an attractive TB candidate
    out["zero_pick_slate_invents_nothing"] = (
        unit([tbr("tb1", 10, 0.66)], coverage_status="ZERO_PICK"), "TP", "total_bases",
        {"N": 0, "A": [], "B": [], "C": [], "comparable": True})
    # 2-5, 10: legitimate-offer, lineup and hindsight requirements
    base2 = [champ("h1", 1, 0.60), champ("h2", 2, 0.58)]
    out["tb_without_offer_cannot_enter"] = (unit(base2 + [tbr("tb1", 10, 0.66, american=None)]), "TP", "total_bases",
                                            {"B": ["h1", "h2"], "excl": "NO_OFFER"})
    out["tb_stale_offer_cannot_enter"] = (
        unit(base2 + [tbr("tb1", 10, 0.66, offer=offer("total_bases", 2, 1.5, -110, observed="2027-05-01T21:40:00Z"))]),
        "TP", "total_bases", {"B": ["h1", "h2"], "excl": "STALE_OFFER"})
    out["tb_post_cutoff_offer_cannot_enter"] = (
        unit(base2 + [tbr("tb1", 10, 0.66, offer=offer("total_bases", 2, 1.5, -110, observed="2027-05-01T22:41:00Z"))]),
        "TP", "total_bases", {"B": ["h1", "h2"], "excl": "OFFER_AFTER_CUTOFF"})
    out["tb_unconfirmed_lineup_cannot_enter"] = (unit(base2 + [tbr("tb1", 10, 0.66, lineup_confirmed=False)]), "TP",
                                                 "total_bases", {"B": ["h1", "h2"], "excl": "LINEUP_NOT_CONFIRMED"})
    hind = tbr("tb1", 10, 0.66)
    hind["challenger"]["slot_source"] = "RETROSPECTIVE_SCHEDULE_LINEUP"
    out["tb_hindsight_slot_cannot_enter"] = (unit(base2 + [hind]), "TP", "total_bases",
                                             {"B": ["h1", "h2"], "excl": "SLOT_NOT_FROM_CONFIRMED_LINEUP"})
    miss = tbr("tb1", 10, 0.66)
    miss["challenger"]["features"]["skill_tb"] = None
    out["tb_missing_critical_feature_fails_closed"] = (unit(base2 + [miss]), "TP", "total_bases",
                                                       {"B": ["h1", "h2"], "excl": "MISSING_CRITICAL:skill_tb"})
    # 6. a qualifying TB candidate displaces the weakest champion pick; N preserved
    out["qualifying_tb_displaces_weakest_champion"] = (
        unit(base2 + [tbr("tb1", 10, 0.66)]), "TP", "total_bases",
        {"N": 2, "B": ["h1", "tb1"], "added": ["tb1"], "removed": ["h2"]})
    # 7. high probability alone is not enough: the price gate binds
    out["high_probability_bad_price_cannot_enter"] = (unit(base2 + [tbr("tb1", 10, 0.66, american=-300)]), "TP",
                                                      "total_bases", {"B": ["h1", "h2"],
                                                                      "excl": "FAILS_PROBABILITY_OR_PRICE_GATE"})
    # 7b. below the Top Pick floor: the realistic TB case in lane TP
    out["tb_below_top_pick_floor_cannot_enter_tp"] = (unit(base2 + [tbr("tb1", 10, 0.48, american=150)]), "TP",
                                                      "total_bases", {"B": ["h1", "h2"],
                                                                      "excl": "FAILS_PROBABILITY_OR_PRICE_GATE"})
    # 8. champion pick lacking an authentic offer -> the unit is non-comparable, never silently shrunk
    bad = champ("h2", 2, 0.58)
    bad["offer"]["authenticated"] = False
    out["champion_offer_invalid_marks_noncomparable"] = (unit([champ("h1", 1, 0.60), bad, tbr("tb1", 10, 0.66)]),
                                                         "TP", "total_bases", {"comparable": False})
    # 9. decisions sealed after first pitch are invalid
    out["sealed_after_first_pitch_invalid"] = (unit(base2, sealed_at="2027-05-01T23:11:00Z"), "TP", "total_bases",
                                               {"comparable": False, "integrity": "SEALED_AFTER_FIRST_PITCH"})
    # 11. concentration: no second selection for a player already selected
    out["same_player_concentration_blocked"] = (unit(base2 + [tbr("tb2", 2, 0.66)]), "TP", "total_bases",
                                                {"B": ["h1", "h2"], "excl": "PLAYER_ALREADY_SELECTED"})
    # 12. lane V: TB at plus money, pessimistic-ROI ordering
    vrow = lambda cid, pid, p, lo, a: row(cid, pid, "home_runs", 1, 0.5, "value", p, [lo, p + 0.03], a)  # noqa: E731
    vt = tbr("tbv", 10, 0.37, american=200)
    vt["hit_probability"], vt["prob_ci"] = 0.36, [0.33, 0.39]          # champion interval half-width 0.03
    out["lane_v_tb_enters_on_pessimistic_roi"] = (
        unit([vrow("v1", 1, 0.19, 0.17, 550), vrow("v2", 2, 0.17, 0.15, 550), vt]), "V", "total_bases",
        {"N": 2, "B": ["tbv", "v1"], "added": ["tbv"], "removed": ["v2"]})
    # 13. market-only arm: highest raw implied probability in the same lane universe
    mo = base2 + [row("h3", 3, "hits", 1, 0.5, "lean", 0.62, [0.57, 0.67], -260)]
    out["market_only_picks_highest_implied"] = (unit(mo), "TP", "total_bases", {"C": ["h1", "h3"]})
    # 14. secondary: frozen hits challenger governs the hits market in lane TP
    hr = lambda cid, pid, chp, lo: row(cid, pid, "hits", 1, 0.5, "top_pick", lo + 0.05, [lo, lo + 0.10], -140,  # noqa: E731
                                        ch_p=chp, entry=hits)
    out["hits_secondary_regoverns_hits_rows"] = (unit([hr("h1", 1, 0.70, 0.60), hr("h2", 2, 0.55, 0.58),
                                                      hr("h3", 3, 0.68, 0.55) | {"recommendation_status": "lean"}]),
                                                 "TP", "hits", {"N": 2, "B": ["h1", "h3"], "added": ["h3"], "removed": ["h2"]})
    return out


def run_suite():
    models = C.load_frozen_models()
    report = {"synthetic": True, "cases": {}}
    for name, (u, lane, mk, exp) in cases().items():
        s = C.select_unit(u, lane, mk, models)
        got = {"N": s["N"], "A": s["A"], "B": s["B"], "C": s["C"], "comparable": s["comparable"],
               "added": s["added"], "removed": s["removed"], "exclusions": s["challenger_exclusions"],
               "integrity": s["integrity"]}
        ok = True
        for k, v in exp.items():
            if k == "excl":
                ok &= v in s["challenger_exclusions"]
            elif k == "integrity":
                ok &= v in s["integrity"]
            else:
                ok &= got[k] == v
        report["cases"][name] = {"pass": bool(ok), "expected": exp, "got": got}
    # outcome blindness: selections cannot depend on outcomes
    u, lane, mk, _ = cases()["qualifying_tb_displaces_weakest_champion"]
    s1 = C.select_unit(u, lane, mk, models)
    u2 = copy.deepcopy(u)
    u2["outcomes_should_be_ignored"] = {r["canonical_id"]: "WIN" for r in u2["rows"]}
    s2 = C.select_unit(u2, lane, mk, models)
    report["cases"]["selection_is_outcome_blind"] = {"pass": (s1["A"], s1["B"], s1["C"]) == (s2["A"], s2["B"], s2["C"])}
    # settlement: push/void returns stake; profit uses the captured offered price
    st = C.settle(["h1", "tb1"], u, {"h1": {"status": "WIN"}, "tb1": {"status": "VOID"}})
    report["cases"]["settlement_void_and_price"] = {"pass": st["stakes"] == 1 and st["void"] == 1
                                                    and abs(st["profit"] - (100 / 140)) < 1e-12, "got": st}
    # strikeouts never enter an arm
    try:
        C.select_unit(u, "TP", "strikeouts", models)
        report["cases"]["strikeouts_research_only"] = {"pass": False}
    except (ValueError, KeyError):
        report["cases"]["strikeouts_research_only"] = {"pass": True}
    report["all_pass"] = all(v["pass"] for v in report["cases"].values())
    return report
