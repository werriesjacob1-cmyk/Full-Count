#!/usr/bin/env python3
"""MLB accuracy challenger harness -- research only, never imported by production.

Implements exactly the preregistration
`research/mlb_accuracy_challenger_prereg_20261001.md`:

* common eligible universe per slate, built from a FROZEN FULL BOARD and its
  graded file (prospective full-candidate evidence class only);
* frozen challenger probabilities C0 (market), C1 (calibrated model),
  C2 (market + model) from `frozen_coefficients.json`;
* equal-volume selection: on each slate every arm takes exactly N_d picks,
  N_d = the champion's (production `top_pick`) count inside the universe;
* the decision table and the preregistered verdict;
* C3 (pitcher outs) proper-score test.

It reads only what it is given. The evaluation entry point refuses any slate
whose board was sealed at or before the preregistration boundary, or any date
in the development window.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
FAMILIES = ("hits", "hits_runs_rbis", "strikeouts", "pitcher_outs")
CLIP = 1e-4

# ---- locked by the preregistration -------------------------------------------------
PRICE_BAND = (0.40, 0.70)            # posted implied probability band (both arms)
MODEL_VERSIONS = ("2026.08.15",)     # production model version the coefficients describe
DEV_WINDOW = ("2026-08-04", "2026-09-27")
MIN_CHAMPION_PICKS = 250             # primary verdict minimum (scored picks)
MIN_SLATES = 60
MIN_C3_ROWS = 150
CHALK_GUARD = 0.03                   # challenger mean q may exceed champion's by at most this
BOOT_B, BOOT_SEED = 2000, 20261001
PRIMARY = "C2_RESIDUAL"
SECONDARY = ("C1_CALIBRATED_EDGE", "C0_MARKET", "RAW_MODEL")


# ---- probability pieces ---------------------------------------------------------------
def implied(odds):
    odds = float(odds)
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def logit(p):
    p = min(max(float(p), CLIP), 1 - CLIP)
    return math.log(p / (1 - p))


def logistic(z):
    return 1.0 / (1.0 + math.exp(-z))


def family(stat):
    return stat if stat in FAMILIES else "other"


def load_coefficients(path=os.path.join(HERE, "frozen_coefficients.json")):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def score(row, coef):
    """Add q, p1 (C0), pcal (C1), p2 (C2) to a row holding p0/odds/family."""
    lp, lq = logit(row["p0"]), logit(row["q"])
    feats = {"lp": lp, "lq": lq}
    arms = coef["arms"]
    for arm, cols in arms.items():
        block = coef["coefficients"][arm]
        beta = (block.get(row["family"]) or block["_pooled"])["beta"]
        row[arm] = logistic(beta[0] + sum(b * feats[c] for b, c in zip(beta[1:], cols)))
    row["resid"] = row["p2"] - row["p1"]
    row["cal_edge"] = row["pcal"] - row["q"]
    return row


# ---- frozen board -> common eligible universe -------------------------------------------
def canonical_board_hash(board):
    body = {k: v for k, v in board.items() if k != "board_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def y_of(grade):
    return 1 if grade == "hit" else 0 if grade == "miss" else None


def universe(board, graded, coef, *, price_band=PRICE_BAND, model_versions=MODEL_VERSIONS):
    """Return (eligible rows, audit). Eligible means: frozen-board record, qc kept, confirmed
    lineup, real posted price inside the band, model probability present, a recommendation
    status, an allowed model version. Outcome y may be None (not settled hit/miss)."""
    if canonical_board_hash(board) != board.get("board_sha256"):
        raise ValueError(f"{board.get('date')}: board hash does not rebuild")
    if graded.get("source_board_sha256") != board.get("board_sha256"):
        raise ValueError(f"{board.get('date')}: graded file is not linked to this board")
    grades = {r["candidate_id"]: r for r in graded.get("records") or []}
    rows, why = [], Counter()
    for r in board.get("records") or []:
        elig, mkt, pred, sel = (r.get("eligibility") or {}), (r.get("market") or {}), \
            (r.get("prediction") or {}), (r.get("selector") or {})
        odds, p0, status = mkt.get("market_odds"), pred.get("hit_probability"), sel.get("recommendation_status")
        version = (r.get("provenance") or {}).get("model_version")
        if elig.get("qc_status") != "kept":
            why["qc_not_kept"] += 1
        elif elig.get("lineup_assumed"):
            why["lineup_assumed"] += 1
        elif odds is None or p0 is None:
            why["no_posted_price_or_probability"] += 1
        elif status is None:
            why["no_recommendation_status"] += 1
        elif version not in model_versions:
            why["model_version_not_frozen"] += 1
        elif not price_band[0] <= implied(odds) <= price_band[1]:
            why["price_outside_band_champion" if status == "top_pick" else "price_outside_band"] += 1
        else:
            g = grades.get(r["candidate_id"]) or {}
            rows.append(score({"id": r["candidate_id"], "date": board["date"], "game_pk": str(r.get("game_pk")),
                               "player_id": str(r.get("player_id")), "family": family(r.get("stat")),
                               "p0": float(p0), "odds": float(odds), "q": implied(odds),
                               "champion": status == "top_pick", "y": y_of(g.get("grade"))}, coef))
    return rows, dict(why)


# ---- equal-volume selection ---------------------------------------------------------
SELECTORS = {
    "C2_RESIDUAL": lambda r: r["resid"],         # model information beyond price
    "C1_CALIBRATED_EDGE": lambda r: r["cal_edge"],
    "C0_MARKET": lambda r: r["p1"],               # most likely by price alone
    "RAW_MODEL": lambda r: r["p0"],
}


def select(rows):
    """Per slate: champion = production top_pick rows; each challenger takes the same N_d by
    its key (ties broken by candidate id). Selection uses no outcome."""
    n = sum(1 for r in rows if r["champion"])
    out = {"CHAMPION": [r for r in rows if r["champion"]]}
    for name, key in SELECTORS.items():
        out[name] = sorted(rows, key=lambda r: (-key(r), r["id"]))[:n]
    return n, out


# ---- metrics ------------------------------------------------------------------------
def profit(odds, y):
    return (odds / 100.0 if odds > 0 else 100.0 / (-odds)) if y else -1.0


def ll(p, y):
    p = min(max(p, CLIP), 1 - CLIP)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def arm_summary(picks):
    s = [r for r in picks if r["y"] is not None]
    n = len(s)
    if not n:
        return {"n_selected": len(picks), "n_scored": 0}
    h = sum(r["y"] for r in s)
    return {"n_selected": len(picks), "n_scored": n, "wins": h, "hit_rate": h / n,
            "mean_q": sum(r["q"] for r in s) / n, "hit_minus_q": h / n - sum(r["q"] for r in s) / n,
            "roi": sum(profit(r["odds"], r["y"]) for r in s) / n,
            "families": dict(Counter(r["family"] for r in s)),
            "max_picks_one_game": max(Counter((r["date"], r["game_pk"]) for r in s).values())}


def clustered_diff(champ, chall, unit, B=BOOT_B, seed=BOOT_SEED):
    """Bootstrap the hit-rate difference (challenger - champion) resampling clusters."""
    key = (lambda r: (r["date"], r["game_pk"])) if unit == "game" else (lambda r: r["player_id"])
    cl = defaultdict(lambda: [[], []])
    for r in champ:
        if r["y"] is not None:
            cl[key(r)][0].append(r["y"])
    for r in chall:
        if r["y"] is not None:
            cl[key(r)][1].append(r["y"])
    ks = sorted(cl)
    rng, draws = random.Random(seed), []
    for _ in range(B):
        a = b = na = nb = 0
        for _k in ks:
            c = cl[ks[rng.randrange(len(ks))]]
            a, na, b, nb = a + sum(c[0]), na + len(c[0]), b + sum(c[1]), nb + len(c[1])
        if na and nb:
            draws.append(b / nb - a / na)
    draws.sort()
    pick = lambda q: draws[min(len(draws) - 1, int(q * len(draws)))]
    return {"n_clusters": len(ks), "ci95": [pick(0.025), pick(0.975)], "one_sided_lower95": pick(0.05)}


def overlap_table(champ, chall):
    ci, hi = {r["id"] for r in champ}, {r["id"] for r in chall}
    part = lambda rs: arm_summary(rs)
    return {"overlap": len(ci & hi),
            "champion_only": part([r for r in champ if r["id"] not in hi]),
            "challenger_only": part([r for r in chall if r["id"] not in ci])}


def verdict(champ_sum, chall_sum, game_ci, n_slates):
    if champ_sum.get("n_scored", 0) < MIN_CHAMPION_PICKS or n_slates < MIN_SLATES:
        return "INSUFFICIENT_N"
    diff = chall_sum["hit_rate"] - champ_sum["hit_rate"]
    if diff <= 0:
        return "REJECTED"
    chalk_ok = chall_sum["mean_q"] - champ_sum["mean_q"] <= CHALK_GUARD
    if game_ci["one_sided_lower95"] > 0 and chalk_ok:
        return "SUPPORTED"
    return "INCONCLUSIVE" if chalk_ok else "INCONCLUSIVE_CHALK_GUARD"


def c3_pitcher_outs(rows, B=BOOT_B, seed=BOOT_SEED):
    rs = [r for r in rows if r["family"] == "pitcher_outs" and r["y"] is not None]
    if not rs:
        return {"n": 0, "verdict": "INSUFFICIENT_N"}
    cl = defaultdict(list)
    for r in rs:
        cl[(r["date"], r["game_pk"])].append(ll(r["p2"], r["y"]) - ll(r["p1"], r["y"]))
    ks = sorted(cl)
    mean = sum(sum(v) for v in cl.values()) / len(rs)
    rng, draws = random.Random(seed), []
    for _ in range(B):
        s = n = 0
        for _k in ks:
            c = cl[ks[rng.randrange(len(ks))]]
            s, n = s + sum(c), n + len(c)
        draws.append(s / n)
    draws.sort()
    pick = lambda q: draws[min(len(draws) - 1, int(q * len(draws)))]
    lo, hi = pick(0.025), pick(0.975)
    v = "INSUFFICIENT_N" if len(rs) < MIN_C3_ROWS else ("SUPPORTED" if hi < 0 else ("REJECTED" if mean >= 0 else "INCONCLUSIVE"))
    return {"n": len(rs), "n_games": len(ks), "mean_ll_p2_minus_p1": mean, "ci95": [lo, hi], "verdict": v}


def _utc(stamp):
    """Parse an ISO-8601 timestamp ('Z' or offset) to an aware UTC datetime; naive is refused."""
    from datetime import datetime, timezone
    t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {stamp}")
    return t.astimezone(timezone.utc)


def evaluate(slates, coef, *, boundary_utc, regime):
    """slates: list of (board, graded). Applies the leakage guards, builds the universe,
    selects at equal volume, and returns the full decision table."""
    if regime not in ("CONFIRMATORY_2027_REGULAR", "POSTSEASON_2026_SHADOW", "SMOKE_TEST_SYNTHETIC"):
        raise ValueError("unknown evidence regime")
    picks, all_rows, audit, n_slates = defaultdict(list), [], {}, 0
    for board, graded in slates:
        d = board["date"]
        if DEV_WINDOW[0] <= d <= DEV_WINDOW[1]:
            raise ValueError(f"{d}: inside the development window; refused")
        if regime != "SMOKE_TEST_SYNTHETIC" and not _utc(board["sealed_at"]) > _utc(boundary_utc):
            raise ValueError(f"{d}: board sealed {board['sealed_at']} not after boundary {boundary_utc}; refused")
        rows, why = universe(board, graded, coef)
        audit[d] = {"eligible": len(rows), "excluded": why}
        n, sel = select(rows)
        if n:
            n_slates += 1
        for k, v in sel.items():
            picks[k] += v
        all_rows += rows
    champ = picks["CHAMPION"]
    out = {"regime": regime, "boundary_utc": boundary_utc, "n_slates_with_champion": n_slates,
           "n_slates": len(slates), "slate_audit": audit, "arms": {}}
    cs = arm_summary(champ)
    out["arms"]["CHAMPION"] = cs
    for name in (PRIMARY, *SECONDARY):
        s = arm_summary(picks[name])
        s["vs_champion_game"] = clustered_diff(champ, picks[name], "game")
        s["vs_champion_player"] = clustered_diff(champ, picks[name], "player")
        s["overlap"] = overlap_table(champ, picks[name])
        out["arms"][name] = s
    if cs.get("n_scored"):
        out["primary_verdict"] = verdict(cs, out["arms"][PRIMARY], out["arms"][PRIMARY]["vs_champion_game"], n_slates)
    else:
        out["primary_verdict"] = "INSUFFICIENT_N"
    scored = [r for r in all_rows if r["y"] is not None]
    out["proper_scores"] = {a: {"log_loss": sum(ll(r[a], r["y"]) for r in scored) / len(scored),
                                "brier": sum((r[a] - r["y"]) ** 2 for r in scored) / len(scored)}
                            for a in ("p0", "q", "p1", "pcal", "p2")} if scored else {}
    out["c3_pitcher_outs"] = c3_pitcher_outs(all_rows)
    out["supply"] = {"eligible_rows": len(all_rows), "scored_rows": len(scored),
                     "champion_picks_per_slate": (len(champ) / n_slates) if n_slates else 0.0,
                     "slates_with_zero_champion_picks": len(slates) - n_slates}
    if regime == "POSTSEASON_2026_SHADOW":
        out["primary_verdict"] = "NOT_APPLICABLE_POSTSEASON_DESCRIPTIVE"
    return out
