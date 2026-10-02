"""Focused tests for the FC-MLB-002 minimum repair (Codex findings 1-4). Synthetic fixtures only."""
import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fc_mlb_002 as F  # noqa: E402

sys.path.insert(0, F.CHAMP)
import prop_probability as pp  # noqa: E402


def pa_rows(rows):
    base = dict(season=2026, game_date="2026-05-01", bat_team="NYY", fld_team="BOS", one=1, slot=1, opp_sp=500)
    return pd.DataFrame([{**base, **r} for r in rows])


class Settlement(unittest.TestCase):
    def test_switch_hitter_fragments_become_one_whole_game_row(self):
        # switch hitter 7: L side 0-for-2, R side 1-for-1 (a HR) -> ONE row, hits 1, TB 4, h1 = 1
        pa = pa_rows([dict(game_pk=1, batter=7, stand="L", p_throws="R", hit=0, tb=0),
                      dict(game_pk=1, batter=7, stand="L", p_throws="R", hit=0, tb=0),
                      dict(game_pk=1, batter=7, stand="R", p_throws="L", hit=1, tb=4),
                      dict(game_pk=1, batter=8, stand="R", p_throws="R", hit=0, tb=0, slot=2)])
        g = F.batter_games(pa)
        self.assertEqual(len(g), 2)
        r = g[g["batter"] == 7].iloc[0]
        self.assertEqual((r["hits"], r["tb"], r["n_pa"], r["h1"], r["h2"], r["tb2"]), (1, 4, 3, 1, 0, 1))
        self.assertFalse(g.duplicated(["game_pk", "batter"]).any())

    def test_two_hits_across_sides_settle_h2(self):
        pa = pa_rows([dict(game_pk=1, batter=7, stand="L", p_throws="R", hit=1, tb=1),
                      dict(game_pk=1, batter=7, stand="R", p_throws="L", hit=1, tb=2)])
        r = F.batter_games(pa).iloc[0]
        self.assertEqual((r["h1"], r["h2"], r["tb2"], r["tb"]), (1, 1, 1, 3))


class SwitchSide(unittest.TestCase):
    def test_pregame_side_from_batside_and_starter_hand(self):
        bg = pd.DataFrame([dict(game_pk=1, batter=7, opp_sp=500), dict(game_pk=1, batter=8, opp_sp=500),
                           dict(game_pk=2, batter=7, opp_sp=600), dict(game_pk=2, batter=9, opp_sp=600)])
        ok, excl = F.assign_matchup_side(bg, {7: "S", 8: "L"}, {500: "R", 600: "L"})
        side = {(r.game_pk, r.batter): (r.stand, r.switch) for r in ok.itertuples()}
        self.assertEqual(side[(1, 7)], ("L", 1))     # switch vs RHP bats L
        self.assertEqual(side[(2, 7)], ("R", 1))     # switch vs LHP bats R
        self.assertEqual(side[(1, 8)], ("L", 0))     # non-switch keeps his side
        self.assertEqual(len(excl), 1)               # batter 9: unknown batSide -> fail closed, counted
        self.assertEqual(int(excl["batter"].iat[0]), 9)

    def test_side_never_uses_realized_pa_hands(self):
        # assign_matchup_side takes no PA data at all; the signature is the guarantee
        import inspect
        self.assertEqual(list(inspect.signature(F.assign_matchup_side).parameters), ["bg", "bat_side", "p_hand"])


class KCounts(unittest.TestCase):
    def test_actual_selected_counts_and_uniqueness(self):
        rows = [dict(game_date="2026-05-01", game_pk=1, batter=i, h1=i % 2, s=float(i)) for i in range(25)]
        rows += [dict(game_date="2026-05-02", game_pk=2, batter=i, h1=1, s=float(i)) for i in range(12)]
        df = pd.DataFrame(rows)
        c = F.topk_counts(df, 20)
        self.assertEqual(c["selected_rows"], 20 + 12)
        self.assertEqual(c["dates_short"], 1)
        self.assertEqual(c["shortfalls"], {"2026-05-02": 8})
        sel = F.topk_select(df, "s", 20)
        self.assertEqual(len(sel), 32)
        self.assertFalse(sel.duplicated(["game_pk", "batter"]).any())

    def test_duplicate_player_game_population_is_refused(self):
        df = pd.DataFrame([dict(game_date="d", game_pk=1, batter=1, h1=1, s=1.0)] * 2)
        with self.assertRaises(ValueError):
            F.topk_select(df, "s", 5)


class MarketIdentity(unittest.TestCase):
    def setUp(self):
        # doubleheader: same teams twice on one date, distinct start times
        self.sched = pd.DataFrame([
            dict(game_pk=101, game_date_utc="2026-08-10T17:05:00Z", away="Detroit Tigers", home="Toronto Blue Jays", sched_date="2026-08-10"),
            dict(game_pk=102, game_date_utc="2026-08-10T23:07:00Z", away="Detroit Tigers", home="Toronto Blue Jays", sched_date="2026-08-10")])
        self.ev = pd.DataFrame([
            dict(game_date="2026-08-10", game_pk=101, batter=1, player_norm="riley greene", h1=1),
            dict(game_date="2026-08-10", game_pk=102, batter=1, player_norm="riley greene", h1=0),
            dict(game_date="2026-08-10", game_pk=101, batter=2, player_norm="will smith", h1=1),
            dict(game_date="2026-08-10", game_pk=101, batter=3, player_norm="will smith", h1=0)])
        mk = lambda eid, start, player, taken, am, game="Detroit Tigers (A B) @ Toronto Blue Jays (C D)": dict(  # noqa: E731
            game_date="2026-08-10", event_id=eid, game=game, player_norm=player, start_time=start, taken_at=taken, american=am)
        self.props = pd.DataFrame([
            mk(9001, "2026-08-10T17:05:00.000Z", "riley greene", "2026-08-10T15:00:00+00:00", -200),
            mk(9001, "2026-08-10T17:05:00.000Z", "riley greene", "2026-08-10T16:59:00+00:00", -210),   # last pregame
            mk(9001, "2026-08-10T17:05:00.000Z", "riley greene", "2026-08-10T17:30:00+00:00", -500),   # after first pitch
            mk(9002, "2026-08-10T23:07:00.000Z", "riley greene", "2026-08-10T20:00:00+00:00", -180),
            mk(9001, "2026-08-10T17:05:00.000Z", "will smith", "2026-08-10T16:00:00+00:00", -150),      # ambiguous name
            mk(9001, "2026-08-10T17:05:00.000Z", "nobody here", "2026-08-10T16:00:00+00:00", -150),     # unmatched
            mk(9003, "2026-08-10T19:00:00.000Z", "x", "2026-08-10T16:00:00+00:00", -150, game="Foo (A) @ Bar (B)")])

    def test_doubleheader_mapped_by_event_not_dropped(self):
        m, ident = F.match_market(self.props, self.sched, self.ev, pp)
        got = {(r.game_pk, r.batter): r.american for r in m.itertuples()}
        self.assertEqual(got, {(101, 1): -210, (102, 1): -180})     # both DH games, last strictly-pregame price
        self.assertEqual(ident["events_matched"], 2)
        self.assertEqual(ident["events_unmatched"], 1)
        self.assertEqual(ident["player_ambiguous_name_in_game"], 1)
        self.assertEqual(ident["player_unmatched_in_eligible_population"], 1)
        self.assertEqual(ident["doubleheader_player_games_matched"], 2)
        self.assertEqual(ident["doubleheader_exclusions"], 0)

    def test_no_price_at_or_after_first_pitch(self):
        m, _ = F.match_market(self.props, self.sched, self.ev, pp)
        self.assertTrue((pd.to_datetime(m["taken_at"], utc=True) < pd.Timestamp("2026-08-10T17:05:00Z")).any())
        self.assertNotIn(-500, set(m["american"]))


class Manifest(unittest.TestCase):
    def _setup(self, tmp):
        d = os.path.join(tmp, "statcast"); os.makedirs(d)
        man = os.path.join(tmp, "MANIFEST.jsonl")
        with open(man, "w") as fh:
            for day, body in (("2026-05-01", b"a,b\n1,2\n"), ("2026-05-02", b"a,b\n3,4\n")):
                with gzip.open(os.path.join(d, f"{day}.csv.gz"), "wb") as g:
                    g.write(body)
                fh.write(json.dumps({"date": day, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}) + "\n")
        return d, man

    def test_verified_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, man = self._setup(tmp)
            self.assertEqual(sorted(F.verify_raw_files(d, man)), ["2026-05-01", "2026-05-02"])

    def test_tampered_extra_missing_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, man = self._setup(tmp)
            with gzip.open(os.path.join(d, "2026-05-01.csv.gz"), "wb") as g:
                g.write(b"a,b\n9,9\n")
            with self.assertRaises(F.ManifestError):
                F.verify_raw_files(d, man)
        with tempfile.TemporaryDirectory() as tmp:
            d, man = self._setup(tmp)
            with gzip.open(os.path.join(d, "2026-05-03.csv.gz"), "wb") as g:
                g.write(b"x")
            with self.assertRaises(F.ManifestError):
                F.verify_raw_files(d, man)
        with tempfile.TemporaryDirectory() as tmp:
            d, man = self._setup(tmp)
            os.remove(os.path.join(d, "2026-05-02.csv.gz"))
            with self.assertRaises(F.ManifestError):
                F.verify_raw_files(d, man)


class ZeroMatch(unittest.TestCase):
    """Zero-match inputs must return an empty result with valid exclusion counts, never raise (Codex PASS-WITH-LIMITATIONS note)."""
    def _fx(self):
        f = MarketIdentity(); f.setUp(); return f

    def _check_empty(self, m, ident):
        self.assertEqual(len(m), 0)
        self.assertEqual(ident["matched_player_games"], 0)
        for k_ in ("events", "events_matched", "events_ambiguous", "events_unmatched", "player_prices_in_matched_events",
                   "player_unmatched_in_eligible_population", "player_ambiguous_name_in_game",
                   "doubleheader_player_games_matched", "doubleheader_exclusions"):
            self.assertIn(k_, ident)

    def test_no_events(self):
        f = self._fx()
        m, ident = F.match_market(f.props.iloc[0:0], f.sched, f.ev, pp)
        self.assertEqual(len(m), 0)
        self.assertEqual(ident["props_rows"], 0)

    def test_all_events_unmatched(self):
        f = self._fx()
        props = f.props.assign(game="Foo (A) @ Bar (B)")
        m, ident = F.match_market(props, f.sched, f.ev, pp)
        self._check_empty(m, ident)
        self.assertEqual(ident["events_matched"], 0)
        self.assertEqual(ident["events_unmatched"], ident["events"])

    def test_all_events_ambiguous(self):
        f = self._fx()
        sched = f.sched.assign(game_date_utc="2026-08-10T17:05:00Z")            # two games, same teams, same time
        props = f.props[f.props["event_id"] == 9001]
        m, ident = F.match_market(props, sched, f.ev, pp)
        self._check_empty(m, ident)
        self.assertEqual(ident["events_ambiguous"], 1)

    def test_all_rows_filtered_by_time(self):
        f = self._fx()
        props = f.props[f.props["event_id"] == 9001].assign(taken_at="2026-08-10T18:00:00+00:00")
        m, ident = F.match_market(props, f.sched, f.ev, pp)
        self._check_empty(m, ident)
        self.assertEqual(ident["events_matched"], 1)
        self.assertEqual(ident["player_prices_in_matched_events"], 0)

    def test_valid_mixed_input_unchanged(self):
        f = self._fx()
        m, ident = F.match_market(f.props, f.sched, f.ev, pp)
        self.assertEqual({(r.game_pk, r.batter): r.american for r in m.itertuples()}, {(101, 1): -210, (102, 1): -180})
        self.assertEqual((ident["events_matched"], ident["events_unmatched"], ident["player_ambiguous_name_in_game"],
                          ident["player_unmatched_in_eligible_population"], ident["matched_player_games"]), (2, 1, 1, 1, 2))


if __name__ == "__main__":
    unittest.main()
