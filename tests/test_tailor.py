"""Tests for src/tailor.py — the orchestration of Sonnet + Haiku.

We feed canned Sonnet/Haiku responses via a FakeClient that returns
queued strings per call. Each tailor() run produces a deterministic
sequence of calls; the tests pin that sequence and verify retry/drop
behavior.

Run with: python -m unittest tests.test_tailor
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from tests.test_verify import FakeClient  # reuse the fake from verify tests

from src.tailor import tailor


PROFILE = {
    "person": {"name": "Sandeep"},
    "experience": [
        {
            "id": "amazon",
            "achievements": [
                {"id": "gdpr", "metrics": ["50 engineers"], "tech": ["AWS"]}
            ],
        }
    ],
}

JOB = {"source": "greenhouse", "company": "stripe", "id": "12345", "url": "https://example/job"}
JOB_KEY = "greenhouse:stripe:12345"

JD_TEXT = "Senior TPM role driving GDPR compliance across consumer products."

CFG = {
    "models": {"tailor": "claude-sonnet-4-6", "verify": "claude-haiku-4-5", "cover_letter": "claude-sonnet-4-6"},
    "limits": {"max_jd_tokens": 8000, "max_output_tokens": 2000, "temperature": 0.0},
    "verification": {"max_retries_per_bullet": 2, "drop_unverified": True},
    "features": {"cover_letter": False},  # off by default in tests
}


def _tailor_response(bullets, headline="default headline", gaps=None):
    return json.dumps({
        "headline": headline,
        "bullets": bullets,
        "gaps": gaps or [],
    })


def _verify_response(verdicts):
    """verdicts: list of (idx, "PASS"|"FAIL", reason)"""
    results = [{"bullet_index": i, "verdict": v, "reason": r} for (i, v, r) in verdicts]
    return json.dumps({"results": results})


class TestTailorHappyPath(unittest.TestCase):

    def test_all_bullets_pass_first_try(self):
        bullets = [
            {"achievement_id": "gdpr", "text": "Led 50 engineers across GDPR program"},
        ]
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "PASS", "")]),
        ])
        result = tailor(
            JOB_KEY,
            profile=PROFILE,
            cfg=CFG,
            jobs=[JOB],
            fake_client=fake,
            jd_text_override=JD_TEXT,
        )
        self.assertEqual(len(result.bullets), 1)
        self.assertEqual(result.dropped_bullet_count, 0)
        self.assertTrue(result.bullets[0]["verified"])
        self.assertEqual(result.cover_letter, "")  # disabled

    def test_cover_letter_when_enabled(self):
        cfg = {**CFG, "features": {"cover_letter": True}}
        bullets = [{"achievement_id": "gdpr", "text": "Led GDPR"}]
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "PASS", "")]),
            "I built compliance programs at Amazon scaling to consumer device portfolios.\n\nMy GDPR work directly maps to your privacy roadmap.\n\nHappy to walk through specifics.",
        ])
        result = tailor(
            JOB_KEY, profile=PROFILE, cfg=cfg, jobs=[JOB],
            fake_client=fake, jd_text_override=JD_TEXT,
        )
        self.assertIn("compliance programs", result.cover_letter)


class TestTailorVerificationLoop(unittest.TestCase):

    def test_failed_bullet_retried_and_passes(self):
        bullets = [
            {"achievement_id": "gdpr", "text": "Led 500 engineers across Kubernetes"},  # hallucinated
        ]
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "FAIL", "claims 500 and Kubernetes, neither in profile")]),
            # retry returns a corrected bullet
            json.dumps({"achievement_id": "gdpr", "text": "Led 50 engineers across GDPR program"}),
            # re-verify passes
            _verify_response([(0, "PASS", "")]),
        ])
        result = tailor(
            JOB_KEY, profile=PROFILE, cfg=CFG, jobs=[JOB],
            fake_client=fake, jd_text_override=JD_TEXT,
        )
        self.assertEqual(len(result.bullets), 1)
        self.assertEqual(result.dropped_bullet_count, 0)
        self.assertIn("50 engineers", result.bullets[0]["text"])

    def test_failed_bullet_dropped_after_max_retries(self):
        bullets = [
            {"achievement_id": "gdpr", "text": "Led 500 engineers"},
        ]
        # 1 initial tailor + 1 verify + 2 retries (each: tailor + verify)
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "FAIL", "metric mismatch")]),
            json.dumps({"achievement_id": "gdpr", "text": "Led 200 engineers"}),
            _verify_response([(0, "FAIL", "still wrong")]),
            json.dumps({"achievement_id": "gdpr", "text": "Led 100 engineers"}),
            _verify_response([(0, "FAIL", "still wrong")]),
        ])
        result = tailor(
            JOB_KEY, profile=PROFILE, cfg=CFG, jobs=[JOB],
            fake_client=fake, jd_text_override=JD_TEXT,
        )
        self.assertEqual(len(result.bullets), 0)
        self.assertEqual(result.dropped_bullet_count, 1)

    def test_mixed_pass_fail_keeps_passing_bullets(self):
        bullets = [
            {"achievement_id": "gdpr", "text": "Led 50 engineers"},          # PASS
            {"achievement_id": "gdpr", "text": "Built Kubernetes cluster"},  # FAIL, drops
        ]
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "PASS", ""), (1, "FAIL", "Kubernetes not in profile")]),
            # 2 retries for bullet 1, both still fail
            json.dumps({"achievement_id": "gdpr", "text": "Built K8s cluster"}),
            _verify_response([(0, "FAIL", "still K8s")]),
            json.dumps({"achievement_id": "gdpr", "text": "Built K8s cluster v2"}),
            _verify_response([(0, "FAIL", "still K8s")]),
        ])
        result = tailor(
            JOB_KEY, profile=PROFILE, cfg=CFG, jobs=[JOB],
            fake_client=fake, jd_text_override=JD_TEXT,
        )
        self.assertEqual(len(result.bullets), 1)
        self.assertEqual(result.dropped_bullet_count, 1)
        self.assertEqual(result.bullets[0]["text"], "Led 50 engineers")


class TestTailorErrors(unittest.TestCase):

    def test_job_key_not_found(self):
        with self.assertRaises(KeyError):
            tailor(
                "nope:bad:999",
                profile=PROFILE, cfg=CFG, jobs=[JOB],
                fake_client=FakeClient([]), jd_text_override=JD_TEXT,
            )

    def test_empty_jd_raises(self):
        with self.assertRaises(RuntimeError):
            tailor(
                JOB_KEY,
                profile=PROFILE, cfg=CFG, jobs=[JOB],
                fake_client=FakeClient([]), jd_text_override="",
            )

    def test_zero_bullets_raises(self):
        fake = FakeClient([_tailor_response([])])
        with self.assertRaises(RuntimeError):
            tailor(
                JOB_KEY,
                profile=PROFILE, cfg=CFG, jobs=[JOB],
                fake_client=fake, jd_text_override=JD_TEXT,
            )


class TestCostEstimate(unittest.TestCase):

    def test_cost_is_nonzero_and_under_acceptance_cap(self):
        bullets = [{"achievement_id": "gdpr", "text": "Led 50 engineers"}]
        fake = FakeClient([
            _tailor_response(bullets),
            _verify_response([(0, "PASS", "")]),
        ])
        result = tailor(
            JOB_KEY, profile=PROFILE, cfg=CFG, jobs=[JOB],
            fake_client=fake, jd_text_override=JD_TEXT,
        )
        # FakeUsage returns 100 input, 50 output per call — both at Sonnet pricing
        # input cost: 100 * $3/M = $0.0003 per call * 1 tailor call
        # output cost: 50 * $15/M = $0.00075 per call
        # Total per call: ~$0.00105
        # Verify (haiku) doesn't add to cost in current code (only tailor calls counted)
        self.assertGreater(result.cost_estimate_usd, 0)
        self.assertLess(result.cost_estimate_usd, 0.10)  # acceptance criterion #9


if __name__ == "__main__":
    unittest.main()
