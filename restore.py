#!/usr/bin/env python3
"""Restore the complete Tier 1 week-3 seal input set and verify every byte.

    python3 restore.py [--root /] [--prefetched DIR] [--report restore_report.json]

* Verifies pinned_inputs.tar.gz against MANIFEST.json, extracts it under
  --root, and checks every bundled file's SHA-256.
* Every upstream-sourced file must match its recorded pregame SHA-256. A file
  already present is kept only if it matches; otherwise it is fetched (from
  --prefetched, a directory laid out like /tmp/claude-0, else from its
  nflverse URL) and verified. A mismatch is NEVER accepted: the file is left
  as fetched under <path>.MISMATCH and the run exits non-zero.
* injuries_2026.csv is not pinned (the seal builder refreshes it); it is only
  fetched when missing.
* Fails closed if numpy / pandas / requests cannot be imported.
Exit 0 only when every pinned file matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 20)
    tmp.rename(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/")
    ap.add_argument("--prefetched", type=Path)
    ap.add_argument("--report", type=Path, default=Path("restore_report.json"))
    a = ap.parse_args()
    root = Path(a.root)
    man = json.loads((HERE / "MANIFEST.json").read_text())
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "root": str(root), "failures": [], "files": {}}
    missing = [m for m in man["python_requirements"] if __import__("importlib.util").util.find_spec(m) is None]
    if missing:
        report["failures"].append(f"python packages missing: {missing}; pip install " + " ".join(missing))
    bundle = HERE / "pinned_inputs.tar.gz"
    if sha(bundle) != man["bundle_sha256"]:
        report["failures"].append("pinned_inputs.tar.gz does not match MANIFEST bundle_sha256")
    else:
        with tarfile.open(bundle) as t:
            t.extractall(root, filter="data")
    for e in man["files"]:
        dest = root / e["path"].lstrip("/")
        status = None
        if e["source"] == "bundle":
            status = "BUNDLE_OK" if dest.exists() and sha(dest) == e["sha256"] else "BUNDLE_MISMATCH"
        elif e["sha256"] is None:
            if not dest.exists():
                fetch(e["source"], dest)
            status = "UNPINNED_PRESENT"
        else:
            if dest.exists() and sha(dest) == e["sha256"]:
                status = "PRESENT_OK"
            else:
                if dest.exists():
                    dest.rename(dest.with_name(dest.name + ".PREEXISTING_MISMATCH"))
                pre = a.prefetched / e["path"].split("/tmp/claude-0/", 1)[1] if a.prefetched else None
                if pre is not None and pre.exists() and sha(pre) == e["sha256"]:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(pre, dest)
                    except OSError:
                        shutil.copy2(pre, dest)
                    status = "PREFETCHED_OK"
                else:
                    try:
                        fetch(e["source"], dest)
                        status = "DOWNLOADED_OK" if sha(dest) == e["sha256"] else "DOWNLOADED_MISMATCH"
                    except Exception as exc:  # network or HTTP failure
                        status = f"DOWNLOAD_FAILED: {type(exc).__name__}: {exc}"
                    if status == "DOWNLOADED_MISMATCH":
                        dest.rename(dest.with_name(dest.name + ".MISMATCH"))
        report["files"][e["path"]] = status
        if not (status.endswith("_OK") or status == "UNPINNED_PRESENT"):
            report["failures"].append(f"{e['path']}: {status}")
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report["ok"] = not report["failures"]
    a.report.write_text(json.dumps(report, indent=1) + "\n")
    counts = {}
    for s in report["files"].values():
        counts[s.split(":")[0]] = counts.get(s.split(":")[0], 0) + 1
    print(json.dumps({"ok": report["ok"], "counts": counts, "failures": report["failures"][:10]}, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
