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
