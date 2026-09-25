"""board_freeze.py -- tests proving the MLB-BOARD-FREEZE-INSTRUMENTATION
acceptance criteria (Issue #91 AGENT CLAIM, workstream
MLB-BOARD-FREEZE-INSTRUMENTATION-20260918):

1. an exact historical Top Pick decision can be replayed from the frozen
   board;
2. selected and non-selected candidates remain distinguishable;
3. no postgame field can alter the frozen universe (chronology + tamper
   detection);
4. chronology and immutability are enforced, not merely documented;
5. the artifact enables rank/argmax calibration analysis (final_rank is
   correctly wired from the real selector ordering).
"""
import copy
import unittest

import board_freeze as bf

DATE = "2026-09-18"
BOARD_GENERATED_AT = "2026-09-18T16:00:00+00:00"
SEALED_AT = "2026-09-18T16:01:00+00:00"
GAME_PK = 745001
GAME_START = "2026-09-18T23:10:00+00:00"  # strictly after BOARD_GENERATED_AT/SEALED_AT

PROVENANCE = {
    "model_version": "2026.08.15",
    "selection_policy_version": "1.0.0",
    "calibration_version": "1.0.0",
    "feature_version": "1.0.0",
    "git_sha": "deadbeef",
}


def candidate(player_id, *, stat="hits", needs=0.5, line=0.5, score=80.0,
              hit_probability=0.65, prop="Over 0.5 Hits", **overrides):
    value = {
        "player_id": player_id,
        "name": f"Player {player_id}",
        "team": "NYY",
        "matchup": "NYY @ BOS",
        "game_pk": GAME_PK,
        "prop": prop,
        # generate_picks.py's real score_*() functions set projection["value"],
        # never "line" -- matched here so this fixture doesn't mask the exact
        # bug board_freeze_grader.py's own adapter work found.
        "projection": {"stat": stat, "needs": needs, "value": line},
        "score": score,
        "hit_probability": hit_probability,
        "raw_hit_probability": hit_probability,
        "calibrated_by": "hits",
        "reliability": "A",
        "sample_n": 120,
        "lift": 0.10,
        "market_odds": -140,
        "market_implied": 0.583,
        "market_edge": 0.067,
        "price_clears": True,
        "status": "neutral",
        "status_reasons": ["not evaluated in this fixture"],
    }
    value.update(overrides)
    return value


def base_pools(top_pick_id=1001, second_id=1002, third_id=1003):
    """One selected Top Pick, one ranked-but-unselected candidate, one
    candidate that fails the positive-read floor -- enough spread across
    the funnel to exercise every bucket board_freeze.py distinguishes."""
    top = candidate(top_pick_id, hit_probability=0.70, score=85.0,
                     status="top_pick", status_reasons=["cleared every gate"])
    second = candidate(second_id, hit_probability=0.62, score=75.0,
                        price_clears=False, status="neutral",
                        status_reasons=["no confirmed edge over the posted price"])
    no_read = candidate(third_id, hit_probability=0.55, score=70.0, lift=-0.05,
                         status="neutral", status_reasons=["below base rate"])

    candidates = [top, second, no_read]
    gated = [top, second, no_read]
    with_read = [top, second]
    no_read_pool = [no_read]
    ranked = [top, second, no_read]
    top10 = [top]
    return candidates, gated, with_read, no_read_pool, ranked, top10


def freeze_and_seal(*, candidates, gated, with_read, no_read, ranked, top10,
                     qc_rejected=(), assumed_lineup=(), by_category=None,
                     moonshots=(), deep_moonshots=(), shadow_tracking=None,
                     sealed_at=SEALED_AT, game_start=GAME_START,
                     board_generated_at=BOARD_GENERATED_AT):
    records = bf.freeze_board(
        date=DATE, board_generated_at=board_generated_at,
        candidates=candidates, qc_rejected=list(qc_rejected),
        assumed_lineup=list(assumed_lineup), gated=gated, with_read=with_read,
        no_read=no_read, ranked=ranked, top10=top10,
        by_category=by_category or {}, moonshots=list(moonshots),
        deep_moonshots=list(deep_moonshots),
        shadow_tracking=shadow_tracking or {}, provenance=PROVENANCE,
    )
    frozen = bf.seal_board(
        date=DATE, board_generated_at=board_generated_at, sealed_at=sealed_at,
        game_start_times={str(GAME_PK): game_start}, records=records,
        provenance=PROVENANCE,
    )
    return records, frozen


class ReplayTopPickDecisionTests(unittest.TestCase):
    """Acceptance criterion 1: an exact historical Top Pick decision can be
    replayed from the frozen board alone."""

    def test_selected_top_pick_carries_every_field_needed_to_replay_it(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        _, frozen = freeze_and_seal(candidates=candidates, gated=gated,
                                     with_read=with_read, no_read=no_read,
                                     ranked=ranked, top10=top10)
        top_records = [r for r in frozen["records"] if r["selector"]["selected_top_pick"]]
        self.assertEqual(len(top_records), 1)
        row = top_records[0]
        # Everything the mission named as required to replay the decision.
        self.assertEqual(row["stat"], "hits")
        self.assertEqual(row["market_side"], "over")
        self.assertEqual(row["prediction"]["hit_probability"], 0.70)
        self.assertEqual(row["market"]["market_odds"], -140)
        self.assertEqual(row["market"]["price_clears"], True)
        self.assertEqual(row["selector"]["recommendation_status"], "top_pick")
        self.assertEqual(row["selector"]["final_rank"], 1)
        self.assertEqual(row["provenance"]["model_version"], "2026.08.15")
        self.assertEqual(row["provenance"]["calibration_version"], "1.0.0")
        self.assertEqual(row["generation_timestamp"], BOARD_GENERATED_AT)
        self.assertEqual(row["game_pk"], str(GAME_PK))


class DistinguishSelectionTests(unittest.TestCase):
    """Acceptance criterion 2: selected vs non-selected candidates remain
    distinguishable, including WHY a non-selected one was not chosen."""

    def test_non_top_pick_ranked_candidate_is_marked_not_selected_with_reason(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        _, frozen = freeze_and_seal(candidates=candidates, gated=gated,
                                     with_read=with_read, no_read=no_read,
                                     ranked=ranked, top10=top10)
        by_id = {r["player_id"]: r for r in frozen["records"]}
        second = by_id["1002"]
        self.assertFalse(second["selector"]["selected_top_pick"])
        self.assertEqual(second["selector"]["final_rank"], 2)
        self.assertTrue(second["eligibility"]["cleared_quality_gate"])
        self.assertTrue(second["eligibility"]["cleared_positive_read_floor"])
        self.assertIsNone(second["eligibility"]["rejection_reason"])  # ranked, just not #1/no edge

    def test_positive_read_floor_reject_carries_its_own_rejection_reason(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        _, frozen = freeze_and_seal(candidates=candidates, gated=gated,
                                     with_read=with_read, no_read=no_read,
                                     ranked=ranked, top10=top10)
        by_id = {r["player_id"]: r for r in frozen["records"]}
        rejected = by_id["1003"]
        self.assertFalse(rejected["eligibility"]["cleared_positive_read_floor"])
        self.assertIn("positive-read floor", rejected["eligibility"]["rejection_reason"])
        self.assertFalse(rejected["selector"]["selected_top_pick"])

    def test_qc_rejected_and_lineup_assumed_holdout_are_preserved_with_reasons(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        qc_rejected = [candidate(2001, qc_reason="rain risk 45% -- postponement likely")]
        assumed_lineup = [candidate(2002, lineup_assumed=True)]
        records, frozen = freeze_and_seal(
            candidates=candidates, gated=gated, with_read=with_read,
            no_read=no_read, ranked=ranked, top10=top10,
            qc_rejected=qc_rejected, assumed_lineup=assumed_lineup,
        )
        by_id = {r["player_id"]: r for r in frozen["records"]}
        self.assertEqual(frozen["record_count"], 5)
        rej = by_id["2001"]
        self.assertEqual(rej["eligibility"]["qc_status"], "qc_rejected")
        self.assertIn("rain risk", rej["eligibility"]["rejection_reason"])
        self.assertIsNone(rej["selector"]["final_rank"])  # never entered the rank pool

        holdout = by_id["2002"]
        self.assertEqual(holdout["eligibility"]["qc_status"], "lineup_assumed_holdout")
        self.assertTrue(holdout["eligibility"]["lineup_assumed"])
        self.assertIn("held out", holdout["eligibility"]["rejection_reason"])


class ChronologyAndTamperTests(unittest.TestCase):
    """Acceptance criteria 3 and 4: no postgame field can alter the frozen
    universe, and chronology/immutability are enforced by the code, not
    merely asserted in a docstring."""

    def test_sealing_at_or_after_first_pitch_is_rejected(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        with self.assertRaises(bf.BoardFreezeError):
            freeze_and_seal(candidates=candidates, gated=gated, with_read=with_read,
                             no_read=no_read, ranked=ranked, top10=top10,
                             sealed_at=GAME_START)  # exactly at first pitch

    def test_missing_game_start_time_fails_closed(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        records = bf.freeze_board(
            date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=candidates,
            qc_rejected=[], assumed_lineup=[], gated=gated, with_read=with_read,
            no_read=no_read, ranked=ranked, top10=top10, by_category={},
            moonshots=[], deep_moonshots=[], shadow_tracking={}, provenance=PROVENANCE,
        )
        with self.assertRaises(bf.BoardFreezeError):
            bf.seal_board(date=DATE, board_generated_at=BOARD_GENERATED_AT,
                          sealed_at=SEALED_AT, game_start_times={}, records=records,
                          provenance=PROVENANCE)

    def test_tampering_with_a_sealed_record_is_detected_on_reverify(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        _, frozen = freeze_and_seal(candidates=candidates, gated=gated,
                                     with_read=with_read, no_read=no_read,
                                     ranked=ranked, top10=top10)
        # A postgame process must never be able to alter the frozen universe
        # undetected -- simulate exactly that (flipping a probability after
        # the fact) and require verify_board_seal to catch it.
        tampered = copy.deepcopy(frozen)
        tampered["records"][0]["prediction"]["hit_probability"] = 0.99
        with self.assertRaises(bf.BoardFreezeError):
            bf.verify_board_seal(tampered)
        # The untouched original must still verify cleanly.
        self.assertEqual(bf.verify_board_seal(frozen), frozen["board_sha256"])

    def test_duplicate_candidate_identity_across_buckets_fails_closed(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        dup = candidates[0]
        with self.assertRaises(bf.BoardFreezeError):
            bf.freeze_board(
                date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=candidates,
                qc_rejected=[dup], assumed_lineup=[], gated=gated, with_read=with_read,
                no_read=no_read, ranked=ranked, top10=top10, by_category={},
                moonshots=[], deep_moonshots=[], shadow_tracking={}, provenance=PROVENANCE,
            )

    def test_missing_provenance_field_fails_closed(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        broken_provenance = dict(PROVENANCE)
        del broken_provenance["calibration_version"]
        records = bf.freeze_board(
            date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=candidates,
            qc_rejected=[], assumed_lineup=[], gated=gated, with_read=with_read,
            no_read=no_read, ranked=ranked, top10=top10, by_category={},
            moonshots=[], deep_moonshots=[], shadow_tracking={},
            provenance=broken_provenance,
        )
        with self.assertRaises(bf.BoardFreezeError):
            bf.seal_board(date=DATE, board_generated_at=BOARD_GENERATED_AT,
                          sealed_at=SEALED_AT, game_start_times={str(GAME_PK): GAME_START},
                          records=records, provenance=broken_provenance)

    def test_empty_board_fails_closed_rather_than_sealing_nothing(self):
        with self.assertRaises(bf.BoardFreezeError):
            bf.freeze_board(
                date=DATE, board_generated_at=BOARD_GENERATED_AT, candidates=[],
                qc_rejected=[], assumed_lineup=[], gated=[], with_read=[],
                no_read=[], ranked=[], top10=[], by_category={}, moonshots=[],
                deep_moonshots=[], shadow_tracking={}, provenance=PROVENANCE,
            )


class RankArgmaxAnalysisTests(unittest.TestCase):
    """Acceptance criterion 5: the artifact must support rank/argmax
    calibration analysis -- final_rank must reflect the real selector
    ordering (rank_for_board's output), not just top-pick/not-top-pick."""

    def test_final_rank_matches_real_selector_ordering_for_every_ranked_candidate(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        _, frozen = freeze_and_seal(candidates=candidates, gated=gated,
                                     with_read=with_read, no_read=no_read,
                                     ranked=ranked, top10=top10)
        by_player = {r["player_id"]: r for r in frozen["records"]}
        self.assertEqual(by_player["1001"]["selector"]["final_rank"], 1)
        self.assertEqual(by_player["1002"]["selector"]["final_rank"], 2)
        self.assertEqual(by_player["1003"]["selector"]["final_rank"], 3)
        # The argmax candidate (rank 1) must be exactly the selected Top Pick.
        argmax = min(frozen["records"], key=lambda r: r["selector"]["final_rank"])
        self.assertTrue(argmax["selector"]["selected_top_pick"])

    def test_category_and_shadow_selection_surfaces_are_recorded_by_identity(self):
        candidates, gated, with_read, no_read, ranked, top10 = base_pools()
        # by_category/shadow_tracking are fresh copied dicts in production
        # (see generate_picks.main()'s own comment on this) -- exercise that
        # exact shape here rather than relying on shared object identity.
        category_copy = dict(candidates[1])
        shadow_copy = dict(candidates[2])
        _, frozen = freeze_and_seal(
            candidates=candidates, gated=gated, with_read=with_read,
            no_read=no_read, ranked=ranked, top10=top10,
            by_category={"hits": [category_copy]},
            shadow_tracking={("hits", "0.5"): [shadow_copy]},
        )
        by_player = {r["player_id"]: r for r in frozen["records"]}
        self.assertTrue(by_player["1002"]["selector"]["selected_category_board"])
        self.assertTrue(by_player["1003"]["selector"]["selected_shadow"])
        self.assertFalse(by_player["1001"]["selector"]["selected_category_board"])


if __name__ == "__main__":
    unittest.main()
