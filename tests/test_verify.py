"""Tests for src/verify.py — the Haiku-based fact verification pass.

Uses a FakeClient that mimics the Anthropic SDK's messages.create() shape
so we can drive the verifier through every code path without an API key.

Run with: python -m unittest tests.test_verify
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from dataclasses import dataclass

from src.verify import verify_bullets, BulletVerdict


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text=text)]
        self.usage = _FakeUsage()


class FakeClient:
    """Records calls and returns a queued response text per call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self  # so client.messages.create works

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no fake response queued")
        return _FakeResponse(self._responses.pop(0))


PROFILE = {
    "experience": [
        {
            "id": "amazon_devices",
            "company": "Amazon",
            "achievements": [
                {
                    "id": "gdpr_program",
                    "metrics": ["50 engineers led", "12-month program"],
                    "tech": ["AWS", "Lambda"],
                }
            ],
        }
    ],
    "skills": {"compliance_regulatory": ["GDPR", "CCPA"]},
}


class TestVerifyBullets(unittest.TestCase):

    def test_all_pass(self):
        bullets = [
            {"achievement_id": "gdpr_program", "text": "Led 50 engineers across GDPR program"},
            {"achievement_id": "gdpr_program", "text": "Delivered 12-month compliance milestone"},
        ]
        fake = FakeClient([json.dumps({
            "results": [
                {"bullet_index": 0, "verdict": "PASS"},
                {"bullet_index": 1, "verdict": "PASS"},
            ]
        })])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(len(verdicts), 2)
        self.assertTrue(all(v.verdict == "PASS" for v in verdicts))

    def test_one_fail(self):
        bullets = [
            {"achievement_id": "gdpr_program", "text": "Led 50 engineers"},
            {"achievement_id": "gdpr_program", "text": "Led 500 engineers across Kubernetes platform"},
        ]
        fake = FakeClient([json.dumps({
            "results": [
                {"bullet_index": 0, "verdict": "PASS"},
                {"bullet_index": 1, "verdict": "FAIL", "reason": "claims 500 engineers and Kubernetes, neither in profile"},
            ]
        })])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(verdicts[0].verdict, "PASS")
        self.assertEqual(verdicts[1].verdict, "FAIL")
        self.assertIn("Kubernetes", verdicts[1].reason)

    def test_missing_verdict_defaults_to_fail(self):
        bullets = [
            {"achievement_id": "gdpr_program", "text": "a"},
            {"achievement_id": "gdpr_program", "text": "b"},
        ]
        # Only one of two verdicts returned — the other should be FAIL
        fake = FakeClient([json.dumps({
            "results": [{"bullet_index": 0, "verdict": "PASS"}]
        })])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(verdicts[0].verdict, "PASS")
        self.assertEqual(verdicts[1].verdict, "FAIL")
        self.assertEqual(verdicts[1].reason, "no verdict returned")

    def test_non_json_response_all_fail(self):
        bullets = [{"achievement_id": "x", "text": "y"}]
        fake = FakeClient(["I'm sorry, I can't help with that."])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(verdicts[0].verdict, "FAIL")
        self.assertIn("non-JSON", verdicts[0].reason)

    def test_empty_bullets_returns_empty(self):
        # Should not even call the model
        fake = FakeClient([])  # no queued response
        verdicts = verify_bullets(PROFILE, [], client=fake)
        self.assertEqual(verdicts, [])
        self.assertEqual(fake.calls, [])

    def test_prose_before_json_is_tolerated(self):
        bullets = [{"achievement_id": "x", "text": "y"}]
        # Model adds preamble (a known failure mode) — verifier should still extract
        fake = FakeClient([
            'Sure, here is the JSON:\n{"results": [{"bullet_index": 0, "verdict": "PASS"}]}\nLet me know if you need anything else.'
        ])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(verdicts[0].verdict, "PASS")

    def test_placeholder_text_must_fail(self):
        """Adversarial: bullet contains [REAL DETAILS HERE]. The verifier's
        system prompt says ALWAYS FAIL — but here we drive the model's response
        deterministically. This test asserts that when the model correctly FAILS
        such a bullet, our code surfaces it as FAIL. (Behavior of a real model
        on this input is verified separately at impl time.)"""
        bullets = [{"achievement_id": "x", "text": "Led [REAL DETAILS HERE] program"}]
        fake = FakeClient([json.dumps({
            "results": [{"bullet_index": 0, "verdict": "FAIL", "reason": "placeholder text"}]
        })])
        verdicts = verify_bullets(PROFILE, bullets, client=fake)
        self.assertEqual(verdicts[0].verdict, "FAIL")

    def test_uses_haiku_model_and_caches_system(self):
        bullets = [{"achievement_id": "x", "text": "y"}]
        fake = FakeClient([json.dumps({"results": [{"bullet_index": 0, "verdict": "PASS"}]})])
        verify_bullets(PROFILE, bullets, client=fake, model="claude-haiku-4-5")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-haiku-4-5")
        self.assertEqual(call["temperature"], 0.0)
        # system was passed as cache-control wrapped list
        self.assertIsInstance(call["system"], list)
        self.assertEqual(call["system"][0]["cache_control"]["type"], "ephemeral")


if __name__ == "__main__":
    unittest.main()
