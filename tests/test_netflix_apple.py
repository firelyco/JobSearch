"""Tests for the Netflix (Eightfold) and Apple (page hydration) adapters — HTTP mocked.

Run with: python -m unittest tests.test_netflix_apple
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from unittest.mock import patch, MagicMock

from src.adapters import netflix, apple


def _json_resp(payload, status=200):
    m = MagicMock(status_code=status)
    m.json.return_value = payload
    return m


def _html_resp(loader_data, status=200):
    # Apple embeds JSON as a JS string literal: JSON.parse("<escaped json>")
    literal = json.dumps(json.dumps({"loaderData": loader_data}))
    m = MagicMock(status_code=status)
    m.text = f"<script>window.__staticRouterHydrationData = JSON.parse({literal});</script>"
    return m


class TestNetflix(unittest.TestCase):

    def test_maps_positions_and_pages(self):
        page1 = {"count": 11, "positions": [
            {"id": str(i), "name": f"Technical Program Manager {i}", "location": "Remote, United States",
             "t_create": 1790294400, "canonicalPositionUrl": f"https://explore.jobs.netflix.net/careers/job/{i}"}
            for i in range(10)]}
        page2 = {"count": 11, "positions": [{"id": "10", "name": "TPM 5", "t_create": 0}]}
        with patch("src.adapters.netflix.requests.get", side_effect=[_json_resp(page1), _json_resp(page2)]) as m:
            jobs = netflix.fetch("program manager")
        self.assertEqual(len(jobs), 11)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(m.call_args_list[1].kwargs["params"]["start"], 10)
        j = jobs[0]
        self.assertEqual((j["source"], j["company"], j["id"]), ("netflix", "netflix", "0"))
        self.assertTrue(j["posted_at"].startswith("2026-"))
        self.assertEqual(jobs[10]["posted_at"], "")  # zero epoch -> unknown

    def test_http_error_returns_empty(self):
        with patch("src.adapters.netflix.requests.get", return_value=_json_resp({}, 500)):
            self.assertEqual(netflix.fetch("program manager"), [])

    def test_detail_returns_description(self):
        with patch("src.adapters.netflix.requests.get", return_value=_json_resp({"job_description": "<p>jd</p>"})):
            self.assertEqual(netflix.fetch_detail({"id": "1"}), "<p>jd</p>")


class TestApple(unittest.TestCase):

    def test_parses_hydration_search_results(self):
        search = {"search": {"totalRecords": 1, "searchResults": [{
            "positionId": "200655895", "postingTitle": "Engineering Program Manager (ANE/ML/AI)",
            "transformedPostingTitle": "engineering-program-manager-ane-ml-ai",
            "postDateInGMT": "2026-10-06T22:43:46.806Z",
            "locations": [{"name": "Sunnyvale"}]}]}}
        with patch("src.adapters.apple.requests.get", return_value=_html_resp(search)):
            jobs = apple.fetch('"engineering program manager"')
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["url"], "https://jobs.apple.com/en-us/details/200655895/engineering-program-manager-ane-ml-ai")
        self.assertEqual(j["location"], "Sunnyvale, United States")
        self.assertEqual(j["posted_at"], "2026-10-06T22:43:46.806Z")

    def test_layout_change_returns_empty(self):
        m = MagicMock(status_code=200, text="<html>no data</html>")
        with patch("src.adapters.apple.requests.get", return_value=m):
            self.assertEqual(apple.fetch('"x"'), [])

    def test_detail_joins_sections(self):
        details = {"jobDetails": {"jobsData": {"jobSummary": "S", "description": "D",
                                               "minimumQualifications": "Q"}}}
        with patch("src.adapters.apple.requests.get", return_value=_html_resp(details)):
            text = apple.fetch_detail({"id": "1", "url": "https://jobs.apple.com/en-us/details/1/x"})
        self.assertIn("Summary\nS", text)
        self.assertIn("Minimum Qualifications\nQ", text)


if __name__ == "__main__":
    unittest.main()
