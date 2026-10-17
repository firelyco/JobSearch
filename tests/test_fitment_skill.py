import os,unittest,copy
from unittest.mock import patch
from src import fitment_skill
from src.evidence_fit import review_result,input_hash
from tests.test_evidence_fit import proposal,review,FACTS,JD,JOB,SKILL_RULES

class SkillPolicy(unittest.TestCase):
 def test_missing_skill_stops_instead_of_falling_back(self):
  with patch.dict(os.environ,{'FITMENT_SKILL_FILE':'missing-fitment-skill.md'},clear=True):
   with self.assertRaisesRegex(RuntimeError,'skill is missing'):fitment_skill.attach({})
 def test_old_five_verdict_file_is_rejected(self):
  with patch.dict(os.environ,{'FITMENT_SKILL_TEXT':'Top tier Strong Middle Reach Pass'},clear=True):
   with self.assertRaisesRegex(RuntimeError,'must define'):fitment_skill.attach({})
 def test_skill_edits_invalidate_cached_assessment(self):
  hashes=[]
  for text in ['THREE VERDICTS ONLY Strong Middle Pass version one','THREE VERDICTS ONLY Strong Middle Pass version two']:
   with patch.dict(os.environ,{'FITMENT_SKILL_TEXT':text},clear=True):
    rules=fitment_skill.attach({});hashes.append(input_hash({},JOB,JD,rules,'test'))
  self.assertNotEqual(*hashes)
 def test_legacy_rule_classifications_are_not_sent_as_policy(self):
  context=fitment_skill.context(FACTS,{**SKILL_RULES,'hard_domain_gates':['contradictory old rule']})
  self.assertNotIn('rules',context);self.assertNotIn('contradictory old rule',str(context))
 def test_old_top_tier_vote_cannot_be_published(self):
  r=review();r['skill_verdict']='top_tier'
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.recommendation,'review');self.assertIsNone(result.public['verdict'])
 def test_pass_needs_cited_central_exclusion(self):
  r=review();r['skill_verdict']='pass'
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.recommendation,'review')
 def test_cited_skill_exclusion_can_override_program_label(self):
  rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+' Specialized data-center controls are excluded.'}
  r=review();row=r['requirements'][0]
  row.update(match='gap',hard_gate_supported=True,exclusion_rule_key='fitment_skill',exclusion_quote='Specialized data-center controls are excluded.')
  r.update(skill_verdict='pass',gate_requirement_ids=['R1'])
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',rules)
  self.assertEqual(result.recommendation,'not');self.assertEqual(result.public['verdict'],'pass')
 def test_unproven_domain_does_not_get_strong_from_generic_leadership(self):
  r=review();r['requirements'][1].update(match='unknown')
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.public['verdict'],'middle')
 def test_public_output_does_not_expose_skill_or_private_rationale(self):
  r=review();r['decisive_reason']='Private career evidence belongs only in authenticated storage.'
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertNotIn('Private career evidence',str(result.public));self.assertNotIn('_fitment_skill',str(result.public))
