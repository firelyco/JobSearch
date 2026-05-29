"""Tests for src/adapters/amazon_jobs.py — search.json parsing + date handling.

HTTP is mocked; no network. Run with: python -m unittest tests.test_amazon_jobs
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from unittest.mock import patch, MagicMock

from src.adapters import amazon_jobs


def _resp(status=200, payload=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload or {}
    m.text = "<html>jd</html>"
    return m


SEARCH_PAGE = {
    "hits": 2,
    "jobs": [
        {
            "id_icims": "10433910",
            "title": "Technical Program Manager, IRQ ",
            "normalized_location": "Herndon, Virginia, USA",
            "location": "US, VA, Herndon",
            "job_path": "/en/jobs/10433910/technical-program-manager-irq",
            "posted_date": "May 29, 2026",
        },
        {
            "id_icims": "10433911",
            "title": "Principal Technical Program Manager",
            "normalized_location": "Seattle, Washington, USA",
            "job_path": "/en/jobs/10433911/principal-tpm",
            "posted_date": "April 01, 2026",
        },
    ],
}


class TestParsePosted(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(amazon_jobs._parse_posted("May 29, 2026").startswith("2026-05-29"))
    def test_empty(self):
        self.assertEqual(amazon_jobs._parse_posted(""), "")
    def test_garbage(self):
        self.assertEqual(amazon_jobs._parse_posted("yesterday"), "")


class TestFetch(unittest.TestCase):

    def test_empty_query_no_request(self):
        with patch("src.adapters.amazon_jobs.requests.get") as g:
            self.assertEqual(amazon_jobs.fetch(""), [])
            g.assert_not_called()

    def test_parses_jobs(self):
        # page 1 returns 2 jobs (hits=2), so pagination stops after one page
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(200, SEARCH_PAGE)):
            jobs = amazon_jobs.fetch("technical program manager")
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["id"], "10433910")
        self.assertEqual(j["source"], "amazon_jobs")
        self.assertEqual(j["company"], "amazon")
        self.assertEqual(j["title"], "Technical Program Manager, IRQ")
        self.assertEqual(j["location"], "Herndon, Virginia, USA")
        self.assertEqual(j["url"], "https://www.amazon.jobs/en/jobs/10433910/technical-program-manager-irq")
        self.assertTrue(j["posted_at"].startswith("2026-05-29"))

    def test_http_error_returns_empty(self):
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(503, {})):
            self.assertEqual(amazon_jobs.fetch("tpm"), [])

    def test_skips_jobs_without_id(self):
        payload = {"hits": 1, "jobs": [{"title": "No ID role", "job_path": "/x"}]}
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(200, payload)):
            self.assertEqual(amazon_jobs.fetch("tpm"), [])

    def test_stops_when_no_jobs(self):
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(200, {"hits": 999, "jobs": []})):
            self.assertEqual(amazon_jobs.fetch("tpm"), [])


class TestFetchDetail(unittest.TestCase):

    def test_returns_html(self):
        job = {"id": "1", "url": "https://www.amazon.jobs/en/jobs/1/x"}
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(200)):
            self.assertEqual(amazon_jobs.fetch_detail(job), "<html>jd</html>")

    def test_no_url_returns_empty(self):
        self.assertEqual(amazon_jobs.fetch_detail({"id": "1", "url": ""}), "")

    def test_http_error_returns_empty(self):
        job = {"id": "1", "url": "https://www.amazon.jobs/en/jobs/1/x"}
        with patch("src.adapters.amazon_jobs.requests.get", return_value=_resp(404)):
            self.assertEqual(amazon_jobs.fetch_detail(job), "")


if __name__ == "__main__":
    unittest.main()
