#!/usr/bin/env python3
"""MLB accuracy challenger evaluation, preregistration v2 -- research only.

Implements sections 6-11 of `research/mlb_accuracy_challenger_prereg_v2_20261001.md`.
Input is (manifest, graded file) per slate. The manifest (manifest.py) fixes
the common operational candidate universe at the board's cutoff; the graded
file supplies outcomes only. Probability quality and picking performance are
reported separately. Selection never reads an outcome.

Reused unchanged from v1 (`harness.py`): the frozen challenger probabilities,
the equal-volume selector, the hit-rate verdict table and the C3 test.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import date as _date

import harness as H
import manifest as MF

BOUNDARY_UTC_V2 = "2026-10-02T05:00:00Z"   # locked: boards sealed strictly after this
REGIMES = ("CONFIRMATORY_2027_REGULAR", "POSTSEASON_2026_SHADOW", "SMOKE_TEST_SYNTHETIC")
CAL_BINS = (0.0, 0.40, 0.50, 0.60, 0.70, 1.0)


def _week(d):
    y, w, _ = _date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def slate_rows(manifest, graded, coef):
    """Eligible rows of the manifest with outcomes attached. Hash and link are verified."""
    MF.verify_manifest(manifest)
    if graded.get("source_board_sha256") != manifest["board_sha256"]:
        raise ValueError(f"{manifest['date']}: graded file is not linked to the manifest's board")
    grades = {g["candidate_id"]: g.get("grade") for g in graded.get("records") or []}
    prim, expl = [], []
    for m in manifest["rows"]:
        if not m["eligible"]:
            continue
        r = {"id": m["candidate_id"], "date": manifest["date"], "week": _week(manifest["date"]),
             "game_pk": m["game_pk"], "player_id": m["player_id"], "stat": m["stat"],
             "family": H.family(m["stat"]), "p0": float(m["p0"]), "odds": float(m["quote_odds"]),
             "q": m["q_raw"], "q_devig": m.get("q_devig"), "champion": m["champion"],
             "y": H.y_of(grades.get(m["candidate_id"]))}
        (prim if m["family_status"] == "PRIMARY" else expl).append(H.score(r, coef))
    return prim, expl


def clustered_diff(champ, chall, unit, B=H.BOOT_B, seed=H.BOOT_SEED):
    """v1's bootstrap with one more unit: 'week' (ISO week of the slate date)."""
    keyf = {"game": lambda r: (r["date"], r["game_pk"]), "player": lambda r: r["player_id"],
            "week": lambda r: r["week"]}[unit]
    cl = defaultdict(lambda: [[], []])
    for i, arm in enumerate((champ, chall)):
        for r in arm:
            if r["y"] is not None:
                cl[keyf(r)][i].append(r["y"])
    ks = sorted(cl)
    rng, draws = random.Random(seed), []
    for _ in range(B):
        a = b = na = nb = 0
        for _k in ks:
            c = cl[ks[rng.randrange(len(ks))]]
            a, na, b, nb = a + sum(c[0]), na + len(c[0]), b + sum(c[1]), nb + len(c[1])
        if na and nb:
            draws.append(b / nb - a / na)
    if not draws:
        return {"n_clusters": len(ks), "ci95": None, "one_sided_lower95": None}
    draws.sort()
    pick = lambda q: draws[min(len(draws) - 1, int(q * len(draws)))]
    return {"n_clusters": len(ks), "ci95": [pick(0.025), pick(0.975)], "one_sided_lower95": pick(0.05)}


def picking_summary(picks, n_slates_total):
    s = H.arm_summary(picks)
    per_day = Counter(r["date"] for r in picks)
    players = Counter(r["player_id"] for r in picks)
    s.update({"picks_per_slate": len(picks) / n_slates_total if n_slates_total else 0.0,
              "market_mix": dict(Counter(r["stat"] for r in picks)),
              "repeated_player_share": (sum(v for v in players.values() if v > 1) / len(picks)) if picks else 0.0,
              "max_picks_one_player": max(players.values()) if players else 0,
              "slates_with_picks": len(per_day)})
    # ROI is valid here: every price is an exact captured, timestamped, pre-cutoff quote.
    s["roi_basis"] = "captured FanDuel quote at the manifest cutoff (data/props), exact match to board price"
    return s


def probability_quality(rows, B=H.BOOT_B, seed=H.BOOT_SEED):
    sc = [r for r in rows if r["y"] is not None]
    if not sc:
        return {"n": 0}
    out = {"n": len(sc), "scores": {}, "calibration": {}}
    for a in ("p0", "q", "p1", "pcal", "p2"):
        out["scores"][a] = {"log_loss": sum(H.ll(r[a], r["y"]) for r in sc) / len(sc),
                            "brier": sum((r[a] - r["y"]) ** 2 for r in sc) / len(sc)}
        bins = []
        for lo, hi in zip(CAL_BINS, CAL_BINS[1:]):
            b = [r for r in sc if lo <= r[a] < hi or (hi == 1.0 and r[a] == 1.0)]
            if b:
                bins.append({"bin": [lo, hi], "n": len(b), "mean_pred": sum(r[a] for r in b) / len(b),
                             "hit_rate": sum(r["y"] for r in b) / len(b)})
        out["calibration"][a] = bins
    dv = [r for r in sc if r.get("q_devig") is not None]
    out["two_sided_devig_subset"] = ({"n": len(dv),
                                      "log_loss_q_raw": sum(H.ll(r["q"], r["y"]) for r in dv) / len(dv),
                                      "log_loss_q_devig": sum(H.ll(r["q_devig"], r["y"]) for r in dv) / len(dv),
                                      "log_loss_p2": sum(H.ll(r["p2"], r["y"]) for r in dv) / len(dv)}
                                     if dv else {"n": 0})
    # residual information on the same rows: LL(p2) - LL(p1), game-clustered
    cl = defaultdict(list)
    for r in sc:
        cl[(r["date"], r["game_pk"])].append(H.ll(r["p2"], r["y"]) - H.ll(r["p1"], r["y"]))
    ks = sorted(cl)
    rng, draws = random.Random(seed), []
    for _ in range(B):
        s = n = 0
        for _k in ks:
            c = cl[ks[rng.randrange(len(ks))]]
            s, n = s + sum(c), n + len(c)
        draws.append(s / n)
    draws.sort()
    pick = lambda q: draws[min(len(draws) - 1, int(q * len(draws)))]
    out["residual_information_ll_p2_minus_p1"] = {"mean": sum(sum(v) for v in cl.values()) / len(sc),
                                                  "ci95": [pick(0.025), pick(0.975)], "n_games": len(ks)}
    return out


def evaluate(pairs, coef, *, boundary_utc=BOUNDARY_UTC_V2, regime):
    """pairs: list of (manifest, graded). Guards, equal-volume selection, the locked verdicts."""
    if regime not in REGIMES:
        raise ValueError("unknown evidence regime")
    picks, picks_xpo = defaultdict(list), defaultdict(list)
    prim_all, expl_all, audit, n_slates_champ = [], [], {}, 0
    red_flag_champion = []
    for m, g in pairs:
        d = m["date"]
        if H.DEV_WINDOW[0] <= d <= H.DEV_WINDOW[1]:
            raise ValueError(f"{d}: inside the development window; refused")
        if regime != "SMOKE_TEST_SYNTHETIC" and not H._utc(m["cutoff_utc"]) > H._utc(boundary_utc):
            raise ValueError(f"{d}: cutoff {m['cutoff_utc']} not after boundary {boundary_utc}; refused")
        prim, expl = slate_rows(m, g, coef)
        grades = {x["candidate_id"]: x.get("grade") for x in g.get("records") or []}
        red_flag_champion += [H.y_of(grades.get(x["candidate_id"])) for x in m["rows"]
                              if x["champion"] and x["family_status"] == "RED_FLAG_SEPARATE"]
        n, sel = H.select(prim)                                   # equal volume, ties by id
        _, sel_x = H.select([r for r in prim if r["family"] != "pitcher_outs"])
        n_slates_champ += bool(n)
        for k, v in sel.items():
            picks[k] += v
        for k, v in sel_x.items():
            picks_xpo[k] += v
        prim_all += prim
        expl_all += expl
        audit[d] = {"manifest_sha256": m["manifest_sha256"], "cutoff_utc": m["cutoff_utc"],
                    "champion_n_d": n, "eligible_primary": len(prim), "counts": m["counts"]}
    ns = len(pairs)
    champ = picks["CHAMPION"]
    out = {"regime": regime, "boundary_utc": boundary_utc, "n_slates": ns,
           "n_slates_with_champion": n_slates_champ, "zero_champion_slates": ns - n_slates_champ,
           "slate_audit": audit, "picking": {}, "probability_quality": {}}
    cs = picking_summary(champ, ns)
    out["picking"]["CHAMPION"] = cs
    for name in (H.PRIMARY, *H.SECONDARY):
        s = picking_summary(picks[name], ns)
        s["vs_champion"] = {u: clustered_diff(champ, picks[name], u) for u in ("game", "player", "week")}
        s["overlap"] = H.overlap_table(champ, picks[name])
        out["picking"][name] = s
    if cs.get("n_scored"):
        out["primary_verdict"] = H.verdict(cs, out["picking"][H.PRIMARY],
                                           out["picking"][H.PRIMARY]["vs_champion"]["game"], n_slates_champ)
    else:
        out["primary_verdict"] = "INSUFFICIENT_N"
    cx, px = H.arm_summary(picks_xpo["CHAMPION"]), H.arm_summary(picks_xpo[H.PRIMARY])
    out["sensitivity_primary_excluding_pitcher_outs"] = {"champion": cx, H.PRIMARY: px,
                                                         "vs_champion_game": clustered_diff(picks_xpo["CHAMPION"], picks_xpo[H.PRIMARY], "game"),
                                                         "status": "DESCRIPTIVE_SENSITIVITY"}
    out["probability_quality"]["primary_universe"] = probability_quality(prim_all)
    out["probability_quality"]["by_family"] = {f: probability_quality([r for r in prim_all if r["family"] == f])
                                               for f in MF.PRIMARY_FAMILIES}
    out["c3_pitcher_outs"] = H.c3_pitcher_outs(prim_all)
    sc_rf = [y for y in red_flag_champion if y is not None]
    out["combined_starter_strikeouts"] = {"status": "RED_FLAG_SEPARATE_NOT_IN_PRIMARY_UNIVERSE",
                                          "champion_picks": len(red_flag_champion), "scored": len(sc_rf),
                                          "hit_rate": (sum(sc_rf) / len(sc_rf)) if sc_rf else None}
    out["exploratory_families"] = {"status": "EXPLORATORY_NO_CLAIMS_NO_PROMOTION",
                                   "eligible_rows_by_stat": dict(Counter(r["stat"] for r in expl_all)),
                                   "champion_picks_by_stat": dict(Counter(r["stat"] for r in expl_all if r["champion"]))}
    if regime == "POSTSEASON_2026_SHADOW":
        out["primary_verdict"] = "NOT_APPLICABLE_POSTSEASON_DESCRIPTIVE"
    return out
