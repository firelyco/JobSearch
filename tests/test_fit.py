"""Production fit scoring fails closed without the private skill pipeline."""
import unittest
from unittest.mock import patch
from src import fit,fit_scorer

class SkillOnlyRouting(unittest.TestCase):
 def test_generic_fit_route_is_retired(self):
  with self.assertRaisesRegex(RuntimeError,'generic scoring is retired'):
   fit.run(jobs=[],profile={},existing={},rules={})
 def test_single_job_route_cannot_use_an_old_prompt(self):
  with self.assertRaisesRegex(RuntimeError,'skill required'):
   fit_scorer.score_fit({}, {},'job',rules={})
 def test_evidence_route_is_the_only_batch_route(self):
  with patch('src.evidence_batch.run',return_value={'done':{}}) as run:
   result=fit.run(jobs=[],profile={},existing={},rules={'evidence_required':True,'_fitment_skill':'skill'})
   self.assertIn('done',result);run.assert_called_once()
