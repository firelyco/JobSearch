import unittest
from unittest.mock import patch, Mock
from src.adapters import lever
from src.jd_fetch import strip_html

class LeverFullDetailTests(unittest.TestCase):
    def test_qualifications_and_salary_are_not_lost(self):
        data = {"descriptionPlain": "Opening summary", "lists": [
            {"text": "Qualifications", "content": "<ul><li>12+ years hardware TPM required</li></ul>"},
            {"text": "Bonus Qualifications", "content": "<li>Automotive preferred</li>"}],
            "additionalPlain": "Closing terms", "salaryRange": {"min": 222000, "max": 267000,
            "currency": "USD", "interval": "per-year-salary"},
            "salaryDescriptionPlain": "The listed range applies only to base salary."}
        with patch.object(lever.requests, "get", return_value=Mock(status_code=200, json=lambda: data)):
            result = strip_html(lever.fetch_detail({"company": "sample", "id": "abc"}))
        for required in ["Qualifications", "12+ years hardware TPM required", "Automotive preferred",
                         "Closing terms", "267000", "USD", "per-year-salary", "base salary"]:
            self.assertIn(required, result)
        self.assertLess(result.index("Qualifications"), result.index("12+ years"))

    def test_html_fallback_and_partial_sections(self):
        data = {"description": "<p>Opening</p>", "lists": [None, {"text": "Required", "content": "<li>Must meet</li>"}],
                "additional": "<p>Closing</p>", "salaryRange": {"min": 20}}
        with patch.object(lever.requests, "get", return_value=Mock(status_code=200, json=lambda: data)):
            result = strip_html(lever.fetch_detail({"company": "sample", "id": "abc"}))
        self.assertIn("Must meet", result)
        self.assertIn("Closing", result)
        self.assertNotIn("Published salary", result)
