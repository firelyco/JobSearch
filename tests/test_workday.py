"""Tests for src/adapters/workday.py — HTTP mocked, no network.

Run with: python -m unittest tests.test_workday
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from unittest.mock import patch, MagicMock

from src.adapters import workday


class TestFetchDetail(unittest.TestCase):

    def test_uses_job_host_and_external_path(self):
        job = {
            "company": "nvidia",
            "id": "/job/US-CA-Santa-Clara/Director-TPM_JR1",
            "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Director-TPM_JR1",
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"jobPostingInfo": {"jobDescription": "<p>jd</p>"}}
        with patch("src.adapters.workday.requests.get", return_value=resp) as m:
            self.assertEqual(workday.fetch_detail(job), "<p>jd</p>")
        self.assertEqual(
            m.call_args[0][0],
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Director-TPM_JR1",
        )

    def test_unparseable_url_returns_empty(self):
        job = {"company": "nvidia", "id": "/job/x", "url": "https://example.com/x"}
        with patch("src.adapters.workday.requests.get") as m:
            self.assertEqual(workday.fetch_detail(job), "")
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
