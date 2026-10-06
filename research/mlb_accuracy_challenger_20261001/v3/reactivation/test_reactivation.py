#!/usr/bin/env python3
"""Reactivation package tests: bindings derive only from an exact, present commit; Jacob's text satisfies the EXISTING
activation verifier; agent-authored or stale authorizations are refused; the dispatcher template refuses to run
unbound. Nothing here writes into the repository, pushes, posts, or touches the trigger."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import activation as AC  # noqa: E402
import prepare_activation as PA  # noqa: E402
import runtime_image as RI  # noqa: E402

REPO = subprocess.check_output(["git", "-C", HERE, "rev-parse", "--show-toplevel"]).decode().strip()
COMMIT = os.environ.get("REACTIVATION_TEST_COMMIT") or subprocess.check_output(
    ["git", "-C", HERE, "rev-parse", "HEAD"]).decode().strip()


def comment_for(text, cid=7000000001, created="2026-10-07T12:00:00Z"):
    return {"id": cid, "issue_url": f"https://api.github.com/repos/{AC.REPOSITORY}/issues/91", "created_at": created,
            "body": text}


class Reactivation(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="react_")

    def step1(self, **kw):
        args = ["--repo", REPO, "--implementation-commit", kw.get("impl", COMMIT),
                "--codex-passed-commit", kw.get("codex", COMMIT), "--out", self.out]
        return PA.main(args + kw.get("extra", []))

    def test_step1_bindings_and_jacob_text_satisfy_the_existing_verifier(self):
        self.assertEqual(self.step1(), 0)
        b = json.load(open(os.path.join(self.out, "REACTIVATION_BINDINGS.json")))
        self.assertEqual(b["implementation_commit"], COMMIT)
        self.assertEqual(b["implementation_tree"],
                         subprocess.check_output(["git", "-C", REPO, "rev-parse", f"{COMMIT}:{AC.V3_DIR}"]).decode().strip())
        self.assertEqual(b["runtime_digest"], RI.MANIFEST_DIGEST)
        self.assertEqual((b["scientific_payload_spec"], b["storage_contract"]),
                         ("fc-mlb-001b-payload-v1", "fc-mlb-001b-r2-cas-1"))
        self.assertEqual(b["prereg_blob"], PA.PREREG_BLOB)
        ts = b["tape_storage"]                       # Jacob 2026-10-06: temporary Actions artifacts, R2 later
        self.assertEqual((ts["initial_backend"], ts["initial_contract"], ts["artifact_retention_days"]),
                         ("github-actions-artifact", "fc-v3-gha-artifact-temp-1", 90))
        self.assertEqual((ts["migration_warn_days_remaining"], ts["migration_critical_days_remaining"]), (45, 30))
        self.assertIn("FC-MLB-001C", ts["migration_milestone"])
        self.assertEqual(ts["durable_target"]["contract"], "fc-mlb-001b-r2-cas-1")
        self.assertEqual(b["trigger"]["id"], "trig_011u98uXVuFEipPfbTT6KGur")
        text = open(os.path.join(self.out, "JACOB_AUTHORIZATION.txt")).read()
        rec = {"implementation_commit": COMMIT, "jacob_authorization_comment_id": 7000000001}
        self.assertEqual([k for k, ok in AC.check_comment(comment_for(text), rec).items() if not ok], [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "v3_dispatch.py")))       # nothing bound in step 1

    def test_step2_binds_record_and_dispatcher_from_jacobs_comment(self):
        self.step1()
        text = open(os.path.join(self.out, "JACOB_AUTHORIZATION.txt")).read()
        cj = os.path.join(self.out, "c.json")
        json.dump(comment_for(text), open(cj, "w"))
        self.assertEqual(self.step1(extra=["--jacob-comment-id", "7000000001", "--comment-json", cj,
                                           "--activation-timestamp", "2026-10-07T12:05:00Z"]), 0)
        raw = open(os.path.join(self.out, "ACTIVATION_7000000001.json"), "rb").read()
        rec = json.loads(raw)
        self.assertEqual([f for f in AC.REQUIRED_FIELDS if not rec.get(f)], [])
        self.assertEqual((rec["implementation_commit"], rec["pr_number"], rec["protocol_version"]), (COMMIT, 220, "V3"))
        disp = open(os.path.join(self.out, "v3_dispatch.py")).read()
        self.assertNotIn("@@IMPLEMENTATION", disp)
        self.assertIn(f'AUTH_COMMIT = "{COMMIT}"', disp)
        self.assertIn(f'ACTIVATION_SHA256 = "{__import__("hashlib").sha256(raw).hexdigest()}"', disp)
        self.assertIn('ACTIVATION_ON_EVIDENCE = "ACTIVATION/ACTIVATION_7000000001.json"', disp)
        wf = open(os.path.join(self.out, "v3-unit-record.yml"), "rb").read()
        self.assertNotIn(b"@@", wf)
        self.assertIn(f"AUTH_COMMIT: '{COMMIT}'".encode(), wf)
        self.assertIn(b"retention-days: ${{ env.RETENTION_DAYS }}", wf)
        self.assertIn(b"RETENTION_DAYS: '90'", wf)
        self.assertIn(f'RECORD_WORKFLOW_SHA256 = "{__import__("hashlib").sha256(wf).hexdigest()}"', disp)
        self.assertIn('REQUEST_BRANCH = "claude/mlb-v3-unit-requests"', disp)
        self.assertNotIn("V3B_R2", disp)                                       # R2 is not an initial requirement
        __import__("yaml").safe_load(wf)

    def test_refusals(self):
        with self.assertRaises(SystemExit):
            self.step1(impl=COMMIT[:10], codex=COMMIT[:10])                    # abbreviated
        with self.assertRaises(SystemExit):
            self.step1(codex="0" * 40)                                         # not the Codex-passed head
        with self.assertRaises(SystemExit):
            self.step1(impl="1" * 40, codex="1" * 40)                          # not present
        no_temp = "dac663c0a28a2a27fbc26fe609de628a5739b99b"                   # 001B head without the temporary store
        if COMMIT != no_temp:
            with self.assertRaisesRegex(SystemExit, "TEMPORARY Actions-artifact store"):
                self.step1(impl=no_temp, codex=no_temp)
        with self.assertRaises(SystemExit):                                    # thresholds must fit the retention
            self.step1(extra=["--artifact-retention-days", "30"])
        self.step1()
        text = open(os.path.join(self.out, "JACOB_AUTHORIZATION.txt")).read()
        for bad in (text + "\nCLAUDE STATUS", text.replace("JACOB AUTHORIZATION: ALLOW", "ALLOW"),
                    text.replace(COMMIT, "deadbeef" * 5)):
            cj = os.path.join(self.out, "bad.json")
            json.dump(comment_for(bad), open(cj, "w"))
            with self.assertRaises(SystemExit):
                self.step1(extra=["--jacob-comment-id", "7000000001", "--comment-json", cj,
                                  "--activation-timestamp", "2026-10-07T12:05:00Z"])
        cj = os.path.join(self.out, "ok.json")
        json.dump(comment_for(text), open(cj, "w"))
        with self.assertRaises(SystemExit):                                    # activation predates authorization
            self.step1(extra=["--jacob-comment-id", "7000000001", "--comment-json", cj,
                              "--activation-timestamp", "2026-10-07T11:00:00Z"])

    def test_unbound_template_refuses_to_run(self):
        spec = importlib.util.spec_from_file_location("disp_t", PA.TEMPLATE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with self.assertRaisesRegex(SystemExit, "UNBOUND TEMPLATE"):
            mod.preflight_001b("/nonexistent", {})


if __name__ == "__main__":
    unittest.main()
