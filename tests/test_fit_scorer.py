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


class TestScoreFit(unittest.TestCase):

    def test_strong(self):
        fake = FakeClient([json.dumps({"recommendation": "strong", "reason": "fintech AI TPM match"})])
        v = score_fit(PROFILE, JOB, "We need a Director TPM for our payments platform.", client=fake)
        self.assertEqual(v.recommendation, "strong")
        self.assertEqual(v.reason, "fintech AI TPM match")

    def test_medium(self):
        fake = FakeClient([json.dumps({"recommendation": "medium", "reason": "adjacent domain"})])
        v = score_fit(PROFILE, JOB, "JD text", client=fake)
        self.assertEqual(v.recommendation, "medium")

    def test_not(self):
        fake = FakeClient([json.dumps({"recommendation": "not", "reason": "role is IC mobile dev"})])
        v = score_fit(PROFILE, JOB, "JD text", client=fake)
        self.assertEqual(v.recommendation, "not")

    def test_uppercase_normalized(self):
        fake = FakeClient([json.dumps({"recommendation": "STRONG", "reason": "x"})])
        v = score_fit(PROFILE, JOB, "JD", client=fake)
        self.assertEqual(v.recommendation, "strong")

    def test_unknown_recommendation_defaults_medium(self):
        fake = FakeClient([json.dumps({"recommendation": "maybe", "reason": "unsure"})])
        v = score_fit(PROFILE, JOB, "JD", client=fake)
        self.assertEqual(v.recommendation, "medium")
        self.assertEqual(v.reason, "unsure")

    def test_non_json_defaults_medium(self):
        fake = FakeClient(["I cannot help with that."])
        v = score_fit(PROFILE, JOB, "JD", client=fake)
        self.assertEqual(v.recommendation, "medium")
        self.assertIn("could not classify", v.reason)

    def test_prose_preamble_tolerated(self):
        fake = FakeClient(['Here you go: {"recommendation": "strong", "reason": "great"}'])
        v = score_fit(PROFILE, JOB, "JD", client=fake)
        self.assertEqual(v.recommendation, "strong")

    def test_empty_jd_still_scores(self):
        fake = FakeClient([json.dumps({"recommendation": "medium", "reason": "title-only"})])
        v = score_fit(PROFILE, JOB, "", client=fake)
        self.assertEqual(v.recommendation, "medium")

    def test_long_jd_truncated(self):
        fake = FakeClient([json.dumps({"recommendation": "strong", "reason": "x"})])
        long_jd = "word " * 5000
        score_fit(PROFILE, JOB, long_jd, client=fake, max_jd_chars=100)
        sent = fake.calls[0]["messages"][0]["content"]
        self.assertIn("[truncated]", sent)

    def test_uses_haiku_and_caches_system(self):
        fake = FakeClient([json.dumps({"recommendation": "strong", "reason": "x"})])
        score_fit(PROFILE, JOB, "JD", client=fake, model="claude-haiku-4-5")
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-haiku-4-5")
        self.assertEqual(call["temperature"], 0.0)
        self.assertIsInstance(call["system"], list)  # cache-control wrapped


if __name__ == "__main__":
    unittest.main()
