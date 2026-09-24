#!/usr/bin/env python3
"""ATL@GB game-day evidence runner (deterministic; bounded stdout).

1. For each given Actions run id: record run metadata, download every
   artifact zip, verify its bytes against GitHub's recorded digest, extract,
   and summarize the sealed snapshot (seal time, sha, counts, decisions,
   quarantine reasons, inactive coverage).
2. Optionally run ONE fresh FanDuel capture with Codex's pinned PR #199 code
   and join it to the receptions B0 snapshot (create-only outputs).
Full outputs stay on disk under --out; only a compact summary is printed.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/werriesjacob1-cmyk/Full-Count"
NFLPRICE = Path("/tmp/claude-0/full-count-worktrees/nflprice")


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})) as r:
        return r.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def summarize_board(path: Path) -> dict:
    d = json.loads(path.read_text())
    snap = d.get("snapshot") or {}
    recs = snap.get("records") or []
    out = {
        "file": path.name, "file_sha256": sha256(path.read_bytes()),
        "status": d.get("status"), "code_sha": snap.get("code_sha"),
        "sealed_at": snap.get("sealed_at"), "source_vintage": snap.get("source_vintage"),
        "snapshot_sha256": snap.get("snapshot_sha256"), "slate_date": snap.get("slate_date"),
        "record_count": len(recs),
        "decisions": dict(collections.Counter(r.get("decision_status") for r in recs)),
        "availability_status": dict(collections.Counter(r.get("availability_status") for r in recs)),
        "players": sorted({(r.get("player_name") or r.get("esb_id") or "?") for r in recs})[:30],
        "availability_block": d.get("availability"),
    }
    reasons = collections.Counter()
    for r in recs:
        for x in r.get("quarantine_reasons") or r.get("reasons") or []:
            reasons[x if isinstance(x, str) else json.dumps(x)[:60]] += 1
    out["quarantine_reasons"] = dict(reasons)
    return out


def fetch_run(run_id: str, out: Path) -> dict:
    run = json.loads(get(f"{API}/actions/runs/{run_id}"))
    meta = {k: run.get(k) for k in ("id", "name", "event", "status", "conclusion", "head_sha",
                                     "created_at", "run_started_at", "updated_at", "html_url")}
    arts = json.loads(get(f"{API}/actions/runs/{run_id}/artifacts"))["artifacts"]
    meta["artifacts"] = []
    for a in arts:
        blob = get(f"{API}/actions/artifacts/{a['id']}/zip")
        digest = sha256(blob)
        zpath = out / f"run_{run_id}_{a['name']}.zip"
        zpath.write_bytes(blob)
        exdir = out / f"run_{run_id}_{a['name']}"
        with zipfile.ZipFile(zpath) as z:
            z.extractall(exdir)
        entry = {"id": a["id"], "name": a["name"], "created_at": a["created_at"],
                 "size": a["size_in_bytes"], "github_digest": a.get("digest"),
                 "downloaded_sha256": digest,
                 "digest_match": a.get("digest") == f"sha256:{digest}", "boards": []}
        for board in sorted(exdir.glob("*shadow-board.json")):
            entry["boards"].append(summarize_board(board))
        meta["artifacts"].append(entry)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--capture", action="store_true", help="run one fresh capture + #199 join")
    ap.add_argument("--tag", default="01")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {"runs": [fetch_run(r, args.out) for r in args.runs]}
    if args.capture:
        receptions = [b for run in summary["runs"] for a in run["artifacts"] for b in a["boards"]
                      if "receptions" in b["file"]]
        b0_path = next(p for r in args.runs for p in args.out.glob(f"run_{r}_*receptions*/nfl-live-receptions-shadow-board.json"))
        cap = args.out / f"capture_{args.tag}"
        integ = args.out / f"integration_{args.tag}.json"
        if cap.exists() or integ.exists():
            raise SystemExit("create-only: capture/integration path already exists")
        c = subprocess.run([sys.executable, "-m", "nfl.research.price_aware_offer_capture", "--output", str(cap)],
                           cwd=NFLPRICE, capture_output=True, text=True, timeout=900)
        summary["capture"] = {"returncode": c.returncode, "stdout_tail": c.stdout[-600:], "stderr_tail": c.stderr[-600:]}
        j = subprocess.run([sys.executable, "-m", "nfl.research.price_to_b0_integration", "--capture-dir", str(cap),
                            "--b0-snapshot", str(b0_path), "--output", str(integ)],
                           cwd=NFLPRICE, capture_output=True, text=True, timeout=600)
        summary["integration_run"] = {"returncode": j.returncode, "stdout_tail": j.stdout[-600:], "stderr_tail": j.stderr[-600:]}
        if integ.exists():
            d = json.loads(integ.read_text())
            recs = d.get("records") or []
            reasons = collections.Counter(x for r in recs for x in (r.get("reasons") or []))
            summary["integration"] = {
                "sha256": sha256(integ.read_bytes()), "integration_sha256": d.get("integration_sha256"),
                "b0_sealed_at": d.get("b0_sealed_at"), "b0_snapshot_sha256": d.get("b0_snapshot_sha256"),
                "capture_started_at": d.get("capture_started_at"),
                "capture_snapshot_sha256": d.get("capture_snapshot_sha256"),
                "counts": d.get("counts"), "n_records": len(recs),
                "n_b0_joined": sum(1 for r in recs if r.get("authoritative_b0_record")),
                "n_bettable": sum(1 for r in recs if r.get("bettable")),
                "reasons": dict(reasons.most_common()),
            }
        summary["b0_used"] = receptions[0]["snapshot_sha256"] if receptions else None
    (args.out / f"summary_{args.tag}.json").write_text(json.dumps(summary, indent=2, default=str))
    compact = json.loads(json.dumps(summary, default=str))
    for run in compact["runs"]:
        for a in run["artifacts"]:
            for b in a["boards"]:
                b.pop("players", None)
                b.pop("availability_block", None)
    print(json.dumps(compact, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
