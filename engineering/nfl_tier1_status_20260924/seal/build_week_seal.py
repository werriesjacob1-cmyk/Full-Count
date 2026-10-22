#!/usr/bin/env python3
"""Build a protocol-v1 prospective seal (PROSPECTIVE_PROTOCOL.md s3).

Runs each workstream's FROZEN commit in its own detached worktree and stores,
per player-game, the exact H1-H3 primary challenger and comparator
predictions (correcting the gap found in the ATL@GB seal, where only
combined configs were stored), the authoritative-rule B0, game/player
identity, kickoffs, source hashes, information cutoffs, parameter hashes and
the SHA-256 of every output file.

It never refits, never reads outcomes, and only seals games whose kickoff is
still in the future. Commit the output directory before the first kickoff.

    PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py \
        --season 2026 --week 3 --label sun_mon [--refresh-injuries] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DRIVERS = HERE / "drivers.py"
SHARED = Path("/tmp/claude-0/nfl_tier1_shared")
WT_ROOT = Path("/tmp/claude-0/seal_wt")
# Frozen versions (PROSPECTIVE_PROTOCOL.md s1). B0 uses the champion harness.
FROZEN = {"B": "8aa8067fbc", "C": "40d842c9a9", "D": "76b42547c3", "B0": "c128fc6b60"}
# Pre-target-week inputs the Thursday seal used; a later file would contain
# target-week rows (the WS-C builder refuses those) -- keep them pinned.
PINNED = {SHARED / "stats_player_week_2026.csv": "736bdddef4779023f7eb1831a1f2c8627181aee60cc280d5f0464cf5f41a8e67",
          SHARED / "pbp" / "play_by_play_2026.csv.gz": "6643f82adb1158c8367fb806cfc531a079ce321ca8e2e21012d196dc1a51ece8"}
INJURY_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2026.csv"
MIN_LEAD = timedelta(minutes=45)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args, cwd=REPO) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


EXTRACT = ("nfl", "engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json",
           "engineering/nfl_tier1_player_opportunity_20260924", "engineering/nfl_tier1_team_context_20260924",
           "engineering/nfl_tier1_touchdown_20260924")


def worktree(ws: str) -> tuple[Path, str]:
    """Fresh extraction of the frozen commit's code and parameter paths only
    (a full checkout is ~2 GB). Re-extracted every run, so it is exactly the
    commit's content."""
    full = git("rev-parse", FROZEN[ws] + "^{commit}")
    path = WT_ROOT / ws
    if path.exists():
        subprocess.check_call(["rm", "-rf", str(path)])
    path.mkdir(parents=True)
    present = [p for p in EXTRACT if subprocess.run(["git", "cat-file", "-e", f"{full}:{p}"], cwd=REPO,
                                                    stderr=subprocess.DEVNULL).returncode == 0]
    archive = subprocess.Popen(["git", "archive", full, *present], cwd=REPO, stdout=subprocess.PIPE)
    subprocess.check_call(["tar", "-x", "-C", str(path)], stdin=archive.stdout)
    if archive.wait():
        raise SystemExit(f"git archive failed for {full}")
    return path, full


def refresh_injuries() -> dict:
    dest = SHARED / "injuries" / "injuries_2026.csv"
    old = sha(dest)
    with urllib.request.urlopen(INJURY_URL, timeout=120) as resp:
        body, last_mod = resp.read(), resp.headers.get("Last-Modified")
    if hashlib.sha256(body).hexdigest() != old:
        dest.rename(dest.with_name(f"injuries_2026.{old[:12]}.csv"))
        dest.write_bytes(body)
    return {"url": INJURY_URL, "upstream_last_modified": last_mod, "previous_sha256": old,
            "sha256": sha(dest), "retrieved_utc": datetime.now(timezone.utc).isoformat()}


def kickoffs(season: int, week: int) -> dict[str, datetime]:
    sys.path.insert(0, str(HERE))
    from drivers import _kickoffs
    return {g: datetime.fromisoformat(k.replace("Z", "+00:00"))
            for g, k in _kickoffs(SHARED / "schedules" / "games.csv", season, week).items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--exclude", default="", help="comma list of game_ids already sealed")
    ap.add_argument("--refresh-injuries", action="store_true")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="write under /tmp; not a seal")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    for path, want in PINNED.items():
        if sha(path) != want:
            raise SystemExit(f"pinned input changed: {path}")
    kick = kickoffs(a.season, a.week)
    excluded = set(filter(None, a.exclude.split(",")))
    games = sorted(g for g, k in kick.items() if k - now > MIN_LEAD and g not in excluded)
    if not games:
        raise SystemExit("no game has kickoff far enough ahead to seal")
    tag = f"{a.season}_w{a.week:02d}_{a.label}"
    out = (Path("/tmp/claude-0/seal_dry") / tag) if a.dry_run else HERE / tag
    out.mkdir(parents=True, exist_ok=True)
    injuries = refresh_injuries() if a.refresh_injuries else {"sha256": sha(SHARED / "injuries" / "injuries_2026.csv"),
                                                              "note": "not refreshed"}
    meta = {}
    for ws in ("B0", "B", "C", "D"):
        wt, full = worktree(ws)
        cmd = [sys.executable, str(DRIVERS), ws, "--season", str(a.season), "--week", str(a.week),
               "--games", ",".join(games), "--out-dir", str(out)] + (["--no-capture"] if a.no_capture else [])
        log = out / f"driver_{ws}.log"
        with log.open("w") as fh:
            rc = subprocess.run(cmd, cwd=wt, env={**os.environ, "PYTHONPATH": str(wt)},
                                stdout=fh, stderr=subprocess.STDOUT).returncode
        if rc:
            raise SystemExit(f"driver {ws} failed (rc={rc}); see {log}")
        meta[ws] = json.loads((out / f"driver_{ws}.json").read_text())
        meta[ws]["frozen_commit"] = full
    b0 = meta["B0"]["b0"]
    primary = []
    for ws in ("B", "C", "D"):
        for r in meta[ws]["rows"]:
            auth = b0.get(f"{r['market']}|{r['gsis_id']}")
            r = {**r, "workstream": ws, "kickoff_utc": kick[r["game_id"]].isoformat().replace("+00:00", "Z"),
                 "b0_authoritative_rule": auth,
                 "b0_matches_authoritative": (None if auth is None or "b0" not in r
                                              else abs(r["b0"] - auth) < 1e-9)}
            if ws == "D":
                # frozen touchdown_consumer.predict falls back to B0 when a lambda's
                # inputs are UNKNOWN (counted as fallback_b0); mirror that here with
                # the authoritative-rule (smoothed) B0 and keep the raw reason.
                for field in ("challenger", "comparator"):
                    if not isinstance(r.get(field + "_prediction"), (int, float)):
                        r[field + "_prediction"] = auth
                        r[field + "_fallback"] = "FALLBACK_B0 (" + str(r.get(field + "_fallback")) + ")"
            primary.append(r)
    (out / "primary_predictions.json").write_text(json.dumps(primary, indent=1, sort_keys=True, default=str) + "\n")
    counts = {}
    for r in primary:
        counts[r["hypothesis"]] = counts.get(r["hypothesis"], 0) + 1
    files = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob("*"))
             if p.is_file() and p.name != "seal.json"}
    seal = {"protocol": "PROSPECTIVE_PROTOCOL.md v1", "season": a.season, "week": a.week, "label": a.label,
            "dry_run": a.dry_run, "generated_utc": now.isoformat(),
            "games": {g: kick[g].isoformat() for g in games}, "excluded_games": sorted(excluded),
            "first_kickoff_utc": min(kick[g] for g in games).isoformat(),
            "rule": "A row counts only if this directory is committed before its game's kickoff. Nothing is back-filled.",
            "frozen_commits": {ws: meta[ws]["frozen_commit"] for ws in meta},
            "pinned_inputs": {str(p): h for p, h in PINNED.items()}, "injuries": injuries,
            "drivers": {ws: {k: v for k, v in m.items() if k not in ("rows", "b0")} for ws, m in meta.items()},
            "row_counts": counts, "b0_mismatches": sum(1 for r in primary if r["b0_matches_authoritative"] is False),
            "files_sha256": files}
    (out / "seal.json").write_text(json.dumps(seal, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({"out": str(out), "games": len(games), "row_counts": counts,
                      "b0_mismatches": seal["b0_mismatches"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
