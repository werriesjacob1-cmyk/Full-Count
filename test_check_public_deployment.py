"""2026-10-12 incident: the public Pages site froze behind a stuck deploy run
while every repository-level freshness check stayed green."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from dashboard import check_public_deployment as cpd

NOW = datetime(2026, 10, 12, 17, 55, tzinfo=timezone.utc)
REPO = "2026-10-12T17:39:14+00:00"


def manifest(prepared_at, commit="c5dee66"):
    return {"prepared_at": prepared_at, "source_commit": commit}


def run(rid, status, created_at):
    return {"id": rid, "status": status, "created_at": created_at}


class EvaluateTests(unittest.TestCase):
    def codes(self, **kw):
        base = {"repo_generated_at": REPO, "public_manifest": manifest("2026-10-12T17:30:00+00:00"),
                "deploy_runs": [], "now": NOW}
        base.update(kw)
        return [c for c, _ in cpd.evaluate(**base)]

    def test_healthy_when_public_tracks_main(self):
        self.assertEqual(self.codes(), [])
        # Normal propagation lag inside the SLA is not an alarm.
        self.assertEqual(self.codes(public_manifest=manifest("2026-10-12T16:45:00+00:00")), [])

    def test_the_actual_incident_is_detected(self):
        found = cpd.evaluate(
            repo_generated_at=REPO,
            public_manifest=manifest("2026-10-06T15:13:14+00:00"),
            deploy_runs=[run(37486111588, "waiting", "2026-10-06T15:16:44Z"),
                         run(38284239299, "completed", "2026-10-12T17:26:41Z")],
            now=NOW)
        codes = [c for c, _ in found]
        self.assertEqual(codes, ["STALE_PUBLICATION", "STUCK_DEPLOY"])
        self.assertIn("37486111588", found[1][1])
        self.assertIn("cancel run 37486111588", found[1][1])

    def test_stuck_statuses_and_threshold(self):
        for status in ("waiting", "queued", "pending", "in_progress", "requested"):
            self.assertEqual(self.codes(deploy_runs=[run(1, status, "2026-10-12T17:00:00Z")]),
                             ["STUCK_DEPLOY"], status)
        # A deploy that just started is normal, not stuck.
        self.assertEqual(self.codes(deploy_runs=[run(1, "in_progress", "2026-10-12T17:50:00Z")]), [])
        self.assertEqual(self.codes(deploy_runs=[run(1, "completed", "2026-10-01T00:00:00Z")]), [])

    def test_phantom_run_bypassed_by_later_success_is_not_blocking(self):
        # Recovery 2026-10-12: 37642347599 stayed 'queued' (cancel and
        # force-cancel both HTTP 409) while deploy 38301593329 succeeded.
        phantom = [run(37642347599, "queued", "2026-10-07T15:07:01Z")]
        self.assertEqual(self.codes(deploy_runs=phantom,
                                    last_success_created_at="2026-10-12T17:02:17Z"), [])
        # With no later success it is still reported as blocking.
        self.assertEqual(self.codes(deploy_runs=phantom,
                                    last_success_created_at="2026-10-06T15:11:31Z"), ["STUCK_DEPLOY"])
        self.assertEqual(self.codes(deploy_runs=phantom), ["STUCK_DEPLOY"])

    def test_unreadable_inputs_fail_closed(self):
        self.assertEqual(self.codes(public_manifest=None), ["PUBLIC_UNREACHABLE"])
        self.assertEqual(self.codes(public_manifest={"source_commit": "x"}), ["PUBLIC_UNREACHABLE"])
        self.assertEqual(self.codes(repo_generated_at=None), ["REPO_UNREADABLE"])

    def test_main_exit_codes(self):
        with mock.patch.object(cpd, "fetch_public_manifest", return_value=manifest("2026-10-06T15:13:14+00:00")), \
             mock.patch.object(cpd, "fetch_non_terminal_deploy_runs", return_value=[]), \
             mock.patch.object(cpd, "fetch_last_success_created_at", return_value=None):
            self.assertEqual(cpd.main(["docs/data.json"]), 1)
        with mock.patch.object(cpd, "fetch_public_manifest", return_value=None), \
             mock.patch.object(cpd, "fetch_non_terminal_deploy_runs", return_value=[]), \
             mock.patch.object(cpd, "fetch_last_success_created_at", return_value=None):
            self.assertEqual(cpd.main(["/nonexistent/data.json"]), 1)


class WatchdogWiringTests(unittest.TestCase):
    def test_watchdog_runs_public_check_independently_and_fails_visibly(self):
        import yaml
        with open(".github/workflows/live-freshness-watchdog.yml", encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        steps = wf["jobs"]["check-and-recover"]["steps"]
        names = [s.get("name") for s in steps]
        check = steps[names.index("Check public Pages deployment")]
        fail = steps[names.index("Fail visibly on a stale public deployment")]
        self.assertIn("always()", check["if"])  # runs even if the live check already failed
        self.assertIn("exit 1", fail["run"])
        sparse = steps[0]["with"]["sparse-checkout"]
        self.assertIn("dashboard/check_public_deployment.py", sparse)
        self.assertIn("docs/data.json", sparse)
        text = open(".github/workflows/live-freshness-watchdog.yml", encoding="utf-8").read()
        # Detection only: no automatic cancel/approve of deploy runs.
        self.assertNotIn("gh run cancel", text)
        self.assertNotIn("pending_deployments", text)


if __name__ == "__main__":
    unittest.main()
