import copy
import json
import unittest
from pathlib import Path

from nfl.prospective.shadow_snapshot import seal_snapshot
from nfl.web.build_shadow_site import build_public_payload

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
        with self.assertRaisesRegex(ValueError, "must explain"):
            build_public_payload(board, source_board_sha256="a" * 64)

    def test_shadow_only_row_cannot_keep_quarantine_reason(self):
        board = copy.deepcopy(self.board)
        row = board["snapshot"]["records"][0]
        row["decision_status"] = "SHADOW_ONLY"
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
