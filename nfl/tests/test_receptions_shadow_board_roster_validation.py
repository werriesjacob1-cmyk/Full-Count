#!/usr/bin/env python3
"""Regression coverage for the live-roster schema/sanity validation in
`nfl-live-receptions-shadow-board.yml`, replacing an exact byte/sha256 pin
that required a manual re-pin PR after every real roster transaction (real
drift 3 times in ~30 hours: 2026-09-19, 2026-09-20, 2026-09-22, each
blocking the workflow entirely until a human noticed and re-pinned).

Extracts the REAL block from the workflow YAML (via the same PyYAML
block-scalar method used throughout this session, not a hand-copied
reimplementation) and executes it with a fake `requests`-shaped session
serving real and deliberately-corrupted roster payloads.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nfl-live-receptions-shadow-board.yml"
STEP_NAME = "Build live Sunday receptions shadow board without publishing"

REAL_ROSTER_COLUMNS = [
    "season", "team", "position", "depth_chart_position", "jersey_number",
    "status", "full_name", "first_name", "last_name", "birth_date", "height",
    "weight", "college", "gsis_id", "espn_id", "sportradar_id", "yahoo_id",
    "rotowire_id", "pff_id", "pfr_id", "fantasy_data_id", "sleeper_id",
    "years_exp", "headshot_url", "ngs_position", "week", "game_type",
    "status_description_abbr", "football_name", "esb_id", "gsis_it_id",
    "smart_id", "entry_year", "rookie_year", "draft_club", "draft_number",
]
TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN",
    "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
]


def _load_capture_script() -> str:
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in doc["jobs"]["board"]["steps"]:
        if step.get("name") == STEP_NAME:
            return step["run"]
    raise AssertionError(f"could not find step {STEP_NAME!r}")


def _extract_roster_validation_block(script: str) -> str:
    start = script.index("ROSTER_REQUIRED_COLUMNS")
    end = script.index("# ---------- historical B0 calibration substrate")
    return script[start:end]


def _make_roster_csv(*, n_per_team: int = 90, teams=TEAMS, columns=REAL_ROSTER_COLUMNS) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    gsis_counter = 0
    for team in teams:
        for _ in range(n_per_team):
            gsis_counter += 1
            row = {c: "" for c in columns}
            row.update({
                c: v for c, v in {
                    "season": "2026", "team": team, "position": "WR",
                    "status": "ACT", "full_name": f"Player {gsis_counter}",
                    "gsis_id": f"00-{gsis_counter:07d}", "esb_id": f"ESB{gsis_counter}",
                }.items() if c in row
            })
            writer.writerow(row)
    return buf.getvalue().encode("utf-8")


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("utf-8")

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, content: bytes):
        self._content = content

    def get(self, url, timeout=None):
        return FakeResponse(self._content)


class RosterValidationBlockTests(unittest.TestCase):
    def setUp(self):
        self.script = _load_capture_script()
        self.block = _extract_roster_validation_block(self.script)
        self.assertIn("ROSTER_REQUIRED_COLUMNS", self.block)
        self.assertIn("MIN_ROSTER_ROWS", self.block)

    def _run_block(self, content: bytes, evidence_root: Path):
        (evidence_root / "model_sources").mkdir(parents=True, exist_ok=True)
        namespace = {
            "hashlib": hashlib, "csv": csv, "io": io,
            "session": FakeSession(content),
            "ROSTER_URL": "https://example.invalid/roster_2026.csv",
            "EVIDENCE_ROOT": evidence_root,
        }
        exec(compile(self.block, str(WORKFLOW_PATH), "exec"), namespace)
        return namespace

    def test_real_shaped_roster_passes_and_is_captured(self):
        content = _make_roster_csv()
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            namespace = self._run_block(content, evidence_root)
            self.assertEqual(namespace["roster_sha"], hashlib.sha256(content).hexdigest())
            self.assertEqual(len(namespace["roster"]), 90 * 32)
            saved = evidence_root / "model_sources" / "roster_2026.csv"
            self.assertTrue(saved.exists())
            self.assertEqual(saved.read_bytes(), content)

    def test_empty_roster_fails_closed(self):
        content = ",".join(REAL_ROSTER_COLUMNS).encode("utf-8") + b"\n"  # header only
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self._run_block(content, Path(tmp))
            self.assertIn("empty", str(ctx.exception))

    def test_schema_break_missing_required_column_fails_closed(self):
        # Mirrors the real, documented depth_charts 2025 schema break --
        # nflverse assets DO sometimes change shape without warning.
        broken_columns = [c for c in REAL_ROSTER_COLUMNS if c != "gsis_id"]
        content = _make_roster_csv(columns=broken_columns)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self._run_block(content, Path(tmp))
            self.assertIn("schema break", str(ctx.exception))
            self.assertIn("gsis_id", str(ctx.exception))

    def test_truncated_roster_below_min_rows_fails_closed(self):
        content = _make_roster_csv(n_per_team=1)  # 32 rows, well under MIN_ROSTER_ROWS
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self._run_block(content, Path(tmp))
            self.assertIn("row count", str(ctx.exception))

    def test_bloated_roster_above_max_rows_fails_closed(self):
        content = _make_roster_csv(n_per_team=200)  # 6400 rows, well over MAX_ROSTER_ROWS
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self._run_block(content, Path(tmp))
            self.assertIn("row count", str(ctx.exception))

    def test_single_team_roster_fails_closed_on_team_coverage(self):
        # Real column/row-count bounds could theoretically pass for a
        # single bloated team's rows (a plausible truncated-by-team-filter
        # failure mode) -- the team-coverage check catches this distinctly.
        content = _make_roster_csv(n_per_team=1600, teams=["KC"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self._run_block(content, Path(tmp))
            self.assertIn("teams", str(ctx.exception))

    def test_no_longer_fails_on_a_legitimate_daily_transaction_drift(self):
        # The exact real-world case that motivated this fix: two real
        # roster snapshots differing only by legitimate transactions (here,
        # one extra real player on one team) must NOT fail closed just
        # because their bytes/hashes differ from each other.
        content_a = _make_roster_csv(n_per_team=90)
        content_b = _make_roster_csv(n_per_team=91)  # one more real transaction
        self.assertNotEqual(
            hashlib.sha256(content_a).hexdigest(), hashlib.sha256(content_b).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp_a:
            self._run_block(content_a, Path(tmp_a))  # must not raise
        with tempfile.TemporaryDirectory() as tmp_b:
            self._run_block(content_b, Path(tmp_b))  # must not raise


if __name__ == "__main__":
    unittest.main()
