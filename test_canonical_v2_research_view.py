#!/usr/bin/env python3
"""Adversarial tests for canonical-v2 date-quarantined research views."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from backtest import canonical_v2_research_certify as rcert
from backtest import canonical_v2_research_view as rview


GEN = "a" * 40
PARENT = "b" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_blob(root, payload):
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    digest = sha(raw)
    path = os.path.join(root, "http_blobs", digest + ".gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as raw_file:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_file, mtime=0
        ) as gz:
            gz.write(raw)
    return digest, len(raw)


class Fixture:
    DAYS = ["2025-08-19", "2025-08-20", "2025-08-21"]

    def __init__(self, root):
        self.root = root

    def build(self):
        os.makedirs(os.path.join(self.root, "date_metadata"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "http_blobs"), exist_ok=True)

        rows = [
            {"date": self.DAYS[0], "game_pk": 1, "player_id": 10,
             "prop_type": "hits", "line": 0.5, "outcome": 1},
            {"date": self.DAYS[1], "game_pk": 2, "player_id": 20,
             "prop_type": "hits", "line": 0.5, "outcome": 0},
            {"date": self.DAYS[2], "game_pk": 3, "player_id": 30,
             "prop_type": "hits", "line": 0.5, "outcome": 1},
        ]
        rows_raw = b"".join(
            (
                json.dumps(r, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode()
            for r in rows
        )
        with open(os.path.join(self.root, "rows.jsonl"), "wb") as handle:
            handle.write(rows_raw)

        for day in self.DAYS:
            ungraded = {}
            if day == self.DAYS[1]:
                ungraded = {
                    "bound Statcast source has no rows for this game": 2
                }
            write_json(
                os.path.join(self.root, "date_metadata", day + ".json"),
                {
                    "date": day,
                    "status": "ok",
                    "row_count": 1,
                    "ungraded_reasons": ungraded,
                },
            )

        ledger_rows = []
        for idx, day in enumerate(self.DAYS, 1):
            game = {
                "gamePk": idx,
                "gameType": "R",
                "officialDate": day,
                "gameDate": day + "T23:00:00Z",
                "status": {"codedGameState": "S", "detailedState": "Scheduled"},
                "teams": {
                    "away": {"team": {"id": 100 + idx, "name": f"A{idx}"}},
                    "home": {"team": {"id": 200 + idx, "name": f"H{idx}"}},
                },
            }
            if day == self.DAYS[1]:
                game["officialDate"] = self.DAYS[0]
                game["resumedFromDate"] = self.DAYS[0]
                game["resumedFrom"] = self.DAYS[0] + "T23:00:00Z"
            digest, n = write_blob(
                self.root,
                {"dates": [{"date": day, "games": [game]}]},
            )
            ledger_rows.append({
                "observed_date": day,
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1/schedule"
                    f"?sportId=1&date={day}"
                ),
                "request_body_sha256": None,
                "status_code": 200,
                "response_sha256": digest,
                "response_bytes": n,
                "exception_type": None,
            })

        # One logical request identity with no successful retry on day 3.
        ledger_rows.extend([
            {
                "observed_date": self.DAYS[2],
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1.1/game/999/feed/live"
                    "?timecode=20250821_225959"
                ),
                "request_body_sha256": None,
                "status_code": 500,
                "response_sha256": None,
                "response_bytes": None,
                "exception_type": None,
            },
            {
                "observed_date": self.DAYS[2],
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1.1/game/999/feed/live"
                    "?timecode=20250821_225959"
                ),
                "request_body_sha256": None,
                "status_code": 500,
                "response_sha256": None,
                "response_bytes": None,
                "exception_type": None,
            },
        ])
        ledger_raw = b"".join(
            (
                json.dumps(r, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode()
            for r in ledger_rows
        )
        ledger_name = "mlb_statsapi_request_ledger.jsonl"
        with open(os.path.join(self.root, ledger_name), "wb") as handle:
            handle.write(ledger_raw)

        report = {
            "verdict": "CANONICAL_V2_CONSOLIDATED",
            "run_id": "fixture-run",
            "requested_date_range": [self.DAYS[0], self.DAYS[-1]],
            "requested_dates": 3,
            "total_rows": 3,
            "assembled_rows_sha256": sha(rows_raw),
            "generation_code_sha": GEN,
            "scientific_parent_sha": PARENT,
            "date_metadata_path": "date_metadata",
            "source_lineage_fingerprint": "f" * 64,
            "source_lineage": [{
                "source": "mlb_statsapi_request_ledger",
                "notes": f"path={ledger_name} bodies=http_blobs/",
            }],
            "http_totals": {"response_body_directory": "http_blobs"},
        }
        report["report_sha256"] = sha(
            json.dumps(
                report, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        write_json(
            os.path.join(self.root, "consolidation_report.json"), report
        )
        return rows


def blocked_parent(extra=None):
    blockers = [
        (
            "source/grader-related ungraded candidates: 2 x "
            "bound Statcast source has no rows for this game"
        ),
        "1 unrecovered StatsAPI request identities",
    ]
    if extra:
        blockers.append(extra)
    return {
        "verdict": "CERTIFICATION BLOCKED",
        "failures": [],
        "blockers": blockers,
        "warnings": ["fixture warning"],
    }


def refresh_manifest(path, mutate):
    manifest = json.load(open(path, encoding="utf-8"))
    mutate(manifest)
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = rcert.sha256_bytes(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    )
    write_json(path, manifest)


class ResearchViewTests(unittest.TestCase):
    def test_whole_date_quarantine_is_preoutcome_and_exact(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as base:
            Fixture(parent).build()
            out = os.path.join(base, "view")
            manifest = rview.materialize(parent, out)
            self.assertEqual(
                manifest["quarantine"]["excluded_dates"],
                ["2025-08-20", "2025-08-21"],
            )
            self.assertEqual(
                manifest["quarantine"]["counts"]["prior_date_resumed_games"],
                1,
            )
            self.assertEqual(
                manifest["quarantine"]["counts"]["unrecovered_statsapi_identities"],
                1,
            )
            kept = [
                json.loads(line)
                for line in open(os.path.join(out, "rows.jsonl"), encoding="utf-8")
                if line.strip()
            ]
            self.assertEqual([r["date"] for r in kept], ["2025-08-19"])

            # Flip every outcome in the parent and prove the quarantine decision
            # itself does not change. (The parent rows hash/report are updated
            # solely so materialize can bind the synthetic parent honestly.)
            rows_path = os.path.join(parent, "rows.jsonl")
            rows = [
                json.loads(line)
                for line in open(rows_path, encoding="utf-8")
                if line.strip()
            ]
            for row in rows:
                row["outcome"] = 1 - row["outcome"]
            raw = b"".join(
                (
                    json.dumps(r, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                for r in rows
            )
            with open(rows_path, "wb") as handle:
                handle.write(raw)
            report_path = os.path.join(parent, "consolidation_report.json")
            report = json.load(open(report_path, encoding="utf-8"))
            report["assembled_rows_sha256"] = sha(raw)
            report.pop("report_sha256", None)
            report["report_sha256"] = sha(
                json.dumps(
                    report, sort_keys=True, separators=(",", ":")
                ).encode()
            )
            write_json(report_path, report)
            self.assertEqual(
                rview.discover_quarantine(parent)["excluded_dates"],
                ["2025-08-20", "2025-08-21"],
            )

    def test_independent_certification_accepts_exact_parent_subset(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as base:
            Fixture(parent).build()
            out = os.path.join(base, "view")
            rview.materialize(parent, out)
            with patch.object(
                rcert.base_cert, "certify", return_value=blocked_parent()
            ):
                result = rcert.certify_research_view(parent, out)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(
                result["independent_quarantine"]["excluded_dates"],
                ["2025-08-20", "2025-08-21"],
            )
            self.assertEqual(result["parent_raw_verdict"], "CERTIFICATION BLOCKED")

    def test_tampered_research_rows_are_not_canonical(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as base:
            Fixture(parent).build()
            out = os.path.join(base, "view")
            rview.materialize(parent, out)
            with open(os.path.join(out, "rows.jsonl"), "ab") as handle:
                handle.write(b" ")
            with patch.object(
                rcert.base_cert, "certify", return_value=blocked_parent()
            ):
                result = rcert.certify_research_view(parent, out)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any(
                "byte-preserving parent subset" in failure
                for failure in result["failures"]
            ))

    def test_manual_extra_excluded_date_is_rejected_even_with_valid_manifest_sha(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as base:
            Fixture(parent).build()
            out = os.path.join(base, "view")
            rview.materialize(parent, out)
            path = os.path.join(out, "research_view_manifest.json")
            refresh_manifest(
                path,
                lambda m: m["quarantine"].update({
                    "excluded_dates": [
                        "2025-08-19", "2025-08-20", "2025-08-21"
                    ]
                }),
            )
            with patch.object(
                rcert.base_cert, "certify", return_value=blocked_parent()
            ):
                result = rcert.certify_research_view(parent, out)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any(
                "excluded dates differ" in failure
                for failure in result["failures"]
            ))

    def test_unrelated_parent_blocker_remains_blocking(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as base:
            Fixture(parent).build()
            out = os.path.join(base, "view")
            rview.materialize(parent, out)
            with patch.object(
                rcert.base_cert,
                "certify",
                return_value=blocked_parent("unrelated scientific blocker"),
            ):
                result = rcert.certify_research_view(parent, out)
            self.assertEqual(result["verdict"], "CERTIFICATION BLOCKED")
            self.assertTrue(any(
                "unresolved parent blocker" in blocker
                for blocker in result["blockers"]
            ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
