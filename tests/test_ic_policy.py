"""The Markdown skill replaces legacy IC-only policy switches."""
import unittest
from src.evidence_fit import review_result
from tests.test_evidence_fit import proposal,review,FACTS,JD,JOB,SKILL_RULES

class ICPolicy(unittest.TestCase):
 def test_senior_ic_is_not_demoted_by_legacy_flag(self):
  result=review_result(proposal(),review(),FACTS,JD,JOB,'potentially_meets_floor',{**SKILL_RULES,'allow_ic_strong':False})
  self.assertEqual(result.recommendation,'strong')
 def test_unknown_pay_does_not_demote(self):
  self.assertEqual(review_result(proposal(),review(),FACTS,JD,JOB,'unknown',SKILL_RULES).recommendation,'strong')
 def test_only_below_maximum_excludes_salary(self):
  for comp,expected in [('below_floor','medium'),('potentially_meets_floor','strong'),('meets_floor','strong')]:
   result=review_result(proposal(),review(),FACTS,JD,JOB,comp,SKILL_RULES)
   self.assertEqual(result.recommendation,expected)
   self.assertEqual(result.public['eligible'],comp!='below_floor')
