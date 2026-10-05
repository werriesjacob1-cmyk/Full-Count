#!/usr/bin/env python3
"""Record / replay every live input of the frozen shadow pipeline (prereg v3 section 8 enforcement).

    python3 netrecord.py --mode record --tape T.json.gz -- generate_picks.py
    python3 netrecord.py --mode replay --tape T.json.gz -- generate_picks.py

Run with cwd = a fresh pinned shadow tree. Every HTTP exchange made through
`requests` (statsapi, pybaseball/FanGraphs/Savant, Rotowire, weather, FanDuel --
the pinned pipeline's only network path) and the run's start instant are recorded. Replay serves exactly those bytes, with the
process clock (time-machine, C-level, pandas-safe) starting at the recorded start
instant and ticking; it performs NO network I/O. Any request absent from the tape is a REPLAY_MISS (raised as a connection
error, counted, and makes the replay fail). The tape is sealed with the slate.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dtm
import gzip
import hashlib
import json
import os
import runpy
import sys
import threading

_LOCK = threading.Lock()
STATE = {"mode": None, "http": {}, "misses": [], "n_http": 0, "started_at": None}
TAPE_VERSION = "mlb-v3-shadow-tape-2"
TIME_MACHINE_VERSION = "2.16.0"   # wheel sha256 e3391ae9c484736850bb44ef125cbad52fe2d1b69e42c95dc88c43af8ead2cc7


def _key(req):
    body = req.body or b""
    if isinstance(body, str):
        body = body.encode()
    return f"{req.method} {req.url} {hashlib.sha256(body).hexdigest()[:16]}"


def install():
    import requests
    from requests.structures import CaseInsensitiveDict
    orig_send = requests.Session.send

    def send(self, request, **kw):
        k = _key(request)
        if STATE["mode"] == "record":
            try:
                resp = orig_send(self, request, **kw)
            except Exception as exc:  # noqa: BLE001 -- recorded so replay fails the same way
                with _LOCK:
                    STATE["http"].setdefault(k, []).append({"exception": f"{type(exc).__name__}: {exc}"})
                    STATE["n_http"] += 1
                raise
            with _LOCK:
                STATE["http"].setdefault(k, []).append({
                    "status": resp.status_code, "reason": resp.reason, "url": resp.url,
                    "encoding": resp.encoding,
                    "headers": {h: resp.headers[h] for h in ("Content-Type", "Content-Encoding") if h in resp.headers},
                    "content_b64": base64.b64encode(resp.content).decode()})
                STATE["n_http"] += 1
            return resp
        with _LOCK:
            queue = STATE["http"].get(k) or []
            item = queue.pop(0) if queue else None
            if item is None:
                STATE["misses"].append(k)
        if item is None:
            raise requests.ConnectionError(f"REPLAY_MISS {k}")
        if "exception" in item:
            raise requests.ConnectionError(f"REPLAYED_FAILURE {item['exception']}")
        r = requests.models.Response()
        r.status_code, r.reason, r.url, r.request = item["status"], item["reason"], item["url"], request
        r._content = base64.b64decode(item["content_b64"])
        r.headers = CaseInsensitiveDict({k2: v for k2, v in item["headers"].items() if k2 != "Content-Encoding"})
        r.encoding = item["encoding"]
        return r

    requests.Session.send = send


GUARD = {"on": False, "allowed": (), "violations": set(), "surfaces": {}, "subprocesses": set()}


def _guard_path(p):
    if isinstance(p, int) or p is None:
        return None
    if isinstance(p, bytes):
        p = p.decode("utf-8", "replace")
    p = str(p)
    if not os.path.isabs(p):
        p = os.path.join(os.getcwd(), p)
    return os.path.normpath(p)


def install_guard(cfg):
    """FC-MLB-001A R5: audit every file the pipeline process opens or lists. Anything outside the
    permitted surfaces (pinned tree, isolated run root, amendment code, stdlib, named OS runtime
    files) is a violation; shadow.run_pipeline fails the run closed. Subprocess executables are logged."""
    GUARD.update(on=True, allowed=tuple(cfg["allowed_prefixes"]))

    def hook(event, args):
        if event in ("open", "os.listdir", "os.scandir"):
            p = _guard_path(args[0] if args else None)
            if p is None:
                return
            hit = next((a for a in GUARD["allowed"] if p == a.rstrip("/") or p.startswith(a)), None)
            if hit is None:
                GUARD["violations"].add(p)
            else:
                GUARD["surfaces"][hit] = GUARD["surfaces"].get(hit, 0) + 1
        elif event == "subprocess.Popen":
            GUARD["subprocesses"].add(os.path.basename(str(args[0])))

    sys.addaudithook(hook)


def tape_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("record", "replay"), required=True)
    ap.add_argument("--tape", required=True)
    ap.add_argument("script", nargs="+")
    a = ap.parse_args(argv)
    STATE["mode"] = a.mode
    traveller = None
    if a.mode == "replay":
        tape = json.load(gzip.open(a.tape))
        if tape.get("tape_version") != TAPE_VERSION:
            raise SystemExit("unexpected tape version")
        STATE["http"], STATE["started_at"] = tape["http"], tape["started_at"]
        import time_machine
        from importlib.metadata import version
        if version("time-machine") != TIME_MACHINE_VERSION:
            raise SystemExit("unexpected time-machine version")
        # the replayed process's clock starts at the recorded start instant and ticks
        traveller = time_machine.travel(_dtm.datetime.fromisoformat(STATE["started_at"]), tick=True)
        traveller.start()
    else:
        STATE["started_at"] = _dtm.datetime.now(_dtm.timezone.utc).isoformat()
    install()
    if os.environ.get("V3A1_GUARD"):
        cfg = json.loads(os.environ["V3A1_GUARD"])
        cfg["allowed_prefixes"] = list(cfg["allowed_prefixes"]) + [os.path.abspath(a.tape) + p for p in
                                                                   ("", ".record.report.json", ".replay.report.json")]
        install_guard(cfg)
    sys.path.insert(0, os.getcwd())
    script = [s for s in a.script if s != "--"]
    sys.argv = script
    rc = 0
    try:
        runpy.run_path(script[0], run_name="__main__")
    except SystemExit as e:
        rc = e.code or 0
    finally:
        if a.mode == "record":
            with gzip.GzipFile(a.tape, "wb", mtime=0) as fh:
                fh.write(json.dumps({"tape_version": TAPE_VERSION, "started_at": STATE["started_at"],
                                     "http": STATE["http"]}, sort_keys=True).encode())
        if traveller is not None:
            traveller.stop()
        report = {"mode": a.mode, "rc": rc, "n_http": STATE["n_http"], "started_at": STATE["started_at"],
                  "replay_misses": STATE["misses"],
                  "unconsumed": sum(len(v) for v in STATE["http"].values()) if a.mode == "replay" else None}
        if GUARD["on"]:
            report["guard"] = {"violations": sorted(GUARD["violations"]),
                               "reads_by_surface": dict(sorted(GUARD["surfaces"].items())),
                               "subprocess_executables": sorted(GUARD["subprocesses"])}
        with open(a.tape + f".{a.mode}.report.json", "w") as fh:
            json.dump(report, fh, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
