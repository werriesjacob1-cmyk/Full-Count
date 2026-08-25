#!/usr/bin/env python3
"""test_event_targeted_observer.py -- coverage for the pure trigger-detection
and FanDuel-diff logic in backtest/event_targeted_observer.py (the
2026-08-25 redesign of the FanDuel live-edge experiment after two passive
observer runs produced zero confirmed repricing). No network calls -- this
tests the decision logic only.

    /tmp/mlbvenv/bin/python3 test_event_targeted_observer.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import event_targeted_observer as eto


def mlb(inning=1, half="Top", outs=0, away=0, home=0, batter="Batter A", pitcher="Pitcher A"):
    return {"inning": inning, "half": half, "outs": outs, "away_score": away,
            "home_score": home, "batter": batter, "pitcher": pitcher,
            "abstract_state": "Live"}


class TriggerDetectionTests(unittest.TestCase):
    def test_no_prior_state_means_no_triggers(self):
        self.assertEqual(eto.detect_triggers(None, mlb()), [])

    def test_identical_state_produces_no_triggers(self):
        state = mlb()
        self.assertEqual(eto.detect_triggers(dict(state), dict(state)), [])

    def test_pitcher_change_detected(self):
        prev, cur = mlb(pitcher="Old Arm"), mlb(pitcher="New Arm")
        triggers = eto.detect_triggers(prev, cur)
        kinds = [t[0] for t in triggers]
        self.assertIn("pitcher_change", kinds)

    def test_scoring_play_detected_either_team(self):
        prev, cur = mlb(away=0, home=0), mlb(away=1, home=0)
        self.assertIn("scoring_play", [t[0] for t in eto.detect_triggers(prev, cur)])
        prev, cur = mlb(away=2, home=1), mlb(away=2, home=2)
        self.assertIn("scoring_play", [t[0] for t in eto.detect_triggers(prev, cur)])

    def test_scoring_play_not_falsely_triggered_by_unknown_scores(self):
        # A missing score (None) must never be compared as if it were a
        # real 0 -- that would fabricate a "scoring play" out of an
        # honestly-unknown state.
        prev = mlb(away=None, home=None)
        cur = mlb(away=0, home=0)
        self.assertNotIn("scoring_play", [t[0] for t in eto.detect_triggers(prev, cur)])

    def test_inning_transition_detected(self):
        prev, cur = mlb(inning=3, half="Bottom"), mlb(inning=4, half="Top")
        self.assertIn("inning_transition", [t[0] for t in eto.detect_triggers(prev, cur)])

    def test_half_inning_flip_alone_is_a_transition(self):
        prev, cur = mlb(inning=3, half="Top"), mlb(inning=3, half="Bottom")
        self.assertIn("inning_transition", [t[0] for t in eto.detect_triggers(prev, cur)])

    def test_batter_change_detected(self):
        prev, cur = mlb(batter="Player A"), mlb(batter="Player B")
        self.assertIn("batter_change", [t[0] for t in eto.detect_triggers(prev, cur)])

    def test_multiple_simultaneous_triggers_all_reported(self):
        # A real half-inning change often carries a pitcher AND batter
        # change too -- all three must be reported, not just the first
        # match found.
        prev = mlb(inning=1, half="Top", batter="A", pitcher="X")
        cur = mlb(inning=1, half="Bottom", batter="B", pitcher="Y")
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertEqual(kinds, {"inning_transition", "batter_change", "pitcher_change"})


def market(status="OPEN", runners=None):
    return {"name": "Test Market", "type": "moneyline", "inPlay": True,
           "status": status, "runners": runners or {}}


def runner(odds=-110, previous_odds=None, handicap=None, status="ACTIVE", name="Runner A"):
    return {"name": name, "odds": odds, "previous_odds": previous_odds,
           "handicap": handicap, "status": status}


class FanduelDiffTests(unittest.TestCase):
    def test_no_prior_state_means_no_changes(self):
        cur = {"m1": market(runners={"r1": runner()})}
        self.assertEqual(eto.diff_fanduel(None, cur), [])

    def test_a_brand_new_market_is_not_reported_as_a_change(self):
        prev = {}
        cur = {"m1": market(runners={"r1": runner()})}
        self.assertEqual(eto.diff_fanduel(prev, cur), [])

    def test_real_odds_change_captured_with_full_detail(self):
        prev = {"m1": market(runners={"r1": runner(odds=-110)})}
        cur = {"m1": market(runners={"r1": runner(odds=-130, previous_odds=-110)})}
        changes = eto.diff_fanduel(prev, cur)
        odds_changes = [c for c in changes if c["kind"] == "odds_change"]
        self.assertEqual(len(odds_changes), 1)
        c = odds_changes[0]
        self.assertEqual(c["old_odds"], -110)
        self.assertEqual(c["new_odds"], -130)
        self.assertEqual(c["previous_win_runner_odds_field"], -110)
        self.assertTrue(c["previous_win_runner_odds_matches_last_observed"])

    def test_previous_win_runner_odds_mismatch_is_reported_not_hidden(self):
        # If FanDuel's own previousWinRunnerOdds field ever disagrees with
        # what we last observed, that's a real, reportable data-quality
        # finding -- must not be silently assumed to match.
        prev = {"m1": market(runners={"r1": runner(odds=-110)})}
        cur = {"m1": market(runners={"r1": runner(odds=-130, previous_odds=-999)})}
        c = [c for c in eto.diff_fanduel(prev, cur) if c["kind"] == "odds_change"][0]
        self.assertFalse(c["previous_win_runner_odds_matches_last_observed"])

    def test_line_change_reported_separately_from_odds_change(self):
        prev = {"m1": market(runners={"r1": runner(odds=-110, handicap=1.5)})}
        cur = {"m1": market(runners={"r1": runner(odds=-110, handicap=2.5)})}
        changes = eto.diff_fanduel(prev, cur)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["kind"], "line_change")
        self.assertEqual(changes[0]["old_line"], 1.5)
        self.assertEqual(changes[0]["new_line"], 2.5)

    def test_status_change_captured(self):
        prev = {"m1": market(status="OPEN")}
        cur = {"m1": market(status="SUSPENDED")}
        changes = eto.diff_fanduel(prev, cur)
        status_changes = [c for c in changes if c["kind"] == "status_change"]
        self.assertEqual(len(status_changes), 1)
        self.assertEqual(status_changes[0]["from"], "OPEN")
        self.assertEqual(status_changes[0]["to"], "SUSPENDED")

    def test_unchanged_state_produces_no_changes(self):
        state = {"m1": market(runners={"r1": runner(odds=-110, handicap=1.5)})}
        self.assertEqual(eto.diff_fanduel(dict(state), dict(state)), [])

    def test_a_removed_market_id_is_silently_skipped_not_a_crash(self):
        # A market present in prev but absent from cur (e.g. genuinely
        # removed) must not raise -- migration/removal handling is a
        # separate, deliberately out-of-scope concern for this pure diff
        # (see fanduel_live_observer.py for that logic); this just must
        # not crash on it.
        prev = {"m1": market(runners={"r1": runner()}), "m2": market()}
        cur = {"m1": market(runners={"r1": runner()})}
        changes = eto.diff_fanduel(prev, cur)  # must not raise
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
