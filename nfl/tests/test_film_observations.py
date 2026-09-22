import json
import unittest
from pathlib import Path

from nfl.research.film_observations import (
    LABEL_FIELDS,
    ObservationValidationError,
    SourceManifest,
    compare_annotations,
    load_jsonl,
    validate_observation,
    validate_unique_bindings,
    verify_source_content,
)


DIGEST = "a" * 64


def manifest(**overrides):
    raw = {
        "source_id": "synthetic-v1",
        "source_type": "synthetic_fixture",
        "rights_verified": False,
        "analysis_rights": "Repository-owned synthetic fixture; no NFL footage or charting.",
        "license_url": None,
        "cost_usd": 0,
        "historical_coverage": "Synthetic two-play example only.",
        "current_coverage": "None.",
        "accessed_at": "2026-09-22T18:00:00Z",
        "content_sha256": DIGEST,
        "blocker": "Real film source has not cleared access and analysis-rights gates.",
    }
    raw.update(overrides)
    return SourceManifest.from_dict(raw)


def observation(annotator="a", play_id="10", **values):
    labels = {}
    for field in LABEL_FIELDS:
        labels[field] = {
            "value": values.get(field, f"{field}_value"),
            "confidence": "HIGH",
            "evidence_basis": "synthetic fixture authoring",
            "provenance_locator": f"synthetic://play/{play_id}/{field}",
            "observed_at": "2026-09-22T18:01:00Z",
        }
    return {
        "schema_version": "1.0",
        "source_id": "synthetic-v1",
        "annotator_id": annotator,
        "annotation_created_at": "2026-09-22T18:02:00Z",
        "game": {
            "season": 2026,
            "week": 3,
            "game_id": "SYN-2026-03-A-B",
            "home_team": "BBB",
            "away_team": "AAA",
        },
        "play": {
            "play_id": play_id,
            "quarter": 1,
            "game_clock": "12:34",
            "snap_timestamp": f"2026-09-20T17:0{play_id[-1]}:00Z",
        },
        "labels": labels,
    }


class FilmObservationTests(unittest.TestCase):
    def test_synthetic_source_cannot_claim_real_rights(self):
        with self.assertRaisesRegex(ObservationValidationError, "cannot claim"):
            manifest(rights_verified=True)

    def test_real_source_fails_closed_without_rights(self):
        with self.assertRaisesRegex(ObservationValidationError, "verified analysis rights"):
            manifest(source_type="licensed_footage", rights_verified=False)

    def test_real_source_requires_license_locator(self):
        with self.assertRaisesRegex(ObservationValidationError, "license_url"):
            manifest(source_type="licensed_charting", rights_verified=True, license_url=None)

    def test_observation_requires_all_labels(self):
        raw = observation()
        del raw["labels"]["coverage"]
        with self.assertRaisesRegex(ObservationValidationError, "coverage"):
            validate_observation(raw, manifest())

    def test_unknown_confidence_cannot_mask_asserted_label(self):
        raw = observation()
        raw["labels"]["coverage"]["confidence"] = "UNKNOWN"
        with self.assertRaisesRegex(ObservationValidationError, "unknown value"):
            validate_observation(raw, manifest())

    def test_source_identity_must_match(self):
        raw = observation()
        raw["source_id"] = "another-source"
        with self.assertRaisesRegex(ObservationValidationError, "does not match"):
            validate_observation(raw, manifest())

    def test_duplicate_binding_is_rejected(self):
        item = validate_observation(observation(), manifest())
        with self.assertRaisesRegex(ObservationValidationError, "duplicate"):
            validate_unique_bindings([item, item])

    def test_independent_comparison_reports_disagreement_and_unknown(self):
        left = [
            validate_observation(observation("primary", "10"), manifest()),
            validate_observation(observation("primary", "11", coverage="COVER_3"), manifest()),
        ]
        second = observation("reviewer", "11", coverage="COVER_1")
        right = [
            validate_observation(observation("reviewer", "10"), manifest()),
            validate_observation(second, manifest()),
        ]
        report = compare_annotations(left, right)
        coverage = report["fields"]["coverage"]
        self.assertEqual(coverage["comparable_records"], 2)
        self.assertEqual(coverage["exact_agreements"], 1)
        self.assertEqual(coverage["exact_agreement_rate"], 0.5)
        self.assertEqual(len(coverage["disagreements"]), 1)

    def test_source_content_digest_mismatch_fails_closed(self):
        path = Path("film_source_digest_test.tmp")
        try:
            path.write_text("different content", encoding="utf-8")
            with self.assertRaisesRegex(ObservationValidationError, "digest mismatch"):
                verify_source_content(path, manifest())
        finally:
            path.unlink(missing_ok=True)

    def test_jsonl_error_includes_line_number(self):
        path = Path("film_jsonl_error_test.tmp")
        try:
            path.write_text(json.dumps(observation()) + "\nnot-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ObservationValidationError, r"film_jsonl_error_test.tmp:2"):
                load_jsonl(path, manifest())
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
