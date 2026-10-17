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
  for text in ['THREE VERDICTS ONLY Strong Middle Pass version one Target annual USD base salary is $250,000','THREE VERDICTS ONLY Strong Middle Pass version two Target annual USD base salary is $250,000']:
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

class ExplicitSkillChecks(unittest.TestCase):
 def test_named_exclusions_are_derived_from_file_not_a_company_list(self):
  text='- **Specialized domain AS THE JOB ITSELF:** controls (Example Campus/Controls).'
  matches=fitment_skill.named_exclusions(text,{'company':'Example'},'Campus Controls program leadership')
  self.assertEqual(matches[0]['example'],'Example Campus/Controls')
  self.assertFalse(fitment_skill.named_exclusions(text,{'company':'Example'},'Consumer software platform'))
 def test_a_named_exclusion_cannot_be_silently_called_strong(self):
  rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+'\n- **Specialized domain AS THE JOB ITSELF:** controls (example platform).'}
  result=review_result(proposal(),review(),FACTS,JD,JOB,'unknown',rules)
  self.assertEqual(result.recommendation,'review')
 def test_missing_skill_steps_are_processing_failure_not_middle(self):
  r=review();r['policy_steps']=r['policy_steps'][:-1]
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.recommendation,'review');self.assertIsNone(result.public['verdict'])

class EvidenceReuse(unittest.TestCase):
 def prior(self):
  return {'inventory':FACTS,'job_text':JD,'job':{k:JOB.get(k) for k in ('title','company','location','url')},'extraction':proposal(),'input_hash':'old-policy','independent_review':{'skill_verdict':'strong'}}
 def seed(self,prior=None,jd=JD):
  from src.evidence_batch import reusable_extraction
  with patch('src.evidence_batch.build_inventory',return_value=FACTS):
   return reusable_extraction(prior or self.prior(),{},JOB,jd,SKILL_RULES,'test','new-policy')
 def test_unchanged_evidence_reuses_extraction_but_never_old_verdict(self):
  seed=self.seed();self.assertEqual(seed['stage'],'review');self.assertEqual(seed['input_hash'],'new-policy')
  self.assertNotIn('independent_review',seed);self.assertNotIn('skill_verdict',seed)
 def test_changed_job_text_requires_new_extraction(self):
  self.assertIsNone(self.seed(jd=JD+' Added mandatory technical qualification.'))
 def test_changed_inventory_requires_new_extraction(self):
  prior=self.prior();prior['inventory']=[];self.assertIsNone(self.seed(prior))
 def test_changed_job_identity_requires_new_extraction(self):
  prior=self.prior();prior['job']['url']='https://different.example';self.assertIsNone(self.seed(prior))

class ExplicitDomainInterpretation(unittest.TestCase):
 def test_named_exclusion_requires_matching_central_work(self):
  skill=SKILL_RULES['_fitment_skill']+'\n- **Specialized domain AS THE JOB ITSELF:** platform infrastructure (Example Campus/Controls).'
  p=proposal();p['requirements'][1]['quote']='Manage platform infrastructure in Campus Controls.'
  jd=JD+' Manage platform infrastructure in Campus Controls.'
  r=review();r['skill_verdict']='middle'
  result=review_result(p,r,FACTS,jd,JOB,'unknown',{**SKILL_RULES,'_fitment_skill':skill})
  self.assertEqual(result.public['verdict'],'pass')
  self.assertEqual(result.private['explicit_skill_exclusions'][0]['requirement_id'],'R2')
 def test_a_preferred_domain_is_not_a_deterministic_exclusion(self):
  skill=SKILL_RULES['_fitment_skill']+'\n- **Specialized domain AS THE JOB ITSELF:** platform infrastructure (Example Campus/Controls).'
  p=proposal();p['requirements'][1].update(quote='Manage platform infrastructure in Campus Controls.',required=False)
  r=review();r['requirements'][1]['required']=False;r['skill_verdict']='middle'
  result=review_result(p,r,FACTS,JD+' Manage platform infrastructure in Campus Controls.',JOB,'unknown',{**SKILL_RULES,'_fitment_skill':skill})
  self.assertNotEqual(result.public.get('verdict'),'pass')

class SameSkillReviewReuse(unittest.TestCase):
 def audit(self):
  return {'inventory':FACTS,'job_text':JD,'job':{k:JOB.get(k) for k in ('title','company','location','url')},'extraction':proposal(),'input_hash':'before-code-fix','independent_review':review(),'public':{'fitment_skill_hash':'test-skill'},'model':'test','review_model':'test'}
 def replay(self,audit):
  from src.evidence_batch import replay_same_skill
  with patch('src.evidence_batch.build_inventory',return_value=FACTS):
   return replay_same_skill(audit,{},JOB,JD,SKILL_RULES,'test','after-code-fix')
 def test_same_policy_review_is_revalidated_without_model_calls(self):
  result=self.replay(self.audit());self.assertEqual(result.public['verdict'],'strong')
  self.assertEqual(result.private['execution']['calls_this_attempt'],0)
 def test_changed_skill_cannot_replay_old_review(self):
  audit=self.audit();audit['public']['fitment_skill_hash']='previous-file';self.assertIsNone(self.replay(audit))
 def test_invalid_review_cannot_replay(self):
  audit=self.audit();audit['independent_review']['policy_steps']=[];self.assertIsNone(self.replay(audit))
 def test_changed_model_cannot_replay(self):
  audit=self.audit();audit['review_model']='different';self.assertIsNone(self.replay(audit))

class CompletedMiddleWithoutEdge(unittest.TestCase):
 def test_valid_review_with_no_supported_edge_is_middle_not_pending(self):
  r=review();r['accepted_differentiators']=[];r['accepted_differentiator_evidence']=[]
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.public['verdict'],'middle');self.assertTrue(result.public['validated'])
  self.assertEqual(result.private['independent_review']['skill_verdict'],'strong')
 def test_stakeholder_influence_is_not_a_people_management_gate(self):
  p=proposal();p['requirements'][0]['category']='people'
  r=review();r['requirements'][0].update(category='people',match='transferable')
  result=review_result(p,r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.public['verdict'],'strong')
 def test_supported_bridge_is_decided_by_skill_review_not_legacy_domain_cap(self):
  r=review();r['requirements'][1]['match']='transferable'
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.public['verdict'],'strong')
 def test_unknown_central_capability_still_cannot_be_strong(self):
  r=review();r['requirements'][1]['match']='unknown'
  result=review_result(proposal(),r,FACTS,JD,JOB,'unknown',SKILL_RULES)
  self.assertEqual(result.public['verdict'],'middle')

class GateProofFromSkill(unittest.TestCase):
 def test_mandatory_independent_architecture_respects_file_guardrail(self):
  line='- **Architecture ownership** (engages on trade-offs, does not own design).'
  rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+'\n'+line}
  p=proposal();q='Experience in architectural design or system design independent of an architect.'
  p['requirements'][0]['quote']=q
  result=review_result(p,review(),FACTS,JD+' '+q,JOB,'unknown',rules)
  self.assertEqual(result.public['verdict'],'pass')
 def test_architecture_discussion_is_not_design_ownership(self):
  line='- **Architecture ownership** (engages on trade-offs, does not own design).'
  rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+'\n'+line}
  p=proposal();q='Facilitate architecture discussions with engineers.';p['requirements'][0]['quote']=q
  result=review_result(p,review(),FACTS,JD+' '+q,JOB,'unknown',rules)
  self.assertEqual(result.public['verdict'],'strong')
 def test_cited_step_zero_exclusion_does_not_need_duplicate_gate_boolean(self):
  line='Reject non-TPM disciplines.';rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+' '+line}
  p=proposal();q='Own the R&D finance budget.';p['requirements'][0]['quote']=q
  r=review();r['policy_steps'][0].update(finding='excluded',policy_quote=line,requirement_ids=['R1'])
  r.update(skill_verdict='pass',gate_requirement_ids=['R1'])
  result=review_result(p,r,FACTS,JD+' '+q,JOB,'unknown',rules)
  self.assertEqual(result.public['verdict'],'pass')

class EarlySkillPass(unittest.TestCase):
 def setup_gate(self):
  line='Reject non-TPM disciplines.';rules={**SKILL_RULES,'_fitment_skill':SKILL_RULES['_fitment_skill']+' '+line}
  p=proposal();q='Own the R&D finance budget.';p['requirements'][0]['quote']=q
  r=review();r.update(coverage_complete=False,skill_verdict='pass',gate_requirement_ids=['R1'])
  r['policy_steps']=[{'step':'0','finding':'excluded','policy_quote':line,'requirement_ids':['R1'],'explanation':'Finance ownership is the central discipline.'}]
  return p,r,rules,JD+' '+q
 def test_proved_gate_does_not_require_unnecessary_later_steps(self):
  p,r,rules,jd=self.setup_gate()
  result=review_result(p,r,FACTS,jd,JOB,'unknown',rules)
  self.assertEqual(result.public['verdict'],'pass');self.assertTrue(result.public['validated'])
  self.assertTrue(result.private['stopped_after_skill_exclusion'])
  self.assertTrue(result.private['non_decisive_validation_notes'])
 def test_invented_decisive_quote_cannot_short_circuit(self):
  p,r,rules,jd=self.setup_gate();p['requirements'][0]['quote']='Invented financial job requirement.'
  result=review_result(p,r,FACTS,jd,JOB,'unknown',rules)
  self.assertEqual(result.recommendation,'review')
