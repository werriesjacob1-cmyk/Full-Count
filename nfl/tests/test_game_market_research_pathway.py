"""FC-NFL-004 adversarial tests: distribution -> pricing -> freeze -> grade."""
import math
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from nfl.prospective import game_market_research_freeze as freeze_mod
from nfl.prospective.espn_final_outcome import extract_espn_final_outcome
from nfl.prospective.game_identity import GameIdentityError, bind_nflverse_game_identity
from nfl.prospective.game_market_pricing import (
    GameMarketPricingError,
    american_to_decimal,
    evaluate_price,
    implied_probability,
    lean_decision,
    no_vig_pair,
)
from nfl.prospective.game_market_research_freeze import (
    GameMarketFreezeError,
    build_research_freeze,
    qb_state_from_schedule,
    verify_research_freeze,
    write_research_freeze,
)
from nfl.prospective.game_market_research_grade import (
    GameMarketResearchGradeError,
    grade_research_freeze,
    write_grade,
)
from nfl.prospective.game_market_snapshot import seal_game_market_snapshot
from nfl.research.game_market_models import centers, distribution, load_params, market_center
from nfl.research.game_score_distribution import (
    MARGIN_SUPPORT,
    GameDistribution,
    conditional_win,
    discrete_pmf,
    solve_center,
)

EVENT = "35601246"
CAPTURED = "2026-09-20T16:00:00Z"
KICKOFF = "2026-09-20T17:00:00+00:00"
FD_MARKET_TIME = "2026-09-20T17:01:00Z"  # FanDuel trails kickoff by 1 minute
SEALED = "2026-09-20T16:01:00Z"
FROZEN = "2026-09-20T16:02:00Z"
SHA = "a" * 64
SCHED_SHA = "c" * 64
ALIASES = {"Miami Dolphins": "MIA", "Buffalo Bills": "BUF"}
PARAMS = load_params()


def _record(market, **over):
    base = {
        "source": "fanduel_nfl", "sportsbook": "FANDUEL", "sport": "NFL", "event_id": EVENT,
        "event_name": "Miami Dolphins @ Buffalo Bills", "event_open_date": FD_MARKET_TIME,
        "market_id": f"{market}-1", "market_name": market, "market_type": "TYPE",
        "market_time": FD_MARKET_TIME, "market_status": "OPEN", "in_play": False,
        "captured_at": CAPTURED, "source_payload_sha256": SHA, "source_artifact": "fixture",
        "source_url": "https://example.test", "market": market,
        "away_team": "Miami Dolphins", "home_team": "Buffalo Bills",
    }
    if market == "spread":
        base.update({"away_line": 3.0, "home_line": -3.0, "away_odds": -110, "home_odds": -110,
                     "away_selection_id": "a-spread", "home_selection_id": "h-spread"})
    elif market == "game_total":
        base.update({"line": 45.0, "over_odds": -105, "under_odds": -115,
                     "over_selection_id": "over-total", "under_selection_id": "under-total"})
    else:
        base.update({"away_odds": 130, "home_odds": -150,
                     "away_selection_id": "a-ml", "home_selection_id": "h-ml"})
    base.update(over)
    return base


def _snapshot(records=None, sealed_at=SEALED, captured=CAPTURED):
    rows = records if records is not None else [
        _record(m, captured_at=captured) for m in ("moneyline", "spread", "game_total")]
    present = {r["market"] for r in rows}
    failures = [{"event_id": EVENT, "market": m, "reason": "TEST_MISSING"}
                for m in sorted({"moneyline", "spread", "game_total"} - present)]
    return seal_game_market_snapshot(rows, failures, event_id=EVENT, sealed_at=sealed_at)


SCHEDULE = [
    {"game_id": "2026_02_BUF_NYJ", "season": "2026", "game_type": "REG", "week": "2",
     "gameday": "2026-09-13", "gametime": "13:00", "away_team": "BUF", "home_team": "NYJ",
     "away_score": "20", "home_score": "17", "result": "-3", "total": "37",
     "away_qb_id": "QB-BUF", "home_qb_id": "QB-NYJ"},
    {"game_id": "2026_02_MIA_NE", "season": "2026", "game_type": "REG", "week": "2",
     "gameday": "2026-09-13", "gametime": "13:00", "away_team": "MIA", "home_team": "NE",
     "away_score": "10", "home_score": "24", "result": "14", "total": "34",
     "away_qb_id": "QB-MIA", "home_qb_id": "QB-NE"},
    {"game_id": "2026_03_MIA_BUF", "season": "2026", "game_type": "REG", "week": "3",
     "gameday": "2026-09-20", "gametime": "13:00", "away_team": "MIA", "home_team": "BUF",
     "away_score": "", "home_score": "", "result": "", "total": "", "espn": "401",
     "gsis": "", "away_qb_id": "QB-MIA", "home_qb_id": "QB-BUF"},
]


def _binding(snapshot, tolerance=2, schedule=SCHEDULE):
    return bind_nflverse_game_identity(
        snapshot, schedule, team_aliases=ALIASES, source_url="https://nflverse.test/games.csv",
        source_sha256=SCHED_SHA, observed_at="2026-09-20T15:59:00Z",
        kickoff_tolerance_minutes=tolerance)


def _b0(**over):
    row = {"game_id": "2026_03_MIA_BUF", "home_team": "BUF", "away_team": "MIA",
           "eligibility": "ELIGIBLE", "predicted_home_margin": 1.0, "predicted_total": 44.0,
           "baseline_name": "GAME_MARKET_B0_PRIOR_SCORING_BLEND"}
    row.update(over)
    return row


def _freeze(snapshot=None, binding="auto", b0=None, qb=None, frozen_at=FROZEN, code_sha="deadbeef"):
    snap = snapshot or _snapshot()
    bind = _binding(snap) if binding == "auto" else binding
    return build_research_freeze(
        market_snapshot=snap, identity_binding=bind, b0_prediction=b0 or _b0(),
        qb_state=qb if qb is not None else qb_state_from_schedule(SCHEDULE, "2026_03_MIA_BUF"),
        params=PARAMS, code_sha=code_sha,
        schedule_source={"url": "https://nflverse.test/games.csv", "sha256": SCHED_SHA},
        frozen_at=frozen_at)


def _sel(record, market, side):
    return next(s for s in record["selections"]
                if s["market"] == market and (s.get("grading_identity") or {}).get("side") == side)


def _espn(home=27, away=24, status="STATUS_FINAL", completed=True, date="2026-09-20T17:00Z"):
    return {"id": "401", "date": date, "status": {"type": {"completed": completed, "name": status}},
            "competitions": [{"id": "401", "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "BUF"}, "score": str(home)},
                {"homeAway": "away", "team": {"abbreviation": "MIA"}, "score": str(away)}]}]}


def _outcome(binding, **kw):
    return extract_espn_final_outcome(binding, _espn(**kw), source_url="https://espn.test",
                                      payload_sha256="d" * 64, observed_at="2026-09-20T21:00:00Z")


class DistributionTests(unittest.TestCase):
    def test_pmf_normalised_and_sides_complementary(self):
        d = GameDistribution.build(margin_center=2.5, total_center=44.0, margin_sigma=13.4,
                                   total_sigma=13.5, margin_weights=PARAMS.margin_weights,
                                   total_weights=PARAMS.total_weights)
        self.assertAlmostEqual(sum(d.margin_pmf.values()), 1.0, places=12)
        for line in (-3.0, -2.5, 7.0):
            hw, hp, hl = d.spread("HOME", line)
            aw, ap, al = d.spread("AWAY", -line)
            self.assertAlmostEqual(hw, al, places=12)
            self.assertAlmostEqual(hp, ap, places=12)
        self.assertEqual(d.spread("HOME", -2.5)[1], 0.0)  # half-point lines never push
        self.assertGreater(d.spread("HOME", -3.0)[1], 0.05)  # key number 3 carries push mass
        o, u = d.total("OVER", 44.0), d.total("UNDER", 44.0)
        self.assertAlmostEqual(o[0], u[2], places=12)

    def test_market_prior_invariant_line_is_fifty_fifty(self):
        # Deviation D1: with key weights, M0 must be solved so its own line is 50/50.
        for line in (-2.5, 3.0, 6.5, 0.0):
            c = market_center(PARAMS, "margin", line)
            d = distribution("M0", PARAMS, c, None)
            self.assertAlmostEqual(conditional_win(d.spread("HOME", -line)), 0.5, places=6)
        c = solve_center("total", 44.5, PARAMS.sigma["M0"]["total"], PARAMS.total_weights)
        d = distribution("M0", PARAMS, None, c)
        self.assertAlmostEqual(conditional_win(d.total("OVER", 44.5)), 0.5, places=6)

    def test_fair_spread_and_moneyline_coherent(self):
        d = distribution("F1", PARAMS, 7.0, 45.0)
        self.assertLess(d.fair_home_spread(), 0)
        self.assertGreater(d.fair_home_win_probability(), 0.6)
        with self.assertRaises(Exception):
            distribution("F1", PARAMS, 7.0, None).total("OVER", 44.5)

    def test_discrete_pmf_rejects_bad_sigma(self):
        with self.assertRaises(ValueError):
            discrete_pmf(0.0, 0.0, MARGIN_SUPPORT)


class PricingTests(unittest.TestCase):
    def test_conversions_and_no_vig(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1 + 100 / 110)
        self.assertAlmostEqual(american_to_decimal(150), 2.5)
        self.assertAlmostEqual(implied_probability(100), 0.5)
        a, b, over = no_vig_pair(-110, -110)
        self.assertAlmostEqual(a, 0.5)
        self.assertAlmostEqual(over, 2 * 110 / 210 - 1)
        for bad in (99, -99, 0, 1.5, True, None, float("nan")):
            with self.assertRaises(GameMarketPricingError):
                american_to_decimal(bad)

    def test_one_sided_and_arbitrage_shaped_pairs_rejected(self):
        with self.assertRaises(GameMarketPricingError):
            no_vig_pair(-110, None)
        with self.assertRaises(GameMarketPricingError):
            no_vig_pair(200, 200)

    def test_ev_with_push_and_break_even(self):
        ev = evaluate_price((0.5, 0.1, 0.4), -110)
        self.assertAlmostEqual(ev["break_even"], 110 / 210)
        self.assertAlmostEqual(ev["p_win_conditional"], 0.5 / 0.9)
        self.assertAlmostEqual(ev["ev_per_unit"], 0.5 * (100 / 110) - 0.4)
        with self.assertRaises(GameMarketPricingError):
            evaluate_price((0.5, 0.5, 0.5), -110)

    def test_lean_decision_fail_closed(self):
        good = {"ev_per_unit": 0.05}
        self.assertEqual(lean_decision(market="spread", gate_reasons=[], lean_eval=good,
                                       model_market_gap=1.0)[0], "RESEARCH_LEAN")
        self.assertEqual(lean_decision(market="spread", gate_reasons=["STALE_QUOTE"], lean_eval=good,
                                       model_market_gap=1.0)[0], "QUARANTINED")
        self.assertEqual(lean_decision(market="spread", gate_reasons=[], lean_eval=good,
                                       model_market_gap=9.0),
                         ("QUARANTINED", ["MODEL_MARKET_GAP_IMPLAUSIBLE"]))
        self.assertEqual(lean_decision(market="spread", gate_reasons=[], lean_eval={"ev_per_unit": 0.0},
                                       model_market_gap=0.1), ("NO_LEAN", ["EV_BELOW_THRESHOLD"]))
        self.assertEqual(lean_decision(market="spread", gate_reasons=[], lean_eval=None,
                                       model_market_gap=None)[0], "QUARANTINED")
        self.assertEqual(lean_decision(market="moneyline", gate_reasons=[], lean_eval=good,
                                       model_market_gap=None)[0], "MEASUREMENT_ONLY")


class IdentityToleranceTests(unittest.TestCase):
    def test_plus_one_minute_binds_only_with_tolerance(self):
        snap = _snapshot()
        with self.assertRaises(GameIdentityError):
            _binding(snap, tolerance=0)
        b = _binding(snap, tolerance=2)
        self.assertEqual(b["scheduled_kickoff"], KICKOFF)
        self.assertEqual(b["sportsbook_market_time"], "2026-09-20T17:01:00+00:00")

    def test_market_time_leading_kickoff_never_binds(self):
        early = [_record(m, market_time="2026-09-20T16:59:00Z", event_open_date="2026-09-20T16:59:00Z")
                 for m in ("moneyline", "spread", "game_total")]
        with self.assertRaises(GameIdentityError):
            _binding(_snapshot(early), tolerance=2)

    def test_tolerance_bounds(self):
        with self.assertRaises(GameIdentityError):
            _binding(_snapshot(), tolerance=30)


class FreezeTests(unittest.TestCase):
    def test_complete_freeze_binds_identity_and_verifies(self):
        rec = _freeze()
        self.assertEqual(verify_research_freeze(rec), rec["record_sha256"])
        self.assertFalse(rec["public_eligible"])
        self.assertEqual(rec["scheduled_kickoff"], KICKOFF)
        spread_home = _sel(rec, "spread", "HOME")
        gi = spread_home["grading_identity"]
        self.assertEqual((gi["nflverse_game_id"], gi["espn_event_id"]), ("2026_03_MIA_BUF", "401"))
        self.assertEqual(set(spread_home["models"]), {"B0", "F1", "M0", "C1"})
        self.assertAlmostEqual(spread_home["no_vig_probability"], 0.5)
        # Market prior at its own line has no edge beyond the vig.
        self.assertAlmostEqual(spread_home["models"]["M0"]["p_win_conditional"], 0.5, places=5)
        self.assertLess(spread_home["models"]["M0"]["ev_per_unit"], 0)
        self.assertIn(spread_home["decision"], {"NO_LEAN", "RESEARCH_LEAN"})

    def test_frozen_record_mutation_detected(self):
        rec = _freeze()
        tampered = deepcopy(rec)
        tampered["selections"][0]["decision"] = "RESEARCH_LEAN"
        with self.assertRaises(GameMarketFreezeError):
            verify_research_freeze(tampered)
        tampered = deepcopy(rec)
        tampered["market_snapshot"]["records"][1]["home_odds"] = 150
        with self.assertRaises(Exception):
            verify_research_freeze(tampered)

    def test_wrong_game_binding_and_mismatched_event(self):
        snap = _snapshot()
        b = _binding(snap)
        with self.assertRaises(GameMarketFreezeError):
            _freeze(snapshot=snap, binding=b, b0=_b0(game_id="2026_03_NYJ_NE"))
        wrong_event = deepcopy(b)
        wrong_event["sportsbook_event_id"] = "999"
        with self.assertRaises(GameMarketFreezeError):
            _freeze(snapshot=snap, binding=wrong_event)
        other_snapshot = _snapshot(sealed_at="2026-09-20T16:01:30Z")
        with self.assertRaises(GameMarketFreezeError):
            _freeze(snapshot=other_snapshot, binding=b)
        forged = deepcopy(b)
        forged["home_team"] = "NYJ"
        with self.assertRaises(GameMarketFreezeError):
            _freeze(snapshot=snap, binding=forged)

    def test_post_kickoff_freeze_refused(self):
        with self.assertRaises(GameMarketFreezeError):
            _freeze(frozen_at="2026-09-20T17:00:00Z")

    def test_stale_line_quarantined(self):
        snap = _snapshot(captured="2026-09-20T13:00:00Z", sealed_at="2026-09-20T13:01:00Z")
        rec = _freeze(snapshot=snap)
        sel = _sel(rec, "spread", "HOME")
        self.assertEqual(sel["decision"], "QUARANTINED")
        self.assertIn("STALE_QUOTE", sel["reasons"])

    def test_one_sided_market_and_missing_price(self):
        rows = [_record("moneyline"), _record("spread", away_odds=None), _record("game_total")]
        rec = _freeze(snapshot=_snapshot(rows))
        home, away = _sel(rec, "spread", "HOME"), _sel(rec, "spread", "AWAY")
        self.assertIn("ONE_SIDED_MARKET", home["reasons"])
        self.assertIsNone(home["no_vig_probability"])
        self.assertIn("MISSING_PRICE", away["reasons"])
        self.assertEqual(away["models"], {})

    def test_no_sportsbook_offer_for_family(self):
        rec = _freeze(snapshot=_snapshot([_record("moneyline"), _record("spread")]))
        total = next(s for s in rec["selections"] if s["market"] == "game_total")
        self.assertEqual(total["decision"], "QUARANTINED")
        self.assertIn("MARKET_NOT_NORMALIZED", total["reasons"])

    def test_qb_change_and_unknown_quarantine(self):
        changed = deepcopy(SCHEDULE)
        changed[2]["home_qb_id"] = "QB-BUF-BACKUP"
        rec = _freeze(qb=qb_state_from_schedule(changed, "2026_03_MIA_BUF"))
        self.assertIn("QB_CHANGE_UNMODELLED", _sel(rec, "spread", "HOME")["reasons"])
        unknown = deepcopy(SCHEDULE)
        unknown[2]["away_qb_id"] = ""
        rec = _freeze(qb=qb_state_from_schedule(unknown, "2026_03_MIA_BUF"))
        sel = _sel(rec, "game_total", "OVER")
        self.assertEqual(sel["decision"], "QUARANTINED")
        self.assertIn("QB_STATE_UNKNOWN", sel["reasons"])

    def test_identity_unbound_and_ineligible_model_quarantine(self):
        rec = _freeze(binding=None)
        self.assertIn("IDENTITY_UNBOUND", _sel(rec, "spread", "HOME")["reasons"])
        rec = _freeze(b0=_b0(eligibility="INSUFFICIENT_HISTORY", predicted_home_margin=None,
                             predicted_total=None))
        sel = _sel(rec, "spread", "HOME")
        self.assertEqual(sel["decision"], "QUARANTINED")
        self.assertIn("MODEL_INELIGIBLE", sel["reasons"])
        self.assertIsNotNone(sel["models"]["M0"])  # market prior still measured

    def test_unknown_code_sha_refused(self):
        with self.assertRaises(GameMarketFreezeError):
            _freeze(code_sha="UNKNOWN")

    def test_duplicate_selection_identity_refused(self):
        with mock.patch.object(freeze_mod, "canonical_sha256", return_value="f" * 64):
            with self.assertRaises(GameMarketFreezeError):
                _freeze()

    def test_create_only_write(self):
        rec = _freeze()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_research_freeze(Path(tmp), rec)
            self.assertIn("week_3", str(path))
            with self.assertRaises(GameMarketFreezeError):
                write_research_freeze(Path(tmp), rec)

    def test_zero_eligible_is_valid(self):
        rec = _freeze()
        decisions = {s["decision"] for s in rec["selections"]}
        self.assertTrue(decisions <= {"NO_LEAN", "RESEARCH_LEAN", "QUARANTINED", "MEASUREMENT_ONLY"})


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.snap = _snapshot()
        self.binding = _binding(self.snap)
        self.rec = _freeze(snapshot=self.snap, binding=self.binding)

    def _grade(self, outcome, later=None):
        return grade_research_freeze(self.rec, outcome, later_snapshot=later,
                                     graded_at="2026-09-20T21:05:00Z")

    def test_spread_push_on_three_and_total_settlement(self):
        g = self._grade(_outcome(self.binding, home=27, away=24))
        by = {(s["market"], s.get("side")): s for s in g["selections"]}
        self.assertEqual(by[("spread", "HOME")]["settlement"], "PUSH")
        self.assertEqual(by[("spread", "HOME")]["units_at_captured_price"], 0.0)
        self.assertEqual(by[("game_total", "OVER")]["settlement"], "HIT")
        self.assertEqual(by[("game_total", "UNDER")]["settlement"], "MISS")
        self.assertEqual(by[("moneyline", "HOME")]["settlement"], "HIT")
        p_push = _sel(self.rec, "spread", "HOME")["models"]["C1"]["p_push"]
        self.assertAlmostEqual(by[("spread", "HOME")]["realised_logloss"]["C1"], -math.log(p_push), places=4)

    def test_total_push(self):
        g = self._grade(_outcome(self.binding, home=24, away=21))
        by = {(s["market"], s.get("side")): s for s in g["selections"]}
        self.assertEqual(by[("game_total", "OVER")]["settlement"], "PUSH")

    def test_not_final_and_wrong_binding_refused(self):
        with self.assertRaises(ValueError):
            _outcome(self.binding, status="STATUS_IN_PROGRESS", completed=False)
        other = deepcopy(self.binding)
        other["away_team"] = "NYJ"
        other_body = {k: v for k, v in other.items() if k != "binding_sha256"}
        other["binding_sha256"] = freeze_mod.canonical_sha256(other_body)
        with self.assertRaises(Exception):
            self._grade(_outcome(other))
        out = _outcome(self.binding)
        out["home_score"] = 99
        with self.assertRaises(GameMarketResearchGradeError):
            self._grade(out)

    def test_mutated_freeze_cannot_be_graded(self):
        self.rec["selections"][0]["decision"] = "RESEARCH_LEAN"
        with self.assertRaises(GameMarketFreezeError):
            self._grade(_outcome(self.binding))

    def test_movement_against_later_presnapshot_and_ordering(self):
        later_rows = [_record("moneyline", captured_at="2026-09-20T16:50:00Z"),
                      _record("spread", captured_at="2026-09-20T16:50:00Z", home_line=-4.0,
                              away_line=4.0),
                      _record("game_total", captured_at="2026-09-20T16:50:00Z", line=46.0)]
        later = _snapshot(later_rows, sealed_at="2026-09-20T16:51:00Z")
        g = self._grade(_outcome(self.binding), later=later)
        by = {(s["market"], s.get("side")): s for s in g["selections"]}
        self.assertEqual(by[("spread", "HOME")]["later_vintage_comparison"]["line_movement_points"], 1.0)
        self.assertEqual(by[("spread", "AWAY")]["later_vintage_comparison"]["line_movement_points"], -1.0)
        self.assertEqual(by[("game_total", "OVER")]["later_vintage_comparison"]["line_movement_points"], 1.0)
        ref = by[("spread", "HOME")]["later_vintage_comparison"]
        self.assertIn("NOT_A_VERIFIED_CLOSE", ref["reference_basis"])
        self.assertEqual(ref["reference_minutes_before_kickoff"], 10.0)
        self.assertIn("NOT_A_VERIFIED_CLOSE", g["later_reference_basis"])
        with self.assertRaises(GameMarketResearchGradeError):
            self._grade(_outcome(self.binding), later=_snapshot())  # sealed before freeze

    def test_grade_create_only(self):
        g = self._grade(_outcome(self.binding))
        with tempfile.TemporaryDirectory() as tmp:
            p = write_grade(Path(tmp) / "g.json", g)
            with self.assertRaises(GameMarketResearchGradeError):
                write_grade(p, g)


class ConfigTests(unittest.TestCase):
    def test_market_configs_need_lines_and_football_configs_need_b0(self):
        self.assertEqual(centers("M0", PARAMS, b0_margin=None, b0_total=None,
                                 line_margin=None, line_total=None), (None, None))
        self.assertEqual(centers("F1", PARAMS, b0_margin=None, b0_total=None,
                                 line_margin=3.0, line_total=44.0), (None, None))
        cm, ct = centers("C1", PARAMS, b0_margin=0.0, b0_total=44.0, line_margin=3.0, line_total=44.0)
        self.assertLess(abs(cm - market_center(PARAMS, "margin", 3.0)), 1.0)



class FitTests(unittest.TestCase):
    def test_fit_recovers_known_structure_on_synthetic_dev(self):
        import random
        from nfl.research.game_market_models import fit_params
        rng = random.Random(7)
        rows = []
        for _ in range(400):
            truth = rng.gauss(0, 6)
            line = round(truth * 2) / 2
            b0 = truth - 2.5 + rng.gauss(0, 4)  # B0 missing home field, noisy
            tt = rng.gauss(44, 5)
            rows.append({"b0_margin": b0, "b0_total": tt + rng.gauss(0, 5), "line_margin": line,
                         "line_total": round(tt * 2) / 2, "actual_margin": round(truth + rng.gauss(0, 13)),
                         "actual_total": max(0, round(tt + rng.gauss(0, 13)))})
        p = fit_params(rows, {"fit_partition": "synthetic"})
        self.assertGreater(p.f1_margin[0], 1.0)  # intercept absorbs missing home field
        self.assertLess(abs(p.c1_k_margin), 0.5)  # football adds little beyond a sharp line
        self.assertEqual(p.provenance["fit_rows"], 400)


if __name__ == "__main__":
    unittest.main()
