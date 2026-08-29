#!/usr/bin/env python3
"""Tests for canonical-v2 HTTP provenance instrumentation."""
from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest

import requests

from backtest import http_provenance as hp


def fake_response(url, body=b'{"ok":true}', status=200):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = "application/json"
    req = requests.Request(
        "GET",
        url,
        headers={
            "User-Agent": "test-agent",
            "Accept": "application/json",
        },
    ).prepare()
    response.request = req
    response.url = req.url
    return response


class LedgerTests(unittest.TestCase):
    def test_finish_binds_response_content_and_archives_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = hp.ResponseLedger(tmp, archive_bodies=True)
            ledger.start_date("2026-05-01")
            response = fake_response(
                "https://statsapi.mlb.com/api/v1/schedule?sportId=1",
                b'{"dates":[1]}',
            )
            ledger.record_response(response)
            summary = ledger.finish_date("2026-05-01")

            self.assertEqual(summary["request_count"], 1)
            self.assertEqual(summary["success_2xx"], 1)
            self.assertEqual(summary["exceptions"], 0)
            self.assertTrue(summary["ledger_file_sha256"])
            self.assertTrue(summary["logical_fingerprint"])

            ledger_path = os.path.join(tmp, summary["ledger_file"])
            entry = json.loads(open(ledger_path, encoding="utf-8").readline())
            self.assertEqual(
                entry["response_sha256"],
                hp._sha256(b'{"dates":[1]}'),
            )
            blob = os.path.join(tmp, entry["archived_body"])
            with gzip.open(blob, "rb") as handle:
                self.assertEqual(handle.read(), b'{"dates":[1]}')

    def test_logical_fingerprint_ignores_thread_arrival_order(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            r1 = fake_response(
                "https://statsapi.mlb.com/api/v1/teams?sportId=1",
                b'{"teams":[1]}',
            )
            r2 = fake_response(
                "https://statsapi.mlb.com/api/v1/schedule?date=2026-05-01",
                b'{"dates":[2]}',
            )

            la = hp.ResponseLedger(a)
            la.start_date("2026-05-01")
            la.record_response(r1)
            la.record_response(r2)
            sa = la.finish_date("2026-05-01")

            lb = hp.ResponseLedger(b)
            lb.start_date("2026-05-01")
            lb.record_response(r2)
            lb.record_response(r1)
            sb = lb.finish_date("2026-05-01")

            self.assertEqual(
                sa["logical_fingerprint"],
                sb["logical_fingerprint"],
            )
            self.assertNotEqual(sa["ledger_file_sha256"], sb["ledger_file_sha256"])

    def test_strict_firewall_blocks_unapproved_host_before_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = hp.ResponseLedger(
                tmp,
                strict_host_firewall=True,
            )
            ledger.start_date("2026-05-01")
            with self.assertRaises(hp.HttpProvenanceError):
                ledger.assert_request_allowed(
                    "GET",
                    "https://www.rotowire.com/baseball/daily-lineups.php",
                    {},
                )
            # Allowed historical source still passes.
            ident = ledger.assert_request_allowed(
                "GET",
                "https://statsapi.mlb.com/api/v1/schedule",
                {"params": {"date": "2026-05-01"}},
            )
            self.assertIn("statsapi.mlb.com", ident["url"])
            ledger.abort_date("2026-05-01", "test complete")

    def test_non_allowlisted_hosts_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = hp.ResponseLedger(tmp)
            ledger.start_date("2026-05-01")
            ledger.record_response(
                fake_response("https://example.com/anything", b"x")
            )
            summary = ledger.finish_date("2026-05-01")
            self.assertEqual(summary["request_count"], 0)

    def test_exception_is_scientific_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = hp.ResponseLedger(tmp)
            ledger.start_date("2026-05-01")
            ledger.record_exception(
                "GET",
                "https://statsapi.mlb.com/api/v1/schedule",
                {"params": {"date": "2026-05-01"}},
                requests.ConnectionError("boom"),
            )
            summary = ledger.finish_date("2026-05-01")
            self.assertEqual(summary["exceptions"], 1)
            entry = json.loads(
                open(
                    os.path.join(tmp, summary["ledger_file"]),
                    encoding="utf-8",
                ).readline()
            )
            self.assertEqual(entry["exception_type"], "ConnectionError")
            self.assertIn("date=2026-05-01", entry["url"])


class HookTransparencyTests(unittest.TestCase):
    def test_hook_returns_exact_original_response_object(self):
        real = requests.sessions.Session.request
        produced = fake_response(
            "https://statsapi.mlb.com/api/v1/teams?sportId=1",
            b'{"teams":[{"id":1}]}',
        )

        def fake_request(session, method, url, **kwargs):
            return produced

        try:
            hp.uninstall_requests_hook()
            requests.sessions.Session.request = fake_request
            hp.install_requests_hook()
            with tempfile.TemporaryDirectory() as tmp:
                ledger = hp.ResponseLedger(tmp)
                ledger.start_date("2026-05-01")
                hp.set_active_ledger(ledger)
                session = requests.Session()
                got = session.request(
                    "GET",
                    "https://statsapi.mlb.com/api/v1/teams",
                    params={"sportId": 1},
                )
                self.assertIs(got, produced)
                self.assertEqual(got.json(), {"teams": [{"id": 1}]})
                summary = ledger.finish_date("2026-05-01")
                self.assertEqual(summary["request_count"], 1)
        finally:
            hp.set_active_ledger(None)
            hp.uninstall_requests_hook()
            requests.sessions.Session.request = real


if __name__ == "__main__":
    unittest.main(verbosity=2)
