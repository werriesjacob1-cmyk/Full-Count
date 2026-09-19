"""board_freeze_grader.py -- tests proving the MLB-BOARD-FREEZE-GRADER
acceptance criteria (Issue #91 AGENT CLAIM, workstream
MLB-BOARD-FREEZE-GRADER-20260918):

1. the adapter correctly reconstructs a gradeable pick for the two primary
   problem markets (hits_runs_rbis, pitcher_outs), the negative control
   (hits), and a combo market (combined_strikeouts);
2. a tampered/unverified frozen board is refused -- fails closed, never
   silently graded;
3. the frozen board's own file/content is never mutated by grading;
4. every graded record can be joined back to its frozen board via
   board_sha256;
5. a record whose market cannot be safely reconstructed (missing/invalid
   lean, for the nrfi/first-inning family) grades `ungraded` with an honest
   reason, never a guessed hit/miss.

Fixtures deliberately use generate_picks.py's REAL finished candidate schema
(projection carries "value", never "line") rather than test_board_freeze.py's
own `candidate()` helper, whose projection uses "line" -- see
board_freeze_grader._to_gradeable_pick's own docstring for the real-schema
gap this difference exposes (a real frozen board's `line` field is expected
to be None; `needs` is what this adapter actually reconstructs from).
"""
import copy
import unittest
import unittest.mock as mock

import board_freeze as bf
import board_freeze_grader as bfg
import grade_results as gr

DATE = "2026-09-18"
BOARD_GENERATED_AT = "2026-09-18T16:00:00+00:00"
SEALED_AT = "2026-09-18T16:01:00+00:00"
GAME_PK = 900001
GAME_START = "2026-09-18T23:10:00+00:00"  # strictly after BOARD_GENERATED_AT/SEALED_AT

FINAL = {"codedGameState": "F", "detailedState": "Final"}
NOT_FINAL = {"codedGameState": "I", "detailedState": "In Progress"}

PROVENANCE = {
    "model_version": "2026.08.15",
    "selection_policy_version": "1.0.0",
    "calibration_version": "1.0.0",
    "feature_version": "1.0.0",
    "git_sha": "deadbeef",
}


def _candidate(player_id, *, stat, needs=None, prop, type_="batter", team="NYY",
              matchup="NYY @ BOS", game_pk=GAME_PK, combo_player_ids=None,
              lean=None, value=None, score=80.0, hit_probability=0.65,
              **overrides):
    """A candidate dict matching generate_picks.py's real finished schema."""
    if value is None:
        value = None if needs is None else float(needs) - 0.5
    projection = {"stat": stat, "value": value}
    if needs is not None:
        projection["needs"] = needs
    body = {
        "type": type_, "player_id": player_id, "name": f"Player {player_id}",
        "team": team, "matchup": matchup, "game_pk": game_pk,
        "prop": prop, "projection": projection,
        "score": score, "hit_probability": hit_probability,
        "raw_hit_probability": hit_probability, "calibrated_by": stat,
        "reliability": "A", "sample_n": 120, "lift": 0.10,
        "market_odds": -140, "market_implied": 0.583, "market_edge": 0.067,
        "price_clears": True, "status": "neutral",
        "status_reasons": ["fixture"],
    }
    if combo_player_ids:
        body["combo_player_ids"] = combo_player_ids
    if lean:
        body["lean"] = lean
    body.update(overrides)
    return body


def _freeze_mixed(*, kept=(), qc_rejected=(), assumed_lineup=(), game_start=GAME_START):
    kept, qc_rejected, assumed_lineup = list(kept), list(qc_rejected), list(assumed_lineup)
    records = bf.freeze_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=kept,
        qc_rejected=qc_rejected, assumed_lineup=assumed_lineup, gated=kept,
        with_read=kept, no_read=[], ranked=kept, top10=kept[:1],
        by_category={}, moonshots=[], deep_moonshots=[], shadow_tracking={},
        provenance=PROVENANCE,
    )
    game_pks = sorted({r["game_pk"] for r in records})
    frozen = bf.seal_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT, sealed_at=SEALED_AT,
        game_start_times={pk: game_start for pk in game_pks}, records=records,
        provenance=PROVENANCE,
    )
    return frozen


def _freeze_one(candidate, **kw):
    return _freeze_mixed(kept=[candidate], **kw)


def _by_stat(frozen, stat):
    matches = [r for r in frozen["records"] if r["stat"] == stat]
    assert len(matches) == 1, f"expected exactly one {stat!r} record, got {len(matches)}"
    return matches[0]


class AdapterReconstructionTests(unittest.TestCase):
    """Acceptance criterion 1: the adapter reconstructs a grade_pick-ready
    dict for the negative control (hits) and the two primary problem
    markets (hits_runs_rbis, pitcher_outs), plus a combo market
    (combined_strikeouts)."""

    def test_hits_negative_control_reconstructs_type_batter_and_numeric_needs(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        record = _by_stat(frozen, "hits")
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["type"], "batter")
        self.assertEqual(pick["game_pk"], GAME_PK)
        self.assertIsInstance(pick["game_pk"], int)
        self.assertEqual(pick["player_id"], 5001)
        self.assertIsInstance(pick["player_id"], int)
        self.assertEqual(pick["projection"]["needs"], 2)
        self.assertEqual(pick["projection"]["value"], 1.5)
        self.assertEqual(pick["prop"], "Over 1.5 Hits")

    def test_hits_runs_rbis_primary_problem_market_reconstructs_correctly(self):
        frozen = _freeze_one(_candidate(5002, stat="hits_runs_rbis", needs=4,
                                        prop="Over 3.5 Hits+Runs+RBIs"))
        record = _by_stat(frozen, "hits_runs_rbis")
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["type"], "batter")
        self.assertEqual(pick["projection"]["needs"], 4)
        self.assertEqual(pick["projection"]["value"], 3.5)

    def test_pitcher_outs_primary_problem_market_reconstructs_type_pitcher(self):
        frozen = _freeze_one(_candidate(5003, stat="pitcher_outs", needs=18,
                                        prop="Over 17.5 Outs Recorded", type_="pitcher"))
        record = _by_stat(frozen, "pitcher_outs")
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["type"], "pitcher")
        self.assertEqual(pick["projection"]["needs"], 18)
        self.assertEqual(pick["projection"]["value"], 17.5)

    def test_combined_strikeouts_combo_reconstructs_int_combo_ids(self):
        frozen = _freeze_one(_candidate(
            5004, stat="combined_strikeouts", needs=9,
            prop="Over 8.5 Combined Strikeouts", type_="pitcher_combo",
            team=None, combo_player_ids=[5004, 5005],
        ))
        record = _by_stat(frozen, "combined_strikeouts")
        # board_freeze.py stringifies combo_player_ids for JSON-safe identity.
        self.assertEqual(record["combo_player_ids"], ["5004", "5005"])
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["combo_player_ids"], [5004, 5005])
        self.assertTrue(all(isinstance(pid, int) for pid in pick["combo_player_ids"]))

    def test_real_projection_schema_populates_frozen_line_from_value_not_a_missing_line_key(self):
        """Regression test for the real-schema gap found while building this
        adapter and fixed in PR #139: board_freeze.py used to read
        projection.get("line"), but every score_*() function in
        generate_picks.py sets "value", never "line" -- so a real frozen
        record's own `line` field used to be None. Now that
        build_candidate_snapshot() reads projection.get("value"), the frozen
        `line` field carries the real value. The adapter never depended on
        this field either way (it reconstructs projection.value from needs
        independently), which this test also confirms."""
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        record = _by_stat(frozen, "hits")
        self.assertEqual(record["line"], 1.5)
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["projection"]["value"], 1.5)  # derived from needs, not `line`


class GradeFrozenBoardEndToEndTests(unittest.TestCase):
    """The full adapter + grade_results.grade_pick path grades each primary
    market family correctly against a realistic box-score fixture."""

    def test_hits_grades_hit_against_a_real_box_line(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        row = graded["records"][0]
        self.assertEqual(row["grade"], "hit")
        self.assertEqual(row["actual"], 2.0)
        self.assertEqual(row["threshold"], 1.5)

    def test_hits_grades_miss_when_the_real_count_falls_short(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 1}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(graded["records"][0]["grade"], "miss")

    def test_hits_runs_rbis_sums_h_plus_r_plus_rbi_including_self_driven_in_hr(self):
        frozen = _freeze_one(_candidate(5002, stat="hits_runs_rbis", needs=4,
                                        prop="Over 3.5 Hits+Runs+RBIs"))
        with mock.patch.object(gr, "get_box_line",
                               return_value=({"h": 2, "r": 1, "rbi": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        row = graded["records"][0]
        self.assertEqual(row["actual"], 5.0)
        self.assertEqual(row["grade"], "hit")

    def test_pitcher_outs_converts_ip_notation_not_ip_times_three(self):
        frozen = _freeze_one(_candidate(5003, stat="pitcher_outs", needs=18,
                                        prop="Over 17.5 Outs Recorded", type_="pitcher"))
        with mock.patch.object(gr, "get_box_line", return_value=({"ip": "6.1"}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        row = graded["records"][0]
        self.assertEqual(row["actual"], 19.0)  # 6*3 + 1 = 19
        self.assertEqual(row["grade"], "hit")  # 19 > 17.5

    def test_combined_strikeouts_sums_both_starters_real_k_counts(self):
        frozen = _freeze_one(_candidate(
            5004, stat="combined_strikeouts", needs=9,
            prop="Over 8.5 Combined Strikeouts", type_="pitcher_combo",
            team=None, combo_player_ids=[5004, 5005],
        ))
        with mock.patch.object(gr, "get_box_line") as mgb, \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            mgb.side_effect = [({"k": 5}, None), ({"k": 5}, None)]
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        row = graded["records"][0]
        self.assertEqual(row["actual"], 10.0)
        self.assertEqual(row["grade"], "hit")
        # get_box_line must have been called with real INT ids, not the
        # frozen record's stringified combo_player_ids.
        called_ids = [call.args[1] for call in mgb.call_args_list]
        self.assertEqual(called_ids, [5004, 5005])

    def test_game_not_final_yet_grades_ungraded_not_a_false_loss(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: NOT_FINAL})
        row = graded["records"][0]
        self.assertEqual(row["grade"], "ungraded")
        self.assertIn("not final", row["reason"])


class UnrecoverableLeanTests(unittest.TestCase):
    """Acceptance criterion 5: nrfi_combined/first_inning_run candidates
    whose lean cannot be safely recovered from the frozen record grade
    `ungraded` with an honest reason -- never a guessed hit/miss."""

    def test_nrfi_combined_with_a_real_lean_reconstructs_and_grades_correctly(self):
        candidate = _candidate(f"nrfi_{GAME_PK}", stat="nrfi_combined", needs=1,
                               prop="No runs in the 1st (both teams)", type_="game",
                               team=None, lean="NRFI")
        frozen = _freeze_one(candidate)
        record = _by_stat(frozen, "nrfi_combined")
        self.assertEqual(record["market_side"], "nrfi")  # lean, lowercased, per identity scheme
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(reason)
        self.assertEqual(pick["lean"], "NRFI")
        with mock.patch.object(gr, "fetch_first_inning_linescore",
                               return_value={"away": {"runs": 0}, "home": {"runs": 0}}), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(graded["records"][0]["grade"], "hit")

    def test_nrfi_combined_missing_lean_is_ungraded_with_an_honest_reason(self):
        # A malformed/legacy candidate that never got a `lean` at all --
        # dashboard.live_state.market_side_token() then falls all the way
        # through to its "over" default, since nothing about this stat's
        # prop text starts with "under ". The frozen record's market_side
        # is therefore "over", not "yrfi"/"nrfi" -- genuinely unrecoverable.
        candidate = _candidate(f"nrfi_{GAME_PK}", stat="nrfi_combined", needs=1,
                               prop="No runs in the 1st (both teams)", type_="game",
                               team=None)  # no lean=...
        frozen = _freeze_one(candidate)
        record = _by_stat(frozen, "nrfi_combined")
        self.assertEqual(record["market_side"], "over")
        pick, reason = bfg._to_gradeable_pick(record)
        self.assertIsNone(pick)
        self.assertIn("lean", reason)
        self.assertIn("nrfi_combined", reason)
        # And the full pipeline must never call grade_pick for this record --
        # it must come back ungraded with the same honest reason, never a
        # guessed hit/miss, even though the game is Final.
        with mock.patch.object(gr, "fetch_first_inning_linescore") as mfi:
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
            mfi.assert_not_called()
        row = graded["records"][0]
        self.assertEqual(row["grade"], "ungraded")
        self.assertIn("lean", row["reason"])


class SealVerificationTests(unittest.TestCase):
    """Acceptance criterion 2: an unverified/tampered frozen board is
    refused outright -- grading fails closed, never partially or silently."""

    def test_tampered_board_is_refused_before_anything_is_graded(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        tampered = copy.deepcopy(frozen)
        tampered["records"][0]["prediction"]["hit_probability"] = 0.99
        with self.assertRaises(bf.BoardFreezeError):
            bfg.grade_frozen_board(tampered, game_statuses={GAME_PK: FINAL})

    def test_board_missing_its_seal_entirely_is_refused(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        unsealed = dict(frozen)
        del unsealed["board_sha256"]
        with self.assertRaises(bf.BoardFreezeError):
            bfg.grade_frozen_board(unsealed, game_statuses={GAME_PK: FINAL})

    def test_a_genuinely_sealed_board_is_accepted(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(graded["record_count"], 1)


class ImmutabilityTests(unittest.TestCase):
    """Acceptance criterion 3: grading a frozen board never mutates it --
    read-only in, a wholly separate artifact out."""

    def test_frozen_board_is_byte_identical_after_grading(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        before = copy.deepcopy(frozen)
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(frozen, before)
        # verify_board_seal must still succeed on the untouched original.
        self.assertEqual(bf.verify_board_seal(frozen), frozen["board_sha256"])

    def test_mutating_the_graded_output_does_not_touch_the_frozen_input(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        graded["records"][0]["grade"] = "TAMPERED"
        graded["records"][0]["prediction"]["hit_probability"] = -1
        self.assertNotEqual(frozen["records"][0]["prediction"]["hit_probability"], -1)
        self.assertEqual(bf.verify_board_seal(frozen), frozen["board_sha256"])


class JoinBackTests(unittest.TestCase):
    """Acceptance criterion 4: every graded record joins back to its frozen
    board via board_sha256 (artifact-level) and candidate_id (row-level)."""

    def test_graded_artifact_carries_the_source_board_sha256(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(graded["source_board_sha256"], frozen["board_sha256"])
        self.assertEqual(graded["date"], frozen["date"])

    def test_every_frozen_record_has_a_matching_graded_record_by_candidate_id(self):
        pool = [
            _candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"),
            _candidate(5002, stat="hits_runs_rbis", needs=4, prop="Over 3.5 Hits+Runs+RBIs"),
        ]
        frozen = _freeze_mixed(kept=pool)
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2, "r": 0, "rbi": 0}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        frozen_ids = {r["candidate_id"] for r in frozen["records"]}
        graded_ids = {r["candidate_id"] for r in graded["records"]}
        self.assertEqual(frozen_ids, graded_ids)
        self.assertEqual(len(graded["records"]), len(frozen["records"]))

    def test_full_board_grades_kept_qc_rejected_and_holdout_buckets_alike(self):
        """The whole point of this module: EVERY candidate in the frozen
        universe gets an outcome attempt, not just the kept/selected ones."""
        kept = [_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits")]
        qc_rejected = [_candidate(5002, stat="hits", needs=1, prop="Over 0.5 Hits",
                                  qc_reason="rain risk")]
        holdout = [_candidate(5003, stat="hits", needs=1, prop="Over 0.5 Hits",
                              lineup_assumed=True)]
        frozen = _freeze_mixed(kept=kept, qc_rejected=qc_rejected, assumed_lineup=holdout)
        self.assertEqual(frozen["record_count"], 3)
        with mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen, game_statuses={GAME_PK: FINAL})
        self.assertEqual(graded["record_count"], 3)
        by_id = {r["player_id"]: r for r in graded["records"]}
        self.assertEqual(by_id["5001"]["grade"], "hit")
        self.assertEqual(by_id["5002"]["eligibility"]["qc_status"], "qc_rejected")
        self.assertEqual(by_id["5002"]["grade"], "hit")  # still gets a real outcome
        self.assertEqual(by_id["5003"]["eligibility"]["qc_status"], "lineup_assumed_holdout")
        self.assertEqual(by_id["5003"]["grade"], "hit")


class FetchGameStatusesReuseTests(unittest.TestCase):
    """grade_frozen_board() must reuse grade_results.fetch_game_statuses
    exactly as grade_day() already does, not reinvent box-score fetching."""

    def test_fetches_game_statuses_itself_when_none_supplied(self):
        frozen = _freeze_one(_candidate(5001, stat="hits", needs=2, prop="Over 1.5 Hits"))
        with mock.patch.object(gr, "fetch_game_statuses",
                               return_value={GAME_PK: FINAL}) as mfs, \
             mock.patch.object(gr, "get_box_line", return_value=({"h": 2}, None)), \
             mock.patch.object(gr, "opportunity_context", return_value={}):
            graded = bfg.grade_frozen_board(frozen)
        mfs.assert_called_once_with(DATE)
        self.assertEqual(graded["records"][0]["grade"], "hit")


class GradedBoardPathTests(unittest.TestCase):
    def test_graded_path_matches_board_freeze_naming_convention_plus_graded(self):
        path = bfg.graded_board_path("2026-09-18")
        self.assertTrue(path.endswith("board_freeze_graded_2026-09-18.json"))


if __name__ == "__main__":
    unittest.main()
