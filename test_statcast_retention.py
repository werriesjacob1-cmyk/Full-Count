#!/usr/bin/env python3
"""Retained Statcast columns must survive the cache/load path.

STATCAST_COLUMNS is applied at engine.py's `keep = [...]` line BEFORE
to_parquet, so this projection IS the source artifact. A field omitted here
is not merely unused -- it is absent from the canonical source vintage, and
recovering it costs a full re-pull and a NEW bound sha256. That is why these
are asserted rather than assumed.

Retention only. Nothing here promotes a column into a score, probability,
threshold or recommendation.
"""
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.engine import STATCAST_COLUMNS

# Verified PRESENT in a real frame (2026-08-20, 2735 pitches, 119 columns).
RETAINED = ("hit_distance_sc", "swing_length", "attack_angle",
            "swing_path_tilt", "attack_direction", "arm_angle", "bat_speed")
# Verified ABSENT from that frame: derived Savant leaderboard metrics.
NOT_IN_PITCH_FRAME = ("squared_up", "blast", "pop_time")


class TestRetention(unittest.TestCase):
    def test_required_fields_are_retained(self):
        for c in RETAINED:
            self.assertIn(c, STATCAST_COLUMNS, f"{c} would be dropped at the pull")

    def test_derived_savant_metrics_are_not_claimed(self):
        """Listing a column that the frame never returns is harmless at
        runtime (keep filters on raw.columns) but is a false claim about
        what the artifact contains."""
        for c in NOT_IN_PITCH_FRAME:
            self.assertNotIn(c, STATCAST_COLUMNS)

    def test_no_duplicates(self):
        self.assertEqual(len(STATCAST_COLUMNS), len(set(STATCAST_COLUMNS)))

    def test_projection_keeps_them_and_survives_a_parquet_round_trip(self):
        """The real keep/write/read path, not a mock."""
        raw = pd.DataFrame({c: [1.0, 2.0] for c in STATCAST_COLUMNS})
        raw["game_date"] = ["2024-05-01", "2024-05-02"]
        raw["extra_unused"] = [9, 9]
        keep = [c for c in STATCAST_COLUMNS if c in raw.columns]
        df = raw[keep].copy()
        self.assertNotIn("extra_unused", df.columns)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.parquet")
            df.to_parquet(p, index=False)
            back = pd.read_parquet(p)
        for c in RETAINED:
            self.assertIn(c, back.columns, f"{c} did not survive the parquet round trip")

    def test_a_missing_optional_column_degrades_gracefully(self):
        """A frame lacking bat tracking (pre-2023) must still project."""
        cols = [c for c in STATCAST_COLUMNS
                if c not in ("swing_length", "attack_angle", "swing_path_tilt",
                             "attack_direction", "arm_angle")]
        raw = pd.DataFrame({c: [1.0] for c in cols})
        keep = [c for c in STATCAST_COLUMNS if c in raw.columns]
        self.assertNotIn("swing_length", keep)
        self.assertEqual(len(raw[keep].copy()), 1)

    def test_hit_distance_unblocks_the_moonshot_requirement(self):
        """mlb_sources.moonshot_rates() needs these four; it degraded to {}
        on every canonical date because one was missing."""
        need = {"batter", "game_pk", "launch_speed", "events", "hit_distance_sc"}
        self.assertTrue(need.issubset(set(STATCAST_COLUMNS)),
                        f"still missing {need - set(STATCAST_COLUMNS)}")

    def test_identity_is_the_file_bytes_not_the_column_list(self):
        """Asserted behaviourally, not by grepping source.

        Retention changes WHICH BYTES the artifact contains -- the projection
        is written to the parquet -- so the new vintage will bind a different
        sha256 than the dead run's. That is expected. What must not change is
        that identity is the file's digest and nothing else.
        """
        from backtest import canonical_durability as cd
        df = pd.DataFrame({c: [1.0, 2.0] for c in STATCAST_COLUMNS})
        df["game_date"] = ["2024-05-01", "2024-05-02"]
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.parquet")
            df.to_parquet(p, index=False)
            ident = cd.statcast_artifact_identity(p)
            self.assertEqual(ident["content_sha256"], cd._sha256_file(p))
            self.assertEqual(len(ident["content_sha256"]), 64)
            # Same bytes -> same identity, on every call.
            self.assertEqual(ident["content_sha256"],
                             cd.statcast_artifact_identity(p)["content_sha256"])

    def test_a_retained_artifact_is_still_reported_usable(self):
        """The required-column intersection must not mark a richer artifact
        unusable now that hit_distance_sc is genuinely retained."""
        from backtest import canonical_durability as cd
        df = pd.DataFrame({c: [1.0, 2.0] for c in STATCAST_COLUMNS})
        df["game_date"] = ["2024-05-01", "2024-05-02"]
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.parquet")
            df.to_parquet(p, index=False)
            self.assertTrue(cd.statcast_artifact_identity(p)["usable"],
                            cd.statcast_artifact_identity(p)["problems"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
