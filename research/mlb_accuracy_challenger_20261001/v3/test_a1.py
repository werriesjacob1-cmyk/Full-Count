"""FC-MLB-001A amendment A1 -- integrity-boundary tests (hermetic: local HTTP server + stub pipeline).

The stub pipeline behaves like the frozen one where it matters: it consults a pybaseball-style disk cache
under $PYBASEBALL_CACHE before fetching over `requests`, and stamps `git rev-parse --short HEAD` into its
board provenance. Every test exercises the real isolation / lock / injected-git-config / guard / trace / netrecord code.
"""
import gzip
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import isolation as ISO  # noqa: E402
import shadow as SH  # noqa: E402

TEST_LOCK = os.path.join(HERE, "test-a1-requirements.lock")
PAYLOAD = {"/a": "alpha-live", "/b": "bravo-live"}

STUB = r'''
import hashlib, json, os, subprocess, requests
cfg = json.load(open("stub_config.json"))
cache = os.environ.get("PYBASEBALL_CACHE") or os.path.join(os.path.expanduser("~"), ".pybaseball", "cache")
os.makedirs(cache, exist_ok=True)
vals = []
for u in cfg["urls"]:
    f = os.path.join(cache, hashlib.sha256(u.encode()).hexdigest())
    if os.path.exists(f):
        vals.append(open(f).read())
    else:
        try:                                   # like the frozen pipeline: a failed source degrades, it does not abort
            t = requests.get(u, timeout=5).text
            open(f, "w").write(t)
        except Exception as exc:
            t = "ERR " + type(exc).__name__
        vals.append(t)
if cfg.get("read_hidden"):
    open(cfg["read_hidden"]).read()
if cfg.get("read_hidden_via_subprocess"):          # invisible to the in-process audit hook
    subprocess.run(["cat", cfg["read_hidden_via_subprocess"]], capture_output=True)
sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
os.makedirs("output", exist_ok=True)
json.dump({"records": vals, "provenance": {"git_sha": sha}}, open("output/board_freeze_stub.json", "w"), sort_keys=True)
'''


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAYLOAD.get(self.path, "missing").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _git(cwd, *a):
    return subprocess.check_output(["git", "-C", cwd, *a], env={"PATH": "/usr/bin:/bin", "HOME": cwd,
                                                                "GIT_CONFIG_NOSYSTEM": "1"}).decode().strip()


class A1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = tempfile.mkdtemp(prefix="a1test_")
        os.environ["V3A1_ISOLATION_BASE"] = os.path.join(cls.base, "iso")
        cls.origin = os.path.join(cls.base, "origin")
        os.makedirs(cls.origin)
        _git(cls.origin, "init", "-q")
        open(os.path.join(cls.origin, "generate_picks.py"), "w").write(STUB)
        for i in range(3):                       # a few commits so a shallow clone differs from a full one
            open(os.path.join(cls.origin, "n.txt"), "w").write(str(i))
            _git(cls.origin, "add", "-A")
            _git(cls.origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"c{i}")
        cls.full_sha = _git(cls.origin, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("HOME", "PYBASEBALL_CACHE")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def tree(self, name, shallow=False, cfg=None):
        w = os.path.join(self.base, name)
        subprocess.check_call(["git", "clone", "-q", *(["--depth", "1"] if shallow else []), "file://" + self.origin, w],
                              env={"PATH": "/usr/bin:/bin", "HOME": self.base, "GIT_CONFIG_NOSYSTEM": "1"})
        urls = [f"http://127.0.0.1:{self.srv.server_port}{p}" for p in sorted(PAYLOAD)]
        with open(os.path.join(w, "stub_config.json"), "w") as fh:
            json.dump({"urls": urls, **(cfg or {})}, fh)
        return w, urls

    def dirty_host_home(self, urls):
        """A host HOME whose pybaseball cache holds WRONG answers for every URL (the Oct-3/4 failure mode)."""
        import hashlib
        h = tempfile.mkdtemp(prefix="hosthome_", dir=self.base)
        c = os.path.join(h, ".pybaseball", "cache")
        os.makedirs(c)
        for u in urls:
            open(os.path.join(c, hashlib.sha256(u.encode()).hexdigest()), "w").write("STALE-HOST-CACHE")
        os.environ["HOME"], os.environ["PYBASEBALL_CACHE"] = h, c
        return h

    def run_p(self, w, tape, mode):
        return json.load(open(SH.run_pipeline(w, tape, mode, script="generate_picks.py", lock_path=TEST_LOCK)))

    # ---- R1/R2: cache isolation -------------------------------------------------------------------------
    def test_record_and_replay_ignore_dirty_host_cache(self):
        w, urls = self.tree("t_dirty")
        self.dirty_host_home(urls)
        tape = os.path.join(self.base, "dirty_tape.json.gz")
        board = self.run_p(w, tape, "record")
        self.assertEqual(board["records"], ["alpha-live", "bravo-live"])     # host cache had no effect
        env = json.load(open(tape + ".record.env.json"))
        self.assertEqual(env["n_http"], 2)                                  # every request reached the tape
        self.assertTrue(all(env["isolation"].values()))
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        replayed = self.run_p(w, tape, "replay")                            # host HOME still dirty
        self.assertEqual(replayed, board)
        rep = json.load(open(tape + ".replay.env.json"))
        self.assertEqual((rep["replay_misses"], rep["unconsumed"]), (0, 0))
        self.assertEqual(ISO.check_replay_compatible(env, rep), [])

    def test_mutant_without_isolation_reproduces_the_defect(self):
        """Pre-A1 behaviour (inherited HOME + cache): the board comes from the stale cache and the tape is empty,
        so a clean replay cannot reproduce it. This is exactly what the amendment removes."""
        w, urls = self.tree("t_legacy")
        self.dirty_host_home(urls)
        tape = os.path.join(self.base, "legacy_tape.json.gz")
        # the locked test packages (requests, time-machine) from the hash lock, but pre-A1 behaviour otherwise:
        # inherited host environment (HOME + pybaseball cache), no isolation, no guard. Never the host site-packages
        # (a clean runner's interpreter has none of them -- that made this test error on GitHub run 37353256200).
        venv = os.path.join(self.base, "legacy_venv")
        ISO.build_venv(venv, TEST_LOCK, os.environ.get("V3A1_WHEEL_CACHE"))
        r = subprocess.run([os.path.join(venv, "bin", "python"), SH.NETRECORD, "--mode", "record", "--tape", tape, "--",
                            "generate_picks.py"], cwd=w, env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        board = json.load(open(os.path.join(w, "output", "board_freeze_stub.json")))
        self.assertEqual(board["records"], ["STALE-HOST-CACHE"] * 2)
        self.assertEqual(json.load(open(tape + ".record.report.json"))["n_http"], 0)
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        with self.assertRaisesRegex(RuntimeError, "replay not exact: misses=2"):
            self.run_p(w, tape, "replay")

    def test_prepopulated_run_root_is_a_breach(self):
        w, _ = self.tree("t_breach")
        iso = ISO.IsolatedRun("replay", w, lock_path=TEST_LOCK)
        os.makedirs(os.path.join(iso.home, ".pybaseball", "cache"))
        open(os.path.join(iso.home, ".pybaseball", "cache", "x"), "w").write("dirty")
        with self.assertRaisesRegex(ISO.IsolationError, "ISOLATION_BREACH"):
            iso.__enter__()
        iso.__exit__(None, None, None)

    # ---- R3: dependency lock -----------------------------------------------------------------------------
    def test_lock_hash_mismatch_fails_closed(self):
        bad = os.path.join(self.base, "bad.lock")
        txt = open(TEST_LOCK).read()
        line = next(ln for ln in txt.splitlines() if ln.startswith("idna=="))
        open(bad, "w").write(txt.replace(line, line[:-1] + ("0" if line[-1] != "0" else "1")))
        with self.assertRaisesRegex(ISO.IsolationError, "locked install failed"):
            ISO.build_venv(os.path.join(self.base, "v_bad"), bad)

    def test_installed_set_must_equal_lock(self):
        v = os.path.join(self.base, "v_ok")
        rep = ISO.build_venv(v, TEST_LOCK)
        self.assertEqual(rep["n_locked"], len(ISO.lock_pins(TEST_LOCK)))
        short = os.path.join(self.base, "short.lock")
        open(short, "w").write("".join(ln + "\n" for ln in open(TEST_LOCK).read().splitlines() if not ln.startswith("idna==")))
        with self.assertRaisesRegex(ISO.IsolationError, r"extra=\['idna'\]"):
            ISO.verify_installed(os.path.join(v, "bin", "python"), short)
        wrong = os.path.join(self.base, "wrong.lock")
        open(wrong, "w").write(open(TEST_LOCK).read().replace("idna==3.20", "idna==3.19"))
        with self.assertRaisesRegex(ISO.IsolationError, r"wrong_version=\['idna'\]"):
            ISO.verify_installed(os.path.join(v, "bin", "python"), wrong)

    # ---- R4: provenance identity -------------------------------------------------------------------------
    def test_provenance_identical_for_shallow_full_and_any_abbrev(self):
        """R4 as frozen: injected GIT_CONFIG_COUNT/KEY_0/VALUE_0 core.abbrev=10 beats hostile repo-local config;
        no PATH shim and no persistent git config is written anywhere."""
        shas = {}
        for name, shallow, abbrev in (("p_full", False, None), ("p_shallow", True, None), ("p_abbrev4", True, "4"),
                                      ("p_abbrev12", False, "12")):
            w, _ = self.tree(name, shallow=shallow)
            if abbrev:
                _git(w, "config", "core.abbrev", abbrev)
            cfg_before = open(os.path.join(w, ".git", "config"), "rb").read()
            tape = os.path.join(self.base, name + ".json.gz")
            board = self.run_p(w, tape, "record")
            shas[name] = board["provenance"]["git_sha"]
            self.assertEqual(open(os.path.join(w, ".git", "config"), "rb").read(), cfg_before)   # nothing persisted
            env = json.load(open(tape + ".record.env.json"))
            self.assertEqual(env["git_identity"]["injected"], {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.abbrev",
                                                               "GIT_CONFIG_VALUE_0": "10"})
            self.assertNotIn("shim_sha256", env["git_identity"])
        self.assertEqual(set(shas.values()), {self.full_sha[:10]}, shas)
        self.assertFalse(os.path.exists(os.path.join(self.base, ".gitconfig")))

    def test_isolated_env_has_injected_git_config_and_no_shim(self):
        with ISO.IsolatedRun("replay", self.base, lock_path=TEST_LOCK) as iso:
            for k, v in ISO.GIT_INJECTED_CONFIG.items():
                self.assertEqual(iso.env[k], v)
            first = iso.env["PATH"].split(":")[0]
            self.assertEqual(first, os.path.dirname(iso.python))                     # venv first, no shim dir
            self.assertFalse(os.path.exists(os.path.join(iso.root, "bin", "git")))
            self.assertEqual(shutil.which("git", path=iso.env["PATH"]), shutil.which("git", path="/usr/local/bin:/usr/bin:/bin"))

    def test_mutant_without_r4_abbrev7_shallow_rejected_with_r4_accepted(self):
        """Frozen bar: abbrev-7 shallow clone provenance is rejected without R4 and accepted with it."""
        w, _ = self.tree("p_abbrev7", shallow=True)
        _git(w, "config", "core.abbrev", "7")
        with ISO.IsolatedRun("record", w, lock_path=TEST_LOCK) as iso:
            bare = {k: v for k, v in iso.env.items() if k not in ISO.GIT_INJECTED_CONFIG}
            without = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=w, env=bare,
                                     capture_output=True, text=True, check=True).stdout.strip()
            withr4 = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=w, env=iso.env,
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(len(without), 7)
        self.assertNotEqual(without, self.full_sha[:10])          # verify_shadow_board would reject this
        self.assertEqual(withr4, self.full_sha[:10])

    def test_mutant_real_git_short_depends_on_clone_config(self):
        w, _ = self.tree("p_mut", shallow=True)
        _git(w, "config", "core.abbrev", "4")
        self.assertEqual(len(_git(w, "rev-parse", "--short", "HEAD")), 4)   # what the pin would stamp without A1

    def test_wrong_provenance_rejected(self):
        board = {"provenance": {"git_sha": "0123456789", **SH.SHADOW_LABELS}}
        with self.assertRaisesRegex(ValueError, "is not the pin"):
            SH.verify_shadow_board(board)

    # ---- tape completeness -------------------------------------------------------------------------------
    def _recorded(self, name):
        w, urls = self.tree(name)
        tape = os.path.join(self.base, name + ".json.gz")
        self.run_p(w, tape, "record")
        os.remove(os.path.join(w, "output", "board_freeze_stub.json"))
        return w, urls, tape

    def _edit_tape(self, tape, fn):
        t = json.load(gzip.open(tape))
        fn(t["http"])
        with gzip.GzipFile(tape, "wb", mtime=0) as fh:
            fh.write(json.dumps(t, sort_keys=True).encode())

    def test_replay_missing_request_fails(self):
        w, urls, tape = self._recorded("m_miss")
        self._edit_tape(tape, lambda h: h.pop(next(k for k in h if k.startswith(f"GET {urls[0]} "))))
        with self.assertRaisesRegex(RuntimeError, "misses=1 unconsumed=0"):
            self.run_p(w, tape, "replay")

    def test_replay_unconsumed_exchange_fails(self):
        w, urls, tape = self._recorded("m_unc")
        self._edit_tape(tape, lambda h: h.setdefault("GET http://127.0.0.1:9/never 0000000000000000", []).append(
            {"status": 200, "reason": "OK", "url": "x", "encoding": None, "headers": {}, "content_b64": ""}))
        with self.assertRaisesRegex(RuntimeError, "misses=0 unconsumed=1"):
            self.run_p(w, tape, "replay")

    # ---- R5: hidden-state guard --------------------------------------------------------------------------
    def test_hidden_local_state_fails_closed(self):
        hidden = os.path.join(self.base, "hidden_host_state.txt")     # outside tree, run root, code, stdlib
        open(hidden, "w").write("secret")
        w, _ = self.tree("g_hidden", cfg={"read_hidden": hidden})
        with self.assertRaisesRegex(RuntimeError, "A1 guard: hidden local state"):
            self.run_p(w, os.path.join(self.base, "g_hidden.json.gz"), "record")

    def test_guard_clean_run_reports_surfaces(self):
        w, _ = self.tree("g_clean")
        tape = os.path.join(self.base, "g_clean.json.gz")
        self.run_p(w, tape, "record")
        env = json.load(open(tape + ".record.env.json"))
        self.assertEqual(env["guard"]["violations"], [])
        self.assertIn("PINNED_TREE", env["guard"]["reads_by_surface"])
        self.assertEqual(env["guard"]["subprocess_executables"], ["git"])
        self.assertNotIn("GITHUB_TOKEN", env["env_keys"])

    # ---- R5: process-tree trace (strace -f) ------------------------------------------------------------------
    def test_subprocess_hidden_read_is_invisible_to_hook_but_caught_by_trace(self):
        hidden = os.path.join(self.base, "hidden_host_state_sub.txt")
        open(hidden, "w").write("secret")
        w, _ = self.tree("t_sub", cfg={"read_hidden_via_subprocess": hidden})
        tape = os.path.join(self.base, "t_sub.json.gz")
        self.run_p(w, tape, "record")                                   # the audit hook alone does not see it
        env = json.load(open(tape + ".record.env.json"))
        self.assertEqual(env["guard"]["violations"], [])
        tr = env["process_trace"]
        self.assertIn("cat", tr["executables"])
        self.assertIn("HOST_STATE", tr["frozen_r5_violation_classes"])  # the trace does
        self.assertTrue(any("hidden_host_state_sub.txt" in e for e in tr["examples"]["HOST_STATE"]), tr["examples"])
        self.assertTrue(os.path.exists(tape + ".record.strace"))

    def test_trace_reports_every_class_outside_frozen_surfaces(self):
        """Frozen R5 permits only the pinned tree (+ sealed overlay) and the isolated HOME; the trace never waives
        the interpreter, shared libraries, git or OS files -- they are reported as frozen-R5 violations."""
        w, _ = self.tree("t_clean")
        tape = os.path.join(self.base, "t_clean.json.gz")
        self.run_p(w, tape, "record")
        tr = json.load(open(tape + ".record.env.json"))["process_trace"]
        self.assertEqual(tr["frozen_r5_permitted_classes"], ["PINNED_TREE", "ISOLATED_HOME"])
        self.assertIn("PINNED_TREE", tr["by_class"])
        self.assertIn("git", tr["executables"])
        for c in ("INTERPRETER_STDLIB", "SHARED_LIBRARIES", "RUN_ROOT_VENV", "EXECUTABLES"):
            self.assertIn(c, tr["frozen_r5_violation_classes"])
        self.assertGreater(tr["frozen_r5_violations"], 0)
        self.assertNotIn("PINNED_TREE", tr["frozen_r5_violation_classes"])

    def test_trace_required_fail_closed_without_strace(self):
        real = shutil.which
        try:
            ISO.shutil.which = lambda name, path=None: None if name == "strace" else real(name, path=path)
            with self.assertRaisesRegex(ISO.IsolationError, "strace is not installed"):
                ISO.trace_command("/dev/null")
        finally:
            ISO.shutil.which = real

    def test_trace_parser_handles_unfinished_and_failed_calls(self):
        t = os.path.join(self.base, "synthetic.strace")
        open(t, "w").write(
            '10 openat(AT_FDCWD, "/w/tree/a.py", O_RDONLY|O_CLOEXEC) = 3\n'
            '11 openat(AT_FDCWD, "/var/cache/x", O_RDONLY <unfinished ...>\n'
            '10 openat(AT_FDCWD, "/nope", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
            '11 <... openat resumed>) = 4\n'
            '12 execve("/usr/bin/git", ["git"], 0x0 /* 3 vars */) = 0\n')
        tr = ISO.summarize_trace(t, "/w/tree", "/w/root")
        self.assertEqual(tr["n_successful_calls"], 3)
        self.assertEqual(tr["frozen_r5_violation_classes"], {"EXECUTABLES": 1, "HOST_STATE": 1})
        self.assertEqual(tr["by_class"]["PINNED_TREE"]["reads"], 1)

    def test_record_replay_environment_mismatch_detected(self):
        rec = {"a1_version": ISO.A1_VERSION, "lock_sha256": "a", "installed_set_sha256": "b", "python": "3.11.15",
               "isolation": {"home_empty_at_start": True}, "guard": {"violations": []}, "mode": "record"}
        self.assertEqual(ISO.check_replay_compatible(rec, dict(rec, mode="replay", python="3.11.2")), [])
        self.assertTrue(ISO.check_replay_compatible(rec, dict(rec, mode="replay", lock_sha256="z")))
        self.assertTrue(ISO.check_replay_compatible(rec, dict(rec, mode="replay", python="3.12.1")))
        self.assertTrue(ISO.check_replay_compatible(rec, dict(rec, mode="replay", guard={"violations": ["/x"]})))

    def test_record_mechanism_conformance(self):
        cur = {"a1_version": ISO.A1_VERSION, "git_identity": {"injected": dict(ISO.GIT_INJECTED_CONFIG)}}
        self.assertEqual(ISO.check_record_conformance(cur), [])
        shim = {"a1_version": "fc-mlb-001a-a1-1", "git_identity": {"shim_sha256": "x", "rule": "rev-parse --short HEAD"}}
        self.assertEqual(len(ISO.check_record_conformance(shim)), 2)      # superseded version AND non-frozen R4


class DrillArtifacts(unittest.TestCase):
    """Runs only when a recorded A1 drill directory is present (set A1_DRILL_DIR): a changed sealed artifact
    must fail verification."""

    def test_changed_artifact_fails(self):
        d = os.environ.get("A1_DRILL_DIR")
        if not d:
            if os.environ.get("A1_REQUIRE_DRILL") == "1":      # CI: a missing drill is a failure, never a silent skip
                self.fail("A1_REQUIRE_DRILL=1 but A1_DRILL_DIR is not set")
            self.skipTest("A1_DRILL_DIR not set")
        self.assertTrue(os.path.isfile(os.path.join(d, "SHA256SUMS")), f"not a drill dir: {d}")
        import a1_drill as AD
        t = tempfile.mkdtemp(prefix="a1mut_")
        m = os.path.join(t, os.path.basename(d.rstrip("/")))
        shutil.copytree(d, m)
        s = json.load(open(os.path.join(m, "schedule.json")))
        s["fetched_at"] = "1999-01-01T00:00:00+00:00"
        json.dump(s, open(os.path.join(m, "schedule.json"), "w"))
        res = os.path.join(t, "r.json")
        self.assertEqual(AD.main(["verify", "--drill-dir", m, "--result", res]), 1)
        r = json.load(open(res))
        self.assertTrue(r["checks"]["sha256sums"].startswith("FAIL"))
        self.assertTrue(r["checks"]["artifact_set"].startswith("FAIL"))
        self.assertEqual(r["gates"]["integrity"], "FAIL")
        self.assertEqual(r["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
