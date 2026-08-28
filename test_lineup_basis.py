#!/usr/bin/env python3
"""Reconcile the lineup we ACTUALLY USED, not one inferred from props.

On HEAD 2ee82ed7 reconciliation rebuilt "the published lineup" from
payload["props"] and inferred provenance from those candidate rows. A prop
population is a strict subset of a batting order, so that view is blind to:

  * a starting hitter who generated no candidate at all
  * a scratch affecting a player with no prop
  * a batting-order-only change among players who all have props
  * a projected lineup becoming CONFIRMED with the identical nine
  * pitcher and game-level rows contaminating "was every row assumed?"

The last one matters most and is the least obvious. Projected -> confirmed
is a real state change even when nothing looks different, because
recommendation eligibility depends on whether a lineup is AUTHORITATIVE,
not on whether the projection happened to be right.

The fix is a dedicated snapshot captured in
generate_picks.build_lineup_basis at the moment fetch_lineups returns,
before scoring or filtering touches anything, carried through the payload
as `lineup_basis`.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard import reconcile as rc  # noqa: E402
from generate_picks import build_lineup_basis  # noqa: E402

NOW = datetime(2026, 8, 28, 21, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
NINE = [805801, 805802, 805803, 805804, 805805, 805806, 805807, 805808, 805809]


def game_meta(away=NINE, home=NINE, away_assumed=False, home_assumed=False, game_pk=99):
    def rows(ids, assumed):
        return [{"name": f"P{i}", "id": pid, "order": i,
                 **({"assumed": True} if assumed else {})}
                for i, pid in enumerate(ids, 1)]
    return [{"game_pk": game_pk, "matchup": "A @ H",
             "away_team": "Aways", "home_team": "Homes",
             "away_lineup": rows(away, away_assumed),
             "home_lineup": rows(home, home_assumed)}]


def payload(gm, props=None, minutes_old=5):
    return {"generated_at": (NOW - timedelta(minutes=minutes_old)).isoformat(),
            "lineup_basis": build_lineup_basis(gm, observed_at=STAMP),
            "props": props or []}


def confirmed(ids=NINE, side="away", game_pk=99):
    return {(game_pk, side): {i: pid for i, pid in enumerate(ids, 1)}}


class TestSnapshotShape(unittest.TestCase):
    def test_one_entry_per_game_per_side_with_the_required_fields(self):
        basis = build_lineup_basis(game_meta(), observed_at=STAMP)
        self.assertEqual(len(basis), 2)
        for e in basis:
            self.assertEqual(
                set(e), {"game_pk", "side", "team", "matchup", "slots",
                         "provenance", "observed_at", "source"})
            self.assertEqual([s["slot"] for s in e["slots"]], list(range(1, 10)))
            self.assertEqual(e["observed_at"], STAMP)
        self.assertEqual({e["side"] for e in basis}, {"away", "home"})

    def test_provenance_is_recorded_per_side(self):
        basis = build_lineup_basis(game_meta(away_assumed=True), observed_at=STAMP)
        by = {e["side"]: e["provenance"] for e in basis}
        self.assertEqual(by, {"away": "assumed", "home": "confirmed"})


class TestTheTwelveRequiredCases(unittest.TestCase):
    # 1
    def test_exact_full_nine_man_match_is_no_mismatch(self):
        self.assertEqual(rc.lineup_mismatches(payload(game_meta()), confirmed()), [])

    # 2
    def test_batting_order_only_swap_is_caught(self):
        swapped = [NINE[1], NINE[0]] + NINE[2:]
        out = rc.lineup_mismatches(payload(game_meta()), confirmed(swapped))
        self.assertEqual(len(out), 1)
        self.assertIn("differs", out[0]["detail"])

    # 3
    def test_late_scratch_replacement_is_caught(self):
        replaced = NINE[:4] + [999999] + NINE[5:]
        out = rc.lineup_mismatches(payload(game_meta()), confirmed(replaced))
        self.assertEqual(len(out), 1)

    # 4  -- the case candidate reconstruction could not see
    def test_change_to_a_hitter_with_no_generated_prop_is_caught(self):
        """Only slot 1 has a prop. The change is at slot 7."""
        props = [{"game_pk": 99, "player_id": NINE[0], "batting_order": 1,
                  "lineup_assumed": False}]
        changed = NINE[:6] + [777777] + NINE[7:]
        out = rc.lineup_mismatches(payload(game_meta(), props=props), confirmed(changed))
        self.assertEqual(len(out), 1, "a starter with no prop is still part of the lineup")

    # 5  -- the semantic case
    def test_projected_to_confirmed_with_identical_nine_is_still_a_mismatch(self):
        out = rc.lineup_mismatches(payload(game_meta(away_assumed=True)), confirmed())
        self.assertEqual(len(out), 1)
        self.assertIn("provenance", out[0]["detail"])
        self.assertEqual(out[0]["published_provenance"], "assumed")

    # 6
    def test_only_away_confirmed(self):
        gm = game_meta(away_assumed=True, home_assumed=True)
        out = rc.lineup_mismatches(payload(gm), confirmed(side="away"))
        self.assertEqual([o["side"] for o in out], ["away"])

    # 7
    def test_only_home_confirmed(self):
        gm = game_meta(away_assumed=True, home_assumed=True)
        out = rc.lineup_mismatches(payload(gm), confirmed(side="home"))
        self.assertEqual([o["side"] for o in out], ["home"])

    # 8
    def test_both_teams_confirmed_produces_two_independent_mismatches(self):
        gm = game_meta(away_assumed=True, home_assumed=True)
        both = {**confirmed(side="away"), **confirmed(side="home")}
        out = rc.lineup_mismatches(payload(gm), both)
        self.assertEqual(sorted(o["side"] for o in out), ["away", "home"])
        self.assertEqual(len({o["fingerprint"] for o in out}), 2)

    # 9
    def test_pitcher_and_game_level_rows_do_not_contaminate_lineup_state(self):
        """These rows carry no batting_order and previously polluted the
        'was every row assumed?' inference."""
        props = [
            {"game_pk": 99, "player_id": 111, "type": "pitcher", "lineup_assumed": True},
            {"game_pk": 99, "player_id": None, "combo_player_ids": None,
             "stat": "nrfi_combined", "lineup_assumed": True},
        ]
        self.assertEqual(
            rc.lineup_mismatches(payload(game_meta(), props=props), confirmed()), [],
            "non-batting rows must not make a confirmed lineup look wrong")

    # 10
    def test_no_authoritative_lineup_never_fabricates_a_mismatch(self):
        gm = game_meta(away_assumed=True, home_assumed=True)
        self.assertEqual(rc.lineup_mismatches(payload(gm), {}), [])

    def test_partial_authoritative_lineup_is_not_treated_as_posted(self):
        """Enforced at the fetch boundary: fewer than nine is a mid-populate
        scrape, not a lineup."""
        from dashboard.run_reconciliation import _confirmed_lineup

        class R:
            status_code = 200
            @staticmethod
            def json():
                return {"teams": {"away": {"battingOrder": [1, 2, 3],
                                           "players": {f"ID{i}": {"person": {"id": i}}
                                                       for i in (1, 2, 3)}},
                                  "home": {}}}
        self.assertIsNone(_confirmed_lineup(99, fetcher=lambda url: R()))

    # 11
    def test_a_later_revision_creates_a_distinct_fingerprint(self):
        gm = game_meta(away_assumed=True)
        first = rc.lineup_mismatches(payload(gm), confirmed())[0]["fingerprint"]
        revised = NINE[:8] + [888888]
        second = rc.lineup_mismatches(payload(gm), confirmed(revised))[0]["fingerprint"]
        self.assertNotEqual(first, second)

    # 12
    def test_a_rebuild_incorporating_the_lineup_is_what_clears_it(self):
        gm_before = game_meta(away_assumed=True)
        lineups = confirmed()
        before = rc.reconcile(payload(gm_before), confirmed_lineups=lineups, now=NOW)
        self.assertTrue(rc.needs_rebuild(before))

        # Asking does not clear it.
        rc.mark_rebuild_requested(before, at=NOW)
        still = rc.reconcile(payload(gm_before), confirmed_lineups=lineups,
                             now=NOW, prior=before)
        self.assertTrue(rc.needs_rebuild(still))

        # A rebuild that actually consumed the confirmed lineup does.
        after = rc.reconcile(payload(game_meta()), confirmed_lineups=lineups,
                             now=NOW, prior=still)
        self.assertFalse(rc.needs_rebuild(after))
        self.assertEqual(len(after["resolved_this_cycle"]), 1)


class TestIndependentOfCandidates(unittest.TestCase):
    def test_reconciliation_reads_lineup_basis_not_props(self):
        """Source-level: the candidate-reconstruction helpers are gone."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dashboard", "reconcile.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("_published_lineup", src)
        self.assertNotIn("_published_assumed", src)
        self.assertIn("lineup_basis", src)

    def test_a_payload_with_props_but_no_basis_reports_nothing(self):
        """Fails safe: without a basis there is no published lineup to be
        wrong, and inventing one from props is what this replaced."""
        p = {"generated_at": STAMP,
             "props": [{"game_pk": 99, "player_id": NINE[0], "batting_order": 1,
                        "lineup_assumed": True}]}
        self.assertEqual(rc.lineup_mismatches(p, confirmed()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
