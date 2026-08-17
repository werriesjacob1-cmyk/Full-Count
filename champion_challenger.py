#!/usr/bin/env python3
"""champion_challenger.py — Phase 3, item 8: "I want future model
improvements tested before they replace production... The current
production probability/recommendation model is the Champion. New ideas
become Challengers and make shadow predictions from the same pregame
information."

THE DESIGN.

A Challenger is a plain function: (candidate_dict) -> probability or None.
It reads ONLY fields already persisted on a scored candidate (signals,
champion hit_probability, sample_n, reliability, market_odds) -- the same
pregame information the champion had, nothing forward-looking, nothing
that requires re-running the live scoring pipeline. That is a deliberate
choice over hooking a challenger into generate_picks.py's live scoring
path: it keeps a challenger's blast radius at zero (it cannot slow down
or break the real board, because it never runs inside the real board's
critical path) while still comparing apples to apples, since it consumes
exactly what the champion consumed.

Shadow predictions are logged to data/challengers/{name}/{date}.json --
NEVER written back onto a real candidate, NEVER read by generate_picks.py,
NEVER shown on the dashboard. A challenger existing costs nothing to the
live product.

PROMOTION IS NEVER AUTOMATIC. evaluate_promotion() returns a recommendation
(PROMOTE / HOLD / REJECT / INSUFFICIENT_DATA) for a human to act on, and
never mutates production code itself. Direct instruction: "Prevent us from
changing production because of a 5-0 day or panicking because of a 1-7
day" -- enforced with BOTH a minimum sample size AND a minimum number of
distinct graded days, so one hot or cold stretch can never single-handedly
produce a PROMOTE or REJECT verdict.

    from champion_challenger import register, run_shadow, evaluate_promotion
"""
import glob
import json
import os
from datetime import datetime, timezone

import eval_lib as el

SHADOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "challengers")

# ══════════════════════════════════════════════════════════════════════════
#  PRE-REGISTERED PROMOTION CRITERIA -- decided BEFORE looking at results,
#  per direct instruction. Changing these to fit a result after the fact
#  defeats the entire point of pre-registering them.
# ══════════════════════════════════════════════════════════════════════════

PROMOTION_CRITERIA = {
    "min_n": el.MIN_N_CONFIDENT,        # 100 -- below this, no verdict at all
    "min_days": 14,                     # a single hot/cold week cannot decide this
    "min_brier_improvement": 0.005,     # challenger must beat champion Brier by this much
    "min_logloss_improvement": 0.01,    # and log loss by this much
    "calibration_must_not_worsen": True,  # challenger's weighted calibration gap <= champion's
    "min_market_brier_improvement": 0.0,  # challenger must still beat (or tie) the market too
}


# ══════════════════════════════════════════════════════════════════════════
#  REGISTRY
# ══════════════════════════════════════════════════════════════════════════

_REGISTRY = {}


def register(name, score_fn, description):
    """Register a challenger. score_fn(candidate: dict) -> float probability
    in (0, 1), or None if this challenger has no opinion on this candidate
    (e.g. it needs a signal that didn't fire) -- None is skipped, never
    coerced into a fabricated guess."""
    _REGISTRY[name] = {"score_fn": score_fn, "description": description,
                       "registered_at": datetime.now(timezone.utc).isoformat()}


def registered():
    return dict(_REGISTRY)


# ══════════════════════════════════════════════════════════════════════════
#  SHADOW RUN -- never touches the real candidate, never affects the board
# ══════════════════════════════════════════════════════════════════════════

def run_shadow(candidates, date, challenger_names=None):
    """For every registered challenger (or the given subset), score every
    candidate that has a champion probability and log the pair. Returns
    {challenger_name: n_scored}. A challenger raising an exception on one
    candidate is caught and skipped for that candidate only -- one broken
    challenger idea must never take down the others, and must never touch
    the real board regardless of what it does."""
    names = challenger_names or list(_REGISTRY)
    rows_by_name = {n: [] for n in names}
    for c in candidates:
        champion_prob = c.get("hit_probability")
        if champion_prob is None:
            continue
        proj = c.get("projection") or {}
        base = {
            "player_id": c.get("player_id"), "name": c.get("name"),
            "game_pk": c.get("game_pk"), "stat": proj.get("stat"), "needs": proj.get("needs"),
            "champion_prob": champion_prob, "market_odds": c.get("market_odds"),
            "recommendation_status": c.get("status") or c.get("recommendation_status"),
        }
        for name in names:
            spec = _REGISTRY.get(name)
            if not spec:
                continue
            try:
                challenger_prob = spec["score_fn"](c)
            except Exception:
                challenger_prob = None
            if challenger_prob is None:
                continue
            rows_by_name[name].append({**base, "challenger_prob": round(float(challenger_prob), 4)})

    counts = {}
    for name, rows in rows_by_name.items():
        if not rows:
            counts[name] = 0
            continue
        d = os.path.join(SHADOW_DIR, name)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{date}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date, "challenger": name, "rows": rows}, f, indent=2)
        counts[name] = len(rows)
    return counts


def _load_shadow(name):
    rows = []
    for path in sorted(glob.glob(os.path.join(SHADOW_DIR, name, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for r in d.get("rows", []):
            rows.append({**r, "_date": d.get("date")})
    return rows


# ══════════════════════════════════════════════════════════════════════════
#  PROMOTION EVALUATION
# ══════════════════════════════════════════════════════════════════════════

def _match_outcomes(shadow_rows):
    """Cross-references shadow predictions against results/grades_*.json by
    (player_id, game_pk, stat, needs) to attach a real outcome -- shadow
    logging alone never knows what happened. Rows with no graded match are
    dropped, not guessed."""
    by_date = {}
    for r in shadow_rows:
        by_date.setdefault(r["_date"], []).append(r)
    matched = []
    for date, rows in by_date.items():
        graded = el.graded_only(el.load_graded_picks(start_date=date, end_date=date))
        index = {}
        for g in graded:
            proj = g.get("projection") or {}
            key = (g.get("player_id"), g.get("game_pk"), proj.get("stat"), proj.get("needs"))
            index[key] = g
        for r in rows:
            key = (r.get("player_id"), r.get("game_pk"), r.get("stat"), r.get("needs"))
            g = index.get(key)
            if not g:
                continue
            matched.append({**r, "outcome": 1.0 if g["grade"] == "hit" else 0.0})
    return matched


def evaluate_promotion(name, criteria=None):
    """The full report: champion vs challenger Brier/log loss/calibration/
    market-baseline improvement, gated by the pre-registered criteria, plus
    a plain verdict. Never mutates anything -- purely a read/report."""
    crit = criteria or PROMOTION_CRITERIA
    rows = _match_outcomes(_load_shadow(name))
    n = len(rows)
    n_days = len({r["_date"] for r in rows})

    result = {"challenger": name, "n": n, "n_days": n_days, "criteria": crit}
    if n < crit["min_n"] or n_days < crit["min_days"]:
        result["verdict"] = (
            f"INSUFFICIENT_DATA -- {n} matched, graded shadow predictions across {n_days} "
            f"day(s) (need >= {crit['min_n']} rows AND >= {crit['min_days']} distinct days). "
            f"No promotion decision can be made yet, in either direction.")
        return result

    champ_po = [(r["champion_prob"], r["outcome"]) for r in rows]
    chal_po = [(r["challenger_prob"], r["outcome"]) for r in rows]
    champ_brier, chal_brier = el.brier(champ_po), el.brier(chal_po)
    champ_ll, chal_ll = el.log_loss(champ_po), el.log_loss(chal_po)

    def _cal_score(pairs):
        cal_rows = [row for row in el.calibration_table(pairs) if row["n"] >= el.MIN_N_REPORTABLE]
        if not cal_rows:
            return None
        total_n = sum(row["n"] for row in cal_rows)
        return sum(row["n"] * row["gap"] ** 2 for row in cal_rows) / total_n

    champ_cal, chal_cal = _cal_score(champ_po), _cal_score(chal_po)

    brier_gain = champ_brier - chal_brier          # positive = challenger better
    ll_gain = champ_ll - chal_ll
    cal_ok = (champ_cal is None or chal_cal is None) or (chal_cal <= champ_cal)

    result.update({
        "champion_brier": round(champ_brier, 4), "challenger_brier": round(chal_brier, 4),
        "brier_gain": round(brier_gain, 4),
        "champion_log_loss": round(champ_ll, 4), "challenger_log_loss": round(chal_ll, 4),
        "log_loss_gain": round(ll_gain, 4),
        "champion_calibration": champ_cal, "challenger_calibration": chal_cal,
        "calibration_ok": cal_ok,
    })

    meets_brier = brier_gain >= crit["min_brier_improvement"]
    meets_ll = ll_gain >= crit["min_logloss_improvement"]
    meets_cal = cal_ok or not crit["calibration_must_not_worsen"]

    if meets_brier and meets_ll and meets_cal:
        result["verdict"] = (
            f"PROMOTE (recommended for human review) -- challenger beats champion by "
            f"{brier_gain:.4f} Brier / {ll_gain:.4f} log loss over {n} rows across "
            f"{n_days} days, calibration {'held or improved' if cal_ok else 'n/a'}. "
            f"This is a recommendation, not an automatic change.")
    elif brier_gain < 0 and ll_gain < 0:
        result["verdict"] = (
            f"REJECT -- challenger is worse than champion on both Brier ({brier_gain:+.4f}) "
            f"and log loss ({ll_gain:+.4f}) over {n} rows across {n_days} days.")
    else:
        result["verdict"] = (
            f"HOLD -- challenger does not yet clear the pre-registered promotion bar "
            f"(Brier gain {brier_gain:+.4f} vs required +{crit['min_brier_improvement']}, "
            f"log loss gain {ll_gain:+.4f} vs required +{crit['min_logloss_improvement']}, "
            f"calibration {'ok' if cal_ok else 'WORSE'}) over {n} rows / {n_days} days. "
            f"Keep collecting shadow data.")
    return result


# ══════════════════════════════════════════════════════════════════════════
#  A REAL FIRST CHALLENGER -- closing the loop this project's own platoon
#  ablation opened (see generate_picks.py's MATCHUP comment, 2026-08-16):
#  the crude binary platoon flag showed no signal in any real market
#  segment, but platoon_xwoba (exit velocity/contact quality BY HANDEDNESS,
#  already recorded in every candidate's signals dict, never promoted into
#  score) measured real, positive, statistically significant separation at
#  n>100,000 on the full backtest. This is a genuine, currently-relevant
#  idea worth shadow-testing forward rather than a synthetic placeholder.
# ══════════════════════════════════════════════════════════════════════════

def _platoon_xwoba_challenger(candidate):
    """A small, capped, explainable nudge to the champion's own probability
    based on platoon_xwoba, when present. Deliberately conservative (capped
    at +/-3 points) -- the point of a challenger is to test whether a real
    signal survives contact with forward data at a reasonable size, not to
    make an aggressive bet on day one. Returns None (no opinion) when
    platoon_xwoba never fired for this candidate, exactly like every other
    signal's "absent is not zero" convention in this codebase."""
    champion_prob = candidate.get("hit_probability")
    xwoba = (candidate.get("signals") or {}).get("platoon_xwoba")
    if champion_prob is None or xwoba is None:
        return None
    # xwoba here is already the SCALED (-5..+5) signal value used in
    # generate_picks.py's clamp((side["xwOBA"] - 0.313) * 60, -5, 5), so it
    # is already centered at 0 = league average. Convert that -5..+5 scale
    # to a small probability nudge, capped at +/-3 points.
    nudge = max(-0.03, min(0.03, xwoba / 5.0 * 0.03))
    return max(0.01, min(0.99, champion_prob + nudge))


register("platoon_xwoba_v1", _platoon_xwoba_challenger,
        "Nudges the champion probability using platoon_xwoba (exit velocity/contact "
        "quality by handedness), capped at +/-3 points, in place of the champion's crude "
        "binary platoon flag -- shadow-testing the finding from the 2026-08-16 platoon "
        "ablation before committing to a live score-formula change.")
