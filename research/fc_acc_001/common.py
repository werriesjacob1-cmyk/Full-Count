"""FC-ACC-001 shared loaders. Read-only over committed evidence; never writes
outside research/fc_acc_001/out. No odds, eligibility or labels are invented:
a missing price/grade stays missing and is counted as such."""
import glob
import hashlib
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def american_profit(odds):
    """Profit on a 1-unit stake that wins at American `odds`."""
    o = float(odds)
    return o / 100.0 if o > 0 else 100.0 / -o


def implied(odds):
    o = float(odds)
    return (-o) / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)


def load_public():
    """Regime A: the immutable public Top Pick ledger joined to the retained
    graded history (docs/history.json). Grade is taken verbatim; anything not
    graded hit/miss in the retained history stays ungraded."""
    reg_path = os.path.join(ROOT, "data/public_top_picks/registry.json")
    hist_path = os.path.join(ROOT, "docs/history.json")
    reg = json.load(open(reg_path))["entries"]
    hist = json.load(open(hist_path))["days"]
    grade = {}
    for day in hist:
        for p in day["picks"]:
            grade[(day["date"], p["id"])] = p.get("grade")
    rows = []
    for cid, e in reg.items():
        s = e["snapshot"]
        rows.append({
            "id": cid, "date": e["slate_date"], "published_at": e["first_published_at"],
            "game_pk": str(s.get("game_pk")), "player": str(s.get("player_id") or s.get("combo_player_ids")),
            "name": s.get("name"), "stat": s.get("stat"), "side": s.get("market_side"),
            "line": (s.get("projection") or {}).get("value"), "p": s.get("hit_probability"),
            "odds": s.get("market_odds"), "q": s.get("market_implied"), "edge": s.get("market_edge"),
            "reliability": s.get("reliability"), "sample_n": s.get("sample_n"),
            "game_start": s.get("game_start"), "type": s.get("type"),
            "calibration_version": (s.get("versions") or {}).get("calibration_version"),
            "model_version": (s.get("versions") or {}).get("model_version"),
            "git_sha": (s.get("versions") or {}).get("git_sha"),
            "market_disagrees": any("market itself disagrees" in r for r in (s.get("status_reasons") or [])),
            "grade": grade.get((e["slate_date"], cid)),
        })
    prov = {"registry": reg_path[len(ROOT) + 1:], "registry_sha256": sha256_file(reg_path),
            "history": hist_path[len(ROOT) + 1:], "history_sha256": sha256_file(hist_path)}
    return rows, prov


def load_freezes(through=None):
    """Regime B: sealed prospective full-candidate board freezes + their grades.

    Amendment A1 (Codex review 6151131715, P2): every board is re-verified with
    the production ``board_freeze.verify_board_seal`` (rebuild, never trust the
    stored hash), and a graded file MUST carry ``source_board_sha256`` equal to
    that verified hash. A missing binding now fails closed."""
    import sys
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from board_freeze import verify_board_seal
    boards, prov = [], []
    for f in sorted(glob.glob(os.path.join(ROOT, "output/board_freeze_2026-*.json"))):
        if "graded" in os.path.basename(f):
            continue
        b = json.load(open(f))
        if through is not None and b["date"] > through:
            continue
        sealed = verify_board_seal(b)
        g_path = f.replace("board_freeze_", "board_freeze_graded_")
        grades = {}
        if os.path.exists(g_path):
            g = json.load(open(g_path))
            if g.get("source_board_sha256") != sealed:
                raise ValueError(f"graded file {g_path} does not bind verified board {f}")
            grades = {r["candidate_id"]: r for r in g["records"]}
        boards.append((b, grades))
        prov.append({"board": f[len(ROOT) + 1:], "sha256": sha256_file(f),
                     "graded": g_path[len(ROOT) + 1:] if grades else None,
                     "graded_sha256": sha256_file(g_path) if grades else None})
    return boards, prov


def break_even_raw(odds):
    """Offered-price break-even probability (vig INCLUDED), from the American odds
    actually offered. Uniform across every market."""
    return None if odds is None else implied(odds)


def fair_novig(market):
    """No-vig fair probability, ONLY where the stored value is one. In the frozen
    boards ``market_implied`` is mixed: two-sided markets (strikeouts,
    pitcher_outs, nrfi_combined; ``market_hold`` present) store a de-vigged fair
    probability, one-sided batter props store the raw break-even. One-sided
    markets have no opposing price, so their fair probability is UNKNOWN (None),
    never imputed."""
    if market.get("market_hold") is None or market.get("market_implied") is None:
        return None
    return market["market_implied"]


# A1 evidence window. Slates on or before this date have final grades and are
# the audited window (Codex 6151131715). Later slates (appended picks, newly
# graded boards) never shift the audited numbers; they are reported separately.
EVIDENCE_CUTOFF = "2026-10-11"


def load_public_reconciled(cutoff=EVIDENCE_CUTOFF):
    """Regime A, amended (A1): the public registry joined to grades from BOTH the
    rolling 45-day ``docs/history.json`` and the retained per-day
    ``results/grades_*.json`` files. Every grade source is read; any
    disagreement between sources raises. Nothing is imputed: picks with no
    hit/miss in any source stay ungraded/void and are counted."""
    rows, prov = load_public()
    excluded_after_cutoff = sum(r["date"] > cutoff for r in rows)
    rows = [r for r in rows if r["date"] <= cutoff]
    sources = {}
    hist = json.load(open(os.path.join(ROOT, "docs/history.json")))["days"]
    for day in hist:
        for p in day["picks"]:
            sources.setdefault((day["date"], p["id"]), {})["history"] = p.get("grade")
    files = sorted(glob.glob(os.path.join(ROOT, "results/grades_*.json")))
    for f in files:
        d = json.load(open(f))
        for p in d.get("public_top_picks") or []:
            k = (d["date"], p["id"])
            prev = sources.setdefault(k, {}).get("results")
            if prev is not None and prev != p.get("grade"):
                raise ValueError(f"conflicting grades within results files for {k}")
            sources[k]["results"] = p.get("grade")
    recon = {"history_only": 0, "results_only": 0, "both_agree": 0, "conflict": 0, "none": 0}
    for r in rows:
        src = sources.get((r["date"], r["id"]), {})
        h, g = src.get("history"), src.get("results")
        if h is not None and g is not None and h != g:
            recon["conflict"] += 1
            raise ValueError(f"history and results disagree for {(r['date'], r['id'])}: {h} vs {g}")
        r["grade_history"], r["grade_results"] = h, g
        r["grade"] = g if g is not None else h
        recon["both_agree" if (h is not None and g is not None) else
              "history_only" if h is not None else "results_only" if g is not None else "none"] += 1
    prov = dict(prov, evidence_cutoff=cutoff, excluded_after_cutoff=excluded_after_cutoff,
                results_files=len(files),
                results_sha256={os.path.basename(f): sha256_file(f) for f in files})
    return rows, prov, recon


def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
