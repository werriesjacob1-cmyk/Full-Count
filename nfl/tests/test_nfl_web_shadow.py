import copy
import json
import unittest
from pathlib import Path

from nfl.prospective.shadow_snapshot import seal_snapshot
from nfl.web.build_shadow_site import build_public_payload
from nfl.web.publish_guard import publication_verdict
from nfl.web.source_run import validate_source_run, validate_artifact_code, WORKFLOW_PATH

FIXTURE = Path(__file__).resolve().parents[2] / "nfl" / "tests" / "fixtures" / "nfl_shadow_board_minimal.json"


class NFLWebShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = cls.board["snapshot"]
        cls.board["snapshot"] = seal_snapshot(
            snapshot["records"],
            slate_date=snapshot["slate_date"],
            code_sha=snapshot["code_sha"],
            source_vintage=snapshot["source_vintage"],
            sealed_at=snapshot["sealed_at"],
        )

    def test_builds_only_public_safe_whitelist(self):
        payload = build_public_payload(copy.deepcopy(self.board), source_board_sha256="a" * 64)
        self.assertEqual(payload["publication_status"], "RESEARCH_ONLY_NOT_PUBLIC_PICKS")
        self.assertFalse(payload["model"]["public_selector_validated"])
        row = payload["records"][0]
        self.assertNotIn("source_url", row)
        self.assertNotIn("source_payload_sha256", row)
        self.assertNotIn("over_selection_id", row)
        self.assertNotIn("under_selection_id", row)
        self.assertNotIn("actual", row)

    def test_quarantined_row_must_explain_quarantine(self):
        board = copy.deepcopy(self.board)
        board["snapshot"]["records"][0]["quarantine_reasons"] = []
        snap = board["snapshot"]
        board["snapshot"] = seal_snapshot(
            snap["records"],
            slate_date=snap["slate_date"],
            code_sha=snap["code_sha"],
            source_vintage=snap["source_vintage"],
            sealed_at=snap["sealed_at"],
        )
        with self.assertRaisesRegex(ValueError, "must explain"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_shadow_only_row_cannot_keep_quarantine_reason(self):
        board = copy.deepcopy(self.board)
        row = board["snapshot"]["records"][0]
        row["decision_status"] = "SHADOW_ONLY"
        snap = board["snapshot"]
        board["snapshot"] = seal_snapshot(
            snap["records"],
            slate_date=snap["slate_date"],
            code_sha=snap["code_sha"],
            source_vintage=snap["source_vintage"],
            sealed_at=snap["sealed_at"],
        )
        with self.assertRaisesRegex(ValueError, "cannot carry"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_outcome_field_is_rejected_not_sanitized_silently(self):
        board = copy.deepcopy(self.board)
        board["snapshot"]["records"][0]["actual"] = 300
        with self.assertRaisesRegex(ValueError, "outcome fields"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_tampered_snapshot_seal_fails_closed(self):
        board = copy.deepcopy(self.board)
        board["snapshot"]["records"][0]["line"] = 999.5
        with self.assertRaisesRegex(ValueError, "snapshot SHA-256"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_normalized_fields_cannot_bypass_seal_verification(self):
        for field, value in (("evidence_class", "PUBLIC_PICKS"), ("schema_version", 99)):
            board = copy.deepcopy(self.board)
            board["snapshot"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "canonical snapshot"):
                build_public_payload(board, source_board_sha256="a" * 64)
        board = copy.deepcopy(self.board)
        board["snapshot"]["records"][0]["observation_id"] = "altered-id"
        with self.assertRaisesRegex(ValueError, "canonical snapshot"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_grading_bridge_fields_are_rejected_even_when_resealed(self):
        for field in ("actual_passing_yards", "outcome_status", "selection_result", "eligible_shadow"):
            board = copy.deepcopy(self.board)
            snap = board["snapshot"]
            snap["records"][0][field] = "diagnostic"
            board["snapshot"] = seal_snapshot(snap["records"], **{k: snap[k] for k in ("slate_date", "code_sha", "source_vintage", "sealed_at")})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "outcome fields"):
                build_public_payload(board, source_board_sha256="a" * 64)

    def test_sealing_after_kickoff_is_not_prospective(self):
        board = copy.deepcopy(self.board)
        snap = board["snapshot"]
        snap["sealed_at"] = snap["records"][0]["event_open_date"]
        board["created_at"] = snap["sealed_at"]
        board["snapshot"] = seal_snapshot(snap["records"], **{k: snap[k] for k in ("slate_date", "code_sha", "source_vintage", "sealed_at")})
        with self.assertRaisesRegex(ValueError, "not sealed pregame"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_source_run_identity_and_artifact_binding(self):
        repo = "owner/repo"
        run = dict(id=123, name="NFL Live Passing-Yards Shadow Board Audit", head_branch="main", status="completed", conclusion="success", path=WORKFLOW_PATH, workflow_id=456, head_sha="a" * 40, repository={"full_name": repo}, head_repository={"full_name": repo})
        workflow = dict(id=456, path=WORKFLOW_PATH)
        self.assertEqual(validate_source_run(run, workflow, repository=repo, run_id="123"), "a" * 40)
        for field, value in (("head_branch", "feature"), ("path", ".github/workflows/impostor.yml"), ("workflow_id", 999), ("conclusion", "failure"), ("head_repository", {"full_name": "fork/repo"})):
            bad = {**run, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_source_run(bad, workflow, repository=repo, run_id="123")
        with self.assertRaisesRegex(ValueError, "artifact code SHA"):
            validate_artifact_code(self.board, "a" * 40)
        validate_artifact_code(self.board, self.board["code_sha"])

    def test_post_kickoff_capture_is_rejected(self):
        board = copy.deepcopy(self.board)
        row = board["snapshot"]["records"][0]
        row["captured_at"] = row["event_open_date"]
        board["snapshot"] = seal_snapshot(
            board["snapshot"]["records"],
            slate_date=board["snapshot"]["slate_date"],
            code_sha=board["snapshot"]["code_sha"],
            source_vintage=board["snapshot"]["source_vintage"],
            sealed_at=board["snapshot"]["sealed_at"],
        )
        with self.assertRaisesRegex(ValueError, "not captured pregame"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_board_and_snapshot_slate_must_match(self):
        board = copy.deepcopy(self.board)
        board["target_local_date"] = "2026-09-14"
        with self.assertRaisesRegex(ValueError, "target_local_date"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_public_selector_transition_fails_closed(self):
        board = copy.deepcopy(self.board)
        board["model"]["public_selector_validated"] = True
        with self.assertRaisesRegex(ValueError, "public_selector_validated"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_opponent_is_derived_from_event_and_bound_team(self):
        payload = build_public_payload(copy.deepcopy(self.board), source_board_sha256="a" * 64)
        self.assertEqual(payload["records"][0]["opponent"], "CAR")

    def test_publication_guard_is_monotonic(self):
        candidate = build_public_payload(
            copy.deepcopy(self.board),
            source_board_sha256="a" * 64,
            source_run_id="123",
            publisher_code_sha="b" * 40,
        )
        placeholder = {
            "publication_status": "RESEARCH_ONLY_NOT_PUBLIC_PICKS",
            "created_at": None,
            "model": {"public_selector_validated": False},
            "records": [], "summary": {"candidates": 0}, "snapshot_sha256": None,
        }
        self.assertEqual(publication_verdict(placeholder, candidate), "NEWER")
        with self.assertRaisesRegex(ValueError, "empty initial placeholder"):
            publication_verdict({**placeholder, "records": [{"id": "existing"}]}, candidate)
        self.assertEqual(publication_verdict(copy.deepcopy(candidate), candidate), "SAME")

        newer = copy.deepcopy(candidate)
        newer["created_at"] = "2026-09-12T19:52:58Z"
        self.assertEqual(publication_verdict(candidate, newer), "NEWER")
        with self.assertRaisesRegex(ValueError, "older snapshot"):
            publication_verdict(newer, candidate)

        conflict = copy.deepcopy(candidate)
        conflict["snapshot_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "different snapshot seal"):
            publication_verdict(candidate, conflict)

    def test_committed_nfl_static_files_match_source(self):
        repo = Path(__file__).resolve().parents[2]
        for name in ("index.html", "app.css", "app.js"):
            self.assertEqual(
                (repo / "nfl" / "web" / "static" / name).read_bytes(),
                (repo / "docs" / "nfl" / name).read_bytes(),
                f"docs/nfl/{name} drifted from nfl/web/static/{name}",
            )

    def test_committed_docs_payload_is_research_only(self):
        repo = Path(__file__).resolve().parents[2]
        payload = json.loads((repo / "docs" / "nfl" / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["surface"], "prospective_research_shadow")
        self.assertEqual(payload["publication_status"], "RESEARCH_ONLY_NOT_PUBLIC_PICKS")
        self.assertFalse(payload["model"]["public_selector_validated"])
        self.assertEqual(payload["summary"]["candidates"], len(payload["records"]))
        for row in payload["records"]:
            self.assertIn(row["decision_status"], {"SHADOW_ONLY", "QUARANTINED"})
            self.assertFalse(set(row).intersection({"actual", "result", "grade", "hit", "settled", "outcome"}))


if __name__ == "__main__":
    unittest.main()
