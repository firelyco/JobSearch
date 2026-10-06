"""Tests for src/adapters/google.py — HTTP mocked, no network.

Run with: python -m unittest tests.test_google
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from unittest.mock import patch, MagicMock

from src.adapters import google


def _row(job_id, title):
    row = [None] * 21
    row[0], row[1] = job_id, title
    row[3] = [None, "<ul><li>Lead programs</li></ul>"]
    row[4] = [None, "<h3>Minimum qualifications</h3>"]
    row[9] = [["Sunnyvale, CA, USA", ["Sunnyvale, CA, USA"], "Sunnyvale", None, "CA", "US"]]
    row[10] = [None, "<p>About the job</p>"]
    row[12] = [1790343670, 766000000]
    row[20] = 3
    return row


def _page(rows, total):
    data = json.dumps([rows, None, total, 20])
    m = MagicMock(status_code=200)
    m.text = ("<script>AF_initDataCallback({key: 'ds:0', hash: '1', data:[], sideChannel: {}});</script>"
              f"<script>AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});</script>")
    return m


class TestGoogle(unittest.TestCase):

    def test_parses_rows_and_sends_level_filters(self):
        with patch("src.adapters.google.requests.get", return_value=_page([_row("123", "Technical Program Manager, Networking")], 1)) as m:
            jobs = google.fetch({"query": '"program manager"', "levels": ["ADVANCED", "DIRECTOR_PLUS"]})
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual((j["source"], j["company"], j["id"]), ("google", "google", "123"))
        self.assertEqual(j["location"], "Sunnyvale, CA, USA")
        self.assertTrue(j["posted_at"].startswith("2026-"))
        params = m.call_args.kwargs["params"]
        self.assertIn(("target_level", "ADVANCED"), params)
        self.assertIn(("target_level", "DIRECTOR_PLUS"), params)

    def test_pages_until_total(self):
        p1 = _page([_row(str(i), f"TPM {i}") for i in range(20)], 25)
        p2 = _page([_row(str(i), f"TPM {i}") for i in range(20, 25)], 25)
        with patch("src.adapters.google.requests.get", side_effect=[p1, p2]) as m:
            jobs = google.fetch('"program manager"')
        self.assertEqual(len(jobs), 25)
        self.assertEqual(m.call_count, 2)

    def test_layout_change_returns_empty(self):
        m = MagicMock(status_code=200, text="<html></html>")
        with patch("src.adapters.google.requests.get", return_value=m):
            self.assertEqual(google.fetch('"x"'), [])

    def test_detail_finds_row_by_id(self):
        page = _page([_row("999", "Other"), _row("123", "TPM")], 2)
        with patch("src.adapters.google.requests.get", return_value=page):
            text = google.fetch_detail({"id": "123"})
        self.assertIn("About the job", text)
        self.assertIn("Lead programs", text)
        self.assertIn("Minimum qualifications", text)


if __name__ == "__main__":
    unittest.main()
