#!/usr/bin/env python3
"""test_alive_brain_state.py -- coverage for backtest/alive_brain_prototype.
fetch_mlb_state(). 2026-08-25: on_1b was a real, silent bug --
`bool((current.get("runners") or [{}]))` is unconditionally True regardless
of actual baserunner state (a falsy `runners` falls back to the non-empty
placeholder `[{}]`, itself truthy). Fixed to read the real per-base
occupancy from `linescore.offense.first/second/third`, and extended with
`battingOrder` and the current play's real `eventType`/`event` -- all
needed for the event-targeted FanDuel observer's trigger-priority upgrade
(real bases-loaded / home-run / lineup-turnover detection instead of
inferring from score deltas).

    /tmp/mlbvenv/bin/python3 test_alive_brain_state.py
"""
import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import alive_brain_prototype as abp


def feed(offense=None, event_type=None, event=None, inning=5, half="Top", outs=1,
         away=2, home=1, batter="Batter A", pitcher="Pitcher A", abstract="Live"):
    return {
        "liveData": {
            "linescore": {
                "currentInning": inning, "inningState": half, "outs": outs,
                "teams": {"away": {"runs": away}, "home": {"runs": home}},
                "offense": offense or {},
            },
            "plays": {
                "currentPlay": {
                    "matchup": {"batter": {"fullName": batter}, "pitcher": {"fullName": pitcher}},
                    "result": {"eventType": event_type, "event": event},
                },
            },
        },
        "gameData": {"status": {"abstractGameState": abstract}},
    }


class FetchMlbStateTests(unittest.TestCase):
    def _fetch(self, payload):
        resp = mock.Mock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with mock.patch.object(abp.requests, "get", return_value=resp):
            state, elapsed = abp.fetch_mlb_state(123)
        return state

    def test_bases_empty_reports_no_runners(self):
        state = self._fetch(feed(offense={}))
        self.assertFalse(state["on_1b"])
        self.assertFalse(state["on_2b"])
        self.assertFalse(state["on_3b"])

    def test_runner_on_first_only_is_reported_accurately(self):
        state = self._fetch(feed(offense={"first": {"id": 1, "fullName": "X"}}))
        self.assertTrue(state["on_1b"])
        self.assertFalse(state["on_2b"])
        self.assertFalse(state["on_3b"])

    def test_bases_loaded_reports_all_three(self):
        state = self._fetch(feed(offense={
            "first": {"id": 1}, "second": {"id": 2}, "third": {"id": 3},
        }))
        self.assertTrue(state["on_1b"])
        self.assertTrue(state["on_2b"])
        self.assertTrue(state["on_3b"])

    def test_batting_order_and_event_type_surfaced(self):
        state = self._fetch(feed(
            offense={"battingOrder": 4}, event_type="home_run", event="Home Run",
        ))
        self.assertEqual(state["batting_order"], 4)
        self.assertEqual(state["last_event_type"], "home_run")
        self.assertEqual(state["last_event"], "Home Run")

    def test_missing_offense_block_does_not_crash(self):
        payload = feed()
        del payload["liveData"]["linescore"]["offense"]
        state = self._fetch(payload)
        self.assertFalse(state["on_1b"])
        self.assertIsNone(state["batting_order"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
