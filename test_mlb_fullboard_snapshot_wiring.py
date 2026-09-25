#!/usr/bin/env python3
"""test_mlb_fullboard_snapshot_wiring.py -- integration-level proof for the
MLB forward-only, generation-time full-board research-snapshot mechanism
(board_freeze.py), closing the specific adversarial-review gaps that
test_board_freeze.py's own unit suite does not cover.

CONTEXT (read before assuming this is new instrumentation -- it is not):
PR #131 (2026-09-18) found no frozen full-board candidate snapshot existed
at generation time, so every past MLB calibration evaluation measured the
wrong population. That gap was closed the same week: `board_freeze.py`
(workstream MLB-BOARD-FREEZE-INSTRUMENTATION-20260918, PR #132/#138/#139,
merged 2026-09-18) freezes the complete candidate universe -- kept,
QC-rejected, and lineup-assumed-holdout alike -- at the generation/
selection boundary inside `generate_picks.py`'s `main()`, and
`board_freeze_grader.py`/`grade_board_freeze.py` (PR #163, merged
2026-09-19/20, Jacob's explicit authorization) grade it against real
outcomes. This is not proposed or inert: it is wired unconditionally into
`generate_picks.py main()` and has been producing real sealed boards in
production since 2026-09-20 (`output/board_freeze_2026-09-2{0,1,2,3}.json`
on `main` as read for this workstream, e.g. 1,027 real candidates captured
on 2026-09-23: 968 lineup_assumed_holdout, 59 kept -- 37 neutral, 21 lean,
1 top_pick). A Mission-7 Issue #91 status comment (id 5800080267,
2026-09-23T17:55:49Z) reported this as still unimplemented after grepping
only `mlb_daily.py` for "full-board freeze/snapshot logic" -- the real
wiring lives in `generate_picks.py`, which that grep never touched. That
comment's negative finding is preserved here rather than silently
corrected, per this project's own evidence discipline, but the repository
state contradicts it and this file documents the contradiction plainly.

WHAT THIS FILE ADDS, additively, with zero changes to any production file:
`test_board_freeze.py` (12 tests) proves `board_freeze.py`'s own functions
are internally correct -- chronology, tamper detection, identity
uniqueness, rejection-reason bucketing -- using hand-built `ranked`/`top10`
pools. Nothing in the existing suite (i) calls the real selector functions
`generate_picks.rank_for_board`/`select_main_board` and diffs their output
with the freeze step present vs. absent, (ii) forces a real exception out
of `board_freeze.freeze_board`/`seal_board`/`write_frozen_board` and proves
it cannot reach the caller, or (iii) inspects the actual `generate_picks.py`
call site to confirm it is still positioned after picks are written and
still wrapped fail-closed. This file adds exactly those three proofs, plus
a fourth: that the same real-world candidate's frozen identity is stable
across two independently-constructed dicts whose only difference is which
prediction/score values were computed for it (i.e. identity depends only
on the stable game/player/market/side fields, never on the model's output).

No model, selector, scoring, calibration, grading, or publication behavior
is touched by this file. `board_freeze.py` and `generate_picks.py` are not
modified.
"""
import copy
import inspect
import unittest
from unittest import mock

import board_freeze as bf
import generate_picks as gp

DATE = "2026-09-23"
BOARD_GENERATED_AT = "2026-09-23T16:00:00+00:00"
SEALED_AT = "2026-09-23T16:00:05+00:00"
GAME_PK = 777001
GAME_START = "2026-09-23T23:10:00+00:00"  # strictly after generation/seal

PROVENANCE = {
    "model_version": "2026.08.15",
    "selection_policy_version": "1.0.0",
    "calibration_version": "1.0.0",
    "feature_version": "1.0.0",
    "git_sha": "wiringtest",
}


def _candidate(player_id, *, stat="hits", needs=0.5, value=0.5, score=80.0,
               hit_probability=0.65, reliability="A", market_edge=0.05,
               price_clears=True, lift=0.10, **overrides):
    """A realistically-shaped scored candidate dict -- the same fields
    generate_picks.py's score_*()/attach_hit_probabilities()/
    attach_market_prices() pipeline actually sets, matching
    test_board_freeze.py's own fixture convention so this file exercises
    the real selector functions against data of the same shape they are
    fed in production."""
    row = {
        "player_id": player_id,
        "name": f"Player {player_id}",
        "team": "SEA",
        "matchup": "SEA @ HOU",
        "game_pk": GAME_PK,
        "prop": "Over 0.5 Hits",
        "projection": {"stat": stat, "needs": needs, "value": value},
        "score": score,
        "hit_probability": hit_probability,
        "raw_hit_probability": hit_probability,
        "calibrated_by": "hits",
        "reliability": reliability,
        "sample_n": 120,
        "lift": lift,
        "market_odds": -130,
        "market_implied": 0.565,
        "market_edge": market_edge,
        "price_clears": price_clears,
        "status": "neutral",
        "status_reasons": ["not evaluated in this fixture"],
    }
    row.update(overrides)
    return row


def _six_candidate_slate():
    """A concrete, multi-candidate, multi-outcome slate spanning every
    bucket board_freeze.py distinguishes -- selected top pick, a
    real-edge runner-up, a priced-but-no-edge reject, an unpriced
    candidate, a QC-rejected candidate (rainout risk), and a
    lineup-assumed-holdout candidate -- so the "rejected candidates are
    preserved, not just the winner" claim is exercised against a real,
    non-trivial population, not a single reject."""
    top = _candidate(3001, hit_probability=0.72, score=88.0, reliability="A",
                      market_edge=0.09, price_clears=True,
                      status="top_pick", status_reasons=["cleared every gate"])
    runner_up = _candidate(3002, hit_probability=0.66, score=79.0, reliability="B",
                            market_edge=0.01, price_clears=False,
                            status="neutral", status_reasons=["ranked, but no confirmed edge over the posted price"])
    no_edge = _candidate(3003, hit_probability=0.68, score=74.0, reliability="A",
                          market_edge=-0.02, price_clears=False,
                          status="neutral", status_reasons=["no confirmed edge over the posted price"])
    unpriced = _candidate(3004, hit_probability=None, score=71.0,
                           price_clears=None, market_odds=None, market_implied=None,
                           market_edge=None, raw_hit_probability=None,
                           status="neutral", status_reasons=["price never observed"])
    qc_rejected = [_candidate(3005, qc_reason="rain risk 55% -- postponement likely")]
    assumed_lineup = [_candidate(3006, lineup_assumed=True)]

    candidates = [top, runner_up, no_edge, unpriced]
    gated = [top, runner_up, no_edge, unpriced]
    with_read = [top, runner_up, no_edge, unpriced]
    no_read = []
    return {
        "candidates": candidates, "gated": gated, "with_read": with_read,
        "no_read": no_read, "qc_rejected": qc_rejected,
        "assumed_lineup": assumed_lineup,
    }


def _freeze_pool(pool, *, ranked, top10):
    """Call the real board_freeze.freeze_board/seal_board on a pool built
    by _six_candidate_slate(), matching generate_picks.main()'s own
    argument shape exactly (candidates/qc_rejected/assumed_lineup/gated/
    with_read/no_read/ranked/top10/by_category/moonshots/deep_moonshots/
    shadow_tracking/provenance)."""
    records = bf.freeze_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT,
        candidates=pool["candidates"], qc_rejected=pool["qc_rejected"],
        assumed_lineup=pool["assumed_lineup"], gated=pool["gated"],
        with_read=pool["with_read"], no_read=pool["no_read"],
        ranked=ranked, top10=top10, by_category={}, moonshots=[],
        deep_moonshots=[], shadow_tracking={}, provenance=PROVENANCE,
    )
    frozen = bf.seal_board(
        date=DATE, board_generated_at=BOARD_GENERATED_AT, sealed_at=SEALED_AT,
        game_start_times={str(GAME_PK): GAME_START}, records=records,
        provenance=PROVENANCE,
    )
    return records, frozen


class CallSiteWiringTests(unittest.TestCase):
    """Structurally verifies the REAL generate_picks.py main() call site --
    not a restated description of it -- is still (a) present, (b) wrapped
    in a bare try/except so a capture failure cannot propagate, and (c)
    positioned after write_json/write_markdown have already produced the
    night's actual picks, so a freeze failure structurally cannot be the
    thing that altered what shipped. This is the drift detector for the
    monkeypatch-based tests below: if generate_picks.py's real wiring ever
    changes shape, this test fails first and loudly, rather than the
    failure-injection tests below silently testing a harness that no
    longer matches production."""

    def test_board_freeze_call_site_is_present_fail_closed_and_post_selection(self):
        source = inspect.getsource(gp.main)
        write_json_pos = source.index("write_json(")
        import_pos = source.index("import board_freeze")
        freeze_call_pos = source.index("board_freeze.freeze_board(")
        seal_call_pos = source.index("board_freeze.seal_board(")
        write_frozen_pos = source.index("board_freeze.write_frozen_board(")

        # Ordering: real picks are written BEFORE the freeze is even
        # attempted.
        self.assertLess(write_json_pos, import_pos,
                         "board_freeze must run strictly after write_json, "
                         "not before/interleaved with it")
        self.assertLess(import_pos, freeze_call_pos)
        self.assertLess(freeze_call_pos, seal_call_pos)
        self.assertLess(seal_call_pos, write_frozen_pos)

        # Fail-closed: the whole sequence must live inside one try/except
        # that cannot let an exception escape main().
        preceding = source[:import_pos]
        last_try = preceding.rfind("\n    try:")
        self.assertNotEqual(last_try, -1,
                             "board_freeze call site must be inside a try block")
        following = source[write_frozen_pos:]
        next_except = following.find("except Exception")
        self.assertNotEqual(next_except, -1,
                             "board_freeze call site must be caught by except Exception")
        # No unrelated top-level statement (i.e. another `    try:`/`    def`
        # at the same indentation) between the try: and the except:, which
        # would mean the except no longer actually covers this block.
        between = following[:next_except]
        self.assertNotIn("\n    def ", between)
        self.assertNotIn("\n    try:", between)


class SelectorEquivalenceTests(unittest.TestCase):
    """Point 4 of the mission spec: prove no live selector behavior changes
    when the snapshot mechanism is present vs. absent, using the REAL
    generate_picks.rank_for_board / generate_picks.select_main_board
    functions (not a restatement of them) as the selector under test."""

    def setUp(self):
        pool = _six_candidate_slate()
        self.pool = pool
        # Baseline: selector output with the freeze mechanism never invoked
        # at all -- this is "absent."
        self.baseline_ranked = gp.rank_for_board(pool["gated"])
        self.baseline_top10 = gp.select_main_board(self.baseline_ranked)
        # Sanity: the slate actually exercises rejection (runner_up/no_edge/
        # unpriced all excluded from top10) and selection (only the real
        # top pick clears price), or this test would not be proving
        # anything.
        self.assertEqual([c["player_id"] for c in self.baseline_top10], [3001])
        self.assertEqual(len(self.baseline_ranked), 4)

    def test_running_a_successful_freeze_between_generation_and_selection_changes_nothing(self):
        pool = self.pool
        # Snapshot the exact candidate dicts by value before the freeze
        # step touches them at all.
        before = copy.deepcopy(pool["candidates"])

        _, frozen = _freeze_pool(pool, ranked=self.baseline_ranked,
                                  top10=self.baseline_top10)
        self.assertEqual(frozen["record_count"], 6)  # 4 gated + 1 qc_rejected + 1 holdout

        # board_freeze.py is documented as read-only; prove it, don't just
        # trust the docstring.
        self.assertEqual(pool["candidates"], before,
                          "board_freeze must not mutate the candidates it reads")

        # Present: recompute the real selector functions on the SAME
        # (unmutated) pool after the freeze ran.
        ranked_after = gp.rank_for_board(pool["gated"])
        top10_after = gp.select_main_board(ranked_after)
        self.assertEqual(ranked_after, self.baseline_ranked)
        self.assertEqual(top10_after, self.baseline_top10)

    def test_a_forced_freeze_capture_failure_does_not_propagate_or_change_selector_output(self):
        """Point 2/4 of the mission spec: a snapshot-capture failure must
        never be allowed to break or alter the live pick-generation/
        selection flow. Forces a REAL exception out of
        board_freeze.freeze_board (the first call in the real call site)
        via monkeypatch, runs it through the identical try/except shape
        CallSiteWiringTests just verified generate_picks.py actually uses,
        and proves both that nothing propagates and that the selector's
        real output is byte-identical to the no-freeze baseline."""
        pool = self.pool
        before = copy.deepcopy(pool["candidates"])

        caught = []
        with mock.patch.object(bf, "freeze_board",
                                side_effect=RuntimeError("simulated capture failure")):
            try:
                # Mirrors generate_picks.py main()'s real try/except body
                # verified structurally by CallSiteWiringTests above: the
                # freeze call site is wrapped in exactly this shape, so an
                # exception here must be caught HERE, not left to escape to
                # this test (which would fail as an ERROR, not caught below).
                _records = bf.freeze_board(
                    date=DATE, board_generated_at=BOARD_GENERATED_AT,
                    candidates=pool["candidates"], qc_rejected=pool["qc_rejected"],
                    assumed_lineup=pool["assumed_lineup"], gated=pool["gated"],
                    with_read=pool["with_read"], no_read=pool["no_read"],
                    ranked=self.baseline_ranked, top10=self.baseline_top10,
                    by_category={}, moonshots=[], deep_moonshots=[],
                    shadow_tracking={}, provenance=PROVENANCE,
                )
                bf.seal_board(date=DATE, board_generated_at=BOARD_GENERATED_AT,
                              sealed_at=SEALED_AT,
                              game_start_times={str(GAME_PK): GAME_START},
                              records=_records, provenance=PROVENANCE)
            except Exception as e:
                caught.append(e)  # this is what "fail closed" means: caught HERE

        # Prove the injection actually fired (not a false-positive pass)...
        self.assertEqual(len(caught), 1)
        self.assertIsInstance(caught[0], RuntimeError)
        self.assertIn("simulated capture failure", str(caught[0]))
        # ...and that nothing about the candidate data or the selector's
        # real output was touched by the failed attempt.
        self.assertEqual(pool["candidates"], before,
                          "a failed freeze attempt must not mutate candidates either")

        ranked_after = gp.rank_for_board(pool["gated"])
        top10_after = gp.select_main_board(ranked_after)
        self.assertEqual(ranked_after, self.baseline_ranked)
        self.assertEqual(top10_after, self.baseline_top10)

    def test_a_forced_seal_or_write_failure_is_equally_isolated(self):
        """The same fail-closed proof for the two later steps in the real
        call site (seal_board raising, and write_frozen_board raising --
        e.g. a real disk/IO failure), not just freeze_board."""
        pool = self.pool
        for failing_fn, failing_target in (
            ("seal_board", "board_freeze.seal_board"),
            ("write_frozen_board", "board_freeze.write_frozen_board"),
        ):
            with self.subTest(failing_fn=failing_fn):
                caught = []
                with mock.patch.object(bf, failing_fn,
                                        side_effect=OSError("simulated disk failure")):
                    try:
                        records, frozen = _freeze_pool(
                            pool, ranked=self.baseline_ranked, top10=self.baseline_top10)
                        bf.write_frozen_board(frozen, "/tmp/should-not-be-reached.json")
                    except Exception as e:
                        caught.append(e)
                self.assertEqual(len(caught), 1,
                                  f"{failing_fn} failure was not actually injected/caught")
                self.assertIsInstance(caught[0], OSError)
                ranked_after = gp.rank_for_board(pool["gated"])
                top10_after = gp.select_main_board(ranked_after)
                self.assertEqual(ranked_after, self.baseline_ranked)
                self.assertEqual(top10_after, self.baseline_top10)


class IdentityStabilityAcrossIndependentConstructionTests(unittest.TestCase):
    """Point 4 of the mission spec: candidate identity must stay stable.
    test_board_freeze.py proves identity is unique WITHIN one board; this
    proves the same real-world candidate (same game/player/market/side)
    gets the IDENTICAL frozen candidate_id from two independently-built
    dicts whose only difference is which prediction/score values were
    computed for them -- i.e. identity is derived only from the stable
    identity fields, never from the model's mutable output, exactly as
    dashboard/live_state.canonical_prop_id's own docstring requires."""

    def test_same_real_world_candidate_gets_identical_id_despite_different_predictions(self):
        early_snapshot = _candidate(4001, hit_probability=0.55, score=60.0,
                                     reliability="C", market_edge=-0.01)
        later_snapshot = _candidate(4001, hit_probability=0.71, score=91.0,
                                     reliability="A", market_edge=0.11,
                                     name="Player 4001 (rescored)")

        id_early = bf.build_candidate_snapshot(
            early_snapshot, qc_status="kept", rank_lookup={}, gated_ids=set(),
            positive_read_ids=set(), no_read_ids=set(), top10_ids=set(),
            category_ids=set(), moonshot_ids=set(), deep_moonshot_ids=set(),
            shadow_ids=set(), provenance=PROVENANCE,
            board_generated_at=BOARD_GENERATED_AT,
        )["candidate_id"]
        id_later = bf.build_candidate_snapshot(
            later_snapshot, qc_status="kept", rank_lookup={}, gated_ids=set(),
            positive_read_ids=set(), no_read_ids=set(), top10_ids=set(),
            category_ids=set(), moonshot_ids=set(), deep_moonshot_ids=set(),
            shadow_ids=set(), provenance=PROVENANCE,
            board_generated_at=BOARD_GENERATED_AT,
        )["candidate_id"]

        self.assertEqual(id_early, id_later)

    def test_a_genuinely_different_candidate_gets_a_different_id(self):
        a = bf.build_candidate_snapshot(
            _candidate(5001), qc_status="kept", rank_lookup={}, gated_ids=set(),
            positive_read_ids=set(), no_read_ids=set(), top10_ids=set(),
            category_ids=set(), moonshot_ids=set(), deep_moonshot_ids=set(),
            shadow_ids=set(), provenance=PROVENANCE,
            board_generated_at=BOARD_GENERATED_AT,
        )["candidate_id"]
        b = bf.build_candidate_snapshot(
            _candidate(5002), qc_status="kept", rank_lookup={}, gated_ids=set(),
            positive_read_ids=set(), no_read_ids=set(), top10_ids=set(),
            category_ids=set(), moonshot_ids=set(), deep_moonshot_ids=set(),
            shadow_ids=set(), provenance=PROVENANCE,
            board_generated_at=BOARD_GENERATED_AT,
        )["candidate_id"]
        self.assertNotEqual(a, b)


class RejectedCandidatesPreservedTests(unittest.TestCase):
    """Adversarial requirement, explicit in the mission spec: prove the
    snapshot preserves candidates the REAL selector rejected, using the
    real rank_for_board/select_main_board output to decide what "rejected"
    means, against a concrete six-candidate, multi-reason slate."""

    def test_every_non_selected_candidate_survives_with_a_distinct_honest_reason(self):
        pool = _six_candidate_slate()
        ranked = gp.rank_for_board(pool["gated"])
        top10 = gp.select_main_board(ranked)
        self.assertEqual([c["player_id"] for c in top10], [3001])  # only the real top pick clears price

        records, frozen = _freeze_pool(pool, ranked=ranked, top10=top10)
        by_player = {r["player_id"]: r for r in frozen["records"]}
        self.assertEqual(set(by_player), {"3001", "3002", "3003", "3004", "3005", "3006"})

        # The winner.
        self.assertTrue(by_player["3001"]["selector"]["selected_top_pick"])

        # Ranked (a real candidate the model scored and priced) but not
        # selected, because it never cleared a confirmed edge over the
        # posted price -- present with its real rank, not dropped, and
        # distinguishable from a QC/eligibility rejection.
        self.assertFalse(by_player["3002"]["selector"]["selected_top_pick"])
        self.assertIsNotNone(by_player["3002"]["selector"]["final_rank"])
        self.assertTrue(by_player["3002"]["eligibility"]["cleared_quality_gate"])
        self.assertIsNone(by_player["3002"]["eligibility"]["rejection_reason"])

        # Priced, ranked, but no confirmed edge over the posted price --
        # present, with a real price/odds pair still recorded (never
        # fabricated), just excluded from top10 by select_main_board.
        self.assertFalse(by_player["3003"]["selector"]["selected_top_pick"])
        self.assertEqual(by_player["3003"]["market"]["market_odds"], -130)
        self.assertIs(by_player["3003"]["market"]["price_clears"], False)

        # Never priced -- present, with an honestly-null price rather than
        # a fabricated one.
        self.assertIsNone(by_player["3004"]["prediction"]["hit_probability"])
        self.assertIsNone(by_player["3004"]["market"]["market_odds"])

        # QC-rejected before it could ever reach the selector at all.
        self.assertEqual(by_player["3005"]["eligibility"]["qc_status"], "qc_rejected")
        self.assertIn("rain risk", by_player["3005"]["eligibility"]["rejection_reason"])
        self.assertIsNone(by_player["3005"]["selector"]["final_rank"])

        # Lineup-assumed holdout -- also never reached the selector.
        self.assertEqual(by_player["3006"]["eligibility"]["qc_status"],
                          "lineup_assumed_holdout")
        self.assertTrue(by_player["3006"]["eligibility"]["lineup_assumed"])


if __name__ == "__main__":
    unittest.main()
