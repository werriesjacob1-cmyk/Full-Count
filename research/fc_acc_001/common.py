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


def load_freezes():
    """Regime B: sealed prospective full-candidate board freezes + their grades."""
    boards, prov = [], []
    for f in sorted(glob.glob(os.path.join(ROOT, "output/board_freeze_2026-*.json"))):
        b = json.load(open(f))
        g_path = f.replace("board_freeze_", "board_freeze_graded_")
        grades = {}
        if os.path.exists(g_path):
            g = json.load(open(g_path))
            if g.get("source_board_sha256") not in (None, b["board_sha256"]):
                raise ValueError(f"graded file {g_path} does not bind board {f}")
            grades = {r["candidate_id"]: r for r in g["records"]}
        boards.append((b, grades))
        prov.append({"board": f[len(ROOT) + 1:], "sha256": sha256_file(f),
                     "graded": g_path[len(ROOT) + 1:] if grades else None,
                     "graded_sha256": sha256_file(g_path) if grades else None})
    return boards, prov


def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
