"""Tests for src/adapters/oracle_cloud.py — HTTP mocked, no network.

Run with: python -m unittest tests.test_oracle_cloud
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from unittest.mock import patch, MagicMock

from src.adapters import oracle_cloud


def _resp(status=200, payload=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload or {}
    return m


SEARCH_PAGE = {
    "items": [{
        "TotalJobsCount": 2,
        "requisitionList": [
            {
                "Id": 291689,
                "Title": "Senior Advisor, Technical Program Management",
                "PrimaryLocation": "Austin, TX, United States",
                "PostedDate": "2026-05-20",
                "ShortDescriptionStr": "summary text",
            },
            {
                "Id": 333524,
                "Title": "Principal Technical Program Manager",
                "PrimaryLocation": "Nashville, TN, United States",
                "PostedDate": "2026-05-26",
            },
        ],
    }],
}

CONFIG = {
    "company": "oracle", "tenant": "eeho", "dc": "us2",
    "site": "jobsearch", "site_number": "CX_45001", "search": "program manager",
}


class TestFetch(unittest.TestCase):

    def test_missing_required_returns_empty(self):
        bad = {"tenant": "x"}  # no dc, no site_number
        with patch("src.adapters.oracle_cloud.requests.get") as g:
            self.assertEqual(oracle_cloud.fetch(bad), [])
            g.assert_not_called()

    def test_parses_jobs(self):
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(200, SEARCH_PAGE)):
            jobs = oracle_cloud.fetch(CONFIG)
        self.assertEqual(len(jobs), 2)
        j = jobs[1]
        self.assertEqual(j["id"], "333524")
        self.assertEqual(j["source"], "oracle_cloud")
        self.assertEqual(j["company"], "oracle")
        self.assertEqual(j["title"], "Principal Technical Program Manager")
        self.assertEqual(j["location"], "Nashville, TN, United States")
        self.assertEqual(j["posted_at"], "2026-05-26")
        self.assertIn("eeho.fa.us2.oraclecloud.com", j["url"])
        self.assertIn("/sites/jobsearch/job/333524", j["url"])

    def test_http_error_returns_empty(self):
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(500, {})):
            self.assertEqual(oracle_cloud.fetch(CONFIG), [])

    def test_empty_requisitionList_breaks(self):
        payload = {"items": [{"TotalJobsCount": 0, "requisitionList": []}]}
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(200, payload)):
            self.assertEqual(oracle_cloud.fetch(CONFIG), [])

    def test_skips_jobs_without_id(self):
        payload = {"items": [{"TotalJobsCount": 1, "requisitionList": [{"Title": "x"}]}]}
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(200, payload)):
            self.assertEqual(oracle_cloud.fetch(CONFIG), [])

    def test_pagination_stops_at_total(self):
        # Single page with TotalJobsCount=2, returns 2 jobs -> offset advances
        # past total -> loop ends after one page (not PAGE_CAP=6).
        gm = MagicMock(status_code=200)
        gm.json.return_value = SEARCH_PAGE
        with patch("src.adapters.oracle_cloud.requests.get", return_value=gm) as g:
            oracle_cloud.fetch(CONFIG)
            self.assertEqual(g.call_count, 1)


class TestFetchDetail(unittest.TestCase):

    def test_concatenates_description_fields(self):
        job = {
            "id": "333524",
            "url": "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/333524",
        }
        payload = {
            "ShortDescriptionStr": "SHORT",
            "ExternalDescriptionStr": "OVERVIEW",
            "ExternalResponsibilitiesStr": "RESP",
            "ExternalQualificationsStr": "QUAL",
        }
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(200, payload)) as g:
            text = oracle_cloud.fetch_detail(job)
            # Verify hits the *Details* sibling resource on the right host
            called_url = g.call_args[0][0]
            self.assertIn("eeho.fa.us2.oraclecloud.com", called_url)
            self.assertIn("/recruitingCEJobRequisitionDetails/333524", called_url)
        for needle in ("SHORT", "OVERVIEW", "RESP", "QUAL"):
            self.assertIn(needle, text)

    def test_no_url_returns_empty(self):
        self.assertEqual(oracle_cloud.fetch_detail({"id": "1", "url": ""}), "")

    def test_http_error_returns_empty(self):
        job = {"id": "1", "url": "https://x.fa.y.oraclecloud.com/x/y/job/1"}
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(404, {})):
            self.assertEqual(oracle_cloud.fetch_detail(job), "")

    def test_missing_description_fields_returns_empty(self):
        job = {"id": "1", "url": "https://x.fa.y.oraclecloud.com/x/y/job/1"}
        with patch("src.adapters.oracle_cloud.requests.get", return_value=_resp(200, {"Title": "x"})):
            self.assertEqual(oracle_cloud.fetch_detail(job), "")


if __name__ == "__main__":
    unittest.main()
