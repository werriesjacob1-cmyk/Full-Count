#!/usr/bin/env python3
"""FC-MLB-001B tests + mutants (criteria TASKS/FC-MLB-001B.md §8.2).

A stub pipeline (a git repo with generate_picks.py) runs through the REAL pinned-runtime sandbox (digest-pinned image,
namespaces, chroot, unprivileged, whole-tree strace) and the real netrecord record/replay. It caches HTTP answers
under $PYBASEBALL_CACHE like the frozen pin, stamps `git rev-parse --short HEAD`, and can be told to attempt
forbidden or subprocess reads. Store tests use a local mock S3 server that implements create-only PUT. Requires root
(sandbox) and network for the first image/wheel fetch (cached afterwards)."""
import gzip
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import isolation as ISO  # noqa: E402
import payload as PL  # noqa: E402
import runtime_image as RI  # noqa: E402
import sandbox as SB  # noqa: E402
import shadow as SH  # noqa: E402
import tape_store as TS  # noqa: E402

TEST_LOCK = os.path.join(HERE, "test-a1-requirements.lock")
PAYLOAD = {"/a": "alpha-live", "/b": "bravo-live"}

STUB = r'''
import hashlib, json, os, socket, subprocess, requests
cfg = json.load(open("stub_config.json"))
cache = os.environ.get("PYBASEBALL_CACHE") or os.path.join(os.path.expanduser("~"), ".pybaseball", "cache")
os.makedirs(cache, exist_ok=True)
vals, side = [], {}
for u in cfg["urls"]:
    f = os.path.join(cache, hashlib.sha256(u.encode()).hexdigest())
    if os.path.exists(f):
        vals.append(open(f).read())
    else:
        try:
            t = requests.get(u, timeout=5).text
            open(f, "w").write(t)
        except Exception as exc:
            t = "ERR " + type(exc).__name__
        vals.append(t)
for p in cfg.get("read_paths", []):                 # attempted reads of host / forbidden state
    try:
        side[p] = open(p).read()[:20]
    except Exception as exc:
        side[p] = "ERR " + type(exc).__name__
for argv in cfg.get("subprocess", []):              # subprocess (and native) reads
    try:
        side[" ".join(argv)] = subprocess.run(argv, capture_output=True, text=True).returncode
    except Exception as exc:
        side[" ".join(argv)] = "ERR " + type(exc).__name__
if cfg.get("raw_socket"):                           # bypasses netrecord entirely
    try:
        socket.create_connection(("127.0.0.1", cfg["raw_socket"]), timeout=3).close()
        side["raw_socket"] = "CONNECTED"
    except Exception as exc:
        side["raw_socket"] = "ERR " + type(exc).__name__
sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
os.makedirs("output", exist_ok=True)
board = {"board_generated_at": "2026-01-01T00:00:00+00:00", "sealed_at": "2026-01-01T00:00:01+00:00",
         "board_sha256": "x", "records": [{"v": v, "generation_timestamp": "2026-01-01T00:00:00+00:00"} for v in vals],
         "provenance": {"git_sha": sha}}
json.dump(board, open("output/board_freeze_stub.json", "w"), sort_keys=True)
json.dump(side, open("output/side.json", "w"), sort_keys=True)
'''


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAYLOAD.get(self.path, "nope").encode()
        self.send_response(200 if self.path in PAYLOAD else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, *a], check=True, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                               "HOME": "/nonexistent"}).stdout.strip()


@unittest.skipUnless(os.geteuid() == 0, "001B sandbox tests need root (CI: sudo)")
class Sandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = tempfile.mkdtemp(prefix="btest_")
        os.chmod(cls.base, 0o755)
        os.environ["V3A1_ISOLATION_BASE"] = os.path.join(cls.base, "iso")
        cls.origin = os.path.join(cls.base, "origin")
        os.makedirs(cls.origin)
        _git(cls.origin, "init", "-q")
        open(os.path.join(cls.origin, "generate_picks.py"), "w").write(STUB)
        for i in range(3):
            open(os.path.join(cls.origin, "n.txt"), "w").write(str(i))
            _git(cls.origin, "add", "-A")
            _git(cls.origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"c{i}")
        cls.full_sha = _git(cls.origin, "rev-parse", "HEAD")
        cls.runtime = RI.ensure_rootfs(SB._cache_dirs()[0])

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.base, ignore_errors=True)

    def tree(self, name, shallow=False, cfg=None):
        w = os.path.join(self.base, name)
        subprocess.check_call(["git", "clone", "-q", *(["--depth", "1"] if shallow else []), "file://" + self.origin, w],
                              env={"PATH": "/usr/bin:/bin", "HOME": self.base, "GIT_CONFIG_NOSYSTEM": "1"})
        urls = [f"http://127.0.0.1:{self.srv.server_port}{p}" for p in sorted(PAYLOAD)]
        with open(os.path.join(w, "stub_config.json"), "w") as fh:
            json.dump({"urls": urls, **(cfg or {})}, fh)
        return w

    def run_b(self, w, tape, mode):
        return json.load(open(SH.run_pipeline_b(w, tape, mode, script="generate_picks.py", lock_path=TEST_LOCK)))

    def env_of(self, tape, mode):
        return json.load(open(tape + f".{mode}.env.json"))

    def dirty_host(self):
        h = tempfile.mkdtemp(prefix="hosthome_", dir=self.base)
        c = os.path.join(h, ".pybaseball", "cache")
        os.makedirs(c)
        for p in PAYLOAD:
            u = f"http://127.0.0.1:{self.srv.server_port}{p}"
            open(os.path.join(c, hashlib.sha256(u.encode()).hexdigest()), "w").write("STALE-HOST-CACHE")
        return mock.patch.dict(os.environ, {"HOME": h, "PYBASEBALL_CACHE": c})

    # ---- record/replay inside the pinned runtime ------------------------------------------------------------
    def test_record_replay_exact_payload_identity_and_runtime_fingerprint(self):
        w = self.tree("rr")
        tape = os.path.join(self.base, "rr.json.gz")
        with self.dirty_host():                                   # inherited I1: host cache never consulted
            rec = self.run_b(w, tape, "record")
        self.assertEqual([r["v"] for r in rec["records"]], ["alpha-live", "bravo-live"])
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        with self.dirty_host():
            rep = self.run_b(w, tape, "replay")
        cmp = PL.compare(PL.payload_bytes(rec), rep)
        self.assertTrue(cmp["literal_identical"], cmp)
        e_rec, e_rep = self.env_of(tape, "record"), self.env_of(tape, "replay")
        for e in (e_rec, e_rep):
            self.assertEqual(e["runtime_image"]["manifest_digest"], RI.MANIFEST_DIGEST)
            self.assertEqual(e["process_trace"]["forbidden_reads"], 0)
            self.assertTrue(all(e["isolation"].values()))
            self.assertEqual(e["user"], "65534:65534")
            self.assertIn("PINNED_RUNTIME_IMAGE", e["process_trace"]["by_class"])
            self.assertIn("LOCKED_DEPENDENCIES", e["process_trace"]["by_class"])
        self.assertEqual((e_rep["replay_misses"], e_rep["unconsumed"]), (0, 0))
        self.assertEqual(e_rep["network"], "none (own network namespace)")
        self.assertEqual(SB.check_compatible(e_rec, e_rep), [])

    def test_provenance_full_shallow_hostile_abbrev(self):
        """Inherited I3 under the pinned runtime: frozen GIT_CONFIG_* core.abbrev=10, no shim."""
        shas = {}
        for name, shallow, abbrev in (("p_full", False, None), ("p_shallow", True, None), ("p_ab4", True, "4"),
                                      ("p_ab12", False, "12")):
            w = self.tree(name, shallow=shallow)
            if abbrev:
                _git(w, "config", "core.abbrev", abbrev)
            shas[name] = self.run_b(w, os.path.join(self.base, name + ".json.gz"), "record")["provenance"]["git_sha"]
        self.assertEqual(set(shas.values()), {self.full_sha[:10]}, shas)

    def test_host_state_is_unreachable_and_subprocess_reads_are_traced(self):
        secret = os.path.join(self.base, "host_secret.txt")
        open(secret, "w").write("secret")
        os.chmod(secret, 0o644)
        cfg = {"read_paths": [secret, "/root/.bashrc", "/var/cache/fontconfig/x", "/etc/os-release"],
               "subprocess": [["cat", "/etc/os-release"], ["fc-list"]]}
        w = self.tree("hs", cfg=cfg)
        tape = os.path.join(self.base, "hs.json.gz")
        self.run_b(w, tape, "record")
        side = json.load(open(os.path.join(w, "output", "side.json")))
        self.assertTrue(str(side[secret]).startswith("ERR"))          # host files do not exist inside
        self.assertTrue(str(side["/root/.bashrc"]).startswith("ERR"))
        self.assertTrue(str(side["/var/cache/fontconfig/x"]).startswith("ERR"))
        self.assertTrue(str(side["fc-list"]).startswith("ERR"))       # font discovery masked
        tr = self.env_of(tape, "record")["process_trace"]
        self.assertIn("/usr/bin/cat", tr["executables"])              # the subprocess is in the trace
        self.assertNotIn("/usr/bin/fc-list", tr["executables"])
        self.assertGreater(tr["by_class"]["PINNED_RUNTIME_IMAGE"]["reads"], 0)   # os-release came from the image
        self.assertNotEqual(side["/etc/os-release"], "")
        self.assertEqual(tr["forbidden_reads"], 0)

    def test_mutant_leaked_host_mount_is_detected_and_fails_closed(self):
        """Mutant: a misconfigured sandbox exposes a host directory at /var/cache/leak; a read of it is
        FORBIDDEN_MUTABLE_STATE and the run fails closed."""
        leak = tempfile.mkdtemp(prefix="leak_", dir=self.base)
        os.chmod(leak, 0o755)
        open(os.path.join(leak, "fontcache"), "w").write("host-cache")
        os.chmod(os.path.join(leak, "fontcache"), 0o644)
        w = self.tree("leak", cfg={"read_paths": ["/var/cache/leak/fontcache"]})
        real = SB.SandboxRun._script

        def leaky(sb, phase, argv, env):
            s = real(sb, phase, argv, env)
            return s.replace('mount -o remount,ro "$R"\n',
                             f'mkdir -p "$R/var/cache/leak"; mount --bind {leak} "$R/var/cache/leak"\nmount -o remount,ro "$R"\n')
        with mock.patch.object(SB.SandboxRun, "_script", leaky):
            with self.assertRaisesRegex(RuntimeError, "forbidden/unclassified reads"):
                self.run_b(w, os.path.join(self.base, "leak.json.gz"), "record")

    def test_replay_has_no_network(self):
        w = self.tree("net", cfg={"raw_socket": self.srv.server_port})
        tape = os.path.join(self.base, "net.json.gz")
        self.run_b(w, tape, "record")
        self.assertEqual(json.load(open(os.path.join(w, "output", "side.json")))["raw_socket"], "CONNECTED")
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        self.run_b(w, tape, "replay")
        self.assertTrue(json.load(open(os.path.join(w, "output", "side.json")))["raw_socket"].startswith("ERR"))

    def test_replay_missing_and_unconsumed_fail(self):
        w = self.tree("mu")
        tape = os.path.join(self.base, "mu.json.gz")
        self.run_b(w, tape, "record")
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        t = json.load(gzip.open(tape))
        t["http"].pop(sorted(t["http"])[0])
        with gzip.GzipFile(tape, "wb", mtime=0) as fh:
            fh.write(json.dumps(t, sort_keys=True).encode())
        with self.assertRaisesRegex(RuntimeError, "misses=1 unconsumed=0"):
            self.run_b(w, tape, "replay")

    def test_lock_hash_mismatch_fails_closed(self):
        bad = os.path.join(self.base, "bad.lock")
        txt = open(TEST_LOCK).read()
        i = txt.index("--hash=sha256:") + len("--hash=sha256:")
        open(bad, "w").write(txt[:i] + ("0" if txt[i] != "0" else "1") + txt[i + 1:])
        w = self.tree("lk")
        with self.assertRaises((SB.SandboxError, ISO.IsolationError)):
            SH.run_pipeline_b(w, os.path.join(self.base, "lk.json.gz"), "record", lock_path=bad)

    def test_prepopulated_root_is_a_breach(self):
        w = self.tree("pp")
        with SB.SandboxRun("record", w, TEST_LOCK, runtime=self.runtime) as sb:
            pass
        real_mkdtemp = tempfile.mkdtemp

        def dirty(*a, **k):
            d = real_mkdtemp(*a, **k)
            os.makedirs(os.path.join(d, "home", ".pybaseball", "cache"))
            open(os.path.join(d, "home", ".pybaseball", "cache", "x"), "w").write("stale")
            return d
        with mock.patch.object(SB.tempfile, "mkdtemp", dirty):
            with self.assertRaisesRegex(ISO.IsolationError, "ISOLATION_BREACH"):
                with SB.SandboxRun("record", w, TEST_LOCK, runtime=self.runtime):
                    pass
        self.assertFalse(os.path.exists(sb.root))


class Offline(unittest.TestCase):
    """Pure tests: payload contract, compatibility, trace classifier, runtime identity, store semantics."""

    BOARD = {"board_generated_at": "2026-10-06T15:00:00+00:00", "sealed_at": "2026-10-06T15:00:01+00:00",
             "board_sha256": "h", "date": "2026-10-06", "provenance": {"git_sha": "7d3ebacd55"},
             "records": [{"player_id": 1, "prediction": {"p": 0.25}, "selector": {"rank": 1},
                          "generation_timestamp": "2026-10-06T15:00:00+00:00"},
                         {"player_id": 2, "prediction": {"p": 0.5}, "selector": {"rank": 2},
                          "generation_timestamp": "2026-10-06T15:00:00+00:00"}]}

    def test_payload_spec_frozen_bytes(self):
        b = PL.payload_bytes(self.BOARD)
        self.assertEqual(b, b'{"date":"2026-10-06","provenance":{"git_sha":"7d3ebacd55"},"records":[{"player_id":1,'
                            b'"prediction":{"p":0.25},"selector":{"rank":1}},{"player_id":2,"prediction":{"p":0.5},'
                            b'"selector":{"rank":2}}]}')
        self.assertEqual(PL.SPEC, "fc-mlb-001b-payload-v1")
        self.assertEqual((PL.ENVELOPE_TOP, PL.ENVELOPE_RECORD),
                         (("board_generated_at", "sealed_at", "board_sha256"), ("generation_timestamp",)))

    def test_envelope_only_change_keeps_bytes_scientific_change_breaks_them(self):
        env = json.loads(json.dumps(self.BOARD))
        env["board_generated_at"] = env["sealed_at"] = "2030-01-01T00:00:00+00:00"
        env["board_sha256"] = "other"
        for r in env["records"]:
            r["generation_timestamp"] = "2030-01-01T00:00:00+00:00"
        self.assertTrue(PL.compare(PL.payload_bytes(self.BOARD), env)["literal_identical"])
        for mutate in (lambda b: b["records"][0]["prediction"].update(p=0.2500000000000001),
                       lambda b: b["records"].reverse(), lambda b: b["provenance"].update(git_sha="0000000000"),
                       lambda b: b["records"][1]["selector"].update(rank=3), lambda b: b.update(extra=None)):
            m = json.loads(json.dumps(self.BOARD))
            mutate(m)
            c = PL.compare(PL.payload_bytes(self.BOARD), m)
            self.assertFalse(c["literal_identical"])
            self.assertNotEqual(c["record_payload_sha256"], c["replay_payload_sha256"])

    def test_nan_payload_refused(self):
        b = json.loads(json.dumps(self.BOARD))
        b["records"][0]["prediction"]["p"] = float("nan")
        with self.assertRaises(PL.PayloadError):
            PL.payload_bytes(b)

    def test_envelope_chronology(self):
        h = lambda b: "h"
        ok = PL.envelope_chronology(self.BOARD, "2026-10-06T15:05:00+00:00", {"t": "2026-10-06T15:06:00+00:00"},
                                    "2026-10-06T22:00:00Z", h)
        self.assertEqual(ok, [])
        bad = json.loads(json.dumps(self.BOARD))
        bad["records"][0]["generation_timestamp"] = "2026-10-06T14:00:00+00:00"
        self.assertTrue(PL.envelope_chronology(bad, "2026-10-06T15:05:00+00:00", {"t": "2026-10-06T23:00:00+00:00"},
                                               "2026-10-06T22:00:00Z", h))

    def test_classifier(self):
        c = lambda p: SB.classify(p, None)
        self.assertEqual(c("/usr/lib/x86_64-linux-gnu/libm.so.6"), "PINNED_RUNTIME_IMAGE")
        self.assertEqual(c("/v3b/run/venv/lib/python3.11/site-packages/numpy/__init__.py"), "LOCKED_DEPENDENCIES")
        self.assertEqual(c("/v3b/run/home/.pybaseball/cache/x"), "ISOLATED_RUN_STATE")
        self.assertEqual(c("/proc/self/status"), "KERNEL_VIRTUAL")
        for forbidden in ("/var/cache/fontconfig/x.cache-9", "/root/.cache/pip/x", "/home/u/.pybaseball/cache/x",
                          "/tmp/prior_run/x", "/sys/devices/system/cpu/online"):
            self.assertEqual(c(forbidden), "FORBIDDEN_MUTABLE_STATE", forbidden)
        self.assertEqual(c("/weird/place"), "UNCLASSIFIED")

    def test_trace_attribution_launcher_vs_sandbox(self):
        t = tempfile.mktemp()
        open(t, "w").write(
            '10 execve("/usr/bin/unshare", ["unshare"], 0x0 /* 1 vars */) = 0\n'
            '10 openat(AT_FDCWD, "/var/cache/hostthing", O_RDONLY) = 3\n'
            '10 clone(child_stack=NULL, flags=SIGCHLD) = 11\n'
            '11 chroot("/x/mnt") = 0\n'
            '11 openat(AT_FDCWD, "/usr/lib/libc.so.6", O_RDONLY|O_CLOEXEC) = 3\n'
            '11 clone(child_stack=NULL, flags=SIGCHLD) = 2 /* 12 in strace\'s PID NS */\n'
            '12 openat(AT_FDCWD, "/var/cache/fontconfig/a", O_RDONLY <unfinished ...>\n'
            '11 openat(AT_FDCWD, "/nope", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
            '12 <... openat resumed>) = 4\n'
            '12 openat(5, "rel", O_RDONLY) = 6\n')
        s = SB.summarize_trace(t)
        self.assertEqual(s["launcher_pre_chroot"]["calls"], 2)               # host launcher, not the pipeline
        self.assertEqual(s["forbidden_or_unclassified"], {"FORBIDDEN_MUTABLE_STATE": 1, "UNCLASSIFIED": 1})
        self.assertEqual(s["by_class"]["PINNED_RUNTIME_IMAGE"]["reads"], 1)

    def test_compatibility_requires_same_image_lock_and_code(self):
        rec = {"b_version": SB.B_VERSION, "runtime_image": {"manifest_digest": "sha256:a", "rootfs_tree_sha256": "r"},
               "lock_sha256": "l", "installed_set_sha256": "i", "python": "3.11.17",
               "git_identity": {"head": "h", "tree": "t", "injected": dict(ISO.GIT_INJECTED_CONFIG)},
               "amendment_code_sha256": {"netrecord.py": "n"}, "isolation": {"a": True},
               "process_trace": {"forbidden_reads": 0}, "setup_trace": {"forbidden_reads": 0}}
        self.assertEqual(SB.check_compatible(rec, json.loads(json.dumps(rec))), [])
        for path, val in ((("runtime_image", "manifest_digest"), "sha256:b"), (("runtime_image", "rootfs_tree_sha256"), "x"),
                          (("lock_sha256",), "z"), (("installed_set_sha256",), "z"), (("python",), "3.11.18"),
                          (("amendment_code_sha256", "netrecord.py"), "m"), (("git_identity", "head"), "g"),
                          (("process_trace", "forbidden_reads"), 1)):
            rep = json.loads(json.dumps(rec))
            d = rep
            for p in path[:-1]:
                d = d[p]
            d[path[-1]] = val
            self.assertTrue(SB.check_compatible(rec, rep), path)

    def test_runtime_identity_detects_drift(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "usr", "bin"))
        open(os.path.join(d, "usr", "bin", "x"), "w").write("a")
        os.symlink("usr/bin", os.path.join(d, "bin"))
        h1 = RI.rootfs_tree_sha256(d)
        self.assertEqual(h1, RI.rootfs_tree_sha256(d))
        open(os.path.join(d, "usr", "bin", "x"), "w").write("b")
        self.assertNotEqual(h1, RI.rootfs_tree_sha256(d))

    def test_runtime_digest_mismatch_fails_closed(self):
        with mock.patch.object(RI, "MANIFEST_DIGEST", "sha256:" + "0" * 64):
            with self.assertRaises(RI.RuntimeImageError):
                RI.fetch_image(tempfile.mkdtemp())


class DrillPath(unittest.TestCase):
    """The REAL runner record path + b_drill record + b_drill verify, with only the network-facing inputs and the
    sandbox replay stubbed (the sandbox itself is covered by Sandbox). Exercises: payload sealing, the content-
    addressed store, the ONE-file Actions-artifact transport, the committed frozen record, envelope chronology,
    and fail-closed verification of a corrupted transport file."""

    def test_record_then_verify_then_corrupted_transport_fails(self):
        import datetime as dt
        import b_drill as BD
        import capture as CP
        import runner as RN
        import seal as SL
        import test_v3 as T3
        import verify_evidence as VE
        base = tempfile.mkdtemp(prefix="bdrill_")
        now = datetime_now = dt.datetime.now(dt.timezone.utc)
        day = (now + dt.timedelta(days=1)).date().isoformat()
        start = f"{day}T23:05:00Z"
        recs = [T3.rec("c1", name="Al Bat"), T3.rec("c2", name="Bo Bat", player="12", p=0.55)]
        gen = (datetime_now - dt.timedelta(minutes=2)).isoformat()
        for r in recs:
            r["generation_timestamp"] = gen
        b = {"date": day, "board_generated_at": gen, "sealed_at": gen, "provenance": dict(T3.PROV), "records": recs}
        b["board_sha256"] = VE.H.canonical_board_hash(b)
        sch = T3.autofill_rosters(T3.sched(T3.game(1, start=start), date=day), recs)
        c = T3.cap(T3.event(101, [T3.market(T3.HIT, [T3.runner_("Al Bat", -120), T3.runner_("Bo Bat", 110, sel=2)])],
                            open_date=start, completed=now.isoformat(), date=day),
                   started=now.isoformat(), completed=now.isoformat())
        tape_bytes = os.urandom(50_000)
        env = {"runtime_image": {"manifest_digest": RI.MANIFEST_DIGEST, "rootfs_tree_sha256": "r"},
               "b_version": SB.B_VERSION, "lock_sha256": "l", "installed_set_sha256": "i", "python": "3.11.17",
               "git_identity": {"head": SH.SHADOW_PIN, "tree": SH.SHADOW_TREE, "injected": dict(ISO.GIT_INJECTED_CONFIG)},
               "amendment_code_sha256": {"netrecord.py": "n"}, "isolation": {"home_empty_at_start": True},
               "kernel_surfaces": SB.kernel_fingerprint(), "user": "65534:65534", "network": "host egress",
               "n_http": 3, "process_trace": {"forbidden_reads": 0, "by_class": {}, "kernel_virtual_paths": ["/proc/stat"],
                                              "n_kernel_virtual_paths": 1},
               "setup_trace": {"forbidden_reads": 0}}
        bp = os.path.join(base, "b.json")
        json.dump(b, open(bp, "w"))

        def fake_pipeline(tree, tape, mode, **k):
            open(tape, "wb").write(tape_bytes)
            json.dump(env, open(tape + ".record.env.json", "w"))
            json.dump({"n_http": 3, "replay_misses": [], "unconsumed": 0}, open(tape + ".record.report.json", "w"))
            return bp
        prov = {"shadow_id": SH.SHADOW_ID, "pin": SH.SHADOW_PIN, "tree": SH.SHADOW_TREE,
                "overlay": {"status": "ABSENT_ON_LIVE_REF", "sealed_sha256": None}}
        out = os.path.join(base, "B_DRILL_TEST")
        tsa_t = (now + dt.timedelta(minutes=1)).replace(microsecond=0)
        with mock.patch.object(RN, "schedule_snapshot", lambda d: sch), \
                mock.patch.object(SH, "build_tree", lambda *a, **k: json.loads(json.dumps(prov))), \
                mock.patch.object(SH, "run_pipeline_b", fake_pipeline), mock.patch.object(SH, "remove_tree", lambda *a: None), \
                mock.patch.object(CP, "capture", lambda: c), mock.patch.object(SL, "tsa_request", lambda d, u: "VE9LRU4="), \
                mock.patch.object(SL, "tsa_check", lambda digest, b64, name: tsa_t):
            self.assertEqual(BD.main(["record", "--repo", "/nonexistent", "--date", day, "--window", "NIGHT", "--out", out,
                                      "--store", os.path.join(base, "store"), "--transport-out", os.path.join(base, "transport")]), 0)
            self.assertNotIn("shadow_tape.json.gz", os.listdir(out))                       # never in the drill dir / git
            meta = json.load(open(os.path.join(out, "B_DRILL.json")))
            tt = meta["tape_transport"]
            tfile = os.path.join(base, "transport", tt["asset_name"])
            self.assertEqual(os.listdir(os.path.join(base, "transport")), [tt["asset_name"]])   # ONE file
            self.assertEqual(TS.file_identity(tfile), (hashlib.sha256(tape_bytes).hexdigest(), len(tape_bytes)))
            self.assertIn(tt["sha256"], tt["asset_name"])
            record = os.path.join(base, "B_DRILL_RECORD.json")
            json.dump({"drill": "B_DRILL_TEST", "sha256sums": open(os.path.join(out, "SHA256SUMS")).read(),
                       "tape": {"asset_name": tt["asset_name"], "sha256": tt["sha256"], "bytes": tt["bytes"], "key": tt["key"]}},
                      open(record, "w"))

            def fake_replay(d, board, overlay_bytes, p, detail, keep_dir=None, tape_locator=None):
                detail["tape_fetched"] = None
                try:
                    TS.fetch_verified(tape_locator, tempfile.mkdtemp())
                    detail["tape_fetched"] = {"verified": True}
                except TS.StoreError as exc:
                    detail["replay_error"] = f"{exc.code}: {exc}"
                    return False
                detail["replay_env"] = dict(env, replay_misses=0, unconsumed=0, network="none (own network namespace)")
                detail["payload"] = PL.compare(open(os.path.join(d, "shadow_payload.json"), "rb").read(), board)
                return True
            with mock.patch.object(VE, "replay_shadow_b", fake_replay):
                res = os.path.join(base, "r.json")
                rc = BD.main(["verify", "--drill-dir", out, "--tape-file", tfile, "--record", record, "--result", res])
                r = json.load(open(res))
                self.assertEqual(rc, 0, {k: v for k, v in r["checks"].items() if v != "PASS"})
                self.assertEqual(r["gates"], {"integrity": "PASS", "replay": "PASS"})
                self.assertTrue(r["payload"]["literal_identical"])
                with open(tfile, "r+b") as fh:                                  # corrupted transport bytes
                    fh.seek(1234)
                    x = fh.read(1)
                    fh.seek(1234)
                    fh.write(bytes([x[0] ^ 1]))
                self.assertEqual(BD.main(["verify", "--drill-dir", out, "--tape-file", tfile, "--record", record,
                                          "--result", res]), 1)
                r = json.load(open(res))
                self.assertTrue(r["checks"]["tape_retrieved_verified"].startswith("FAIL"))
                self.assertIn("TAPE_HASH_MISMATCH", r["checks"]["tape_retrieved_verified"])
                self.assertEqual(BD.main(["verify", "--drill-dir", out, "--tape-file", os.path.join(base, "nope.json.gz"),
                                          "--record", record, "--result", res]), 1)   # absent -> fail closed
                json.dump(dict(json.load(open(record)), sha256sums="tampered"), open(record, "w"))
                self.assertEqual(BD.main(["verify", "--drill-dir", out, "--tape-file", tfile, "--record", record,
                                          "--result", res]), 1)
                self.assertTrue(json.load(open(res))["checks"]["frozen_record_identity"].startswith("FAIL"))
        shutil.rmtree(base, ignore_errors=True)


class _S3(http.server.BaseHTTPRequestHandler):
    """S3-compatible mock (R2 path-style). Verifies SigV4 exactly, enforces x-amz-content-sha256 and create-only
    If-None-Match, and can inject: transient 5xx, lost PUT responses, dropped/truncated GETs, substituted bytes."""
    protocol_version = "HTTP/1.1"
    objects, creds, faults, requests_seen = {}, {"AK": "SK"}, {}, []

    def _auth_ok(self):
        auth = self.headers.get("Authorization", "")
        try:
            cred = auth.split("Credential=")[1].split(",")[0]
            ak, day, region = cred.split("/")[:3]
            signed = auth.split("SignedHeaders=")[1].split(",")[0].split(";")
        except IndexError:
            return False
        if ak not in self.creds:
            return False
        extra = {h: self.headers.get(h) for h in signed if h not in ("host", "x-amz-content-sha256", "x-amz-date")}
        want = TS.sigv4_headers(self.command, f"http://{self.headers['Host']}{self.path}", region, ak, self.creds[ak],
                                self.headers.get("x-amz-content-sha256"), self.headers.get("x-amz-date"), extra)
        return want["authorization"] == auth

    def _reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _fault(self, name):
        n = self.faults.get(name, 0)
        if n:
            self.faults[name] = n - 1 if n > 0 else n          # negative = forever
            return True
        return False

    def do_PUT(self):
        self.requests_seen.append(("PUT", self.path))
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if not self._auth_ok():
            return self._reply(403, b"SignatureDoesNotMatch")
        if self._fault("put_503"):
            return self._reply(503)
        if hashlib.sha256(body).hexdigest() != self.headers.get("x-amz-content-sha256"):
            return self._reply(400, b"XAmzContentSHA256Mismatch")
        if self.path in self.objects and self.headers.get("If-None-Match") == "*":
            return self._reply(412, b"PreconditionFailed")
        self.objects[self.path] = body
        if self._fault("put_lose_response"):                   # stored, but the client never hears back
            self.close_connection = True
            self.connection.shutdown(2)
            return
        self.send_response(200)
        self.send_header("ETag", '"%s"' % hashlib.md5(body).hexdigest())
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.requests_seen.append(("GET", self.path))
        if not self._auth_ok():
            return self._reply(403, b"InvalidAccessKeyId")
        if self._fault("get_drop"):
            self.close_connection = True
            self.connection.shutdown(2)
            return
        b = self.objects.get(self.path)
        if b is None:
            return self._reply(404, b"NoSuchKey")
        if self._fault("get_truncate"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b[: len(b) // 2])
            self.close_connection = True
            self.connection.shutdown(2)
            return
        self._reply(200, b)

    def log_message(self, *a):
        pass


class R2Mutants(unittest.TestCase):
    """FC-MLB-001B prospective R2 path against the mock: every mutant must fail closed; retries must never create
    ambiguous evidence."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _S3)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.endpoint = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _S3.objects.clear()
        _S3.faults.clear()
        _S3.requests_seen.clear()
        self.d = tempfile.mkdtemp()
        self.tape = os.path.join(self.d, "t.json.gz")
        with open(self.tape, "wb") as fh:
            fh.write(os.urandom(400_000))
        self.sha, self.n = TS.file_identity(self.tape)
        self.env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def store(self, secret="SK"):
        return TS.R2Store(self.endpoint, "fc-v3-evidence-tapes", "AK", secret, sleep=lambda s: None, timeout_s=20)

    def path(self):
        return f"/fc-v3-evidence-tapes/{TS.key_for(self.sha)}"

    def test_create_readback_and_seal_identity(self):
        loc = TS.put_verified(self.store(), self.tape, self.sha, self.n)
        self.assertEqual((loc["write_status"], loc["verified_readback"]), ("CREATED", True))
        self.assertEqual((loc["key"], loc["sha256"], loc["bytes"], loc["bucket"], loc["endpoint"]),
                         (TS.key_for(self.sha), self.sha, self.n, "fc-v3-evidence-tapes", self.endpoint))
        self.assertTrue(loc["etag"])
        again = TS.put_verified(self.store(), self.tape, self.sha, self.n)      # create-only: recognized, not rewritten
        self.assertEqual(again["write_status"], "EXISTS_VERIFIED")
        self.assertEqual(len(_S3.objects), 1)

    def test_upload_refuses_local_bytes_that_are_not_the_identity(self):
        with self.assertRaises(TS.TapeHashMismatch):
            TS.put_verified(self.store(), self.tape, "3" * 64, self.n)
        with self.assertRaises(TS.TapeSizeMismatch):
            TS.put_verified(self.store(), self.tape, self.sha, self.n + 1)
        self.assertEqual(_S3.requests_seen, [])                              # nothing was sent

    def test_missing_object(self):
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            with self.assertRaises(TS.TapeMissing):
                TS.fetch_verified(self.store().locator(self.sha, self.n))

    def test_wrong_object_served(self):
        TS.put_verified(self.store(), self.tape, self.sha, self.n)
        _S3.objects[self.path()] = os.urandom(1000)                           # another object at the address
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            with self.assertRaises(TS.TapeSizeMismatch):
                TS.fetch_verified(self.store().locator(self.sha, self.n))

    def test_same_key_wrong_bytes_conflict_and_one_byte_corruption(self):
        b = bytearray(open(self.tape, "rb").read())
        b[12345] ^= 0x01
        _S3.objects[self.path()] = bytes(b)                                   # pre-occupied by substituted bytes
        with self.assertRaises(TS.ObjectConflict):
            TS.put_verified(self.store(), self.tape, self.sha, self.n)
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            with self.assertRaises(TS.TapeHashMismatch):
                TS.fetch_verified(self.store().locator(self.sha, self.n))

    def test_truncated_download(self):
        TS.put_verified(self.store(), self.tape, self.sha, self.n)
        _S3.faults["get_truncate"] = 1                                        # transient: retried, then verified
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            p = TS.fetch_verified(self.store().locator(self.sha, self.n), tempfile.mkdtemp())
            self.assertEqual(TS.file_identity(p), (self.sha, self.n))
            _S3.faults["get_truncate"] = -1                                   # persistent: fail closed
            with self.assertRaises(TS.StoreUnavailable):
                TS.fetch_verified(self.store().locator(self.sha, self.n), tempfile.mkdtemp())

    def test_credential_failure_is_not_retried_and_not_missing(self):
        st = self.store(secret="WRONG")
        with self.assertRaises(TS.StoreAuthError):
            TS.put_verified(st, self.tape, self.sha, self.n)
        self.assertEqual(st.attempts, [("PUT", 403)])                         # exactly one attempt
        TS.put_verified(self.store(), self.tape, self.sha, self.n)
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "WRONG"}):
            with self.assertRaises(TS.StoreAuthError):
                TS.fetch_verified(self.store().locator(self.sha, self.n))
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "", "V3B_R2_SECRET_ACCESS_KEY": ""}):
            with self.assertRaises(TS.StoreAuthError):
                TS.fetch_verified(self.store().locator(self.sha, self.n))

    def test_network_interruption(self):
        _S3.faults["put_lose_response"] = 1               # stored, response lost -> retry meets 412 -> proven
        loc = TS.put_verified(self.store(), self.tape, self.sha, self.n)
        self.assertEqual(loc["write_status"], "EXISTS_VERIFIED")
        self.assertEqual(len(_S3.objects), 1)                                 # never a second / different object
        _S3.faults["get_drop"] = 2                                            # transient: retried
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            self.assertTrue(TS.fetch_verified(self.store().locator(self.sha, self.n), tempfile.mkdtemp()))
            _S3.faults["get_drop"] = -1                                       # persistent: fail closed
            with self.assertRaises(TS.StoreUnavailable):
                TS.fetch_verified(self.store().locator(self.sha, self.n), tempfile.mkdtemp())

    def test_transient_5xx_then_success_and_persistent_5xx(self):
        _S3.faults["put_503"] = 2
        self.assertEqual(TS.put_verified(self.store(), self.tape, self.sha, self.n)["write_status"], "CREATED")
        _S3.objects.clear()
        _S3.faults["put_503"] = -1
        with self.assertRaises(TS.StoreUnavailable):
            TS.put_verified(self.store(), self.tape, self.sha, self.n)
        self.assertEqual(_S3.objects, {})

    def test_runner_turns_every_store_failure_into_a_miss_unit(self):
        import runner as RN
        import schedule_plan as SP
        env = {"V3B_TAPE_STORE": "r2", "V3B_R2_ENDPOINT": self.endpoint, "V3B_R2_BUCKET": "fc-v3-evidence-tapes",
               "V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "WRONG"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaisesRegex(SP.MissUnit, "STORE_AUTH_FAILED"):
                RN._store_tape("prospective", self.tape, self.sha, self.n)
        with mock.patch.dict(os.environ, {"V3B_TAPE_STORE": "localfs:/tmp/x"}):
            with self.assertRaisesRegex(SP.MissUnit, "prospective requires r2"):
                RN._store_tape("prospective", self.tape, self.sha, self.n)
        with mock.patch.dict(os.environ, dict(env, V3B_R2_SECRET_ACCESS_KEY="SK")):
            with mock.patch.object(TS, "RETRY_DELAYS_S", ()):
                loc = RN._store_tape("prospective", self.tape, self.sha, self.n)
        self.assertEqual((loc["store"], loc["write_status"], loc["verified_readback"]), ("r2", "CREATED", True))


class Store(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.tape = os.path.join(self.d, "t.json.gz")
        open(self.tape, "wb").write(os.urandom(300_000))
        self.sha, self.n = TS.file_identity(self.tape)

    def _roundtrip(self, store):
        loc = store.put(self.tape, self.sha, self.n)
        self.assertEqual(loc["key"], f"v3/tapes/sha256/{self.sha}.json.gz")
        got = TS.fetch_verified(loc, tempfile.mkdtemp())
        self.assertEqual(TS.file_identity(got), (self.sha, self.n))
        with self.assertRaises(TS.ObjectExists):                       # create-only: never overwritten
            store.put(self.tape, self.sha, self.n)
        return loc

    def test_localfs_create_only_and_fail_closed(self):
        s = TS.LocalFSStore(os.path.join(self.d, "store"))
        loc = self._roundtrip(s)
        with self.assertRaises(TS.TapeMissing):
            TS.fetch_verified(dict(loc, sha256="1" * 64, key=TS.key_for("1" * 64)))
        with self.assertRaises(TS.TapeSizeMismatch):
            TS.fetch_verified(dict(loc, bytes=self.n + 1))
        obj = os.path.join(s.root, loc["key"])
        os.chmod(obj, 0o644)
        b = bytearray(open(obj, "rb").read())
        b[100] ^= 1
        open(obj, "wb").write(bytes(b))                                 # substituted bytes, same size
        with self.assertRaises(TS.TapeHashMismatch):
            TS.fetch_verified(loc)
        with self.assertRaises(TS.StoreError):                          # key must be the content address
            TS.fetch_verified(dict(loc, key="v3/tapes/sha256/other.json.gz"))

    def test_put_refuses_bytes_not_matching_identity(self):
        with self.assertRaises(TS.TapeHashMismatch):
            TS.LocalFSStore(os.path.join(self.d, "s2")).put(self.tape, "2" * 64, self.n)

    def test_actions_artifact_drill_transport_fails_closed(self):
        loc = {"store": TS.ARTIFACT_KIND, "key": TS.key_for(self.sha), "sha256": self.sha, "bytes": self.n}
        dl = os.path.join(self.d, "dl")
        os.makedirs(dl)
        good = os.path.join(dl, TS.transport_asset_name(self.sha))
        shutil.copyfile(self.tape, good)
        got = TS.fetch_verified(dict(loc, local_path=good), tempfile.mkdtemp())
        self.assertEqual(TS.file_identity(got), (self.sha, self.n))
        with self.assertRaises(TS.TapeMissing):                          # absent
            TS.fetch_verified(dict(loc, local_path=None))
        other = os.path.join(dl, "renamed.json.gz")
        shutil.copyfile(self.tape, other)
        with self.assertRaises(TS.StoreError):                           # name not bound to the sealed sha256
            TS.fetch_verified(dict(loc, local_path=other))
        with open(good, "r+b") as fh:                                    # truncated
            fh.truncate(self.n - 1)
        with self.assertRaises(TS.TapeSizeMismatch):
            TS.fetch_verified(dict(loc, local_path=good))
        b = bytearray(open(self.tape, "rb").read())
        b[7] ^= 0x40
        open(good, "wb").write(bytes(b))                                 # substituted, same size
        with self.assertRaises(TS.TapeHashMismatch):
            TS.fetch_verified(dict(loc, local_path=good))

    def test_sigv4_aws_reference_vector(self):
        h = TS.sigv4_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt", "us-east-1",
                             "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", TS.EMPTY_SHA256,
                             "20130524T000000Z", {"Range": "bytes=0-9"})
        self.assertTrue(h["authorization"].endswith(
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))


if __name__ == "__main__":
    unittest.main()
