#!/usr/bin/env python3
"""FC-MLB-001A amendment A1: per-run isolated execution of the frozen shadow pipeline (R1-R5).

Every RECORD and every REPLAY of the pinned pipeline runs in its OWN fresh root:

  <root>/home     HOME (also XDG_* and PYBASEBALL_CACHE live under it); empty at start
  <root>/tmp      TMPDIR/TMP/TEMP; empty at start
  <root>/venv     fresh venv: `pip install --no-deps --only-binary=:all: --require-hashes -r LOCK`,
                  then the installed set must equal the lock exactly (fail closed)
  <root>/bin/git  provenance shim (R4): exactly `git rev-parse --short HEAD` answers HEAD[:10] of the
                  full commit id; every other git call is passed to the real git unchanged

The pipeline's environment is EXPLICIT (never inherited). Record mode alone passes through the
named network/CA variables needed to reach live sources; their NAMES are fingerprinted, not values.
netrecord.py installs an audit-hook guard (R5) from V3A1_GUARD: every file the pipeline process
opens/lists outside the permitted surfaces is a violation and the run fails closed.

Guarantees (and limits) are stated in AMENDMENT_A1.md. This is process-level isolation, not an OS
sandbox: subprocesses (only the git shim is expected) are logged, not audited.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHADOW_LOCK = os.path.join(HERE, "shadow-requirements.lock")
A1_VERSION = "fc-mlb-001a-a1-1"
RECORD_PASSTHROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
                      "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
VENV_BOOTSTRAP = {"pip", "setuptools"}           # seeded by `python -m venv`; reported, never used by the pipeline
# Read-only OS runtime surfaces the interpreter/libraries may consult (certificates, time zones, name
# resolution, mime types, fonts). Each entry is justified in AMENDMENT_A1.md; nothing data-bearing.
OS_RUNTIME_PREFIXES = ("/dev/", "/proc/", "/sys/", "/etc/ssl/", "/etc/pki/", "/etc/ca-certificates",
                       "/usr/share/ca-certificates/", "/usr/lib/ssl/", "/usr/share/zoneinfo/", "/etc/localtime",
                       "/etc/timezone", "/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/host.conf",
                       "/etc/gai.conf", "/etc/services", "/etc/protocols", "/etc/mime.types", "/usr/share/mime/",
                       "/etc/os-release", "/usr/lib/os-release", "/usr/share/fonts/", "/etc/fonts/",
                       "/usr/share/zoneinfo")


class IsolationError(RuntimeError):
    pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_pins(lock_path):
    pins = {}
    for line in open(lock_path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s]+)\s+--hash=sha256:[0-9a-f]{64}$", line)
        if not m:
            raise IsolationError(f"lock line is not 'name==version --hash=sha256:<64 hex>': {line!r}")
        pins[_norm(m.group(1))] = m.group(2)
    return pins


def _dir_empty(path):
    return not os.path.exists(path) or not any(os.scandir(path))


def _git_shim(bin_dir, real_git):
    os.makedirs(bin_dir, exist_ok=True)
    shim = os.path.join(bin_dir, "git")
    body = ("#!/bin/sh\n"
            "# FC-MLB-001A R4: deterministic provenance; `rev-parse --short HEAD` -> first 10 hex of the full HEAD id.\n"
            "if [ \"$#\" -eq 3 ] && [ \"$1\" = rev-parse ] && [ \"$2\" = --short ] && [ \"$3\" = HEAD ]; then\n"
            f"  full=$('{real_git}' rev-parse HEAD) || exit $?\n"
            "  printf '%s\\n' \"$(printf '%s' \"$full\" | cut -c1-10)\"\n"
            "  exit 0\n"
            "fi\n"
            f"exec '{real_git}' \"$@\"\n")
    with open(shim, "w") as fh:
        fh.write(body)
    os.chmod(shim, 0o755)
    return shim, sha256_bytes(body.replace(real_git, "<REAL_GIT>").encode())


def _installed_set(py):
    out = subprocess.check_output(
        [py, "-I", "-c", "import json, importlib.metadata as m; "
                         "print(json.dumps(sorted({(d.metadata['Name'], d.version) for d in m.distributions()})))"],
        env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"}).decode()
    return [(n, v) for n, v in json.loads(out)]


def build_venv(venv_dir, lock_path, wheel_cache=None):
    """Fresh venv from the hash lock; returns installed-set report. Fails closed on any mismatch."""
    subprocess.run([sys.executable, "-m", "venv", "--clear", venv_dir], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    py = os.path.join(venv_dir, "bin", "python")
    env = {k: os.environ[k] for k in RECORD_PASSTHROUGH + ("PIP_CERT",) if k in os.environ}
    env.update({"PATH": os.path.dirname(py) + ":/usr/bin:/bin", "HOME": os.path.join(venv_dir, ".piphome"),
                "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PYTHONNOUSERSITE": "1"})
    if wheel_cache:     # content is still hash-verified by --require-hashes; the cache cannot change bytes
        env["PIP_CACHE_DIR"] = wheel_cache
    r = subprocess.run([py, "-m", "pip", "install", "-q", "--no-deps", "--only-binary=:all:", "--require-hashes",
                        "-r", lock_path], env=env, capture_output=True, text=True)
    if r.returncode:
        raise IsolationError(f"locked install failed (rc {r.returncode}): {r.stderr.strip()[-1200:]}")
    shutil.rmtree(env["HOME"], ignore_errors=True)
    return verify_installed(py, lock_path)


def verify_installed(py, lock_path):
    pins = lock_pins(lock_path)
    inst = _installed_set(py)
    got = {_norm(n): v for n, v in inst if _norm(n) not in VENV_BOOTSTRAP}
    if got != pins:
        extra = sorted(set(got) - set(pins))
        missing = sorted(set(pins) - set(got))
        wrong = sorted(k for k in set(got) & set(pins) if got[k] != pins[k])
        raise IsolationError(f"installed set != lock (extra={extra}, missing={missing}, wrong_version={wrong})")
    lines = "\n".join(f"{n}=={v}" for n, v in sorted((_norm(n), v) for n, v in inst)) + "\n"
    return {"installed_set_sha256": sha256_bytes(lines.encode()),
            "bootstrap": sorted(f"{_norm(n)}=={v}" for n, v in inst if _norm(n) in VENV_BOOTSTRAP),
            "n_locked": len(pins)}


class IsolatedRun:
    """One isolated execution root. Use: with IsolatedRun(mode, workdir, extra_allowed) as iso: iso.env ..."""

    def __init__(self, mode, workdir, lock_path=SHADOW_LOCK, extra_allowed=(), base=None, wheel_cache=None):
        if mode not in ("record", "replay"):
            raise ValueError(mode)
        self.mode, self.workdir, self.lock_path = mode, os.path.abspath(workdir), lock_path
        base = base or os.environ.get("V3A1_ISOLATION_BASE") or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix=f"v3a1_{mode}_", dir=base)
        self.home, self.tmp = os.path.join(self.root, "home"), os.path.join(self.root, "tmp")
        self.venv, self.bin = os.path.join(self.root, "venv"), os.path.join(self.root, "bin")
        self.wheel_cache = wheel_cache or os.environ.get("V3A1_WHEEL_CACHE") or os.path.join(base, "v3a1_wheelcache")
        self.extra_allowed = tuple(os.path.abspath(p) for p in extra_allowed)

    def __enter__(self):
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.tmp, exist_ok=True)
        pybb = os.path.join(self.home, ".pybaseball", "cache")
        # R1/R2: prove the run starts from nothing (a pre-populated root is a breach, not a warning)
        start = {"home_empty_at_start": _dir_empty(self.home), "tmp_empty_at_start": _dir_empty(self.tmp),
                 "pybaseball_cache_empty_at_start": _dir_empty(pybb)}
        if not all(start.values()):
            raise IsolationError(f"ISOLATION_BREACH: run root not empty at start {start}")
        deps = build_venv(self.venv, self.lock_path, self.wheel_cache)
        real_git = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
        if not real_git:
            raise IsolationError("git not found")
        _, shim_sha = _git_shim(self.bin, real_git)
        py = os.path.join(self.venv, "bin", "python")
        stdlib = {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
        allowed = sorted({self.workdir + "/", self.root + "/", HERE + "/", *(p.rstrip("/") + "/" for p in stdlib),
                          *(p + ("/" if os.path.isdir(p) else "") for p in self.extra_allowed), *OS_RUNTIME_PREFIXES})
        env = {"PATH": f"{self.bin}:{os.path.dirname(py)}:/usr/local/bin:/usr/bin:/bin",
               "HOME": self.home, "XDG_CACHE_HOME": os.path.join(self.home, ".cache"),
               "XDG_CONFIG_HOME": os.path.join(self.home, ".config"), "XDG_DATA_HOME": os.path.join(self.home, ".local/share"),
               "MPLCONFIGDIR": os.path.join(self.home, ".config", "matplotlib"), "PYBASEBALL_CACHE": pybb,
               "TMPDIR": self.tmp, "TMP": self.tmp, "TEMP": self.tmp, "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
        passed = []
        if self.mode == "record":
            for k in RECORD_PASSTHROUGH:
                if k in os.environ:
                    env[k] = os.environ[k]
                    passed.append(k)
            for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
                if k in env and os.path.exists(env[k]):
                    allowed.append(os.path.abspath(env[k]))
        env["V3A1_GUARD"] = json.dumps({"allowed_prefixes": sorted(set(allowed))})
        self.env, self.python = env, py
        self.fingerprint = {
            "a1_version": A1_VERSION, "mode": self.mode,
            "lock_sha256": sha256_bytes(open(self.lock_path, "rb").read()), **deps,
            "python": platform.python_version(), "python_implementation": platform.python_implementation(),
            "machine": platform.machine(), "isolation": start,
            "git_identity": {"shim_sha256": shim_sha, "rule": "rev-parse --short HEAD -> full HEAD id [:10]"},
            "env_keys": sorted(env), "passthrough_env_keys": passed,
            "guard_surfaces": {"pinned_tree": True, "run_root": True, "amendment_code": True, "stdlib": True,
                               "os_runtime_prefixes": list(OS_RUNTIME_PREFIXES),
                               "extra": [os.path.basename(p) for p in self.extra_allowed]}}
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)
        return False


def check_replay_compatible(record_fp, replay_fp):
    """A1 units: replay must use the identical lock + installed set and the same interpreter line."""
    problems = []
    for k in ("a1_version", "lock_sha256", "installed_set_sha256"):
        if record_fp.get(k) != replay_fp.get(k):
            problems.append(f"{k}: record={record_fp.get(k)} replay={replay_fp.get(k)}")
    if str(record_fp.get("python", "")).rsplit(".", 1)[0] != str(replay_fp.get("python", "")).rsplit(".", 1)[0]:
        problems.append(f"python minor: record={record_fp.get('python')} replay={replay_fp.get('python')}")
    for fp in (record_fp, replay_fp):
        if not all((fp.get("isolation") or {}).values()):
            problems.append(f"{fp.get('mode')}: isolation flags {fp.get('isolation')}")
        if (fp.get("guard") or {}).get("violations"):
            problems.append(f"{fp.get('mode')}: guard violations {fp['guard']['violations'][:5]}")
    return problems
