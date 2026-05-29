"""Sanity tests for the scorer.

Run with: python -m unittest tests.test_scorer
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
import yaml
from pathlib import Path
from src.scorer import score_job, classify


def load_real_config():
    """Load the actual production role_config.yml so tests cover real behavior."""
    config_path = Path(__file__).parent.parent / "config" / "role_config.yml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


CONFIG = load_real_config()


class TestScorer(unittest.TestCase):

    def test_vp_program_management_us_is_hot(self):
        job = {
            "title": "VP, Technical Program Management",
            "location": "Boston, MA",
            "company": "anthropic",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 80, f"VP TPM in US should be hot, got {score}: {reasons}")
        self.assertEqual(classify(score, CONFIG), "hot")

    def test_director_tpm_remote_is_hot(self):
        job = {
            "title": "Director, Technical Program Management",
            "location": "US-Remote",
            "company": "databricks",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 80, f"got {score}: {reasons}")

    def test_sr_manager_program_management_is_above_threshold(self):
        job = {
            "title": "Senior Manager, Technical Program Management",
            "location": "United States - Remote",
            "company": "stripe",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 60, f"got {score}: {reasons}")

    def test_senior_tpm_boston_is_above_threshold(self):
        job = {
            "title": "Senior Technical Program Manager",
            "location": "Boston, MA",
            "company": "mongodb",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 60, f"got {score}: {reasons}")

    def test_staff_tpm_remote_is_above_threshold(self):
        job = {
            "title": "Staff Technical Program Manager",
            "location": "Remote, USA",
            "company": "twilio",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 60, f"got {score}: {reasons}")

    def test_head_of_program_management_is_hot(self):
        job = {
            "title": "Head of Technical Program Management",
            "location": "New York, NY",
            "company": "openai",
        }
        score, _ = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 80)

    def test_staff_tpm_comma_format_kept(self):
        """Walmart-style 'Staff, Technical Program Manager' (comma after the
        seniority word) must pass the title gate, not just the space form."""
        job = {
            "title": "Staff, Technical Program Manager",
            "location": "Sunnyvale, CA",
            "company": "walmart",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 60, f"comma-format Staff TPM should pass, got {score}: {reasons}")

    def test_principal_tpm_comma_format_kept(self):
        job = {
            "title": "Principal, Technical Program Manager",
            "location": "Sunnyvale, CA",
            "company": "walmart",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertGreaterEqual(score, 60, f"comma-format Principal TPM should pass, got {score}: {reasons}")

    def test_plain_tpm_is_dropped(self):
        """No seniority modifier = not our target. Should not pass title gate."""
        job = {
            "title": "Technical Program Manager",
            "location": "Remote",
            "company": "stripe",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertEqual(score, 0, f"plain TPM should drop, got {score}: {reasons}")

    def test_tpm_ii_is_dropped(self):
        """Level 2 TPM is below our target tier."""
        job = {
            "title": "Technical Program Manager II",
            "location": "Remote",
            "company": "stripe",
        }
        score, _ = score_job(job, CONFIG)
        self.assertEqual(score, 0)

    def test_intern_dropped(self):
        job = {
            "title": "Senior Technical Program Manager Intern",
            "location": "Remote",
            "company": "stripe",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertEqual(score, 0)
        self.assertIn("excluded", reasons[0])

    def test_new_grad_dropped(self):
        job = {
            "title": "Technical Program Manager, New Graduate",
            "location": "Pittsburgh",
            "company": "duolingo",
        }
        score, _ = score_job(job, CONFIG)
        self.assertEqual(score, 0)

    def test_non_tpm_role_dropped(self):
        job = {"title": "Senior Software Engineer", "location": "Remote", "company": "stripe"}
        score, _ = score_job(job, CONFIG)
        self.assertEqual(score, 0)

    def test_director_product_management_is_dropped(self):
        """Director of Product Management is a different role; should not match."""
        job = {
            "title": "Director, Product Management",
            "location": "Remote",
            "company": "stripe",
        }
        score, _ = score_job(job, CONFIG)
        self.assertEqual(score, 0)

    def test_india_location_dropped(self):
        job = {
            "title": "Senior Technical Program Manager",
            "location": "Bengaluru, India",
            "company": "mongodb",
        }
        score, reasons = score_job(job, CONFIG)
        self.assertLess(score, 60, f"India should be below notify threshold, got {score}")

    def test_dublin_location_dropped(self):
        job = {
            "title": "Senior Technical Program Manager",
            "location": "Dublin",
            "company": "mongodb",
        }
        score, _ = score_job(job, CONFIG)
        self.assertLess(score, 60)

    def test_excluded_company(self):
        config = {**CONFIG, "excluded_companies": ["badcorp"]}
        job = {
            "title": "VP, Technical Program Management",
            "location": "Remote",
            "company": "badcorp",
        }
        score, _ = score_job(job, config)
        self.assertEqual(score, 0)

    def test_classify_thresholds(self):
        self.assertEqual(classify(95, CONFIG), "hot")
        self.assertEqual(classify(80, CONFIG), "hot")
        self.assertEqual(classify(65, CONFIG), "standard")
        self.assertEqual(classify(40, CONFIG), "low")
        self.assertEqual(classify(0, CONFIG), "drop")

    def test_seniority_priority_vp_beats_director(self):
        """A title containing both 'VP' and 'Director' should get VP bonus, not Director."""
        job = {
            "title": "VP and Director of Technical Program Management",
            "location": "Remote",
            "company": "x",
        }
        score, reasons = score_job(job, CONFIG)
        seniority_reasons = [r for r in reasons if "vp" in r.lower() or "director" in r.lower()]
        self.assertTrue(any("vp" in r.lower() for r in seniority_reasons),
                        f"Expected VP bonus to take priority, got: {seniority_reasons}")


class TestIsRecent(unittest.TestCase):
    from datetime import datetime, timezone, timedelta
    NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)

    def test_recent_kept(self):
        from datetime import timedelta
        ts = (self.NOW - timedelta(days=5)).isoformat()
        from src.scorer import is_recent
        self.assertTrue(is_recent(ts, 21, now=self.NOW))

    def test_exactly_at_cutoff_kept(self):
        from datetime import timedelta
        ts = (self.NOW - timedelta(days=21)).isoformat()
        from src.scorer import is_recent
        self.assertTrue(is_recent(ts, 21, now=self.NOW))

    def test_over_cutoff_dropped(self):
        from datetime import timedelta
        ts = (self.NOW - timedelta(days=22)).isoformat()
        from src.scorer import is_recent
        self.assertFalse(is_recent(ts, 21, now=self.NOW))

    def test_empty_posted_at_kept(self):
        from src.scorer import is_recent
        self.assertTrue(is_recent("", 21, now=self.NOW))
        self.assertTrue(is_recent(None, 21, now=self.NOW))

    def test_unparseable_kept(self):
        from src.scorer import is_recent
        self.assertTrue(is_recent("yesterday", 21, now=self.NOW))

    def test_future_dated_kept(self):
        from datetime import timedelta
        from src.scorer import is_recent
        ts = (self.NOW + timedelta(days=3)).isoformat()
        self.assertTrue(is_recent(ts, 21, now=self.NOW))

    def test_zero_max_age_disables_filter(self):
        from datetime import timedelta
        from src.scorer import is_recent
        ts = (self.NOW - timedelta(days=500)).isoformat()
        self.assertTrue(is_recent(ts, 0, now=self.NOW))

    def test_z_suffix_iso_parsed(self):
        from src.scorer import is_recent
        ts = "2026-05-25T12:00:00Z"  # 4 days before NOW
        self.assertTrue(is_recent(ts, 21, now=self.NOW))


if __name__ == "__main__":
    unittest.main()
