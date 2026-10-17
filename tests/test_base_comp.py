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
            ("Annual USD base salary: $212,600 - $319,800", "potentially_meets_floor"),
            ("Annual USD base salary: $200,000 - $250,000", "potentially_meets_floor"),
            ("Annual USD base salary: $250,000", "potentially_meets_floor"),
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




class EmployerSalaryFormats(unittest.TestCase):
    def test_annual_salary_html_without_base_word(self):
        jd = '<div>Annual Salary:</div><span>$365,000</span><span>–</span><span>$565,000 USD</span>'
        self.assertEqual(verified_base_comp_status(jd, '', 250000), 'meets_floor')

    def test_yearly_salary_overlap_remains_provisional(self):
        jd = '<div>The reasonably estimated yearly salary for this role is:</div><span>$224,000</span><span>–</span><span>$280,000 USD</span>'
        self.assertEqual(verified_base_comp_status(jd, '', 250000), 'potentially_meets_floor')

    def test_entities_and_bare_upper_bound(self):
        jd = 'Annual Salary:&nbsp; $365,000 &ndash; 565,000 USD'
        self.assertEqual(verified_base_comp_status(jd, '', 250000), 'meets_floor')


    def test_total_compensation_and_ote_do_not_qualify(self):
        for jd in ['Total compensation: $365,000 – $565,000 USD', 'Annual Salary: $365,000 – $565,000 USD OTE including commissions']:
            self.assertEqual(verified_base_comp_status(jd, '', 250000), 'unknown')

class SalaryCurrencyPlacement(unittest.TestCase):
    def test_usd_after_each_amount_is_verified(self):
        self.assertEqual(verified_base_comp_status('Annual base salary range is 272,000 USD - 425,500 USD. Benefits additional.', '', 250000),'meets_floor')
    def test_yearly_unit_does_not_hide_overlapping_upper_bound(self):
        self.assertEqual(verified_base_comp_status('Annual USD base salary: $230,000/year - $300,000/year.', '', 250000),'potentially_meets_floor')
    def test_currency_postfix_cannot_promote_total_compensation(self):
        self.assertEqual(verified_base_comp_status('Total compensation range is 272,000 USD - 425,500 USD.', '', 250000),'unknown')

    def test_preceding_bonus_cannot_be_mistaken_for_base(self):
        self.assertEqual(verified_base_comp_status('Bonus $500,000. Annual USD base salary: $200,000.', '', 250000),'below_floor')

    def test_real_unicode_dash_preserves_upper_bound(self):
        self.assertEqual(verified_base_comp_status('Annual USD base salary: $230,000 \u2013 $300,000.', '', 250000),'potentially_meets_floor')
