"""grade_board_freeze.py -- tests proving the missing grading-half of the
winner's-curse calibration instrumentation (Issue #91, PR #131/#132/#138's
convergent finding, closed by "SUPERCLAUDE — CONTINUE EXECUTION WHILE
INDEPENDENT REVIEW RUNS").

Uses the same real candidate/freeze-fixture helpers `test_board_freeze_grader.py`
already established (finished-candidate schema with `projection.value`, never
`.line`), rather than inventing a second fixture shape.
"""
import copy
import os
import tempfile
import unittest

import board_freeze as bf
import board_freeze_grader as bfg
import grade_board_freeze as gbf

DATE = "2026-09-18"
BOARD_GENERATED_AT = "2026-09-18T16:00:00+00:00"
SEALED_AT = "2026-09-18T16:01:00+00:00"
GAME_PK = 900001
GAME_START = "2026-09-18T23:10:00+00:00"

FINAL = {"codedGameState": "F", "detailedState": "Final"}

PROVENANCE = {
    "model_version": "2026.08.15",
    "selection_policy_version": "1.0.0",
    "calibration_version": "1.0.0",
    "feature_version": "1.0.0",
    "git_sha": "deadbeef",
}


def _candidate(player_id, *, stat="hits", needs=2, prop="2+ Hits", value=None, **overrides):
    if value is None:
        value = float(needs) - 0.5
    projection = {"stat": stat, "value": value, "needs": needs}
    body = {
        "type": "batter", "player_id": player_id, "name": f"Player {player_id}",
        "team": "NYY", "matchup": "NYY @ BOS", "game_pk": GAME_PK,
        "prop": prop, "projection": projection,
        "score": 80.0, "hit_probability": 0.65, "raw_hit_probability": 0.65,
        "calibrated_by": stat, "reliability": "A", "sample_n": 120, "lift": 0.10,
        "market_odds": -140, "market_implied": 0.583, "market_edge": 0.067,
        "price_clears": True, "status": "neutral", "status_reasons": ["fixture"],
    }
    body.update(overrides)
    return body


def _sealed_board(*, candidates):
    records = bf.freeze_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=list(candidates),
        qc_rejected=[], assumed_lineup=[], gated=list(candidates),
        with_read=list(candidates), no_read=[], ranked=list(candidates),
        top10=list(candidates)[:1], by_category={}, moonshots=[], deep_moonshots=[],
        shadow_tracking={}, provenance=PROVENANCE,
    )
    game_pks = sorted({r["game_pk"] for r in records})
    return bf.seal_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT, sealed_at=SEALED_AT,
        game_start_times={pk: GAME_START for pk in game_pks}, records=records, provenance=PROVENANCE,
    )


class GradeDateTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._orig_output_dir = gbf.OUTPUT_DIR
        gbf.OUTPUT_DIR = self._tmpdir.name
        self.addCleanup(setattr, gbf, "OUTPUT_DIR", self._orig_output_dir)
        # bfg.graded_board_path/board_freeze_grader.OUTPUT_DIR must point at
        # the same tmp dir, since it's a separate module-level constant.
        self._orig_bfg_output_dir = bfg.OUTPUT_DIR
        bfg.OUTPUT_DIR = self._tmpdir.name
        self.addCleanup(setattr, bfg, "OUTPUT_DIR", self._orig_bfg_output_dir)

    def _write_frozen_board(self, frozen):
        import json
        path = gbf.frozen_board_path(DATE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(frozen, f)
        return path

    def test_no_op_when_no_frozen_board_exists(self):
        result = gbf.grade_date("2026-09-01")
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(bfg.graded_board_path("2026-09-01")))

    def test_corrupt_frozen_board_file_never_raises(self):
        """Real defect found by independent review (Issue #91 comment
        5746165033): the file read used to sit OUTSIDE the try/except, so a
        truncated/corrupt output/board_freeze_{date}.json raised an
        uncaught json.decoder.JSONDecodeError, exiting the whole job
        non-zero -- the opposite of the "picks pipeline unaffected" claim.
        Reproduces that exact scenario directly against the real file the
        grader reads, not a mock."""
        path = gbf.frozen_board_path(DATE)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"records": [truncated garbage, not valid json')

        result = gbf.grade_date(DATE)  # must not raise

        self.assertIsNone(result)
        self.assertFalse(os.path.exists(bfg.graded_board_path(DATE)))

    def test_main_never_raises_on_a_corrupt_file_either(self):
        path = gbf.frozen_board_path(DATE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not even close to json")

        rc = gbf.main(date_override=DATE)  # must not raise

        self.assertEqual(rc, 0)

    def test_grades_a_real_sealed_board_and_writes_graded_artifact(self):
        frozen = _sealed_board(candidates=[_candidate(1, needs=2)])
        self._write_frozen_board(frozen)

        # grade_frozen_board fetches game statuses itself when not supplied;
        # patch grade_results.fetch_game_statuses so this stays network-free.
        import unittest.mock as mock
        import grade_results as gr
        with mock.patch.object(gr, "fetch_game_statuses", return_value={GAME_PK: FINAL}):
            result = gbf.grade_date(DATE)

        self.assertIsNotNone(result)
        self.assertEqual(result["record_count"], 1)
        out_path = bfg.graded_board_path(DATE)
        self.assertTrue(os.path.exists(out_path))

    def test_tampered_board_is_not_graded(self):
        frozen = _sealed_board(candidates=[_candidate(1, needs=2)])
        tampered = copy.deepcopy(frozen)
        tampered["records"][0]["score"] = 999.0  # mutate after sealing
        self._write_frozen_board(tampered)

        import unittest.mock as mock
        import grade_results as gr
        with mock.patch.object(gr, "fetch_game_statuses", return_value={GAME_PK: FINAL}):
            result = gbf.grade_date(DATE)

        self.assertIsNone(result, "a tampered board must never be graded")
        self.assertFalse(os.path.exists(bfg.graded_board_path(DATE)))

    def test_main_uses_date_override_without_touching_real_clock(self):
        frozen = _sealed_board(candidates=[_candidate(1, needs=2)])
        self._write_frozen_board(frozen)

        import unittest.mock as mock
        import grade_results as gr
        with mock.patch.object(gr, "fetch_game_statuses", return_value={GAME_PK: FINAL}):
            rc = gbf.main(date_override=DATE)

        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(bfg.graded_board_path(DATE)))


if __name__ == "__main__":
    unittest.main()
