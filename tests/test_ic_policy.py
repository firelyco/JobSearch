import json
import unittest
from datetime import datetime, timezone
from src import fit
from src.fit_scorer import score_fit
from tests.test_fit_scorer import PROFILE, JOB
from tests.test_verify import FakeClient

RULES = {"allow_ic_strong": True, "ic_strong_requires_comp": True, "comp_floor_usd": 200000, "fit_policy_version": "test-ic-v2"}

class ICPolicy(unittest.TestCase):
    def result(self, compensation, recommendation="strong", step="none"):
        fake = FakeClient([json.dumps({"recommendation": recommendation, "step": step, "role_level": "ic", "comp_status": compensation, "role_summary": "Delivers AI platform programs"})])
        return score_fit(PROFILE, JOB, "AI platform job", client=fake, rules=RULES)

    def test_salary_at_end_of_long_posting_is_retained(self):
        fake = FakeClient([json.dumps({"recommendation": "strong", "role_level": "ic", "comp_status": "meets_floor", "role_summary": "AI platform operations"})])
        jd = "Lead technical programs. " + "Benefits and company information. " * 400 + "Compensation Range: $230,000 - $280,000 + bonus."
        score_fit(PROFILE, JOB, jd, client=fake, rules=RULES)
        self.assertIn("$230,000 - $280,000", fake.calls[0]["messages"][0]["content"])

    def test_good_compensation_allows_strong_ic(self):
        self.assertEqual(self.result("meets_floor").recommendation, "strong")

    def test_overlapping_range_allows_strong_with_offer_confirmation(self):
        result = self.result("potentially_meets_floor")
        self.assertEqual(result.recommendation, "strong")
        self.assertIn("Confirm compensation", result.reason)

    def test_missing_or_low_compensation_remains_provisional(self):
        for status in ("unknown", "below_floor", "invented"):
            result = self.result(status)
            self.assertEqual((result.recommendation, result.step), ("medium", "comp"))

    def test_domain_gate_still_rejects_even_with_good_pay(self):
        self.assertEqual(self.result("meets_floor", "not", "domain").recommendation, "not")

    def test_new_policy_invalidates_old_scores_without_losing_other_entries(self):
        records = [{"source": "ashby", "company": "example", "id": "1", "title": "Staff TPM"}, {"source": "ashby", "company": "example", "id": "2", "title": "Director TPM"}]
        keys = ["ashby:example:1", "ashby:example:2"]
        previous = {keys[0]: {"recommendation": "medium", "reason": "old IC penalty"}, keys[1]: {"recommendation": "strong", "reason": "preserved", "policy_version": RULES["fit_policy_version"]}}
        fake = FakeClient([json.dumps({"recommendation": "strong", "step": "none", "role_level": "ic", "comp_status": "meets_floor", "role_summary": "AI platform delivery"})])
        result = fit.run(jobs=records, profile=PROFILE, existing=previous, rules=RULES, job_keys=[keys[0]], fake_client=fake, jd_text_for={keys[0]: "job description"}, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(result[keys[0]]["recommendation"], "strong")
        self.assertEqual(result[keys[0]]["policy_version"], RULES["fit_policy_version"])
        self.assertEqual(result[keys[1]], previous[keys[1]])
        self.assertEqual(len(fake.calls), 1)

    def test_current_policy_score_stays_cached(self):
        key = "ashby:example:1"
        result = fit.run(jobs=[{"source": "ashby", "company": "example", "id": "1"}], profile=PROFILE, rules=RULES, existing={key: {"recommendation": "strong", "policy_version": RULES["fit_policy_version"]}}, fake_client=FakeClient([]))
        self.assertEqual(result[key]["recommendation"], "strong")

if __name__ == "__main__": unittest.main()
