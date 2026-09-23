#!/usr/bin/env python3
"""Integration coverage for the role-adjusted-challenger wiring added to
`nfl-live-receptions-shadow-board.yml` (Mission 2, Workstream A).

Extracts the REAL blocks from the workflow YAML (via the same PyYAML
block-scalar method used throughout this project -- not a hand-copied
reimplementation) and executes them with realistic fixture data, driving
the actual detection logic (real target-share ranking, real official
inactive-report absence detection), the actual per-candidate injection
into the live scoring loop, and the actual aggregate atomic-write lane --
using the REAL imported functions from `receptions_role_adjusted_challenger`
and `receptions_shadow`, not mocks of them.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

import yaml

from nfl.research.receptions_role_adjusted_challenger import (
    FROZEN_COMMITTEE_MODEL,
    build_role_adjusted_challenger_record,
)
from nfl.research.receptions_shadow import current_b0_projection, score_shadow_candidate
from nfl.normalize.pregame_availability import evaluate_candidate
from nfl.prospective.receptions_challenger_snapshot import write_challenger_evidence_atomically

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "nfl-live-receptions-shadow-board.yml"
STEP_NAME = "Build live Sunday receptions shadow board without publishing"

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5


def _load_script() -> str:
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in doc["jobs"]["board"]["steps"]:
        if step.get("name") == STEP_NAME:
            return step["run"]
    raise AssertionError(f"could not find step {STEP_NAME!r}")


def _extract(script: str, start_marker: str, end_marker: str) -> str:
    start = script.index(start_marker)
    end = script.index(end_marker)
    return script[start:end]


def _make_roster():
    return [
        {"gsis_id": "00-W1", "team": "KC", "position": "WR", "status": "ACT", "full_name": "Top WR"},
        {"gsis_id": "00-W2", "team": "KC", "position": "WR", "status": "ACT", "full_name": "Candidate WR"},
        {"gsis_id": "00-W3", "team": "KC", "position": "WR", "status": "ACT", "full_name": "Other WR"},
    ]


def _make_prior_by_player_and_team_targets():
    prior_by_player = defaultdict(list)
    team_week_targets = defaultdict(float)
    # 5 real prior weeks: W1 gets 8 targets/wk (top usage), W2 gets 4, W3
    # gets 2, plus 6 "elsewhere" targets/wk so team_week_targets = 20.
    for week in range(1, 6):
        prior_by_player["00-W1"].append({
            "season": 2025, "week": week, "season_type": "REG", "targets": 8.0, "receptions": 6.0, "team": "KC",
        })
        prior_by_player["00-W2"].append({
            "season": 2025, "week": week, "season_type": "REG", "targets": 4.0, "receptions": 3.0, "team": "KC",
        })
        prior_by_player["00-W3"].append({
            "season": 2025, "week": week, "season_type": "REG", "targets": 2.0, "receptions": 1.0, "team": "KC",
        })
        team_week_targets[(2025, week, "KC")] = 20.0
    return prior_by_player, team_week_targets


def _make_bound_row():
    return {
        "gsis_id": "00-W2", "team": "KC", "player_name": "Candidate WR",
        "event_id": "EVT1", "market_id": "MKT1", "line": 3.5,
        "over_odds": -115, "under_odds": -105,
        "event_open_date": "2026-09-27T17:00:00.000Z", "captured_at": "2026-09-26T00:00:00Z",
        "binding_status": "BOUND",
        "event_home_team": "KC", "event_away_team": "OPP",
    }


def _make_bound_reports(*, absent_gsis_id="00-W1"):
    return [{
        "report_published_at": "2026-09-27T15:00:00Z",
        "report_observed_at": "2026-09-27T16:00:00Z",
        "teams": [
            {
                "team": "KC",
                "players": [{"binding_status": "BOUND", "gsis_id": absent_gsis_id}],
            },
            {"team": "OPP", "players": []},
        ],
    }]


class DetectionBlockTests(unittest.TestCase):
    def setUp(self):
        self.script = _load_script()
        self.block = _extract(
            self.script, "inactive_gsis_ids = {",
            "# ---------- projection, score, availability, quarantine ----------",
        )
        self.assertIn("team_removed_player", self.block)

    def _run(self, roster, bound, bound_reports, prior_by_player, team_week_targets):
        namespace = {
            "statistics": statistics, "defaultdict": defaultdict,
            "roster": roster, "bound": bound, "bound_reports": bound_reports,
            "prior_by_player": prior_by_player, "team_week_targets": team_week_targets,
        }
        exec(compile(self.block, str(WORKFLOW_PATH), "exec"), namespace)
        return namespace

    def test_real_absence_event_is_detected_for_the_correct_team(self):
        roster = _make_roster()
        prior_by_player, team_week_targets = _make_prior_by_player_and_team_targets()
        ns = self._run(roster, [_make_bound_row()], _make_bound_reports(), prior_by_player, team_week_targets)
        self.assertEqual(ns["team_removed_player"], {"KC": "00-W1"})
        teammates = {t[0] for t in ns["team_teammates_for_role_adjustment"]["KC"]}
        self.assertEqual(teammates, {"00-W2", "00-W3"})

    def test_no_event_when_top_usage_wr_is_not_reported_inactive(self):
        roster = _make_roster()
        prior_by_player, team_week_targets = _make_prior_by_player_and_team_targets()
        # Official report marks a DIFFERENT (non-top-usage) player inactive.
        ns = self._run(roster, [_make_bound_row()], _make_bound_reports(absent_gsis_id="00-W3"), prior_by_player, team_week_targets)
        self.assertEqual(ns["team_removed_player"], {})

    def test_no_event_when_no_official_report_at_all(self):
        roster = _make_roster()
        prior_by_player, team_week_targets = _make_prior_by_player_and_team_targets()
        ns = self._run(roster, [_make_bound_row()], [], prior_by_player, team_week_targets)
        self.assertEqual(ns["team_removed_player"], {})

    def test_no_event_when_no_real_prior_target_share_history_exists(self):
        roster = _make_roster()
        ns = self._run(roster, [_make_bound_row()], _make_bound_reports(), defaultdict(list), defaultdict(float))
        self.assertEqual(ns["team_removed_player"], {})


class PerCandidateInjectionTests(unittest.TestCase):
    """Drives the REAL `for row in bound:` loop body (from the workflow
    YAML) with realistic fixtures and the REAL imported scoring/challenger
    functions, verifying the role-adjusted lane is populated correctly and
    never disturbs the primary snapshot_records list."""

    def setUp(self):
        self.script = _load_script()
        self.detection_block = _extract(
            self.script, "inactive_gsis_ids = {",
            "# ---------- projection, score, availability, quarantine ----------",
        )
        self.loop_block = _extract(self.script, "for row in bound:", "sealed_at = utcnow()")

    def _namespace(self, *, force_role_adjustment_failure=False):
        from nfl.research.receptions_frozen_challenger import (
            FROZEN_NB_FIT, compare_b0_vs_frozen_challenger,
        )
        from nfl.prospective.receptions_challenger_snapshot import build_challenger_snapshot_record

        roster = _make_roster()
        prior_by_player, team_week_targets = _make_prior_by_player_and_team_targets()
        bound = [_make_bound_row()]
        bound_reports = _make_bound_reports()

        ns = {
            "statistics": statistics, "defaultdict": defaultdict,
            "roster": roster, "bound": bound, "bound_reports": bound_reports,
            "prior_by_player": prior_by_player, "team_week_targets": team_week_targets,
        }
        exec(compile(self.detection_block, str(WORKFLOW_PATH), "exec"), ns)

        ns.update({
            "residuals": REAL_SHAPE_RESIDUALS,
            "snapshot_records": [],
            "challenger_snapshot_records": [],
            "challenger_build_failures": [],
            "role_adjusted_records": [],
            "role_adjusted_build_failures": [],
            "excluded_unbound": [],
            "quarantine_counts": Counter(), "history_counts": defaultdict(int),
            "evaluate_candidate": evaluate_candidate,
            "current_b0_projection": current_b0_projection,
            "score_shadow_candidate": score_shadow_candidate,
            "compare_b0_vs_frozen_challenger": compare_b0_vs_frozen_challenger,
            "build_challenger_snapshot_record": build_challenger_snapshot_record,
            "CHALLENGER_MODEL_VERSION": f"NEGATIVE_BINOMIAL_POOLED_V1_alpha_{FROZEN_NB_FIT['alpha']}",
            "FROZEN_COMMITTEE_MODEL": FROZEN_COMMITTEE_MODEL,
            "build_role_adjusted_challenger_record": (
                mock.Mock(side_effect=RuntimeError("forced role-adjustment failure"))
                if force_role_adjustment_failure else build_role_adjusted_challenger_record
            ),
            "Counter": __import__("collections").Counter,
        })
        # Real prior history (last 5 receptions: 6/6/6/6/6 = 6.0 projection)
        # for the candidate WR himself, satisfying current_b0_projection's
        # real MIN_HISTORY=3 requirement -- separate from the WR-role
        # target-share history used only for the redistribution feature.
        ns["prior_by_player"]["00-W2"] = [
            {"season": 2025, "week": w, "season_type": "REG", "targets": 4.0, "receptions": 3.0, "team": "KC"}
            for w in range(1, 6)
        ]
        return ns

    def test_role_adjusted_record_built_for_the_real_absence_event(self):
        ns = self._namespace()
        exec(compile(self.loop_block, str(WORKFLOW_PATH), "exec"), ns)
        self.assertEqual(len(ns["snapshot_records"]), 1)
        self.assertEqual(len(ns["role_adjusted_records"]), 1)
        record = ns["role_adjusted_records"][0]
        self.assertEqual(record["candidate_player_id"], "00-W2")
        self.assertEqual(record["removed_player_id"], "00-W1")
        self.assertGreater(record["adjusted_projection"], record["b0_projection"])
        self.assertEqual(record["removed_player_availability_source"], "NFLVERSE_OFFICIAL_INACTIVE_REPORT")

    def test_no_role_adjusted_record_when_removed_player_is_the_candidate_himself(self):
        ns = self._namespace()
        ns["bound"] = [{**_make_bound_row(), "gsis_id": "00-W1"}]  # candidate IS the removed player
        ns["team_removed_player"] = {"KC": "00-W1"}
        exec(compile(self.loop_block, str(WORKFLOW_PATH), "exec"), ns)
        self.assertEqual(len(ns["role_adjusted_records"]), 0)

    def test_role_adjustment_failure_never_disturbs_primary_board_or_other_challenger(self):
        ns = self._namespace(force_role_adjustment_failure=True)
        exec(compile(self.loop_block, str(WORKFLOW_PATH), "exec"), ns)
        # Primary record and the SEPARATE frozen-NB challenger lane both
        # still built successfully -- only the role-adjusted lane failed.
        self.assertEqual(len(ns["snapshot_records"]), 1)
        self.assertEqual(ns["snapshot_records"][0]["decision_status"], "SHADOW_ONLY")
        self.assertEqual(len(ns["challenger_snapshot_records"]), 1)
        self.assertEqual(len(ns["role_adjusted_records"]), 0)
        self.assertEqual(len(ns["role_adjusted_build_failures"]), 1)
        self.assertIn("forced role-adjustment failure", ns["role_adjusted_build_failures"][0]["failure_reason"])

    def test_no_qualifying_opportunity_leaves_role_adjusted_records_empty_not_fabricated(self):
        ns = self._namespace()
        ns["bound_reports"] = []
        ns["team_removed_player"] = {}
        ns["team_teammates_for_role_adjustment"] = {}
        exec(compile(self.loop_block, str(WORKFLOW_PATH), "exec"), ns)
        self.assertEqual(len(ns["role_adjusted_records"]), 0)
        self.assertEqual(len(ns["role_adjusted_build_failures"]), 0)


class AggregateWriteTests(unittest.TestCase):
    def setUp(self):
        self.script = _load_script()
        self.block = _extract(
            self.script, "lane from being built and written.\ntry:", "board = {",
        )
        self.block = self.block[self.block.index("try:"):]
        self.assertIn("write_challenger_evidence_atomically", self.block)

    def _run(self, evidence_root, *, force_failure=False):
        namespace = {
            "os": os, "EVIDENCE_ROOT": evidence_root,
            "sealed_at": "2026-09-26T01:00:00Z", "TARGET_DATE": "2026-09-27",
            "FROZEN_COMMITTEE_MODEL": FROZEN_COMMITTEE_MODEL,
            "team_removed_player": {"KC": "00-W1"},
            "role_adjusted_records": [{"candidate_player_id": "00-W2"}],
            "role_adjusted_build_failures": [],
            "write_challenger_evidence_atomically": (
                mock.Mock(side_effect=RuntimeError("forced aggregate write failure"))
                if force_failure else write_challenger_evidence_atomically
            ),
        }
        with mock.patch.dict(os.environ, {"FULL_COUNT_CODE_SHA": "deadbeef"}):
            exec(compile(self.block + "\nreached_next_statement = True\n", str(WORKFLOW_PATH), "exec"), namespace)
        return namespace

    def test_clean_run_writes_a_readable_evidence_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            ns = self._run(evidence_root)
            path = evidence_root / "nfl-receptions-role-adjusted-comparison.json"
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data["record_count"], 1)
            self.assertIn("did NOT beat", data["model_held_out_finding"])
            self.assertTrue(ns.get("reached_next_statement"))

    def test_forced_write_failure_is_contained_and_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            ns = self._run(evidence_root, force_failure=True)
            self.assertEqual(list(evidence_root.iterdir()), [])
            self.assertEqual(len(ns["role_adjusted_build_failures"]), 1)
            self.assertIn("aggregate role-adjusted seal/write failed", ns["role_adjusted_build_failures"][0]["failure_reason"])
            self.assertTrue(ns.get("reached_next_statement"))


if __name__ == "__main__":
    unittest.main()
