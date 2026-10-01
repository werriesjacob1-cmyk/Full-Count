#!/usr/bin/env python3
"""Final evaluation for prereg v3 -- sections 11-15. Research only.

COLLECTION (runner.py) never reads outcomes. FINAL EVALUATION happens through
exactly one entry point, `evaluate_from_evidence(evidence_root, regime)`:
  1. one-look guard: refuses before the preregistered analysis condition, and
     refuses if a FINAL_ANALYSIS lock for the regime already exists;
  2. mandatory evidence verification (verify_evidence.load_chain/verify_unit):
     anchored chain, artifact hashes, manifest rebuilt from the sealed capture,
     sealed overlay, GitHub receipt re-fetched, TSA tokens verified from bytes,
     shadow board reproduced by replay. Unverifiable -> rejected;
  3. the lock is written and published BEFORE any outcome is opened;
  4. outcomes come only from the pinned grader run on the sealed shadow boards
     (no production graded file is ever opened);
  5. equal-volume comparison and the locked verdict table.
There is no caller-supplied receipt, timestamp, `verified` flag or optional chain.
The frozen coefficients and v1 selector are reused unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import harness as H  # noqa: E402  (v1: frozen arms, selector, C3)
import harness_v2 as H2  # noqa: E402  (probability quality, clustered diff incl. week)
import manifest_v3 as M3  # noqa: E402
import regimes as RG  # noqa: E402
import shadow as SH  # noqa: E402
import verify_evidence as VE  # noqa: E402

V3_BOUNDARY_UTC = "2026-10-02T06:00:00Z"   # slates cut off at/before this are NONCONFIRMATORY / DRILL ONLY
PRACTICAL_DELTA = 0.05     # minimum practically meaningful hit-rate gain (C2 - shadow champion)
MIN_CHAMPION_SETTLED = 250  # coverage floors, NOT a power guarantee
MIN_SLATES = 60
CHALK_GUARD = 0.03
GRADE_CLASS = {"hit": "settled", "miss": "settled", "void": "void", "push": "push"}
# Prereg s13: "One analysis runs after the 2027 regular season, on or after 2027-10-05, once every
# covered unit is graded or 7 days have passed." Enforced at the strictest reading consistent with the
# text: not before 2027-10-05; and unless every covered game is terminal, not before BOTH 2027-10-12
# and 7 days after the last covered unit. s11: postseason "reported once after the Series" -> not
# before the day after the regime window.
ANALYSIS_RULES = {"2027_REGULAR_CONFIRMATORY": {"earliest": date(2027, 10, 5), "grace_floor": date(2027, 10, 12)},
                  "2026_POSTSEASON_SHADOW": {"earliest": date(2026, 11, 16), "grace_floor": date(2026, 11, 16)}}
TERMINAL_GAME_STATES = ("Final", "Game Over", "Completed Early", "Postponed", "Cancelled", "Suspended")


class OneLookRefused(Exception):
    pass


# ---- module-level effects (the only external reads/writes of final evaluation) ----------------
def utc_now():
    return datetime.now(timezone.utc)


def fetch_game_states(game_pks):
    """statsapi game status only (not outcomes): {game_pk: detailedState}."""
    import requests
    out = {}
    for gp in sorted(set(game_pks)):
        r = requests.get("https://statsapi.mlb.com/api/v1/schedule", params={"gamePk": gp}, timeout=30)
        r.raise_for_status()
        games = [g for d in r.json().get("dates") or [] for g in d.get("games") or []]
        out[str(gp)] = (games[0].get("status") or {}).get("detailedState") if games else None
    return out


def publish_lock(evidence_root, lock):
    """Durable one-look lock: committed + pushed to the append-only evidence ref before outcomes."""
    path = os.path.join(evidence_root, f"FINAL_ANALYSIS_{lock['regime']}.json")
    with open(path, "w") as fh:
        json.dump(lock, fh, indent=1, sort_keys=True)
    for cmd in (["add", path], ["commit", "-m", f"MLB v3 FINAL ANALYSIS LOCK {lock['regime']}"],
                ["push", "origin", "HEAD:claude/mlb-challenger-v3-evidence"]):
        subprocess.run(["git", "-C", evidence_root, *cmd], check=True)


def grade_shadow_board(board):
    """Outcomes ONLY via the pinned grader (board_freeze_grader at the shadow pin) on the sealed board."""
    repo = VE._repo_root()
    work = tempfile.mkdtemp(prefix="v3grade_")
    tree = os.path.join(work, "tree")
    try:
        subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", tree, SH.SHADOW_PIN], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", tree, "sparse-checkout", "disable"], check=True, capture_output=True)
        bp = os.path.join(work, "board.json")
        json.dump(board, open(bp, "w"))
        out = subprocess.check_output(["python3", "-c", "import json,sys,board_freeze_grader as g;"
                                       "print(json.dumps(g.grade_frozen_board(json.load(open(sys.argv[1])))))", bp],
                                      cwd=tree, env=dict(os.environ, TZ="UTC"))
        return json.loads(out)
    finally:
        SH.remove_tree(repo, tree)


# ---- one-look guard ----------------------------------------------------------------------------
def one_look_check(regime, evidence_root, unit_dates, covered_games, now):
    if os.path.exists(os.path.join(evidence_root, f"FINAL_ANALYSIS_{regime}.json")):
        raise OneLookRefused("ONE_LOOK_ALREADY_TAKEN")
    rule = ANALYSIS_RULES.get(regime)
    if rule is None:
        raise OneLookRefused(f"no final analysis defined for {regime}")
    today = now.date()
    if today < rule["earliest"]:
        raise OneLookRefused(f"BEFORE_ANALYSIS_DATE {rule['earliest']}")
    grace = max([rule["grace_floor"]] + [date.fromisoformat(d) + timedelta(days=7) for d in unit_dates])
    if today >= grace:
        return "SEVEN_DAY_GRACE_ELAPSED"
    states = fetch_game_states(covered_games)
    if covered_games and all(states.get(str(g)) in TERMINAL_GAME_STATES for g in covered_games):
        return "ALL_COVERED_GAMES_TERMINAL"
    raise OneLookRefused(f"WAITING_FOR_GRADING_OR_GRACE until {grace}")


# ---- statistics (pure) -------------------------------------------------------------------------
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


def _compute(pairs, coef, spec, regime):
    """pairs: [(verified manifest, pinned-grader output)] for one regime."""
    picks, prim_all, audit, n_champ = defaultdict(list), [], {}, 0
    for m, g in pairs:
        rows = _rows(m, g, coef)
        eligible_ids = {r["id"] for r in rows}
        n, sel = H.select(rows)
        for arm, v in sel.items():           # every arm draws only from the same sealed eligible ids
            if not {r["id"] for r in v} <= eligible_ids:
                raise AssertionError(f"{arm} selected outside the sealed eligible universe")
            picks[arm] += v
        n_champ += bool(n)
        prim_all += rows
        audit[f"{m['date']}/{m['window']}"] = {"manifest_sha256": m["manifest_sha256"], "n_d": n,
                                               "eligible_primary": len(rows)}
    out = {"regime": regime, "confirmatory": spec["confirmatory"], "n_slates_used": len(pairs),
           "n_slates_with_champion": n_champ, "zero_champion_slates": len(pairs) - n_champ,
           "slate_audit": audit, "picking": {}}
    champ = picks["CHAMPION"]
    cs = H2.picking_summary(champ, len(pairs))
    cs["counts"] = arm_counts(champ)
    out["picking"]["SHADOW_CHAMPION"] = cs
    for name in (H.PRIMARY, *H.SECONDARY):
        s = H2.picking_summary(picks[name], len(pairs))
        s["counts"] = arm_counts(picks[name])
        s["vs_champion"] = {u: H2.clustered_diff(champ, picks[name], u) for u in ("game", "player", "week")}
        s["overlap"] = H.overlap_table(champ, picks[name])
        out["picking"][name] = s
    prim = out["picking"][H.PRIMARY]
    out["primary_verdict"] = (verdict_v3(cs, prim, prim["vs_champion"]["game"], n_champ) if cs.get("n_scored")
                              else "INSUFFICIENT_N")
    out["dependence_sensitivity"] = {u + "_lb_positive": (prim["vs_champion"][u]["one_sided_lower95"] or 0) > 0
                                     for u in ("player", "week")}
    if not spec["confirmatory"]:
        out["primary_verdict"] = "NOT_APPLICABLE_DESCRIPTIVE_REGIME"
    out["probability_quality"] = H2.probability_quality(prim_all)
    out["c3_pitcher_outs_secondary"] = dict(H.c3_pitcher_outs(prim_all), role="SECONDARY_NO_PROMOTION_ON_ITS_OWN")
    return out


# ---- the single final-evaluation entry point -------------------------------------------------
def evaluate_from_evidence(evidence_root, regime, coef=None):
    spec = RG.REGIMES.get(regime)
    if spec is None or regime == "SMOKE_TEST_SYNTHETIC":
        raise ValueError(f"unknown or non-evidence regime {regime!r}")
    coef = coef or H.load_coefficients()
    seals = VE.load_chain(evidence_root)                       # mandatory; raises on any break
    in_regime = [s for s in seals if spec["first"] <= s["date"] <= spec["last"]]
    now = utc_now()
    trigger = one_look_check(regime, evidence_root, [s["date"] for s in in_regime],
                             sorted({g for s in in_regime for g in s["covered_games"]}), now)
    invalid, verified = {}, []
    for s in in_regime:
        unit = f"{s['date']}/{s['window']}"
        if not M3.utc(s["cutoff_utc"]) > M3.utc(V3_BOUNDARY_UTC):
            invalid[unit] = "PRE_V3_BOUNDARY_DRILL_ONLY"
            continue
        try:
            status, detail, man = VE.verify_unit(evidence_root, s)
        except VE.EvidenceUnverified as exc:
            invalid[unit] = f"CONFIRMATORY_REJECT_EVIDENCE_UNVERIFIED: {exc}"
            continue
        if status != "VERIFIED":
            invalid[unit] = f"SLATE_INVALID_NO_CONFIRMATORY_USE:{status}"
            continue
        verified.append((s, man))
    RG.check_regime(regime, [m for _, m in verified])          # gameType + calendar, no override
    publish_lock(evidence_root, {"regime": regime, "trigger": trigger, "locked_at": now.isoformat(),
                                 "units_verified": sorted(f"{s['date']}/{s['window']}" for s, _ in verified),
                                 "units_invalid": invalid})
    pairs = []
    for s, man in verified:                                    # outcomes opened only now, only via the pin
        board = VE._load(os.path.join(evidence_root, "seals", f"{s['date']}_{s['window']}"), "shadow_board.json.gz")
        pairs.append((man, grade_shadow_board(board)))
    out = _compute(pairs, coef, spec, regime)
    out.update({"invalid_slates": invalid, "one_look_trigger": trigger})
    return out
