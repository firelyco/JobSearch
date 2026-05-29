"""Tests for src/fit.py — incremental scoring, capping, pruning.

Run with: python -m unittest tests.test_fit
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from datetime import datetime, timezone
from tests.test_verify import FakeClient

from src import fit


PROFILE = {
    "person": {"headline_variants": {"default": "Director TPM"}},
    "experience": [{"id": "x", "company": "Co", "title": "TPM", "start": "2020",
                    "themes": [], "achievements": [{"id": "a", "summary": "did things"}]}],
    "skills": {"pm": ["Roadmap"]},
    "preferences": {"industries_of_interest": ["ai"]},
}

def job(source, company, jid, title="Director TPM"):
    return {"source": source, "company": company, "id": jid, "title": title, "location": "Remote, US"}

def key(j):
    return f"{j['source']}:{j['company']}:{j['id']}"

NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)

def verdict(rec, reason="r"):
    return json.dumps({"recommendation": rec, "reason": reason})


class TestRun(unittest.TestCase):

    def test_scores_all_when_no_cache(self):
        jobs = [job("greenhouse", "stripe", "1"), job("lever", "netflix", "2")]
        fake = FakeClient([verdict("strong"), verdict("medium")])
        jd = {key(j): "some jd" for j in jobs}
        scores = fit.run(jobs=jobs, profile=PROFILE, existing={}, fake_client=fake,
                         jd_text_for=jd, now=NOW)
        self.assertEqual(len(scores), 2)
        self.assertEqual(scores["greenhouse:stripe:1"]["recommendation"], "strong")
        self.assertEqual(scores["lever:netflix:2"]["recommendation"], "medium")
        self.assertEqual(scores["greenhouse:stripe:1"]["scored_at"], NOW.isoformat())

    def test_only_scores_uncached(self):
        jobs = [job("greenhouse", "stripe", "1"), job("lever", "netflix", "2")]
        existing = {"greenhouse:stripe:1": {"recommendation": "strong", "reason": "cached", "scored_at": "old"}}
        # Only ONE new verdict should be requested (for job 2)
        fake = FakeClient([verdict("not")])
        jd = {key(j): "jd" for j in jobs}
        scores = fit.run(jobs=jobs, profile=PROFILE, existing=existing, fake_client=fake,
                         jd_text_for=jd, now=NOW)
        self.assertEqual(len(scores), 2)
        # job 1 unchanged (still cached value), job 2 newly scored
        self.assertEqual(scores["greenhouse:stripe:1"]["reason"], "cached")
        self.assertEqual(scores["lever:netflix:2"]["recommendation"], "not")
        self.assertEqual(len(fake.calls), 1)  # only one model call

    def test_rescore_ignores_cache(self):
        jobs = [job("greenhouse", "stripe", "1")]
        existing = {"greenhouse:stripe:1": {"recommendation": "strong", "reason": "cached", "scored_at": "old"}}
        fake = FakeClient([verdict("not", "re-evaluated")])
        scores = fit.run(jobs=jobs, profile=PROFILE, existing=existing, fake_client=fake,
                         jd_text_for={"greenhouse:stripe:1": "jd"}, rescore=True, now=NOW)
        self.assertEqual(scores["greenhouse:stripe:1"]["recommendation"], "not")
        self.assertEqual(scores["greenhouse:stripe:1"]["reason"], "re-evaluated")

    def test_prunes_stale_scores(self):
        jobs = [job("greenhouse", "stripe", "1")]
        existing = {
            "greenhouse:stripe:1": {"recommendation": "strong", "reason": "keep", "scored_at": "old"},
            "lever:gone:99": {"recommendation": "medium", "reason": "stale", "scored_at": "old"},
        }
        fake = FakeClient([])  # nothing new to score
        scores = fit.run(jobs=jobs, profile=PROFILE, existing=existing, fake_client=fake,
                         jd_text_for={}, now=NOW)
        self.assertIn("greenhouse:stripe:1", scores)
        self.assertNotIn("lever:gone:99", scores)
        self.assertEqual(len(fake.calls), 0)

    def test_cap_limits_scoring(self):
        jobs = [job("greenhouse", "c", str(i)) for i in range(10)]
        fake = FakeClient([verdict("medium")] * 3)  # only 3 will be consumed
        jd = {key(j): "jd" for j in jobs}
        scores = fit.run(jobs=jobs, profile=PROFILE, existing={}, fake_client=fake,
                         jd_text_for=jd, max_jobs=3, now=NOW)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(len(scores), 3)  # only 3 scored this run

    def test_no_jobs_need_scoring_makes_no_calls(self):
        jobs = [job("greenhouse", "stripe", "1")]
        existing = {"greenhouse:stripe:1": {"recommendation": "strong", "reason": "c", "scored_at": "old"}}
        fake = FakeClient([])
        scores = fit.run(jobs=jobs, profile=PROFILE, existing=existing, fake_client=fake,
                         jd_text_for={}, now=NOW)
        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(len(scores), 1)


if __name__ == "__main__":
    unittest.main()
