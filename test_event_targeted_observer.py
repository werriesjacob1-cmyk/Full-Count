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


def mlb(inning=1, half="Top", outs=0, away=0, home=0, batter="Batter A", pitcher="Pitcher A",
        on_1b=None, on_2b=None, on_3b=None, batting_order=None,
        last_event_type=None, last_event=None):
    return {"inning": inning, "half": half, "outs": outs, "away_score": away,
            "home_score": home, "batter": batter, "pitcher": pitcher,
            "on_1b": on_1b, "on_2b": on_2b, "on_3b": on_3b,
            "batting_order": batting_order,
            "last_event_type": last_event_type, "last_event": last_event,
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

    def test_real_home_run_detected_via_mlb_event_type_not_score_delta(self):
        # 2026-08-25 upgrade: a real home run is detected from MLB's own
        # result.eventType (via fetch_mlb_state), not inferred from a score
        # jump -- a solo HR is only a 1-run delta, which the old score-only
        # heuristic could never distinguish from a bases-empty single that
        # somehow scored a run on an error.
        prev = mlb(away=2, home=1, batter="A", last_event_type=None)
        cur = mlb(away=3, home=1, batter="A", last_event_type="home_run", last_event="Home Run")
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("home_run", kinds)

    def test_home_run_not_re_triggered_every_poll_while_still_current_play(self):
        prev = mlb(batter="A", last_event_type="home_run", last_event="Home Run")
        cur = mlb(batter="A", last_event_type="home_run", last_event="Home Run")
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertNotIn("home_run", kinds)

    def test_multi_run_scoring_play_distinguished_from_single_run(self):
        prev = mlb(away=1, home=1)
        cur = mlb(away=1, home=4)  # +3 in one poll
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("multi_run_scoring_play", kinds)
        self.assertNotIn("scoring_play", kinds)

    def test_single_run_stays_plain_scoring_play(self):
        prev = mlb(away=1, home=1)
        cur = mlb(away=1, home=2)
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("scoring_play", kinds)
        self.assertNotIn("multi_run_scoring_play", kinds)

    def test_bases_loaded_detected_from_real_occupancy(self):
        prev = mlb(on_1b=True, on_2b=False, on_3b=False)
        cur = mlb(on_1b=True, on_2b=True, on_3b=True)
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("bases_loaded", kinds)

    def test_bases_loaded_not_triggered_when_unknown(self):
        # Old/incomplete state (on_Nb fields absent -> None) must never be
        # treated as "loaded" -- an honestly-unknown base state is not a
        # leverage event.
        prev = mlb()
        cur = mlb()
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertNotIn("bases_loaded", kinds)

    def test_bases_loaded_only_fires_on_the_transition_not_every_poll(self):
        prev = mlb(on_1b=True, on_2b=True, on_3b=True)
        cur = mlb(on_1b=True, on_2b=True, on_3b=True)
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertNotIn("bases_loaded", kinds)

    def test_batting_order_turnover_detected_on_wraparound(self):
        prev = mlb(batter="I", batting_order=9)
        cur = mlb(batter="J", batting_order=1)
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("batting_order_turnover", kinds)
        self.assertNotIn("batter_change", kinds)  # turnover supersedes the generic kind

    def test_normal_batting_order_increase_is_a_plain_batter_change(self):
        prev = mlb(batter="A", batting_order=3)
        cur = mlb(batter="B", batting_order=4)
        kinds = {t[0] for t in eto.detect_triggers(prev, cur)}
        self.assertIn("batter_change", kinds)
        self.assertNotIn("batting_order_turnover", kinds)


class BurstPlanTests(unittest.TestCase):
    def test_no_triggers_uses_the_default_plan(self):
        self.assertEqual(eto.burst_plan_for([]), (eto.BURST_INTERVAL_S, eto.BURST_POLLS))

    def test_pitcher_change_bursts_at_the_hardest_tier(self):
        plan = eto.burst_plan_for([("pitcher_change", "x -> y")])
        self.assertEqual(plan, eto.TIER_BURST_PLAN[1])

    def test_routine_batter_change_bursts_at_the_lightest_tier(self):
        plan = eto.burst_plan_for([("batter_change", "x -> y")])
        self.assertEqual(plan, eto.TIER_BURST_PLAN[5])

    def test_simultaneous_triggers_use_the_more_aggressive_tier(self):
        # A batter change alongside a pitcher change must burst as hard as
        # the pitcher change alone -- the lower-priority trigger must never
        # dilute the response to the higher-priority one.
        plan = eto.burst_plan_for([("batter_change", "x"), ("pitcher_change", "y")])
        self.assertEqual(plan, eto.TIER_BURST_PLAN[1])

    def test_every_trigger_kind_has_an_assigned_tier(self):
        for kind in eto.TRIGGER_KINDS:
            self.assertIn(kind, eto.TRIGGER_TIERS, f"{kind} has no tier assigned")


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
