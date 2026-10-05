#!/usr/bin/env python3
"""FC-MLB-001B: the pinned runtime boundary (criteria TASKS/FC-MLB-001B.md §3-§4).

Every RECORD and every REPLAY of the frozen shadow pipeline runs inside a sandbox whose entire filesystem view is
an explicit, enumerated list of surfaces. Nothing else from the host exists inside it:

  /usr, /etc (+ bin, lib, lib64, sbin -> usr)  PINNED_RUNTIME_IMAGE   read-only, from the digest-pinned image
  /v3b/tree                                    PINNED_SOURCE_TREE     the frozen shadow pin (+ SEALED_OVERLAY file)
  /v3b/run/pin.git                             PINNED_GIT_OBJECTS     read-only bare repo holding ONLY the pin commit
  /v3b/run/home, /v3b/run/tmp                  ISOLATED_RUN_STATE     fresh, empty at start (HOME/XDG/cache/TMPDIR)
  /v3b/run/venv, /v3b/wheels                   LOCKED_DEPENDENCIES    venv built from the hash lock (read-only at run)
  /v3b/io                                      SEALED_TAPE_AND_REPORT the tape (sealed in replay) and run report
  /v3b/code                                    AMENDMENT_CODE         copy of netrecord.py + lock (sha256 recorded)
  /v3b/netca/ca.pem                            RECORD_NETWORK_TRUST   record mode only: the egress proxy CA bundle
  /proc (own pid namespace), /dev (null, zero, random, urandom, shm, fd)   KERNEL_VIRTUAL (enumerated, fingerprinted)

Not present at all: host HOME and caches, /var (so no fontconfig or other cache), /tmp content, /root, /home, /sys,
/opt, /mnt, the host repository, prior runs. Font discovery is eliminated from the execution path: the image's
`fc-list` is masked and OS font directories are empty, so matplotlib only sees its own hash-locked bundled fonts.

Mechanism: `unshare` mount + pid namespaces (+ a network namespace in REPLAY: no network exists) -> tmpfs root ->
read-only binds of the enumerated surfaces -> root remounted read-only -> `chroot --userspec=65534:65534` (the
pipeline runs unprivileged). The whole process tree runs under host `strace -f` (open/openat/execve + chroot/clone
for exact process attribution, pid-namespace translated). Every successful open/exec of every sandboxed process is
classified; FORBIDDEN or UNCLASSIFIED reads fail the run. Launcher steps before chroot are host tooling, reported
separately. Requires root on the host (GitHub runners: sudo).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import isolation as ISO  # noqa: E402  (inherited: lock parsing, injected git config, A1 isolation semantics)
import runtime_image as RI  # noqa: E402

B_VERSION = "fc-mlb-001b-b1"
UID = GID = 65534
M = "/v3b"
DEV_NODES = ("null", "zero", "random", "urandom")
FONT_DIRS = ("usr/share/fonts", "usr/local/share/fonts", "etc/fonts")
MASKED_EXECUTABLES = ("usr/bin/fc-list", "usr/bin/fc-match", "usr/bin/fc-cache")
DETERMINISM_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                   "NUMEXPR_MAX_THREADS": "1", "MPLBACKEND": "Agg"}
TRACE_SYSCALLS = "openat,open,execve,chroot,clone,clone3,fork,vfork"
FORBIDDEN_CLASSES = ("FORBIDDEN_MUTABLE_STATE", "UNCLASSIFIED")


class SandboxError(RuntimeError):
    pass


def _sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


RUNTIME_GLIBC = (2, 36)          # Debian bookworm, the pinned image's libc


def _wheel_compatible(filename):
    """Is this wheel installable in the pinned runtime (CPython 3.11, linux x86_64, glibc 2.36)?"""
    parts = filename[:-4].split("-")
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    if not any(t in ("cp311", "py3", "py2.py3") for t in py.split(".")) and not (abi == "abi3" and py.startswith("cp3")):
        return False
    if not any(a in ("cp311", "abi3", "none") for a in abi.split(".")):
        return False
    for t in plat.split("."):
        if t == "any":
            return True
        m = re.fullmatch(r"manylinux_(\d+)_(\d+)_x86_64", t)
        if m and (int(m.group(1)), int(m.group(2))) <= RUNTIME_GLIBC:
            return True
        if t in ("manylinux1_x86_64", "manylinux2010_x86_64", "manylinux2014_x86_64", "linux_x86_64"):
            return True
    return False


def _lock_hashes(lock_path):
    out = {}
    for line in open(lock_path):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, rest = line.split("==", 1)
            ver = rest.split()[0]
            out[(ISO._norm(name), ver)] = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
    return out


def ensure_wheels(lock_path, wheel_dir):
    """The exact hash-locked wheel files for the pinned runtime. Each locked sha256 is resolved to its exact PyPI file
    (no resolver, no tag preference), checked for compatibility with the pinned runtime, downloaded and re-hashed;
    pip verifies every file again inside the sandbox (--require-hashes). Fail closed on any mismatch."""
    import urllib.request
    os.makedirs(wheel_dir, exist_ok=True)
    for (name, ver), hashes in sorted(_lock_hashes(lock_path).items()):
        have = [f for f in os.listdir(wheel_dir) if f.endswith(".whl") and _sha_file(os.path.join(wheel_dir, f)) in hashes]
        if have:
            continue
        meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=60))
        files = [f for f in meta["urls"] if f["digests"]["sha256"] in hashes and f["filename"].endswith(".whl")
                 and _wheel_compatible(f["filename"])]
        if not files:
            raise SandboxError(f"no locked wheel for {name}=={ver} is installable in the pinned runtime")
        f = files[0]
        dest = os.path.join(wheel_dir, f["filename"])
        with urllib.request.urlopen(f["url"], timeout=300) as r, open(dest + ".part", "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
        if _sha_file(dest + ".part") != f["digests"]["sha256"]:
            os.remove(dest + ".part")
            raise SandboxError(f"downloaded {f['filename']} does not match its locked sha256")
        os.replace(dest + ".part", dest)
    return wheel_dir


def _cache_dirs():
    base = os.environ.get("V3B_RUNTIME_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "v3b_runtime")
    return base, os.environ.get("V3B_WHEEL_DIR") or os.path.join(base, "wheels")


class SandboxRun:
    """One fresh sandbox root for one record or replay. Use as a context manager; call setup() then run()."""

    def __init__(self, mode, tree, lock_path, overlay_rel=None, base=None, runtime=None, wheel_dir=None):
        if mode not in ("record", "replay"):
            raise ValueError(mode)
        if os.geteuid() != 0:
            raise SandboxError("the 001B sandbox needs root on the host (namespaces, chroot); use sudo")
        for tool in ("unshare", "mount", "chroot", "strace"):
            if not shutil.which(tool, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"):
                raise SandboxError(f"host tool {tool} missing (fail closed)")
        self.mode, self.tree, self.lock_path, self.overlay_rel = mode, os.path.abspath(tree), lock_path, overlay_rel
        cache, wheels = _cache_dirs()
        self.rootfs, self.image = runtime or RI.ensure_rootfs(cache)
        self.wheel_dir = ensure_wheels(lock_path, wheel_dir or wheels)
        base = base or os.environ.get("V3A1_ISOLATION_BASE") or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix=f"v3b_{mode}_", dir=base)
        os.chmod(self.root, 0o755)
        self.dirs = {k: os.path.join(self.root, k) for k in ("home", "tmp", "venv", "io", "code", "mnt", "pin.git")}

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    def __enter__(self):
        for k in ("home", "tmp", "venv", "io", "code", "mnt"):
            os.makedirs(self.dirs[k], exist_ok=True)
        start = {"home_empty_at_start": ISO._dir_empty(self.dirs["home"]),
                 "tmp_empty_at_start": ISO._dir_empty(self.dirs["tmp"]),
                 "pybaseball_cache_empty_at_start": ISO._dir_empty(os.path.join(self.dirs["home"], ".pybaseball", "cache"))}
        if not all(start.values()):
            raise ISO.IsolationError(f"ISOLATION_BREACH: run root not empty at start {start}")
        self.isolation = start
        for f in ("netrecord.py",):
            shutil.copy(os.path.join(HERE, f), self.dirs["code"])
        shutil.copy(self.lock_path, os.path.join(self.dirs["code"], "lock.txt"))
        self.code_sha256 = {f: _sha_file(os.path.join(self.dirs["code"], f)) for f in sorted(os.listdir(self.dirs["code"]))}
        self.git_identity = self._pin_git()
        for k in ("home", "tmp", "venv", "io"):
            os.chown(self.dirs[k], UID, GID)
        subprocess.run(["chown", "-R", f"{UID}:{GID}", self.tree], check=True)
        return self

    def __exit__(self, *exc):
        subprocess.run(["umount", "-R", "-l", self.dirs["mnt"]], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)
        return False

    def _pin_git(self):
        """PINNED_GIT_OBJECTS: a bare repo that contains only the commit the tree is checked out at (shallow), so
        `git rev-parse` inside the sandbox reads deterministic, content-addressed objects and nothing else."""
        # host-side launcher reads of the tree (it may already be owned by the sandbox user): safe.directory scoped
        # to this one command, never persisted
        tg = ["git", "-c", f"safe.directory={self.tree}", "-C", self.tree]
        head = subprocess.check_output([*tg, "rev-parse", "HEAD"], text=True).strip()
        common = subprocess.check_output([*tg, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                         text=True).strip()
        g = self.dirs["pin.git"]
        env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": self.root}
        subprocess.run(["git", "init", "-q", "--bare", g], check=True, env=env)
        r = subprocess.run(["git", "--git-dir", g, "fetch", "-q", "--no-tags", "--depth", "1", "--upload-pack",
                            f"git -c safe.directory={common} -c uploadpack.allowAnySHA1InWant=true upload-pack",
                            "file://" + common, head], env=env, capture_output=True, text=True)
        if r.returncode:
            raise SandboxError(f"could not pin git objects for {head}: {r.stderr.strip()[-600:]}")
        subprocess.run(["git", "--git-dir", g, "update-ref", "--no-deref", "HEAD", head], check=True, env=env)
        for p in ("hooks", "logs", "FETCH_HEAD", "description", "info"):
            q = os.path.join(g, p)
            if os.path.isdir(q):
                shutil.rmtree(q)
            elif os.path.exists(q):
                os.remove(q)
        tree_id = subprocess.check_output(["git", "--git-dir", g, "rev-parse", "HEAD^{tree}"], text=True, env=env).strip()
        return {"mechanism": "env GIT_CONFIG_COUNT/GIT_CONFIG_KEY_0/GIT_CONFIG_VALUE_0 (frozen R4) + GIT_DIR=pinned objects",
                "injected": dict(ISO.GIT_INJECTED_CONFIG), "head": head, "tree": tree_id,
                "system_config": "disabled", "global_config": "/dev/null"}

    # ---- the sandbox -------------------------------------------------------------------------------------------
    def env(self, phase):
        e = {"PATH": f"{M}/run/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": f"{M}/run/home",
             "XDG_CACHE_HOME": f"{M}/run/home/.cache", "XDG_CONFIG_HOME": f"{M}/run/home/.config",
             "XDG_DATA_HOME": f"{M}/run/home/.local/share", "MPLCONFIGDIR": f"{M}/run/home/.config/matplotlib",
             "PYBASEBALL_CACHE": f"{M}/run/home/.pybaseball/cache", "TMPDIR": f"{M}/run/tmp", "TMP": f"{M}/run/tmp",
             "TEMP": f"{M}/run/tmp", "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "GIT_DIR": f"{M}/run/pin.git",
             **ISO.GIT_INJECTED_CONFIG, **DETERMINISM_ENV}
        passed = []
        if phase == "setup":
            e.update({"HOME": f"{M}/run/venv/.piphome", "PIP_NO_CACHE_DIR": "1", "PIP_CONFIG_FILE": "/dev/null",
                      "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"})
        elif self.mode == "record":
            for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
                if k in os.environ:
                    e[k] = os.environ[k]
                    passed.append(k)
            if self._host_ca():
                for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
                    e[k] = f"{M}/netca/ca.pem"
                passed.append("CA_BUNDLE->/v3b/netca/ca.pem")
        return e, passed

    def _host_ca(self):
        for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
            p = os.environ.get(k)
            if p and os.path.isfile(p):
                return p
        return None

    def _script(self, phase, argv, env):
        q = shlex.quote
        R = q(self.dirs["mnt"])
        lines = ["set -eu", f"R={R}", 'mount -t tmpfs -o mode=0755,size=16m v3b-root "$R"']
        mk = ["usr", "etc", "proc", "dev", "tmp", "root", "home", "v3b/tree", "v3b/run/home", "v3b/run/tmp",
              "v3b/run/venv", "v3b/run/pin.git", "v3b/code", "v3b/io", "v3b/wheels", "v3b/netca"]
        lines.append("mkdir -p " + " ".join(f'"$R/{d}"' for d in mk))
        lines += [f'ln -s {t} "$R/{n}"' for n, t in (("bin", "usr/bin"), ("lib", "usr/lib"), ("lib64", "usr/lib64"),
                                                      ("sbin", "usr/sbin"))]
        lines.append('bro() { mount --bind "$1" "$2"; mount -o remount,bind,ro "$2"; }')
        lines.append('brw() { mount --bind "$1" "$2"; }')
        lines += [f'bro {q(os.path.join(self.rootfs, "usr"))} "$R/usr"', f'bro {q(os.path.join(self.rootfs, "etc"))} "$R/etc"']
        for x in MASKED_EXECUTABLES:                       # font discovery eliminated from the execution path
            if os.path.lexists(os.path.join(self.rootfs, x)):
                lines.append(f'mount --bind /dev/null "$R/{x}"')
        for d in FONT_DIRS:
            if os.path.isdir(os.path.join(self.rootfs, d)):
                lines.append(f'mount -t tmpfs -o ro,size=4k,mode=0755 v3b-nofonts "$R/{d}"')
        lines.append('mount -t proc proc "$R/proc"')
        lines.append('mount -t tmpfs -o mode=0755,size=1m v3b-dev "$R/dev"')
        for n in DEV_NODES:
            lines.append(f'touch "$R/dev/{n}"; mount --bind /dev/{n} "$R/dev/{n}"')
        lines += ['mkdir "$R/dev/shm"; mount -t tmpfs -o mode=1777,size=512m v3b-shm "$R/dev/shm"',
                  'ln -s /proc/self/fd "$R/dev/fd"; ln -s /proc/self/fd/0 "$R/dev/stdin"',
                  'ln -s /proc/self/fd/1 "$R/dev/stdout"; ln -s /proc/self/fd/2 "$R/dev/stderr"']
        lines += [f'brw {q(self.tree)} "$R/v3b/tree"', f'brw {q(self.dirs["home"])} "$R/v3b/run/home"',
                  f'brw {q(self.dirs["tmp"])} "$R/v3b/run/tmp"', f'brw {q(self.dirs["io"])} "$R/v3b/io"',
                  f'bro {q(self.dirs["pin.git"])} "$R/v3b/run/pin.git"', f'bro {q(self.dirs["code"])} "$R/v3b/code"']
        if phase == "setup":
            lines += [f'brw {q(self.dirs["venv"])} "$R/v3b/run/venv"', f'bro {q(self.wheel_dir)} "$R/v3b/wheels"']
        else:
            lines.append(f'bro {q(self.dirs["venv"])} "$R/v3b/run/venv"')
            ca = self._host_ca()
            if self.mode == "record" and ca:
                lines.append(f'touch "$R/v3b/netca/ca.pem"; bro {q(ca)} "$R/v3b/netca/ca.pem"')
        lines.append('mount -o remount,ro "$R"')
        envs = " ".join(q(f"{k}={v}") for k, v in sorted(env.items()))
        cmd = " ".join(q(a) for a in argv)
        lines.append(f'exec chroot --userspec={UID}:{GID} --groups={GID} "$R" /usr/bin/env -i {envs} '
                     f'/bin/sh -c {q("cd /v3b/tree && exec " + cmd)}')
        return "\n".join(lines) + "\n"

    def _exec(self, phase, argv, trace_path, timeout_s, network):
        env, passed = self.env(phase)
        script = os.path.join(self.root, f"{phase}.sh")
        with open(script, "w") as fh:
            fh.write(self._script(phase, argv, env))
        ns = ["unshare", "--mount", "--pid", "--fork", "--propagation", "private"] + ([] if network else ["--net"])
        cmd = ["strace", "-f", "-qq", "--pidns-translation", "-e", f"trace={TRACE_SYSCALLS}", "-e", "signal=none",
               "-o", trace_path, *ns, "/bin/sh", script]
        proc = subprocess.run(cmd, timeout=timeout_s, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
        return proc, sorted(env), passed

    def setup(self, trace_path):
        """Build the venv INSIDE the pinned runtime from the hash lock (no network, no index), then verify the
        installed set equals the lock exactly (fail closed)."""
        argv = ["/bin/sh", "-c",
                "set -e; /usr/local/bin/python3.11 -m venv /v3b/run/venv; "
                "/v3b/run/venv/bin/python -m pip install -q --no-index --find-links /v3b/wheels --no-deps "
                "--only-binary=:all: --require-hashes -r /v3b/code/lock.txt; "
                "/v3b/run/venv/bin/python -I -c \"import json, importlib.metadata as m, sys, platform; "
                "json.dump({'dists': sorted({(d.metadata['Name'], d.version) for d in m.distributions()}), "
                "'python': platform.python_version(), 'executable': sys.executable}, "
                "open('/v3b/run/venv/.v3b_installed.json', 'w'))\"; rm -rf /v3b/run/venv/.piphome"]
        proc, _, _ = self._exec("setup", argv, trace_path, 1200, network=False)
        info_p = os.path.join(self.dirs["venv"], ".v3b_installed.json")
        if proc.returncode or not os.path.exists(info_p):
            raise SandboxError(f"locked install inside the pinned runtime failed (rc {proc.returncode}): "
                               f"{proc.stderr.decode(errors='replace')[-1500:]}")
        info = json.load(open(info_p))
        inst = [(n, v) for n, v in info["dists"]]
        pins = ISO.lock_pins(self.lock_path)
        got = {ISO._norm(n): v for n, v in inst if ISO._norm(n) not in ISO.VENV_BOOTSTRAP}
        if got != pins:
            raise ISO.IsolationError(f"installed set != lock (extra={sorted(set(got) - set(pins))}, "
                                     f"missing={sorted(set(pins) - set(got))})")
        lines = "\n".join(f"{n}=={v}" for n, v in sorted((ISO._norm(n), v) for n, v in inst)) + "\n"
        self.deps = {"installed_set_sha256": ISO.sha256_bytes(lines.encode()), "n_locked": len(pins),
                     "bootstrap": sorted(f"{ISO._norm(n)}=={v}" for n, v in inst if ISO._norm(n) in ISO.VENV_BOOTSTRAP),
                     "python_in_runtime": info["python"]}
        if info["python"] != self.image["python_version"]:
            raise SandboxError(f"runtime python {info['python']} != image {self.image['python_version']}")
        self.setup_trace = summarize_trace(trace_path, self)
        # R1/R2 (inherited): HOME/TMP must still be empty when the pipeline starts
        if not (ISO._dir_empty(self.dirs["home"]) and ISO._dir_empty(self.dirs["tmp"])):
            raise ISO.IsolationError("ISOLATION_BREACH: setup left state in HOME/TMP")
        return self.deps

    def run(self, argv, trace_path, timeout_s=1800):
        proc, env_keys, passed = self._exec("run", argv, trace_path, timeout_s, network=(self.mode == "record"))
        self.trace = summarize_trace(trace_path, self)
        self.fingerprint = {
            "b_version": B_VERSION, "a1_version": ISO.A1_VERSION, "mode": self.mode,
            "runtime_image": self.image, "lock_sha256": ISO.sha256_bytes(open(self.lock_path, "rb").read()),
            **self.deps, "python": self.deps["python_in_runtime"], "machine": platform.machine(),
            "isolation": self.isolation, "git_identity": self.git_identity, "amendment_code_sha256": self.code_sha256,
            "env_keys": env_keys, "passthrough_env_keys": passed, "determinism_env": dict(DETERMINISM_ENV),
            "network": "host egress (record only; proxy)" if self.mode == "record" else "none (own network namespace)",
            "user": f"{UID}:{GID}", "font_discovery": {"masked_executables": list(MASKED_EXECUTABLES),
                                                       "emptied_font_dirs": list(FONT_DIRS)},
            "kernel_surfaces": kernel_fingerprint(),
            "process_trace": self.trace, "setup_trace": self.setup_trace}
        return proc


def check_compatible(rec, rep):
    """Replay must run the identical pinned runtime, lock, installed set, interpreter, code and git identity, with
    clean isolation and zero forbidden reads on both sides. Kernel surfaces may differ (recorded, not pinned)."""
    def get(d, path):
        for p in path:
            d = (d or {}).get(p)
        return d
    problems = []
    for path in (("b_version",), ("runtime_image", "manifest_digest"), ("runtime_image", "rootfs_tree_sha256"),
                 ("lock_sha256",), ("installed_set_sha256",), ("python",), ("git_identity", "head"),
                 ("git_identity", "tree"), ("git_identity", "injected"), ("amendment_code_sha256",)):
        if get(rec, path) != get(rep, path) or get(rec, path) is None:
            problems.append(f"{'.'.join(path)}: record={get(rec, path)} replay={get(rep, path)}")
    for side, fp in (("record", rec), ("replay", rep)):
        if not fp.get("isolation") or not all(fp["isolation"].values()):
            problems.append(f"{side}: isolation flags {fp.get('isolation')}")
        for t in ("process_trace", "setup_trace"):
            if get(fp, (t, "forbidden_reads")) != 0:
                problems.append(f"{side}: {t} forbidden reads {get(fp, (t, 'forbidden_reads'))}")
    return problems


def kernel_fingerprint():
    """KERNEL_VIRTUAL surfaces are not cryptographically pinned; record what varies across machines."""
    u = os.uname()
    return {"kernel_release": u.release, "kernel_version": u.version, "machine": u.machine, "cpu_count": os.cpu_count(),
            "pinned": False}


# ---- trace classification -----------------------------------------------------------------------------------------
_LINE = re.compile(r'^(\d+)\s+(.*)$')
_CALL = re.compile(r'^(openat|open|execve|chroot|clone3?|v?fork)\((.*)$')
_RESUMED = re.compile(r'^<\.\.\. (openat|open|execve|chroot|clone3?|v?fork) resumed>(.*)$')
_PATHARG = re.compile(r'^(?:AT_FDCWD, |(\d+), )?"((?:[^"\\]|\\.)*)"')
_RES = re.compile(r'= (-?\d+)(?: /\* (\d+) in strace\'s PID NS \*/)?')


def classify(path, sb):
    p = os.path.normpath(path if path.startswith("/") else os.path.join("/v3b/tree", path))
    under = lambda b: p == b or p.startswith(b.rstrip("/") + "/")
    if sb is not None and sb.overlay_rel and p == os.path.join("/v3b/tree", sb.overlay_rel):
        return "SEALED_OVERLAY"
    for base, cls in (("/v3b/tree", "PINNED_SOURCE_TREE"), ("/v3b/run/pin.git", "PINNED_GIT_OBJECTS"),
                      ("/v3b/run/home", "ISOLATED_RUN_STATE"), ("/v3b/run/tmp", "ISOLATED_RUN_STATE"),
                      ("/v3b/run/venv", "LOCKED_DEPENDENCIES"), ("/v3b/wheels", "LOCKED_DEPENDENCIES"),
                      ("/v3b/io", "SEALED_TAPE_AND_REPORT"), ("/v3b/code", "AMENDMENT_CODE"),
                      ("/v3b/netca", "RECORD_NETWORK_TRUST"), ("/dev/shm", "ISOLATED_RUN_STATE"),
                      ("/proc", "KERNEL_VIRTUAL"), ("/dev", "KERNEL_VIRTUAL"),
                      ("/usr", "PINNED_RUNTIME_IMAGE"), ("/etc", "PINNED_RUNTIME_IMAGE"), ("/bin", "PINNED_RUNTIME_IMAGE"),
                      ("/lib", "PINNED_RUNTIME_IMAGE"), ("/lib64", "PINNED_RUNTIME_IMAGE"), ("/sbin", "PINNED_RUNTIME_IMAGE")):
        if under(base):
            return cls
    if p in ("/", "/v3b", "/v3b/run", "/tmp", "/root", "/home"):
        return "SANDBOX_SKELETON"            # empty directories of the read-only tmpfs root
    if any(under(b) for b in ("/var", "/tmp", "/root", "/home", "/run", "/opt", "/mnt", "/media", "/srv", "/sys")):
        return "FORBIDDEN_MUTABLE_STATE"
    return "UNCLASSIFIED"


def summarize_trace(trace_path, sb=None, examples=10):
    """Exact process attribution: a process is SANDBOXED once it (or an ancestor, via clone/fork after the
    ancestor was sandboxed) executed chroot() successfully. Everything earlier is the host LAUNCHER."""
    sandboxed, pending, opens, launcher = set(), {}, [], {"execs": set(), "calls": 0}
    for raw in open(trace_path, errors="replace"):
        m = _LINE.match(raw.rstrip("\n"))
        if not m:
            continue
        pid, rest = m.group(1), m.group(2)
        c = _CALL.match(rest)
        if c:
            call, args = c.group(1), c.group(2)
            if args.endswith("<unfinished ...>"):
                pending[(pid, call)] = args
                continue
        else:
            r = _RESUMED.match(rest)
            if not r or (pid, r.group(1)) not in pending:
                continue
            call, args = r.group(1), pending.pop((pid, r.group(1))) + r.group(2)
        res = _RES.search(args.rsplit(")", 1)[-1] if ")" in args else args) or _RES.search(args)
        if not res:
            continue
        rv, host_child = int(res.group(1)), res.group(2)
        if call in ("clone", "clone3", "fork", "vfork"):
            if rv > 0 and pid in sandboxed:
                sandboxed.add(host_child or str(rv))
            continue
        if call == "chroot":
            if rv == 0:
                sandboxed.add(pid)
            continue
        if rv < 0:
            continue                                          # failed probes read nothing
        pa = _PATHARG.match(args)
        if not pa:
            continue
        path = pa.group(2).encode().decode("unicode_escape")
        if pid not in sandboxed:
            launcher["calls"] += 1
            if call == "execve":
                launcher["execs"].add(os.path.basename(path))
            continue
        if pa.group(1):                                       # dirfd-relative: never silently classified
            opens.append((call, f"<dirfd {pa.group(1)}>/{path}", False, "UNCLASSIFIED"))
            continue
        write = call != "execve" and any(f in args for f in ("O_WRONLY", "O_RDWR|O_CREAT", "O_CREAT", "O_TRUNC"))
        opens.append((call, path, write, classify(path, sb)))
    by, ex, execs = {}, {}, set()
    for call, path, write, cls in opens:
        k = by.setdefault(cls, {"reads": 0, "writes": 0, "execs": 0, "unique": set()})
        k["execs" if call == "execve" else ("writes" if write else "reads")] += 1
        k["unique"].add(path)
        if call == "execve":
            execs.add(path)
        lst = ex.setdefault(cls, [])
        if path not in lst and len(lst) < examples:
            lst.append(path)
    kernel_paths = sorted(by.get("KERNEL_VIRTUAL", {}).get("unique", set()))
    forbidden = {c: by[c]["reads"] + by[c]["writes"] + by[c]["execs"] for c in FORBIDDEN_CLASSES if c in by}
    return {"method": f"host strace -f --pidns-translation -e trace={TRACE_SYSCALLS}; successful calls of sandboxed "
                      "processes only (post-chroot), classified by enumerated mount",
            "n_successful_calls": len(opens), "executables": sorted(execs),
            "by_class": {c: {"reads": v["reads"], "writes": v["writes"], "execs": v["execs"], "unique_paths": len(v["unique"])}
                         for c, v in sorted(by.items())},
            "examples": {c: ex[c] for c in sorted(ex)},
            "kernel_virtual_paths": kernel_paths[:200], "n_kernel_virtual_paths": len(kernel_paths),
            "forbidden_or_unclassified": forbidden, "forbidden_reads": sum(forbidden.values()),
            "launcher_pre_chroot": {"calls": launcher["calls"], "executables": sorted(launcher["execs"])}}
