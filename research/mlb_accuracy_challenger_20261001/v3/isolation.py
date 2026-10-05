#!/usr/bin/env python3
"""FC-MLB-001A amendment A1: per-run isolated execution of the frozen shadow pipeline (R1-R5).

Every RECORD and every REPLAY of the pinned pipeline runs in its OWN fresh root:

  <root>/home     HOME (also XDG_* and PYBASEBALL_CACHE live under it); empty at start
  <root>/tmp      TMPDIR/TMP/TEMP; empty at start
  <root>/venv     fresh venv: `pip install --no-deps --only-binary=:all: --require-hashes -r LOCK`,
                  then the installed set must equal the lock exactly (fail closed)
R4 (frozen mechanism): every git call the pinned pipeline makes runs with the injected command-scope
config GIT_CONFIG_COUNT=1 / GIT_CONFIG_KEY_0=core.abbrev / GIT_CONFIG_VALUE_0=10 (overrides repo-local
config); system and global git config are disabled. No PATH shim, no persistent git config.

The pipeline's environment is EXPLICIT (never inherited). Record mode alone passes through the
named network/CA variables needed to reach live sources; their NAMES are fingerprinted, not values.
netrecord.py installs an audit-hook guard (R5) from V3A1_GUARD: every file the pipeline process
opens/lists outside the permitted surfaces is a violation and the run fails closed. In addition the
whole process tree (subprocesses and native/OS reads included) is traced with `strace -f` (openat/open/
execve) and every successful open is classified (trace_surfaces). The audit hook is process-level only;
the strace trace is the process-tree record. Neither is an OS sandbox.
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
A1_VERSION = "fc-mlb-001a-a1-2"   # a1-2: R4 via injected GIT_CONFIG_* (a1-1 used a PATH shim)
# R4, exactly as frozen: command-scope git config injected through the environment
GIT_INJECTED_CONFIG = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.abbrev", "GIT_CONFIG_VALUE_0": "10"}
RECORD_PASSTHROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
                      "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
VENV_BOOTSTRAP = {"pip", "setuptools"}           # seeded by `python -m venv`; reported, never used by the pipeline
# Read-only OS runtime surfaces the interpreter/libraries may consult (certificates, time zones, name
# resolution, mime types, fonts). Each entry is justified in AMENDMENT_A1.md; nothing data-bearing.
OS_RUNTIME_PREFIXES = ("/dev/", "/proc/", "/sys/", "/etc/ssl/", "/etc/pki/", "/etc/ca-certificates",
                       "/usr/share/ca-certificates/", "/usr/lib/ssl/", "/usr/share/zoneinfo/", "/etc/localtime",
                       "/etc/timezone", "/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/host.conf",
                       "/etc/gai.conf", "/etc/services", "/etc/protocols", "/etc/mime.types", "/usr/share/mime/",
                       "/etc/os-release", "/usr/lib/os-release", "/usr/share/fonts", "/etc/fonts/",
                       "/usr/share/zoneinfo",
                       # matplotlib font discovery probes these standard OS font locations (rendering only)
                       "/usr/local/share/fonts", "/usr/X11/lib/X11/fonts", "/usr/X11R6/lib/X11/fonts",
                       "/usr/lib/openoffice/share/fonts", "/usr/share/texmf/fonts")


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
        self.venv = os.path.join(self.root, "venv")
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
        if not shutil.which("git", path="/usr/local/bin:/usr/bin:/bin"):
            raise IsolationError("git not found")
        py = os.path.join(self.venv, "bin", "python")
        stdlib = {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
        # the interpreter's stdlib zip entry on sys.path (probed by the import system even when absent)
        stdlib_zip = os.path.join(sys.base_prefix, "lib", f"python{sys.version_info.major}{sys.version_info.minor}.zip")
        allowed = sorted({self.workdir + "/", self.root + "/", HERE + "/", *(p.rstrip("/") + "/" for p in stdlib), stdlib_zip,
                          *(p + ("/" if os.path.isdir(p) else "") for p in self.extra_allowed), *OS_RUNTIME_PREFIXES})
        env = {"PATH": f"{os.path.dirname(py)}:/usr/local/bin:/usr/bin:/bin",
               "HOME": self.home, "XDG_CACHE_HOME": os.path.join(self.home, ".cache"),
               "XDG_CONFIG_HOME": os.path.join(self.home, ".config"), "XDG_DATA_HOME": os.path.join(self.home, ".local/share"),
               "MPLCONFIGDIR": os.path.join(self.home, ".config", "matplotlib"), "PYBASEBALL_CACHE": pybb,
               "TMPDIR": self.tmp, "TMP": self.tmp, "TEMP": self.tmp, "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
               **GIT_INJECTED_CONFIG}
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
            "git_identity": {"mechanism": "env GIT_CONFIG_COUNT/GIT_CONFIG_KEY_0/GIT_CONFIG_VALUE_0",
                             "injected": dict(GIT_INJECTED_CONFIG), "system_config": "disabled",
                             "global_config": os.devnull},
            "env_keys": sorted(env), "passthrough_env_keys": passed,
            "guard_surfaces": {"pinned_tree": True, "run_root": True, "amendment_code": True, "stdlib": True,
                               "os_runtime_prefixes": list(OS_RUNTIME_PREFIXES),
                               "extra": [os.path.basename(p) for p in self.extra_allowed]}}
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)
        return False


# ---- R5 process-tree trace (the frozen "drill trace method": strace -f openat, whole process tree) -------
# The audit hook above only sees the pipeline's own Python process. The trace below records every successful
# open/openat/execve of the WHOLE process tree (subprocesses, native libraries, the dynamic loader, OS files),
# and classifies each path. Frozen R5 permits exactly: the pinned tree (which holds the sealed overlay) and
# the isolated HOME. Every other class is reported as a frozen-R5 violation; nothing is silently allowed.
TRACE_SYSCALLS = "openat,open,execve"
FROZEN_R5_CLASSES = ("PINNED_TREE", "ISOLATED_HOME")
_TRACE_LINE = re.compile(r'^(\d+)\s+(?:(openat|open|execve)\((?:AT_FDCWD, )?"((?:[^"\\]|\\.)*)"(.*)'
                         r'|<\.\.\. (openat|open|execve) resumed>(.*))$')
_TRACE_RESULT = re.compile(r'= (-?\d+)(?: (E[A-Z]+))?')


def trace_command(trace_path):
    st = shutil.which("strace", path="/usr/local/bin:/usr/bin:/bin")
    if not st:
        raise IsolationError("R5 process-tree trace required but strace is not installed (fail closed)")
    return [st, "-f", "-qq", "-e", f"trace={TRACE_SYSCALLS}", "-e", "signal=none", "-o", trace_path]


def _classify(path, ctx):
    p = os.path.normpath(os.path.join(ctx["cwd"], path)) if not path.startswith("/") else os.path.normpath(path)
    under = lambda base: p == base or p.startswith(base.rstrip("/") + "/")
    if under(ctx["workdir"]):
        return "PINNED_TREE"
    if under(ctx["home"]):
        return "ISOLATED_HOME"
    if under(ctx["root"]):
        return "RUN_ROOT_" + (p[len(ctx["root"]) + 1:].split("/", 1)[0].upper() or "DIR")   # TMP / VENV
    if ctx["tape"] and (p == ctx["tape"] or p.startswith(ctx["tape"] + ".")):
        return "SEALED_TAPE_AND_REPORT"
    if under(HERE):
        return "AMENDMENT_CODE"
    if ctx.get("gitdir") and under(ctx["gitdir"]):
        return "REPO_GIT_DIR"                         # git's object store/refs (a worktree's .git lives outside it)
    if any(under(b) for b in ctx["interp"]):
        return "INTERPRETER_STDLIB"
    if p == "/etc/ld.so.cache" or ".so" in os.path.basename(p) or any(under(b) for b in ("/lib", "/lib64", "/usr/lib64", "/usr/lib/x86_64-linux-gnu")):
        return "SHARED_LIBRARIES"
    if any(under(b) for b in ("/proc", "/sys", "/dev")):
        return "OS_VIRTUAL_" + p.split("/")[1].upper()
    if any(under(b) for b in ("/var/cache", "/var/lib", "/var/tmp", "/tmp", "/root", "/home")):
        return "HOST_STATE"
    if any(under(b) for b in ("/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin", "/sbin", "/usr/lib/git-core")):
        return "EXECUTABLES"
    if under("/etc"):
        return "OS_CONFIG"
    if any(under(b) for b in ("/usr/share", "/usr/lib/locale", "/usr/local/share", "/usr/lib")):
        return "OS_DATA"
    return "OTHER"


def summarize_trace(trace_path, workdir, root, tape_path=None, examples=12):
    """Parse an `strace -f` log of the pipeline's process tree into a machine-independent surface summary."""
    ctx = {"cwd": os.path.abspath(workdir), "workdir": os.path.abspath(workdir), "root": os.path.abspath(root),
           "home": os.path.join(os.path.abspath(root), "home"), "tape": os.path.abspath(tape_path) if tape_path else None,
           "interp": sorted({os.path.realpath(sys.executable), sysconfig.get_paths()["stdlib"],
                             sysconfig.get_paths()["platstdlib"],
                             os.path.join(sys.base_prefix, "lib", f"python{sys.version_info.major}{sys.version_info.minor}.zip")})}
    try:
        gd = subprocess.run(["git", "-C", ctx["workdir"], "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, timeout=30).stdout.strip()
        ctx["gitdir"] = os.path.normpath(gd) if gd and not gd.startswith(ctx["workdir"] + "/") else None
    except (OSError, subprocess.SubprocessError):
        ctx["gitdir"] = None
    labels = [(ctx["workdir"], "<PINNED_TREE>"), (ctx["root"], "<RUN_ROOT>"), (HERE, "<AMENDMENT_CODE>")]
    if ctx["gitdir"]:
        labels.append((ctx["gitdir"], "<REPO_GIT_DIR>"))
    if ctx["tape"]:
        labels.append((os.path.dirname(ctx["tape"]), "<TAPE_DIR>"))
    pending, opens = {}, []
    for line in open(trace_path, errors="replace"):
        m = _TRACE_LINE.match(line.rstrip("\n"))
        if not m:
            continue
        pid = m.group(1)
        if m.group(2):
            call, path, rest = m.group(2), m.group(3), m.group(4)
            if rest.endswith("<unfinished ...>"):
                pending[(pid, call)] = (path, rest)
                continue
        else:
            call, rest = m.group(5), m.group(6)
            if (pid, call) not in pending:
                continue
            path, head = pending.pop((pid, call))
            rest = head + rest
        r = _TRACE_RESULT.search(rest)
        if not r or int(r.group(1)) < 0:
            continue                                  # failed probes (ENOENT etc.) read nothing
        write = call != "execve" and any(f in rest for f in ("O_WRONLY", "O_CREAT", "O_TRUNC"))
        opens.append((call, path.encode().decode("unicode_escape"), write))
    by_class, ex, execs = {}, {}, set()
    for call, path, write in opens:
        c = _classify(path, ctx)
        k = by_class.setdefault(c, {"reads": 0, "writes": 0, "execs": 0, "unique_paths": set()})
        k["execs" if call == "execve" else ("writes" if write else "reads")] += 1
        k["unique_paths"].add(path)
        if call == "execve":
            execs.add(os.path.basename(path))
        lab = path
        for base, name in labels:
            if lab.startswith(base):
                lab = name + lab[len(base):]
                break
        ex.setdefault(c, [])
        if lab not in ex[c] and len(ex[c]) < examples:
            ex[c].append(lab)
    frozen_viol = {c: v["reads"] + v["execs"] for c, v in by_class.items() if c not in FROZEN_R5_CLASSES and v["reads"] + v["execs"]}
    return {"method": f"strace -f -e trace={TRACE_SYSCALLS} (whole process tree; successful calls only)",
            "n_successful_calls": len(opens), "executables": sorted(execs),
            "by_class": {c: {**{k: v[k] for k in ("reads", "writes", "execs")}, "unique_paths": len(v["unique_paths"])}
                         for c, v in sorted(by_class.items())},
            "examples": {c: ex[c] for c in sorted(ex)},
            "frozen_r5_permitted_classes": list(FROZEN_R5_CLASSES),
            "frozen_r5_violation_classes": dict(sorted(frozen_viol.items())),
            "frozen_r5_violations": sum(frozen_viol.values())}


def check_record_conformance(record_fp):
    """The recorded unit must have been produced by the CURRENT A1 mechanism (a1_version, frozen R4 injection)."""
    problems = []
    if record_fp.get("a1_version") != A1_VERSION:
        problems.append(f"a1_version: record={record_fp.get('a1_version')} current={A1_VERSION}")
    if (record_fp.get("git_identity") or {}).get("injected") != GIT_INJECTED_CONFIG:
        problems.append(f"R4: record git identity mechanism is not the frozen injected core.abbrev=10: "
                        f"{record_fp.get('git_identity')}")
    return problems


def check_replay_compatible(record_fp, replay_fp):
    """A1 units: replay must use the identical lock + installed set and the same interpreter line
    (mechanism conformance of the RECORD is checked separately by check_record_conformance)."""
    problems = []
    for k in ("lock_sha256", "installed_set_sha256"):
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
