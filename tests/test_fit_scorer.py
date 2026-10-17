"""Tests for src/fit_scorer.py — profile condensation + Haiku verdict parsing.

Run with: python -m unittest tests.test_fit_scorer
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from tests.test_verify import FakeClient  # reuse the SDK-shaped fake

from src.fit_scorer import score_fit, condense_profile, FitVerdict


PROFILE = {
    "person": {
        "headline_variants": {"default": "Director TPM, 20y, fintech + devices + robotics"},
    },
    "experience": [
        {
            "id": "intuit", "company": "Intuit", "title": "Principal TPM",
            "start": "2022", "end": None, "themes": ["ai", "fintech"],
            "achievements": [
                {"id": "a1", "summary": "AI expert productivity", "details": "verbose text dropped"},
            ],
        },
        {
            "id": "bg", "company": "Berkshire Grey", "title": "Director TPM",
            "start": "2020", "end": "2022", "themes": ["robotics"],
            "achievements": [{"id": "a2", "summary": "Built TPM org"}],
        },
    ],
    "skills": {
        "program_management": ["Roadmap", "Risk"],
        "ai_ml": ["Generative AI"],
    },
    "preferences": {"industries_of_interest": ["ai", "fintech", "robotics"]},
}

JOB = {"title": "Director, Technical Program Management", "company": "stripe", "location": "Remote, US"}


class TestCondenseProfile(unittest.TestCase):

    def test_includes_headline_and_companies(self):
        text = condense_profile(PROFILE)
        self.assertIn("Director TPM, 20y", text)
        self.assertIn("Intuit", text)
        self.assertIn("Berkshire Grey", text)

    def test_includes_achievement_summaries_not_details(self):
        text = condense_profile(PROFILE)
        self.assertIn("AI expert productivity", text)
        self.assertNotIn("verbose text dropped", text)

    def test_includes_flattened_skills(self):
        text = condense_profile(PROFILE)
        self.assertIn("Roadmap", text)
        self.assertIn("Generative AI", text)

    def test_includes_industries(self):
        text = condense_profile(PROFILE)
        self.assertIn("fintech", text)




if __name__ == "__main__":
    unittest.main()
