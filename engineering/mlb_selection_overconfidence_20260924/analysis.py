#!/usr/bin/env python3
"""MLB Top Pick selection-overconfidence diagnostic (Mission 12, Workstream D).

Implements PREREGISTRATION.md (committed alone, before this file existed).
Research-only: reads committed artifacts at a pinned commit via `git show`
and writes a small report.json next to this file. Writes nothing else.

Usage (from anywhere inside the repository):
    python3 engineering/mlb_selection_overconfidence_20260924/analysis.py
    python3 engineering/mlb_selection_overconfidence_20260924/analysis.py --data-sha <sha>

Pure estimand functions (band_of, gap, assign_cells, decompose,
cluster_bootstrap, joint_decompose_bootstrap) have no repository I/O and are
exercised on synthetic fixtures by test_analysis.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
from collections import Counter, defaultdict

DATA_SHA = "3890c23a15b17fae29407d01489350f97c570d84"
MISSION11_SHA = "8acd197448d549e15a53527bcffeaf8b5a34d2eb"  # located by scanning docs/history.json history
SEED = 20260924
B = 2000
MIN_REF = 15
BAND_EDGES = (0.0, 0.30, 0.45, 0.60, 0.65, 0.70, 0.75, 1.0000001)
HIGH_BANDS = ("[0.60,0.65)", "[0.65,0.70)", "[0.70,0.75)", "[0.75,1.00]")
SPLIT_DATE = "2026-09-08"
HERE = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────── pure estimand code ───────────────────────────

def band_of(p):
    """Pre-registered probability band label for predicted p."""
    if p is None:
        return None
    for lo, hi in zip(BAND_EDGES[:-1], BAND_EDGES[1:]):
        if lo <= p < hi:
            if hi > 1.0:
                return f"[{lo:.2f},1.00]"
            return f"[{lo:.2f},{hi:.2f})"
    return None


def is_high(p):
    return p is not None and p >= 0.60


def settled(rows):
    return [r for r in rows if r.get("y") is not None]


def gap(rows):
    """mean(y - p) over settled rows; None when empty."""
    s = settled(rows)
    if not s:
        return None
    return sum(r["y"] - r["p"] for r in s) / len(s)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _cell_keys(r):
    """Fallback hierarchy of reference cells for one selected row."""
    return [("mb", r["market"], band_of(r["p"])),
            ("mh", r["market"], "p>=0.60"),
            ("ab", "*", band_of(r["p"]))]


def _ref_cells(reference):
    cells = defaultdict(list)
    for r in settled(reference):
        cells[("mb", r["market"], band_of(r["p"]))].append(r)
        if is_high(r["p"]):
            cells[("mh", r["market"], "p>=0.60")].append(r)
        cells[("ab", "*", band_of(r["p"]))].append(r)
    return cells


def assign_cells(selected, reference, min_ref=MIN_REF):
    """Pre-registered fallback: (market, band) -> (market, p>=0.60) ->
    (all markets, band) -> unmatched. Returns list of cell keys (or None)
    aligned with `selected`."""
    cells = _ref_cells(reference)
    out = []
    for r in selected:
        chosen = None
        for key in _cell_keys(r):
            if len(cells.get(key, ())) >= min_ref:
                chosen = key
                break
        out.append(chosen)
    return out


def decompose(selected, reference, assignment):
    """G = mean(y-p), W = mean(g_ref(cell)), S_sel = G - W over matched,
    settled selected rows. Cells are fixed by `assignment`; g_ref is
    computed from `reference` (so a bootstrap can pass resampled rows)."""
    cells = _ref_cells(reference)
    gref = {}
    ys, ws = [], []
    for r, key in zip(selected, assignment):
        if key is None or r.get("y") is None:
            continue
        if key not in gref:
            ref_rows = cells.get(key)
            if not ref_rows:
                return None
            gref[key] = sum(x["y"] - x["p"] for x in ref_rows) / len(ref_rows)
        ys.append(r["y"] - r["p"])
        ws.append(gref[key])
    if not ys:
        return None
    G = sum(ys) / len(ys)
    W = sum(ws) / len(ws)
    return {"n": len(ys), "G": G, "W": W, "S_sel": G - W}


def percentile(sorted_xs, q):
    if not sorted_xs:
        return None
    k = (len(sorted_xs) - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_xs[int(k)]
    return sorted_xs[f] + (sorted_xs[c] - sorted_xs[f]) * (k - f)


def _group(rows, cluster_key):
    groups = defaultdict(list)
    for r in rows:
        groups[cluster_key(r)].append(r)
    return groups


def cluster_bootstrap(rows, stat_fn, cluster_key, b=B, seed=SEED):
    """Percentile 95% CI of stat_fn(rows) resampling whole clusters."""
    groups = _group(rows, cluster_key)
    keys = sorted(groups, key=str)
    point = stat_fn(rows)
    if len(keys) < 2 or point is None:
        return {"point": point, "ci95": None, "n_clusters": len(keys),
                "note": "insufficient clusters" if len(keys) < 2 else "no data"}
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        sample = []
        for _k in range(len(keys)):
            sample.extend(groups[keys[rng.randrange(len(keys))]])
        v = stat_fn(sample)
        if v is not None:
            vals.append(v)
    vals.sort()
    return {"point": point, "ci95": [percentile(vals, 0.025), percentile(vals, 0.975)],
            "n_clusters": len(keys), "n_valid_resamples": len(vals)}


def joint_decompose_bootstrap(selected, reference, cluster_key, b=B, seed=SEED,
                              min_ref=MIN_REF):
    """Decomposition with clusters resampled JOINTLY from the union of the
    selected and reference sets; g_ref re-estimated in every resample; cell
    assignment fixed at the full-sample assignment (pre-registered)."""
    assignment = assign_cells(selected, reference, min_ref)
    point = decompose(selected, reference, assignment)
    tagged = ([("S", i, r) for i, r in enumerate(selected)] +
              [("R", i, r) for i, r in enumerate(reference)])
    groups = defaultdict(list)
    for t in tagged:
        groups[cluster_key(t[2])].append(t)
    keys = sorted(groups, key=str)
    levels = Counter(("unmatched" if a is None else a[0])
                     for r, a in zip(selected, assignment) if r.get("y") is not None)
    res = {"point": point, "n_clusters": len(keys),
           "assignment_levels_settled": dict(levels)}
    if point is None or len(keys) < 2:
        res["note"] = "insufficient"
        return res
    rng = random.Random(seed)
    draws = {"G": [], "W": [], "S_sel": [], "share_W": []}
    dropped = 0
    for _ in range(b):
        s_rows, s_asg, r_rows = [], [], []
        for _k in range(len(keys)):
            for role, i, r in groups[keys[rng.randrange(len(keys))]]:
                if role == "S":
                    s_rows.append(r)
                    s_asg.append(assignment[i])
                else:
                    r_rows.append(r)
        d = decompose(s_rows, r_rows, s_asg)
        if d is None:
            dropped += 1
            continue
        for k in ("G", "W", "S_sel"):
            draws[k].append(d[k])
        if abs(d["G"]) > 1e-9:
            draws["share_W"].append(d["W"] / d["G"])
    for k in draws:
        draws[k].sort()
    res["ci95"] = {k: [percentile(v, 0.025), percentile(v, 0.975)] for k, v in draws.items()}
    res["share_W_point"] = (point["W"] / point["G"]) if abs(point["G"]) > 1e-9 else None
    res["dropped_resamples"] = dropped
    return res


def ci_status(ci):
    if not ci or ci[0] is None:
        return "no_interval"
    if ci[1] < 0:
        return "below_0"
    if ci[0] > 0:
        return "above_0"
    return "straddles_0"


def decide(primary, db_matched, pub_settled, db_sel_settled, frac_level12):
    """Pre-registered decision rule (PREREGISTRATION.md 'Decision rule')."""
    out = {"minimums_met": pub_settled >= 200 and db_sel_settled >= 30,
           "frac_pub_matched_level_1_2": frac_level12,
           "coverage_ok": frac_level12 is not None and frac_level12 >= 0.70}
    if not out["minimums_met"]:
        out["verdict"] = "inconclusive (insufficient n)"
        return out
    s_ci = primary["ci95"]["S_sel"]
    w_ci = primary["ci95"]["W"]
    h2 = (ci_status(s_ci) == "below_0" and db_matched["point"] is not None
          and db_matched["point"]["S_sel"] < 0 and out["coverage_ok"])
    h2_strong = h2 and ci_status(db_matched["ci95"]["S_sel"]) == "below_0"
    h1 = ci_status(w_ci) == "below_0"
    out.update({
        "H2_supported": h2, "H2_strong": h2_strong, "H1_supported": h1,
        "S_sel_ci_status": ci_status(s_ci), "W_ci_status": ci_status(w_ci),
        "H2_over_H1": h2 and ci_status(w_ci) != "below_0",
        "H1_over_H2": h1 and ci_status(s_ci) != "below_0",
        "S_sel_demonstrated_absent_beyond_minus5pp": bool(s_ci and s_ci[0] is not None and s_ci[0] > -0.05),
        "W_demonstrated_absent_beyond_minus5pp": bool(w_ci and w_ci[0] is not None and w_ci[0] > -0.05),
    })
    if h2 and h1:
        v = "both H1 and H2 supported"
    elif out["H2_over_H1"]:
        v = "H2 over H1 (selection-induced)"
    elif out["H1_over_H2"]:
        v = "H1 over H2 (world-model error)"
    else:
        v = "inconclusive (neither mechanism demonstrated)"
    out["verdict"] = v
    return out


# ─────────────────────────────── data loading ───────────────────────────────

def _git(*args):
    return subprocess.check_output(["git", "-C", HERE, *args])


def git_json(sha, path):
    return json.loads(_git("show", f"{sha}:{path}"))


def git_ls(sha, prefix):
    return _git("ls-tree", "--name-only", sha, prefix + "/").decode().split()


def _y(grade):
    return 1 if grade == "hit" else 0 if grade == "miss" else None


def _implied(odds):
    if odds is None:
        return None
    return (-odds) / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def load_grades(sha):
    pub, db = [], []
    for path in sorted(git_ls(sha, "results")):
        base = os.path.basename(path)
        if not (base.startswith("grades_") and base.endswith(".json")):
            continue
        date = base[len("grades_"):-len(".json")]
        g = git_json(sha, path)
        for x in g.get("public_top_picks") or []:
            pub.append({
                "pop": "PUB", "id": x.get("id"), "date": date, "game_pk": str(x.get("game_pk")),
                "player_id": str(x.get("player_id")) if x.get("player_id") is not None else None,
                "market": x.get("stat") or (x.get("projection") or {}).get("stat"),
                "p": x.get("hit_probability"), "odds": x.get("market_odds"),
                "implied": x.get("market_implied"), "edge": x.get("market_edge"),
                "reliability": x.get("reliability"), "fair_test": x.get("fair_test"),
                "grade": x.get("grade"), "y": _y(x.get("grade")),
                "status": x.get("recommendation_status"),
                "published_at": x.get("published_top_pick_at"),
                "qualification_ts": x.get("qualification_timestamp"),
                "game_start": x.get("game_start"),
                "model_version": (x.get("versions") or {}).get("model_version"),
            })
        for x in g.get("picks") or []:
            if x.get("recommendation_status") is None or x.get("hit_probability") is None:
                continue
            from_id = x.get("player_id")
            db.append({
                "pop": "DB", "id": _db_id(x), "date": date, "game_pk": str(x.get("game_pk")),
                "player_id": str(from_id) if from_id is not None else None,
                "market": (x.get("projection") or {}).get("stat"),
                "p": x.get("hit_probability"), "odds": x.get("market_odds"),
                "implied": x.get("market_implied"), "edge": x.get("market_edge"),
                "reliability": x.get("reliability"), "fair_test": x.get("fair_test"),
                "lineup_assumed": bool(x.get("lineup_assumed")),
                "grade": x.get("grade"), "y": _y(x.get("grade")),
                "status": x.get("recommendation_status"), "rank": x.get("rank"),
                "prediction_ts": x.get("prediction_timestamp"),
                "model_version": x.get("model_version"),
            })
    return pub, db


def _db_id(x):
    """v2 canonical id via the production identity function itself
    (dashboard/live_state.canonical_prop_id, the scheme used by the registry
    and board_freeze.py). Only used to join DB to PUB/FB; a row it cannot
    identify simply does not join."""
    try:
        import sys
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from dashboard.live_state import canonical_prop_id
        return canonical_prop_id(x)
    except Exception:
        return None


def load_fb(sha):
    rows, meta = [], []
    names = git_ls(sha, "output")
    boards = sorted(n for n in names if os.path.basename(n).startswith("board_freeze_2"))
    for bpath in boards:
        date = os.path.basename(bpath)[len("board_freeze_"):-len(".json")]
        board = git_json(sha, bpath)
        gpath = f"output/board_freeze_graded_{date}.json"
        graded = git_json(sha, gpath) if gpath in names else None
        linked = bool(graded and graded.get("source_board_sha256") == board.get("board_sha256"))
        starts = [v for v in (board.get("game_start_times") or {}).values()]
        m = {"date": date, "record_count": board.get("record_count"),
             "board_generated_at": board.get("board_generated_at"),
             "sealed_at": board.get("sealed_at"),
             "earliest_first_pitch": min(starts) if starts else None,
             "sealed_before_first_pitch": (bool(starts) and _ts(board["sealed_at"]) < min(_ts(s) for s in starts)),
             "graded_file": graded is not None, "sha_linked": linked,
             "graded_at": graded.get("graded_at") if graded else None,
             "provenance": board.get("provenance")}
        src = graded["records"] if linked else board["records"]
        n_settled = 0
        for x in src:
            pred, mk, el, sel = x["prediction"], x["market"], x["eligibility"], x["selector"]
            y = _y(x.get("grade")) if linked else None
            n_settled += y is not None
            rows.append({
                "pop": "FB", "id": x["candidate_id"], "date": date, "game_pk": str(x.get("game_pk")),
                "player_id": x.get("player_id"), "market": x.get("stat"),
                "p": pred.get("hit_probability"), "odds": mk.get("market_odds"),
                "implied": mk.get("market_implied"), "edge": mk.get("market_edge"),
                "reliability": pred.get("reliability"),
                "fair_test": x.get("fair_test") if linked else None,
                "qc_status": el.get("qc_status"), "lineup_assumed": bool(el.get("lineup_assumed")),
                "status": sel.get("recommendation_status"), "final_rank": sel.get("final_rank"),
                "t10": bool(sel.get("selected_top_pick")),
                "grade": x.get("grade") if linked else None, "y": y,
            })
        m["n_settled"] = n_settled
        meta.append(m)
    return rows, meta


def _ts(s):
    from datetime import datetime, timezone
    s = s[:-1] + "+00:00" if s.endswith("Z") else s
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ─────────────────────────────── reporting ───────────────────────────────

def game_cluster(r):
    return (r["date"], r["game_pk"])


def player_cluster(r):
    return r.get("player_id") or r.get("game_pk") or r.get("id")


def r4(x):
    return None if x is None else round(x, 4)


def rci(ci):
    return None if ci is None else [r4(c) for c in ci]


def summarize(rows, name, boot=True):
    s = settled(rows)
    out = {"population": name, "n_predicted": len(rows), "n_settled": len(s),
           "n_clusters_game": len({game_cluster(r) for r in s}),
           "mean_p_settled": r4(mean(r["p"] for r in s)),
           "realized": r4(mean(r["y"] for r in s)),
           "gap": r4(gap(rows))}
    if s:
        imp = [r for r in s if r.get("implied") is not None]
        out["n_priced_settled"] = len(imp)
        out["mean_market_implied_priced"] = r4(mean(r["implied"] for r in imp))
        out["realized_priced"] = r4(mean(r["y"] for r in imp))
    if boot and len(s) >= 2:
        bs = cluster_bootstrap(rows, gap, game_cluster)
        out["gap_ci95_game"] = rci(bs["ci95"])
        # design effect: cluster SE vs iid SE (approx via CI width)
        n = len(s)
        pbar = out["realized"]
        iid_se = math.sqrt(max(pbar * (1 - pbar), 1e-9) / n)
        if bs["ci95"]:
            out["cluster_to_iid_se_ratio"] = r4(((bs["ci95"][1] - bs["ci95"][0]) / 3.92) / iid_se)
    return out


def missing_bounds(rows):
    n = len(rows)
    s = settled(rows)
    uns = [r for r in rows if r.get("y") is None]
    if not n:
        return None
    hits = sum(r["y"] for r in s)
    return {"n_predicted": n, "n_settled": len(s), "n_unsettled": len(uns),
            "unsettled_grades": dict(Counter(str(r.get("grade")) for r in uns)),
            "mean_p_settled": r4(mean(r["p"] for r in s)),
            "mean_p_unsettled": r4(mean(r["p"] for r in uns)),
            "realized_all_unsettled_miss": r4(hits / n),
            "realized_all_unsettled_hit": r4((hits + len(uns)) / n)}


def split_table(rows, keyfn, boot=False, min_n=1):
    groups = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None:
            groups[k].append(r)
    out = {}
    for k in sorted(groups, key=str):
        g = groups[k]
        s = settled(g)
        if len(s) < min_n:
            out[str(k)] = {"n_predicted": len(g), "n_settled": len(s), "note": f"n_settled<{min_n}"}
            continue
        e = {"n_predicted": len(g), "n_settled": len(s), "mean_p": r4(mean(r["p"] for r in s)),
             "realized": r4(mean(r["y"] for r in s)) if s else None, "gap": r4(gap(g))}
        imp = [r["implied"] for r in s if r.get("implied") is not None]
        if imp:
            e["mean_market_implied"] = r4(mean(imp))
        if boot and len(s) >= 5:
            bs = cluster_bootstrap(g, gap, game_cluster)
            e["gap_ci95_game"] = rci(bs["ci95"])
        out[str(k)] = e
    return out


def price_band(r):
    o = r.get("odds")
    if o is None:
        return None
    if o <= -200:
        return "<=-200"
    if o <= -150:
        return "(-200,-150]"
    if o <= -110:
        return "(-150,-110]"
    return ">-110"


def edge_band(r):
    e = r.get("edge")
    if e is None:
        return None
    return "<0.05" if e < 0.05 else "[0.05,0.10)" if e < 0.10 else ">=0.10"


def half(r):
    return "early(<=09-07)" if r["date"] < SPLIT_DATE else "late(>=09-08)"


def fmt_decomp(res):
    p = res.get("point")
    if not p:
        return res
    return {"n_matched_settled": p["n"], "G": r4(p["G"]), "W": r4(p["W"]), "S_sel": r4(p["S_sel"]),
            "ci95": {k: rci(v) for k, v in (res.get("ci95") or {}).items()},
            "share_W_point": r4(res.get("share_W_point")),
            "n_clusters": res.get("n_clusters"),
            "assignment_levels_settled": res.get("assignment_levels_settled"),
            "dropped_resamples": res.get("dropped_resamples")}


def verify_mission11(sha):
    h = git_json(sha, "docs/history.json")
    rows = [p for d in h["days"] for p in d["picks"]]
    st = [p for p in rows if p.get("grade") in ("hit", "miss")]
    dates = sorted(d["date"] for d in h["days"])
    return {"commit": sha, "history_generated_at": h.get("generated_at"),
            "n_rows": len(rows), "n_settled": len(st), "date_min": dates[0], "date_max": dates[-1],
            "n_slate_dates": len(dates),
            "mean_p_all": r4(mean(p["hit_probability"] for p in rows)),
            "mean_p_settled": r4(mean(p["hit_probability"] for p in st)),
            "realized_settled": r4(mean(1 if p["grade"] == "hit" else 0 for p in st)),
            "unsettled_states": dict(Counter(str(p.get("settlement_state")) for p in rows
                                             if p.get("grade") not in ("hit", "miss"))),
            "market_mix": dict(Counter(p.get("stat") for p in rows)),
            "population": "docs/history.json days[].picks = graded public first-exposure Top Picks "
                          "(recommendation_status top_pick), one row per canonical id"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-sha", default=DATA_SHA)
    ap.add_argument("--out", default=os.path.join(HERE, "report.json"))
    args = ap.parse_args()
    sha = args.data_sha

    pub, db = load_grades(sha)
    fb, fb_meta = load_fb(sha)
    reg = git_json(sha, "data/public_top_picks/registry.json")["entries"]

    rep = {"prereg": "PREREGISTRATION.md", "data_sha": sha, "seed": SEED, "B": B,
           "min_ref": MIN_REF, "deviations": []}

    # ── Step 2: verify Mission 11 figure
    rep["mission11_verification"] = {
        "at_mission11_commit": verify_mission11(MISSION11_SHA),
        "at_data_sha": verify_mission11(sha)}

    # ── populations
    db_sel = [r for r in db if r["status"] == "top_pick"]
    db_ref = [r for r in db if r["status"] != "top_pick"]
    db_ref_elig = [r for r in db_ref if r["reliability"] in ("A", "B")
                   and not r["lineup_assumed"] and r["odds"] is not None]
    fb_u = [r for r in fb if r["p"] is not None]
    fb_e = [r for r in fb_u if r["qc_status"] == "kept" and not r["lineup_assumed"]
            and r["odds"] is not None]
    fb_s = [r for r in fb_e if r["status"] == "top_pick"]
    fb_ns_e = [r for r in fb_e if r["status"] != "top_pick"]
    fb_t10 = [r for r in fb_u if r["t10"]]

    pub_ids = [r["id"] for r in pub]
    rep["pub_integrity"] = {
        "n_rows": len(pub), "n_unique_ids": len(set(pub_ids)),
        "ids_not_in_registry": len(set(pub_ids) - set(reg)),
        "registry_ids_not_in_grades": len(set(reg) - set(pub_ids)),
        "p_mismatch_vs_registry_snapshot": sum(
            1 for r in pub if r["id"] in reg and
            abs((reg[r["id"]]["snapshot"].get("hit_probability") or -1) - (r["p"] or -2)) > 1e-9),
        "status_values": dict(Counter(r["status"] for r in pub)),
        "date_min": min(r["date"] for r in pub), "date_max": max(r["date"] for r in pub),
    }

    pops = {"PUB": pub, "DB": db, "DB_sel": db_sel, "DB_ref": db_ref, "DB_ref_elig": db_ref_elig,
            "DB_ref_p>=0.60": [r for r in db_ref if is_high(r["p"])],
            "FB_U": fb_u, "FB_E": fb_e, "FB_S": fb_s, "FB_E_nonselected": fb_ns_e,
            "FB_E_nonselected_p>=0.60": [r for r in fb_ns_e if is_high(r["p"])],
            "FB_T10": fb_t10}
    rep["populations"] = {k: summarize(v, k) for k, v in pops.items()}
    rep["missing_outcomes"] = {k: missing_bounds(v) for k, v in pops.items()}

    # fair_test sensitivity summaries
    rep["populations_fair_test_only"] = {
        k: summarize([r for r in v if r.get("fair_test") is True], k, boot=True)
        for k, v in pops.items() if any(r.get("fair_test") is not None for r in v)}

    # ── H1 / H2 decomposition
    pub_s = settled(pub)
    asg = assign_cells(pub, db_ref)
    lv = Counter(("unmatched" if a is None else a[0]) for r, a in zip(pub, asg) if r["y"] is not None)
    frac12 = (lv.get("mb", 0) + lv.get("mh", 0)) / len(pub_s) if pub_s else None

    primary = joint_decompose_bootstrap(pub, db_ref, game_cluster)
    db_matched = joint_decompose_bootstrap(db_sel, db_ref, game_cluster)
    rep["decomposition"] = {
        "primary_PUB_vs_DB_ref_game": fmt_decomp(primary),
        "matched_DB_sel_vs_DB_ref_game": fmt_decomp(db_matched),
        "sens_PUB_vs_DB_ref_elig_game": fmt_decomp(joint_decompose_bootstrap(pub, db_ref_elig, game_cluster)),
        "sens_DB_sel_vs_DB_ref_elig_game": fmt_decomp(joint_decompose_bootstrap(db_sel, db_ref_elig, game_cluster)),
        "sens_PUB_vs_DB_ref_player": fmt_decomp(joint_decompose_bootstrap(pub, db_ref, player_cluster)),
        "sens_DB_sel_vs_DB_ref_player": fmt_decomp(joint_decompose_bootstrap(db_sel, db_ref, player_cluster)),
        "sens_fair_test_PUB_vs_DB_ref_game": fmt_decomp(joint_decompose_bootstrap(
            [r for r in pub if r.get("fair_test") is True],
            [r for r in db_ref if r.get("fair_test") is True], game_cluster)),
        "sens_fair_test_DB_sel_vs_DB_ref_game": fmt_decomp(joint_decompose_bootstrap(
            [r for r in db_sel if r.get("fair_test") is True],
            [r for r in db_ref if r.get("fair_test") is True], game_cluster)),
    }
    # Added sensitivity (deviation D2): exclude from DB_ref any id that is
    # also a PUB id on the same date (same outcome counted on both sides).
    pub_keys = {(r["date"], r["id"]) for r in pub}
    db_ref_nopub = [r for r in db_ref if (r["date"], r["id"]) not in pub_keys]
    rep["decomposition"]["sens_PUB_vs_DB_ref_excluding_PUB_ids_game"] = fmt_decomp(
        joint_decompose_bootstrap(pub, db_ref_nopub, game_cluster))
    rep["decomposition"]["DB_ref_rows_that_are_PUB_ids_same_date"] = len(db_ref) - len(db_ref_nopub)

    # date stability
    rep["decomposition"]["date_halves"] = {
        h: {"PUB_vs_DB_ref": fmt_decomp(joint_decompose_bootstrap(
                [r for r in pub if half(r) == h], [r for r in db_ref if half(r) == h], game_cluster)),
            "DB_sel_vs_DB_ref": fmt_decomp(joint_decompose_bootstrap(
                [r for r in db_sel if half(r) == h], [r for r in db_ref if half(r) == h], game_cluster))}
        for h in ("early(<=09-07)", "late(>=09-08)")}

    rep["decision"] = decide(primary, db_matched, len(pub_s), len(settled(db_sel)), frac12)

    # reference cells actually used (transparency)
    cells = _ref_cells(db_ref)
    used = Counter(a for r, a in zip(pub, asg) if r["y"] is not None and a is not None)
    rep["reference_cells_used_by_PUB"] = [
        {"cell": list(k), "n_pub_settled": n, "n_ref_settled": len(cells[k]),
         "ref_mean_p": r4(mean(x["p"] for x in cells[k])),
         "ref_realized": r4(mean(x["y"] for x in cells[k])),
         "g_ref": r4(gap(cells[k]))} for k, n in sorted(used.items(), key=lambda kv: -kv[1])]

    # ── descriptive splits
    def band_key(r):
        return band_of(r["p"])

    def mkt(r):
        return r["market"]

    rep["reliability_by_band"] = {k: split_table(pops[k], band_key, boot=True)
                                  for k in ("PUB", "DB_sel", "DB_ref", "FB_U", "FB_E", "FB_S")}
    rep["market_split"] = {k: split_table(pops[k], mkt, boot=True)
                           for k in ("PUB", "DB_sel", "DB_ref_p>=0.60", "FB_E_nonselected_p>=0.60", "FB_S")}
    rep["market_x_band_selected_vs_nonselected_DB"] = {
        m: {"DB_sel": split_table([r for r in db_sel if r["market"] == m], band_key),
            "DB_ref": split_table([r for r in db_ref if r["market"] == m and is_high(r["p"])], band_key)}
        for m in sorted({r["market"] for r in db_sel})}
    rep["price_band_split_real_odds_only"] = {k: split_table(pops[k], price_band)
                                              for k in ("PUB", "DB_sel", "DB_ref_p>=0.60", "FB_E")}
    rep["selection_intensity_market_edge"] = {k: split_table(pops[k], edge_band, boot=True)
                                              for k in ("PUB", "DB_sel", "DB_ref_p>=0.60")}

    def db_rank_tercile(r):
        rk = r.get("rank")
        if rk is None:
            return None
        return "rank<=10" if rk <= 10 else "rank11-30" if rk <= 30 else "rank>30"

    def fb_rank(r):
        rk = r.get("final_rank")
        if rk is None:
            return "unranked"
        return "rank<=10" if rk <= 10 else "rank11-50" if rk <= 50 else "rank>50"
    rep["rank_split"] = {"DB_ref_p>=0.60_by_rank": split_table(pops["DB_ref_p>=0.60"], db_rank_tercile),
                         "DB_sel_by_rank": split_table(db_sel, db_rank_tercile),
                         "FB_E_by_final_rank": split_table(fb_e, fb_rank)}
    rep["date_split_by_half"] = {k: split_table(pops[k], half, boot=True)
                                 for k in ("PUB", "DB_sel", "DB_ref_p>=0.60")}
    rep["per_date_PUB"] = split_table(pub, lambda r: r["date"])

    # ── H3: probability drift, frozen vs published, final-run status
    db_by = {(r["date"], r["id"]): r for r in db if r["id"]}
    fb_by = {(r["date"], r["id"]): r for r in fb}
    db_games = defaultdict(set)
    for r in db:
        db_games[r["date"]].add(r["game_pk"])

    def final_status(r):
        m = db_by.get((r["date"], r["id"]))
        if m is not None:
            return "final_run_top_pick" if m["status"] == "top_pick" else "final_run_downgraded"
        if r["game_pk"] in db_games.get(r["date"], ()):
            return "game_in_final_run_but_row_absent"
        return "game_not_in_final_run"
    pub_dates_with_db = {r["date"] for r in db}
    pub_cov = [r for r in pub if r["date"] in pub_dates_with_db]
    drift_db = [(r, db_by[(r["date"], r["id"])]) for r in pub_cov if (r["date"], r["id"]) in db_by]
    drift_fb = [(r, fb_by[(r["date"], r["id"])]) for r in pub if (r["date"], r["id"]) in fb_by
                and fb_by[(r["date"], r["id"])]["p"] is not None]

    def drift_summary(pairs):
        if not pairs:
            return {"n": 0}
        d = [a["p"] - b["p"] for a, b in pairs]
        rows = [{"date": a["date"], "game_pk": a["game_pk"], "d": x} for (a, _b), x in zip(pairs, d)]
        bs = cluster_bootstrap(rows, lambda rr: mean(x["d"] for x in rr) if rr else None, game_cluster)
        return {"n": len(d), "mean_p_published_minus_later": r4(mean(d)), "ci95_game": rci(bs["ci95"]),
                "n_published_higher": sum(1 for x in d if x > 1e-9),
                "n_equal": sum(1 for x in d if abs(x) <= 1e-9),
                "n_published_lower": sum(1 for x in d if x < -1e-9),
                "later_status": dict(Counter(str(b["status"]) for _a, b in pairs))}
    rep["H3"] = {
        "PUB_vs_final_run_DB": drift_summary(drift_db),
        "PUB_vs_frozen_FB": drift_summary(drift_fb),
        "PUB_rows_on_FB_dates": sum(1 for r in pub if r["date"] in {m["date"] for m in fb_meta}),
        "PUB_final_run_status_counts": dict(Counter(final_status(r) for r in pub_cov)),
        "exploratory_PUB_gap_by_final_run_status": split_table(pub_cov, final_status, boot=True),
        "FB_S_ids_published": sum(1 for r in fb_s if (r["date"], r["id"]) in pub_keys),
        "FB_S_n": len(fb_s),
    }

    # ── provenance / information cutoff
    pub_violate = [r for r in pub if r.get("published_at") and r.get("game_start")
                   and _ts(r["published_at"]) >= _ts(r["game_start"])]
    q_violate = [r for r in pub if r.get("qualification_ts") and r.get("game_start")
                 and _ts(r["qualification_ts"]) >= _ts(r["game_start"])]
    starts = {}
    for r in pub:
        if r.get("game_start"):
            starts[r["game_pk"]] = r["game_start"]
    db_checkable = [r for r in db if r["game_pk"] in starts and r.get("prediction_ts")]
    db_violate = [r for r in db_checkable if _ts(r["prediction_ts"]) >= _ts(starts[r["game_pk"]])]
    rep["provenance"] = {
        "PUB_published_at_not_before_game_start": len(pub_violate),
        "PUB_qualification_not_before_game_start": len(q_violate),
        "PUB_with_timestamps": sum(1 for r in pub if r.get("published_at") and r.get("game_start")),
        "DB_rows_checkable_against_a_known_game_start": len(db_checkable),
        "DB_prediction_ts_not_before_game_start": len(db_violate),
        "model_versions": {k: dict(Counter(str(r.get("model_version")) for r in v))
                           for k, v in (("PUB", pub), ("DB", db))},
        "FB_boards": fb_meta,
    }
    rep["counts"] = {"PUB": len(pub), "DB": len(db), "DB_sel": len(db_sel), "DB_ref": len(db_ref),
                     "DB_ref_elig": len(db_ref_elig), "FB_U": len(fb_u), "FB_E": len(fb_e),
                     "FB_S": len(fb_s), "FB_T10": len(fb_t10),
                     "DB_date_range": [min(r["date"] for r in db), max(r["date"] for r in db)]}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=False)
        f.write("\n")
    print(json.dumps({"decision": rep["decision"],
                      "primary": rep["decomposition"]["primary_PUB_vs_DB_ref_game"],
                      "db_matched": rep["decomposition"]["matched_DB_sel_vs_DB_ref_game"]}, indent=1))


if __name__ == "__main__":
    main()
