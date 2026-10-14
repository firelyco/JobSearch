import json
import unittest
from src.fit_scorer import score_fit, verified_base_comp_status
from tests.test_fit_scorer import PROFILE, JOB
from tests.test_verify import FakeClient

class BaseCompPolicy(unittest.TestCase):
    def test_literal_salary_boundaries(self):
        for text, expected in [
            ("Annual USD base salary: $251,000 - $300,000", "meets_floor"),
            ("Annual USD base salary: $250,000 - $300,000", "potentially_meets_floor"),
            ("Annual USD base salary: $230,000 - $280,000", "potentially_meets_floor"),
            ("Annual USD base salary: $150,000 - $240,000", "below_floor"),
            ("Annual USD base salary: $250,000", "below_floor"),
            ("Annual USD base salary: $260K - $300K", "meets_floor"),
            ("Annual USD total compensation: $400,000", "unknown"),
            ("Annual CAD base salary: $400,000", "unknown"),
            ("Monthly USD base salary: $260,000", "unknown"),
            ("Annual base salary: $260,000", "unknown"),
            ("Competitive salary plus equity", "unknown"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(verified_base_comp_status(text, text, 250000), expected)

    def test_invented_evidence_does_not_pass(self):
        self.assertEqual(verified_base_comp_status("Competitive pay", "Annual USD base salary: $300,000", 250000), "unknown")

    def test_all_roles_require_verified_base_despite_model_claim(self):
        rules = {"allow_ic_strong": True, "strong_requires_base_comp": True, "comp_floor_usd": 250000}
        for level in ("ic", "people_manager", "unknown"):
            for quote, expected in [
                ("Annual USD base salary: $260,000 - $300,000", "strong"),
                ("Annual USD base salary: $230,000 - $280,000", "medium"),
                ("Annual USD base salary: $150,000 - $240,000", "medium"),
                ("Competitive salary", "medium"),
            ]:
                fake = FakeClient([json.dumps({"recommendation": "strong", "role_level": level, "comp_status": "meets_floor", "comp_evidence": quote, "role_summary": "AI program delivery"})])
                with self.subTest(level=level, quote=quote):
                    result = score_fit(PROFILE, JOB, quote, client=fake, rules=rules)
                    self.assertEqual(result.recommendation, expected)
                    self.assertNotIn("250", result.reason)

    def test_domain_failure_not_overridden_by_pay(self):
        quote = "Annual USD base salary: $300,000"
        fake = FakeClient([json.dumps({"recommendation": "not", "step": "domain", "comp_evidence": quote, "role_summary": "Requires specialist industry knowledge"})])
        result = score_fit(PROFILE, JOB, quote, client=fake, rules={"strong_requires_base_comp": True, "comp_floor_usd": 250000})
        self.assertEqual(result.recommendation, "not")
