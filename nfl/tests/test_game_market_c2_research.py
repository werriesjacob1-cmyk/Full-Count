#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from nfl.research import game_market_b0_research as b0_research
from nfl.research import game_market_c2_research as research


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


def _build_synthetic_sources(n_weeks=5):
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


class GameMarketC2ResearchPinTests(unittest.TestCase):
    def test_pinned_source_dicts_are_locked(self):
        self.assertEqual(research.PINNED_TEAM_OFFENSE_SOURCE["bytes"], 1031542)
        self.assertEqual(
            research.PINNED_TEAM_OFFENSE_SOURCE["sha256"],
            "cbf47080fa575f2f40cfb0ca0fd2f7fc52d708264aca21020cea33ddc0377d53",
        )
        self.assertEqual(research.PINNED_PBP_PLAY_SOURCE["bytes"], 77094861)
        self.assertEqual(
            research.PINNED_PBP_PLAY_SOURCE["sha256"],
            "1115f9979d55fcdbac76e8a59823860145884b56b6608d5581a60de717cd8836",
        )

    def test_team_offense_digest_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team_offense_week.csv"
            path.write_text(OFFENSE_HEADER, encoding="utf-8")
            with self.assertRaisesRegex(research.GameMarketC2ResearchError, "byte drift"):
                research.load_pinned_team_offense_rows(path)

    def test_pbp_plays_digest_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pbp_plays_filtered.csv"
            path.write_text(PLAY_HEADER, encoding="utf-8")
            with self.assertRaisesRegex(research.GameMarketC2ResearchError, "byte drift"):
                research.load_pinned_pbp_play_rows(path)


class GameMarketC2ResearchEndToEndTests(unittest.TestCase):
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
        original_offense = dict(research.PINNED_TEAM_OFFENSE_SOURCE)
        original_plays = dict(research.PINNED_PBP_PLAY_SOURCE)
        b0_research.PINNED_SCHEDULE_SOURCE["bytes"] = schedule_path.stat().st_size
        b0_research.PINNED_SCHEDULE_SOURCE["sha256"] = b0_research.sha256_file(schedule_path)
        research.PINNED_TEAM_OFFENSE_SOURCE["bytes"] = offense_path.stat().st_size
        research.PINNED_TEAM_OFFENSE_SOURCE["sha256"] = research.sha256_file(offense_path)
        research.PINNED_PBP_PLAY_SOURCE["bytes"] = plays_path.stat().st_size
        research.PINNED_PBP_PLAY_SOURCE["sha256"] = research.sha256_file(plays_path)

        def _restore():
            b0_research.PINNED_SCHEDULE_SOURCE.clear()
            b0_research.PINNED_SCHEDULE_SOURCE.update(original_schedule)
            research.PINNED_TEAM_OFFENSE_SOURCE.clear()
            research.PINNED_TEAM_OFFENSE_SOURCE.update(original_offense)
            research.PINNED_PBP_PLAY_SOURCE.clear()
            research.PINNED_PBP_PLAY_SOURCE.update(original_plays)

        self.addCleanup(_restore)
        return research.run_research(schedule_path, offense_path, plays_path)

    def test_end_to_end_run_is_reproducible_and_well_formed(self):
        first = self._pin_and_run()
        second = self._pin_and_run()
        # Same pinned inputs must reproduce identical numeric results.
        self.assertEqual(
            first["evaluation_vs_b0"]["development_2000_2019"],
            second["evaluation_vs_b0"]["development_2000_2019"],
        )
        self.assertIn(
            first["status"],
            ("RESEARCH_CHALLENGER_GATE_PASSED_PENDING_REVIEW", "RESEARCH_CHALLENGER_REJECTED"),
        )
        self.assertIn("promotion_gate", first)
        self.assertIn("margin_features", first)
        self.assertIn("total_features", first)
        self.assertFalse(any("market" in name for name in first["margin_features"]))


if __name__ == "__main__":
    unittest.main()
