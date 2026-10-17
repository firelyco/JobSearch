"""Tests for src/adapters/ashby.py location handling.

Run with: python -m unittest tests.test_ashby
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest

from src.adapters.ashby import _location


def addr(region, country):
    return {"postalAddress": {"addressRegion": region, "addressCountry": country}}


class TestAshbyLocation(unittest.TestCase):

    def test_current_fields_build_full_location(self):
        # Ashby dropped `locationName`; reading it left every location blank.
        j = {"location": "San Francisco", "address": addr("California", "United States"),
             "secondaryLocations": [{"location": "London", "address": addr("Greater London", "United Kingdom")}]}
        self.assertEqual(_location(j), "San Francisco, California, United States; London, Greater London, United Kingdom")

    def test_remote_flag_prefixed(self):
        j = {"location": "New York", "address": addr("New York", "United States"), "isRemote": True}
        self.assertEqual(_location(j), "Remote; New York, United States")

    def test_legacy_location_name_still_read(self):
        self.assertEqual(_location({"locationName": "Boston"}), "Boston")

    def test_missing_everything(self):
        self.assertEqual(_location({}), "")


if __name__ == "__main__":
    unittest.main()

class AshbySalaryCapture(unittest.TestCase):
    def test_detail_requests_and_includes_annual_salary_separately(self):
        from unittest.mock import patch,MagicMock
        from src.adapters import ashby
        response=MagicMock(status_code=200)
        response.json.return_value={'jobs':[{'id':'1','descriptionHtml':'<p>Program work</p>','compensation':{'summaryComponents':[{'compensationType':'Salary','currencyCode':'USD','interval':'1 YEAR','minValue':200000,'maxValue':275000}]}}]}
        with patch.object(ashby.requests,'get',return_value=response) as get:
            detail=ashby.fetch_detail({'id':'1','company':'example'})
        self.assertEqual(get.call_args.kwargs['params']['includeCompensation'],'true')
        self.assertIn('Annual USD base salary',detail);self.assertIn('275,000',detail)
