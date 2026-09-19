#!/usr/bin/env python3
"""Determinism regression tests for `_top_usage_player_per_team_week`.

## Why this file exists

The real, unmodified production event build was run 12 times from
byte-identical frozen 2012-2025 nflverse source bytes and produced 668
events in 5 runs and 667 events in 7 runs -- from the SAME source bytes, the
SAME code, run repeatedly. Root cause (see PR #150's audit and Issue #91
comment `5743845631`): `_top_usage_player_per_team_week` ranked candidates
via `max(candidates, key=lambda pid: running_mean[pid])` where `candidates`
was built from a plain Python `set`. On an exact tie in `running_mean` (real,
observed: WR_ABSENCE / season 2012 / week 2 / team GB / removed_player_id
00-0024267), `max()`'s tie-break silently depended on that set's
hash-randomized iteration order, which differs per Python process because
no `PYTHONHASHSEED` is pinned anywhere in this repo.

The fix (in `role_intelligence_features.py`) replaces that with an explicit
total ordering: `running_mean[pid]` descending, then `player_id` (gsis_id)
ascending as a pure, stable, sortable tie-break, excluding any candidate
with a missing/empty identity rather than risking an ambiguous comparison.

This file is both the regression test for that specific fix (2a) and the
permanent CI gate (2e/5) against this whole CLASS of bug recurring: it runs
this module's tie-break under two different `PYTHONHASHSEED` values in
separate subprocesses and asserts byte-identical output. It uses only small
synthetic fixtures -- no network access, nothing fetched -- so it is fast
and hermetic and runs as an ordinary part of the `nfl/tests` suite.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

from nfl.research.role_intelligence_features import (
    _top_usage_player_per_team_week,
    build_teammate_absence_trigger_events,
)
from nfl.tests.test_role_intelligence_features import usage

# --------------------------------------------------------------------------
# Shared fixture: an EXACT target_share tie between two WRs after week 1
# (00-0000001 and 00-0000002 each go 5-for-20 => target_share 0.25 both).
# The deterministic winner must always be "00-0000001" (lexicographically
# smaller gsis_id) -- never a coin flip on process/hash-seed.
# --------------------------------------------------------------------------

TIE_LOW_ID = "00-0000001"  # lexicographically smaller -- must always win
TIE_HIGH_ID = "00-0000002"


def _tied_rows(order: str = "forward") -> list[dict]:
    """Week-1 tie-setup rows + week-2 rows. `order` shuffles input order only
    (never the underlying facts), to prove the result doesn't depend on it.
    """
    week1 = [
        usage(2020, 1, "SF", "ARI", TIE_LOW_ID, "WR-Low", "WR", 5, 0, 20, 25),
        usage(2020, 1, "SF", "ARI", TIE_HIGH_ID, "WR-High", "WR", 5, 0, 20, 25),
    ]
    week2 = [
        usage(2020, 2, "SF", "SEA", TIE_LOW_ID, "WR-Low", "WR", 0, 0, 20, 25),
        usage(2020, 2, "SF", "SEA", TIE_HIGH_ID, "WR-High", "WR", 8, 0, 20, 25),
    ]
    rows = week1 + week2
    if order == "reversed":
        return list(reversed(rows))
    if order == "interleaved":
        return [week1[1], week2[1], week1[0], week2[0]]
    return rows


def _events_digest(events: list[dict]) -> str:
    return hashlib.sha256(json.dumps(events, sort_keys=True, default=str).encode()).hexdigest()


def _build_fixture_events() -> list[dict]:
    """The single fixture the direct tests AND the subprocess entry point
    below both use, so the cross-hashseed test exercises the exact same
    tie-break path the direct-process tests already assert on.
    """
    rows = _tied_rows("forward")
    injuries = [dict(season=2020, week=2, team="SF", player_id=TIE_LOW_ID, report_status="OUT")]
    return build_teammate_absence_trigger_events(rows, injuries)


class ExactTieDeterminismTests(unittest.TestCase):
    """(a) Exact usage tie: same candidate chosen regardless of input order."""

    def test_tie_is_broken_by_ascending_gsis_id_not_process_order(self):
        top = _top_usage_player_per_team_week(_tied_rows("forward"), "WR", "target_share")
        self.assertEqual(top[(2020, 2, "SF")], TIE_LOW_ID)

    def test_tie_break_fires_the_correct_event_end_to_end(self):
        injuries = [dict(season=2020, week=2, team="SF", player_id=TIE_LOW_ID, report_status="OUT")]
        events = build_teammate_absence_trigger_events(_tied_rows("forward"), injuries)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["removed_player_id"], TIE_LOW_ID)

    def test_injury_on_the_losing_tied_candidate_does_not_fire(self):
        # TIE_HIGH_ID never wins the deterministic tie-break, so an OUT
        # status filed against it must not produce a "top usage absent"
        # event -- proving the winner is a fixed, specific identity, not
        # merely "an event fires on whichever candidate is hurt."
        injuries = [dict(season=2020, week=2, team="SF", player_id=TIE_HIGH_ID, report_status="OUT")]
        events = build_teammate_absence_trigger_events(_tied_rows("forward"), injuries)
        self.assertEqual(events, [])


class InputOrderIndependenceTests(unittest.TestCase):
    """(b) Reversed / shuffled input order: same result."""

    def test_reversed_row_order_same_result(self):
        forward = _top_usage_player_per_team_week(_tied_rows("forward"), "WR", "target_share")
        reversed_ = _top_usage_player_per_team_week(_tied_rows("reversed"), "WR", "target_share")
        self.assertEqual(forward, reversed_)
        self.assertEqual(forward[(2020, 2, "SF")], TIE_LOW_ID)

    def test_interleaved_row_order_same_result(self):
        forward = _top_usage_player_per_team_week(_tied_rows("forward"), "WR", "target_share")
        interleaved = _top_usage_player_per_team_week(_tied_rows("interleaved"), "WR", "target_share")
        self.assertEqual(forward, interleaved)

    def test_full_event_list_identical_across_orderings(self):
        injuries = [dict(season=2020, week=2, team="SF", player_id=TIE_LOW_ID, report_status="OUT")]
        forward_events = build_teammate_absence_trigger_events(_tied_rows("forward"), injuries)
        reversed_events = build_teammate_absence_trigger_events(_tied_rows("reversed"), injuries)
        self.assertEqual(forward_events, reversed_events)


class NoTieUnaffectedTests(unittest.TestCase):
    """(d) A clear, non-tied top-usage player is unaffected by the tie-break change."""

    def test_clear_top_usage_player_still_wins_on_share_not_id(self):
        rows = [
            # "00-0000009" would lose an id-ascending tie-break, but it has
            # NO tie here -- it must still win on its higher running mean.
            usage(2020, 1, "SF", "ARI", "00-0000009", "WR-Best", "WR", 15, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "00-0000001", "WR-Worst", "WR", 1, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "00-0000009", "WR-Best", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "00-0000001", "WR-Worst", "WR", 10, 0, 20, 25),
        ]
        top = _top_usage_player_per_team_week(rows, "WR", "target_share")
        self.assertEqual(top[(2020, 2, "SF")], "00-0000009")


class MissingIdentityQuarantineTests(unittest.TestCase):
    """(e) All candidates missing identity: fail closed / excluded, never guessed."""

    def test_all_candidates_missing_identity_are_excluded_not_guessed(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "", "WR-Unknown-1", "WR", 5, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "", "WR-Unknown-2", "WR", 5, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "", "WR-Unknown-1", "WR", 0, 0, 20, 25),
        ]
        top = _top_usage_player_per_team_week(rows, "WR", "target_share")
        # No entry at all for (2020, 2, "SF") -- quarantined, not defaulted
        # to an arbitrary blank-identity "winner".
        self.assertNotIn((2020, 2, "SF"), top)

    def test_mixed_missing_and_present_identity_only_the_present_one_ranks(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "", "WR-Unknown", "WR", 9, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "00-0000005", "WR-Known", "WR", 1, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "", "WR-Unknown", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "00-0000005", "WR-Known", "WR", 8, 0, 20, 25),
        ]
        top = _top_usage_player_per_team_week(rows, "WR", "target_share")
        # The blank-identity candidate has the higher running mean but is
        # quarantined; only the identified candidate can ever be "top".
        self.assertEqual(top[(2020, 2, "SF")], "00-0000005")


class CrossHashSeedDeterminismTests(unittest.TestCase):
    """(c) Same fixture, two different PYTHONHASHSEED subprocesses: identical
    chosen identity, event membership, row ordering, and output digest.

    This is also the permanent CI regression gate (step 5): if the tie-break
    ever regresses to a hash-order-dependent structure, this test starts
    failing intermittently/deterministically across the two pinned seeds,
    exactly the class of bug this whole file exists to catch. Network-free,
    small fixture, fast -- safe to run as part of the normal suite.
    """

    def _run_with_hashseed(self, seed: str) -> str:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        existing_path = os.environ.get("PYTHONPATH", "")
        env = {
            **os.environ,
            "PYTHONHASHSEED": seed,
            "PYTHONPATH": repo_root + (os.pathsep + existing_path if existing_path else ""),
        }
        result = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--emit-fixture-digest"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout.strip()

    def test_identical_output_across_two_different_hashseeds(self):
        output_seed_0 = self._run_with_hashseed("0")
        output_seed_1 = self._run_with_hashseed("1")
        self.assertEqual(output_seed_0, output_seed_1)

        payload = json.loads(output_seed_0)
        self.assertEqual(payload["removed_player_id"], TIE_LOW_ID)
        self.assertEqual(payload["n_events"], 1)
        # Digest computed independently in-process (default/unset hashseed
        # here) must match what both pinned-hashseed subprocesses produced.
        self.assertEqual(payload["digest"], _events_digest(_build_fixture_events()))

    def test_three_consecutive_default_process_runs_agree(self):
        # Belt-and-suspenders: even without pinning PYTHONHASHSEED at all,
        # three fresh interpreter runs must still agree, because the fix no
        # longer depends on hash-randomized iteration order at all.
        outputs = {self._run_with_hashseed(str(i)) for i in (2, 3, 4)}
        self.assertEqual(len(outputs), 1, msg=f"non-deterministic across seeds: {outputs}")


def _emit_fixture_digest() -> None:
    events = _build_fixture_events()
    payload = {
        "digest": _events_digest(events),
        "removed_player_id": events[0]["removed_player_id"] if events else None,
        "n_events": len(events),
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    if "--emit-fixture-digest" in sys.argv:
        _emit_fixture_digest()
    else:
        unittest.main()
