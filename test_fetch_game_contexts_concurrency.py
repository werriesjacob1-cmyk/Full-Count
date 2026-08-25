#!/usr/bin/env python3
"""test_fetch_game_contexts_concurrency.py -- coverage for the 2026-08-25
fix to grade_results.fetch_game_contexts(): the per-game MLB feed fetch
loop was sequential, so real MLB Stats API slowness on 2026-08-24 (~15
hours degraded) turned into a multi-minute sum across every distinct game
in the active/recent population, blowing dashboard-live.yml's timeout
budget (see run #260/#252's own job-step timing: "Grade published Top
Picks" and "Reprice pregame candidates" each took 4-8 minutes instead of
low single-digit seconds). This proves the fetch is now genuinely
concurrent (bounded wall time, not summed) AND that its return shape is
byte-for-byte identical to the old sequential version for the same inputs
-- this is a pure orchestration change, not a data-shape change.

    /tmp/mlbvenv/bin/python3 test_fetch_game_contexts_concurrency.py
"""
import sys
import time
import unittest
import unittest.mock as mock

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import grade_results as gr


def _feed(game_pk, state="In Progress", coded="I"):
    return {"gameData": {"status": {"codedGameState": coded, "detailedState": state}}}


class ReturnShapeTests(unittest.TestCase):
    def setUp(self):
        gr._GAME_FEED_CACHE.clear()

    def test_same_output_shape_as_the_old_sequential_version(self):
        feeds = {1: _feed(1), 2: _feed(2), 3: None}  # 3 simulates a fetch failure
        with mock.patch.object(gr, "fetch_game_feed", side_effect=lambda pk, refresh=False: feeds.get(pk)):
            contexts = gr.fetch_game_contexts([1, 2, 3], refresh=True)
        self.assertEqual(set(contexts), {1, 2})
        self.assertEqual(contexts[1]["status"], feeds[1]["gameData"]["status"])
        self.assertEqual(contexts[1]["feed"], feeds[1])
        self.assertNotIn(3, contexts)  # a failed fetch stays absent, never fabricated

    def test_duplicate_and_none_game_pks_are_deduplicated_and_filtered(self):
        calls = []
        with mock.patch.object(gr, "fetch_game_feed",
                                side_effect=lambda pk, refresh=False: calls.append(pk) or _feed(pk)):
            contexts = gr.fetch_game_contexts([5, 5, None, "5", 6], refresh=True)
        self.assertEqual(sorted(set(calls)), [5, 6])  # "5" and 5 collapse to one real fetch
        self.assertEqual(set(contexts), {5, 6})

    def test_invalid_game_pk_values_are_skipped_not_crashed_on(self):
        with mock.patch.object(gr, "fetch_game_feed", side_effect=lambda pk, refresh=False: _feed(pk)):
            contexts = gr.fetch_game_contexts(["not-a-number", None, 9], refresh=True)
        self.assertEqual(set(contexts), {9})

    def test_empty_input_returns_empty_dict_without_error(self):
        with mock.patch.object(gr, "fetch_game_feed") as fetch:
            contexts = gr.fetch_game_contexts([], refresh=True)
        self.assertEqual(contexts, {})
        fetch.assert_not_called()


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        gr._GAME_FEED_CACHE.clear()

    def test_wall_time_is_bounded_by_the_slowest_call_not_the_sum(self):
        # Five "games", each simulating a 0.2s-latency MLB feed fetch --
        # sequential would take >=1.0s; concurrent should take well under
        # that (bounded by ~one call plus scheduling overhead).
        per_call_latency = 0.2
        n_games = 5

        def slow_fetch(pk, refresh=False):
            time.sleep(per_call_latency)
            return _feed(pk)

        with mock.patch.object(gr, "fetch_game_feed", side_effect=slow_fetch):
            start = time.monotonic()
            contexts = gr.fetch_game_contexts(list(range(1, n_games + 1)), refresh=True)
            elapsed = time.monotonic() - start

        self.assertEqual(len(contexts), n_games)
        sequential_floor = per_call_latency * n_games
        self.assertLess(
            elapsed, sequential_floor * 0.7,
            f"fetch_game_contexts took {elapsed:.3f}s for {n_games} games at "
            f"{per_call_latency}s each ({sequential_floor:.3f}s if sequential) -- "
            "this should be running concurrently, not summing latencies.",
        )

    def test_a_single_slow_game_does_not_block_the_others_from_completing(self):
        def fetch(pk, refresh=False):
            if pk == 1:
                time.sleep(0.3)
            return _feed(pk)

        with mock.patch.object(gr, "fetch_game_feed", side_effect=fetch):
            start = time.monotonic()
            contexts = gr.fetch_game_contexts([1, 2, 3, 4], refresh=True)
            elapsed = time.monotonic() - start

        self.assertEqual(set(contexts), {1, 2, 3, 4})
        # Bounded by the one slow game (~0.3s), not 0.3s + three more calls.
        self.assertLess(elapsed, 0.55)


if __name__ == "__main__":
    unittest.main(verbosity=2)
