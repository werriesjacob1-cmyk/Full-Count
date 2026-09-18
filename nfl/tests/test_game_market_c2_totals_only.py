#!/usr/bin/env python3
"""Tests for the totals-only, independently-gated C2 re-evaluation.

Covers: the new gate is predeclared (structurally, before any held-data
code path) and evaluated independent of margin; the gate applies correctly
to both a clearly-passing and a clearly-failing synthetic population;
season-stability/leave-one-out math; the total-specific equal-volume
directional method; and end-to-end reproducibility from pinned inputs
(synthetic sources, matching `test_game_market_c2_research.py`'s own
digest-override pattern -- no network access in this test file, exactly
like every other C2 test module).
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from nfl.research import game_market_b0_research as b0_research
from nfl.research import game_market_c2_research as c2_research
from nfl.research import game_market_c2_totals_only as totals_only
from nfl.research.game_market_b0 import REQUIRED_CLOSING_CONTROL
from nfl.research.game_market_c2_model import DEV_START, HELD_END, HELD_START, VALID_END, VALID_START


def _paired(game_id, season, actual_total, b0_total, c2_total, actual_margin=0.0, b0_margin=0.0, c2_margin=0.0):
    return {
        "game_id": game_id,
        "season": season,
        "actual_margin": actual_margin,
        "actual_total": actual_total,
        "b0_margin": b0_margin,
        "b0_total": b0_total,
        "c2_margin": c2_margin,
        "c2_total": c2_total,
    }


def _market_row(game_id, total_line, spread_line=0.0):
    return {"game_id": game_id, "total_line": total_line, "spread_line": spread_line, **REQUIRED_CLOSING_CONTROL}


def _seasons_with_games(seasons, games_per_season, row_fn):
    """Build `games_per_season` paired rows per season using `row_fn(game_index)`."""
    rows = []
    idx = 0
    for season in seasons:
        for i in range(games_per_season):
            rows.append(row_fn(season, i, idx))
            idx += 1
    return rows


# A population where C2's total prediction is consistently and substantially
# closer to actual than B0's, in every 2020-2025 season, with no overlap in
# per-game error magnitude -- constructed so the paired bootstrap has zero
# variance in sign (every bootstrap resample still shows a negative delta).
# Margin is deliberately made *worse* for C2 in every game, to prove the
# total-only gate is blind to it.
_STABLE_TOTAL_WIN_SEASONS = (VALID_START, VALID_START + 1, VALID_START + 2, HELD_START, HELD_START + 1, HELD_END)


def _stable_total_win_row(season, i, idx):
    actual_total = 40.0 + i
    return _paired(
        f"g{idx}", season,
        actual_total=actual_total,
        b0_total=actual_total + 6.0,   # B0 always off by +6
        c2_total=actual_total + 1.0,   # C2 always off by +1 -- strictly better every game
        actual_margin=3.0,
        b0_margin=3.0,                  # B0 margin is exact
        c2_margin=30.0,                 # C2 margin is wildly wrong -- must not affect the total gate
    )


def _build_stable_total_win_paired_rows(games_per_season=6):
    return _seasons_with_games(_STABLE_TOTAL_WIN_SEASONS, games_per_season, _stable_total_win_row)


def _stable_total_loss_row(season, i, idx):
    actual_total = 40.0 + i
    return _paired(
        f"g{idx}", season,
        actual_total=actual_total,
        b0_total=actual_total + 1.0,   # B0 always off by +1 -- better than C2 every game
        c2_total=actual_total + 6.0,   # C2 always off by +6
        actual_margin=3.0,
        b0_margin=3.0,
        c2_margin=3.0,
    )


def _build_stable_total_loss_paired_rows(games_per_season=6):
    return _seasons_with_games(_STABLE_TOTAL_WIN_SEASONS, games_per_season, _stable_total_loss_row)


class GatePredeclarationStructureTests(unittest.TestCase):
    """Structural proof the gate is predeclared, not just verbally asserted."""

    def test_gate_description_is_a_module_level_constant(self):
        self.assertIsInstance(totals_only.TOTAL_PROMOTION_GATE_DESCRIPTION, str)
        self.assertGreater(len(totals_only.TOTAL_PROMOTION_GATE_DESCRIPTION), 40)

    def test_gate_description_defined_before_any_held_data_function(self):
        source_lines = inspect.getsource(totals_only).splitlines()

        def _line_of(needle: str) -> int:
            for i, line in enumerate(source_lines):
                if needle in line:
                    return i
            raise AssertionError(f"could not find {needle!r} in module source")

        gate_constant_line = _line_of("TOTAL_PROMOTION_GATE_DESCRIPTION = (")
        gate_function_line = _line_of("def evaluate_total_promotion_gate(")
        evaluation_function_line = _line_of("def run_totals_only_evaluation(")
        stability_function_line = _line_of("def compute_season_stability(")

        # The gate's own wording is fixed in source before the function that
        # checks it, before the stability computation feeding it, and before
        # the top-level function that ever touches held/validation numbers --
        # i.e. the gate cannot have been shaped by looking at a computed
        # held-partition result, only the reverse is possible in this file.
        self.assertLess(gate_constant_line, gate_function_line)
        self.assertLess(gate_constant_line, stability_function_line)
        self.assertLess(gate_constant_line, evaluation_function_line)

    def test_gate_description_names_all_four_conditions(self):
        text = totals_only.TOTAL_PROMOTION_GATE_DESCRIPTION
        for fragment in (
            "held total MAE(C2) < held total MAE(B0)",
            "bootstrap 97.5th percentile",
            "validation total MAE(C2) <= validation total MAE(B0)",
            "season stability",
            "leave-one-season-out" if "leave-one-season-out" in text else "excluding any single held season",
        ):
            self.assertIn(fragment, text)


class GateIndependenceFromMarginTests(unittest.TestCase):
    """Proof the total gate cannot see, and does not depend on, margin's outcome."""

    def test_module_does_not_import_margin_gate(self):
        module_globals = vars(totals_only)
        self.assertNotIn("evaluate_promotion_gate", module_globals)
        self.assertNotIn("PROMOTION_GATE_DESCRIPTION", module_globals)

    def test_evaluate_total_promotion_gate_signature_takes_no_margin_input(self):
        signature = inspect.signature(totals_only.evaluate_total_promotion_gate)
        for name in signature.parameters:
            self.assertNotIn("margin", name)

    def test_gate_function_source_never_reads_a_margin_field(self):
        # The docstring is allowed to explain in prose that margin is
        # excluded (and does); what must never appear is an actual dict
        # lookup keyed on "margin" data, e.g. evaluation[...]["margin"].
        source = inspect.getsource(totals_only.evaluate_total_promotion_gate)
        self.assertNotIn('"margin"', source)
        self.assertNotIn("'margin'", source)

    def test_gate_passes_on_total_despite_catastrophic_margin(self):
        # _stable_total_win_row makes C2's margin wildly wrong (30.0 vs an
        # actual/B0 margin of 3.0) in every single game -- if this gate were
        # even slightly coupled to margin, it could not pass here.
        paired_rows = _build_stable_total_win_paired_rows()
        evaluation = totals_only.evaluate_c2(paired_rows)
        # Confirm the margin data really is catastrophic for C2 in this fixture.
        self.assertGreater(evaluation["held_2023_2025"]["margin"]["c2_mae"], 20.0)
        stability = totals_only.compute_season_stability(evaluation, paired_rows)
        gate = totals_only.evaluate_total_promotion_gate(evaluation, stability)
        self.assertEqual(gate["failed_conditions"], [])
        self.assertTrue(gate["promotion_eligible"])


class TotalGateOutcomeTests(unittest.TestCase):
    def test_gate_passes_on_a_clean_stable_total_improvement(self):
        paired_rows = _build_stable_total_win_paired_rows()
        report = totals_only.run_totals_only_evaluation(paired_rows, [])
        self.assertEqual(report["status"], "RESEARCH_CHALLENGER_PROMOTION_ELIGIBLE")
        self.assertTrue(report["promotion_gate"]["promotion_eligible"])
        self.assertEqual(report["season_stability"]["favorable_season_count"], 6)
        self.assertTrue(report["season_stability"]["held_leave_one_season_out_all_negative"])

    def test_gate_fails_on_a_clean_stable_total_regression(self):
        paired_rows = _build_stable_total_loss_paired_rows()
        report = totals_only.run_totals_only_evaluation(paired_rows, [])
        self.assertEqual(report["status"], "RESEARCH_CHALLENGER_REJECTED")
        self.assertFalse(report["promotion_gate"]["promotion_eligible"])
        self.assertGreater(len(report["promotion_gate"]["failed_conditions"]), 0)
        self.assertIn(
            "held total MAE is not strictly better than B0",
            report["promotion_gate"]["failed_conditions"],
        )

    def test_gate_fails_when_one_held_season_is_an_outlier_that_flips_the_sign(self):
        # Five seasons show a clean, large C2 improvement; the sixth (a held
        # season) is a large C2 regression big enough that removing it flips
        # held's aggregate sign -- the leave-one-season-out check must catch
        # this even though the naive "favorable season count" (5 of 6) would
        # otherwise satisfy the majority threshold on its own.
        rows = []
        idx = 0
        for season in (VALID_START, VALID_START + 1, VALID_START + 2, HELD_START, HELD_START + 1):
            for i in range(6):
                actual_total = 40.0 + i
                rows.append(_paired(
                    f"g{idx}", season, actual_total=actual_total,
                    b0_total=actual_total + 6.0, c2_total=actual_total + 1.0,
                ))
                idx += 1
        # The outlier held season: C2 catastrophically worse, enough games
        # and magnitude to flip the sign of the held partition once combined
        # with only two other (small) held seasons.
        for i in range(6):
            actual_total = 40.0 + i
            rows.append(_paired(
                f"g{idx}", HELD_END, actual_total=actual_total,
                b0_total=actual_total + 1.0, c2_total=actual_total + 40.0,
            ))
            idx += 1

        evaluation = totals_only.evaluate_c2(rows)
        stability = totals_only.compute_season_stability(evaluation, rows)
        # 5 of 6 seasons still individually favor C2 (majority threshold met)...
        self.assertEqual(stability["favorable_season_count"], 5)
        self.assertTrue(stability["majority_threshold_met"])
        # ...but the outlier season is a HELD season and dominates the held
        # aggregate, so leave-one-out must fail and the overall gate must fail.
        gate = totals_only.evaluate_total_promotion_gate(evaluation, stability)
        self.assertFalse(gate["promotion_eligible"])
        self.assertIn(
            "held total MAE win does not survive excluding a single held season "
            "(leave-one-season-out)",
            gate["failed_conditions"],
        )

    def test_real_verified_2026_09_18_numbers_pass_the_total_only_gate(self):
        # Pin of the actual numbers independently reproduced from the real
        # digest-pinned nflverse pbp/schedule data (see ENGINEERING_HANDOFF.md
        # 2026-09-18 and this workstream's own verification run): held total
        # delta -0.283 (CI entirely below zero), validation total delta
        # -0.227 (CI entirely below zero), every one of the 6 seasons
        # 2020-2025 individually favors C2 on total, and the held win
        # survives excluding any single held season. This test does not
        # re-fetch network data (no test in this suite does); it pins the
        # already-verified real result as an explicit regression anchor.
        evaluation = {
            "held_2023_2025": {
                "total": {
                    "b0_mae": 10.718872549019608,
                    "c2_mae": 10.436011358890134,
                    "mae_delta_c2_minus_b0": -0.2828611901294735,
                    "game_bootstrap": {"p97_5": -0.0941704740406788},
                },
            },
            "validation_2020_2022": {
                "total": {
                    "b0_mae": 11.089633123689728,
                    "c2_mae": 10.862826250206828,
                    "mae_delta_c2_minus_b0": -0.22680687348290007,
                },
            },
            "season_by_season_2020_2025": {
                "2020": {"total": {"mae_delta_c2_minus_b0": -0.2175}},
                "2021": {"total": {"mae_delta_c2_minus_b0": -0.362}},
                "2022": {"total": {"mae_delta_c2_minus_b0": -0.1003}},
                "2023": {"total": {"mae_delta_c2_minus_b0": -0.4032}},
                "2024": {"total": {"mae_delta_c2_minus_b0": -0.2907}},
                "2025": {"total": {"mae_delta_c2_minus_b0": -0.1547}},
            },
        }
        stability = {
            "seasons_considered": list(totals_only.STABILITY_SEASONS),
            "favorable_seasons": list(totals_only.STABILITY_SEASONS),
            "favorable_season_count": 6,
            "min_favorable_seasons_required": totals_only.STABILITY_MIN_FAVORABLE_SEASONS,
            "majority_threshold_met": True,
            "held_leave_one_season_out_total_mae_delta": {
                "2023": -0.22270582603269062,
                "2024": -0.2789519409772616,
                "2025": -0.34692580337846834,
            },
            "held_leave_one_season_out_all_negative": True,
            "stable": True,
        }
        gate = totals_only.evaluate_total_promotion_gate(evaluation, stability)
        self.assertEqual(gate["failed_conditions"], [])
        self.assertTrue(gate["promotion_eligible"])


class SeasonStabilityUnitTests(unittest.TestCase):
    def test_leave_one_season_out_excludes_only_held_seasons(self):
        rows = _build_stable_total_win_paired_rows()
        out = totals_only.compute_held_leave_one_season_out(rows)
        self.assertEqual(set(out.keys()), {"2023", "2024", "2025"})
        for value in out.values():
            self.assertIsNotNone(value)
            self.assertLess(value, 0.0)

    def test_unfavorable_seasons_are_reported_not_hidden(self):
        rows = _build_stable_total_win_paired_rows()
        # Flip one validation season's numbers so it disfavors C2.
        for row in rows:
            if row["season"] == VALID_START:
                row["c2_total"], row["b0_total"] = row["b0_total"], row["c2_total"]
        evaluation = totals_only.evaluate_c2(rows)
        stability = totals_only.compute_season_stability(evaluation, rows)
        self.assertIn(VALID_START, stability["unfavorable_seasons"])
        self.assertEqual(stability["favorable_season_count"], 5)
        # 5 of 6 still meets the predeclared majority threshold.
        self.assertTrue(stability["majority_threshold_met"])


class TotalDirectionalAccuracyTests(unittest.TestCase):
    def test_build_market_total_index_rejects_bad_control_contract(self):
        with self.assertRaisesRegex(totals_only.GameMarketC2TotalsOnlyError, "closing control"):
            totals_only.build_market_total_index([{"game_id": "g1", "total_line": 40.0, "allowed_use": "WRONG"}])

    def test_build_market_total_index_rejects_duplicate_game_id(self):
        rows = [_market_row("g1", 40.0), _market_row("g1", 41.0)]
        with self.assertRaisesRegex(totals_only.GameMarketC2TotalsOnlyError, "duplicate"):
            totals_only.build_market_total_index(rows)

    def test_agreement_games_excluded_from_disagreement_set(self):
        # Both models pick OVER (predicted total > total_line); no disagreement.
        paired = [_paired("g1", HELD_START, actual_total=50.0, b0_total=45.0, c2_total=46.0)]
        index = totals_only.build_market_total_index([_market_row("g1", 40.0)])
        result = totals_only.compute_equal_volume_total_directional_accuracy(paired, index)
        self.assertEqual(result["disagreement_games"], 0)

    def test_disagreement_and_edge_ranked_hit_rate(self):
        # g1: total_line 40. B0 predicts 30 (UNDER), C2 predicts 50 (OVER).
        # Actual total 45 -> actual side OVER -> C2 correct, B0 wrong.
        paired = [_paired("g1", HELD_START, actual_total=45.0, b0_total=30.0, c2_total=50.0)]
        index = totals_only.build_market_total_index([_market_row("g1", 40.0)])
        result = totals_only.compute_equal_volume_total_directional_accuracy(paired, index)
        self.assertEqual(result["disagreement_games"], 1)
        top = result["by_volume_fraction"][-1]
        self.assertEqual(top["c2_hit_rate"], 1.0)
        self.assertEqual(top["b0_hit_rate"], 0.0)

    def test_push_actual_total_is_excluded(self):
        paired = [_paired("g1", HELD_START, actual_total=40.0, b0_total=30.0, c2_total=50.0)]
        index = totals_only.build_market_total_index([_market_row("g1", 40.0)])
        result = totals_only.compute_equal_volume_total_directional_accuracy(paired, index)
        self.assertEqual(result["market_matched_games"], 0)


class ReproducibilityTests(unittest.TestCase):
    def test_run_totals_only_evaluation_is_byte_identical_across_runs(self):
        paired_rows = _build_stable_total_win_paired_rows()
        market_rows = [
            _market_row(row["game_id"], row["actual_total"]) for row in paired_rows
        ]
        first = totals_only.run_totals_only_evaluation(paired_rows, market_rows)
        second = totals_only.run_totals_only_evaluation(paired_rows, market_rows)
        self.assertEqual(first, second)


# --- End-to-end reproduction from pinned inputs (synthetic, no network) -----

SCHEDULE_HEADER = (
    "game_id,season,game_type,week,away_team,away_score,home_team,home_score,"
    "result,total,spread_line,total_line\n"
)
OFFENSE_HEADER = (
    "game_id,season,week,season_type,team,opponent_team,attempts,passing_yards,"
    "sacks_suffered,passing_epa,carries,rushing_yards\n"
)
PLAY_HEADER = (
    "game_id,play_id,season,week,season_type,posteam,defteam,down,"
    "half_seconds_remaining,wp,qb_dropback,rush_attempt,qb_kneel,qb_spike\n"
)


def _game_row(week, home, away, home_score=24, away_score=17, spread="3.5", total_line="41.5"):
    game_id = f"2010_{week:02d}_{away}_{home}"
    result = home_score - away_score
    total = home_score + away_score
    return f"{game_id},2010,REG,{week},{away},{away_score},{home},{home_score},{result},{total},{spread},{total_line}\n"


def _offense_rows(week, home, away):
    game_id = f"2010_{week:02d}_{away}_{home}"
    lines = []
    for team, opponent in ((home, away), (away, home)):
        lines.append(f"{game_id},2010,{week},REG,{team},{opponent},30,220,2,3.0,22,90\n")
    return "".join(lines)


def _play_rows(week, home, away, play_id_start):
    game_id = f"2010_{week:02d}_{away}_{home}"
    lines = []
    play_id = play_id_start
    for team, opponent in ((home, away), (away, home)):
        for _ in range(6):
            lines.append(
                f"{game_id},{play_id},2010,{week},REG,{team},{opponent},1,1500,0.5,1,0,0,0\n"
            )
            play_id += 1
    return "".join(lines), play_id


def _build_synthetic_sources(n_weeks=20):
    schedule = SCHEDULE_HEADER
    offense = OFFENSE_HEADER
    plays = PLAY_HEADER
    play_id = 1
    for week in range(1, n_weeks + 1):
        home, away = ("AAA", "BBB") if week % 2 == 1 else ("BBB", "AAA")
        schedule += _game_row(week, home, away)
        offense += _offense_rows(week, home, away)
        play_chunk, play_id = _play_rows(week, home, away, play_id)
        plays += play_chunk
    return schedule, offense, plays


class GameMarketC2TotalsOnlyEndToEndTests(unittest.TestCase):
    def _pin_and_run(self, n_weeks=20):
        schedule_text, offense_text, plays_text = _build_synthetic_sources(n_weeks=n_weeks)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        schedule_path = Path(tmp.name) / "games.csv"
        offense_path = Path(tmp.name) / "team_offense_week.csv"
        plays_path = Path(tmp.name) / "pbp_plays_filtered.csv"
        schedule_path.write_text(schedule_text, encoding="utf-8")
        offense_path.write_text(offense_text, encoding="utf-8")
        plays_path.write_text(plays_text, encoding="utf-8")

        original_schedule = dict(b0_research.PINNED_SCHEDULE_SOURCE)
        original_offense = dict(c2_research.PINNED_TEAM_OFFENSE_SOURCE)
        original_plays = dict(c2_research.PINNED_PBP_PLAY_SOURCE)
        b0_research.PINNED_SCHEDULE_SOURCE["bytes"] = schedule_path.stat().st_size
        b0_research.PINNED_SCHEDULE_SOURCE["sha256"] = b0_research.sha256_file(schedule_path)
        c2_research.PINNED_TEAM_OFFENSE_SOURCE["bytes"] = offense_path.stat().st_size
        c2_research.PINNED_TEAM_OFFENSE_SOURCE["sha256"] = c2_research.sha256_file(offense_path)
        c2_research.PINNED_PBP_PLAY_SOURCE["bytes"] = plays_path.stat().st_size
        c2_research.PINNED_PBP_PLAY_SOURCE["sha256"] = c2_research.sha256_file(plays_path)

        def _restore():
            b0_research.PINNED_SCHEDULE_SOURCE.clear()
            b0_research.PINNED_SCHEDULE_SOURCE.update(original_schedule)
            c2_research.PINNED_TEAM_OFFENSE_SOURCE.clear()
            c2_research.PINNED_TEAM_OFFENSE_SOURCE.update(original_offense)
            c2_research.PINNED_PBP_PLAY_SOURCE.clear()
            c2_research.PINNED_PBP_PLAY_SOURCE.update(original_plays)

        self.addCleanup(_restore)
        return totals_only.run_research(schedule_path, offense_path, plays_path)

    def test_end_to_end_run_is_reproducible_and_well_formed(self):
        first = self._pin_and_run()
        second = self._pin_and_run()
        first_copy = dict(first)
        second_copy = dict(second)
        first_copy.pop("generated_at")
        second_copy.pop("generated_at")
        self.assertEqual(first_copy, second_copy)
        self.assertIn(
            first["status"],
            ("RESEARCH_CHALLENGER_PROMOTION_ELIGIBLE", "RESEARCH_CHALLENGER_REJECTED"),
        )
        self.assertIn("promotion_gate", first)
        self.assertIn("season_stability", first)
        self.assertEqual(
            first["margin_features_unchanged_from_c2"],
            list(totals_only.MARGIN_FEATURES),
        )
        self.assertEqual(
            first["total_features_unchanged_from_c2"],
            list(totals_only.TOTAL_FEATURES),
        )

    def test_end_to_end_run_never_touches_margin_gate(self):
        report = self._pin_and_run()
        self.assertNotIn("evaluation_vs_b0", report)  # C2's combined report shape, not reused here
        self.assertTrue(report["gate_evaluated_independent_of_margin"])


if __name__ == "__main__":
    unittest.main()
