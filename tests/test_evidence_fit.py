import copy,json,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from src import fit
from src.evidence_fit import build_inventory,review_result,assess,input_hash,EvidenceVerdict
from tests.test_verify import FakeClient

FACTS=[{'id':'E1','kind':'achievement','text':'Delivered shared software platform programs across multiple product teams.'},
 {'id':'E2','kind':'achievement','text':'Built and managed a team of program managers on a large portfolio.'},
 {'id':'E3','kind':'achievement','text':'Delivered common services and developer adoption programs.'}]
QUOTES=['Lead cross-functional platform delivery programs.','Deliver shared software services across product teams.','Own senior organization-wide program outcomes.']
JD=' '.join(QUOTES)+' Annual Salary: $300,000 - $400,000 USD.'
JOB={'source':'test','company':'example','id':'1','title':'Staff TPM','location':'Remote, United States','url':'https://example.com/job'}
def proposal():
 requirements=[{'id':f'R{i+1}','quote':q,'category':category,'critical':True,'required':True,'match':'direct','evidence_ids':[eid],'explanation':'Comparable delivery','hard_gate':False} for i,(q,category,eid) in enumerate(zip(QUOTES,['program','domain','scope'],['E1','E3','E2']))]
 return {'requirements':requirements,'differentiators':[{'kind':'org_builder','evidence_ids':['E2'],'requirement_ids':['R3']},{'kind':'ai_platform_delivery','evidence_ids':['E3'],'requirement_ids':['R2']}],
 'scope':{'kind':'senior_ic','quote':QUOTES[2]},'location':{'status':'eligible','quote':JOB['location']},'coverage_complete':True}
SKILL_RULES={'_fitment_skill':'THREE VERDICTS ONLY Strong Middle Pass', 'fitment_skill_hash':'test-skill','comp_floor_usd':250000}

def review():
 return {'skill_verdict':'strong','decisive_reason':'Comparable program delivery and relevant differentiated experience.','gate_requirement_ids':[],'requirements':[{'id':f'R{i+1}','match':'direct','critical':True,'required':True,'hard_gate_supported':False,'explanation':'Original evidence supports this match.'} for i in range(3)],'accepted_differentiators':['org_builder','ai_platform_delivery'],'scope_supported':True,'location_supported':True,'coverage_complete':True,'missing_critical_requirements':[]}

class EvidenceDecision(unittest.TestCase):
 def evaluate(self,p=None,r=None,facts=None,comp='meets_floor',rules=None):return review_result(p or proposal(),r or review(),facts or FACTS,JD,JOB,comp,rules)
 def test_strong_requires_direct_reviewed_requirements(self):
  result=self.evaluate();self.assertEqual(result.recommendation,'strong');self.assertEqual(result.public['tier'],'strong')
 def test_two_edges_cannot_cover_unknown_domain(self):
  r=review();r['requirements'][1]['match']='unknown';result=self.evaluate(r=r);self.assertEqual(result.recommendation,'medium')
 def test_one_direct_edge_is_strong_not_top_tier(self):
  r=review();r['accepted_differentiators']=['org_builder'];self.assertEqual(self.evaluate(r=r).public['tier'],'strong')
 def test_zero_edges_is_middle(self):
  r=review();r['accepted_differentiators']=[];self.assertEqual(self.evaluate(r=r).recommendation,'medium')
 def test_missing_critical_requirement_blocks_strong(self):
  r=review();r['missing_critical_requirements']=['Cluster operations'];self.assertEqual(self.evaluate(r=r).recommendation,'review')
 def test_invented_quote_blocks_strong(self):
  p=proposal();p['requirements'][0]['quote']='This quotation was invented';self.assertEqual(self.evaluate(p=p).recommendation,'review')
 def test_invented_evidence_blocks_strong(self):
  p=proposal();p['requirements'][0]['evidence_ids']=['invented'];self.assertEqual(self.evaluate(p=p).recommendation,'review')
 def test_skills_list_is_not_direct_delivery_proof(self):
  facts=copy.deepcopy(FACTS);facts[2]['kind']='skill';self.assertEqual(self.evaluate(facts=facts).recommendation,'medium')
 def test_missing_evidence_is_not_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(hard_gate=True,evidence_ids=[]);r['requirements'][1].update(match='gap',hard_gate_supported=True)
  self.assertEqual(self.evaluate(p=p,r=r).recommendation,'medium')
 def test_confirmed_exclusion_is_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(hard_gate=True,evidence_ids=[],exclusion_rule_key='hard_domain_gates',exclusion_quote='Specialized silicon design');r['requirements'][1].update(match='gap',hard_gate_supported=True)
  self.assertEqual(self.evaluate(p=p,r=r,rules={'hard_domain_gates':['Specialized silicon design']}).recommendation,'not')
 def test_preferred_requirement_cannot_be_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(required=False,hard_gate=True);r['requirements'][1].update(required=False,match='unknown',hard_gate_supported=True)
  self.assertNotEqual(self.evaluate(p=p,r=r).recommendation,'not')
 def test_compensation_does_not_change_career_fit(self):
  for comp in ['potentially_meets_floor','unknown']:
   result=self.evaluate(comp=comp);self.assertEqual(result.public['career_fit'],'strong');self.assertEqual(result.recommendation,'strong')
  self.assertEqual(self.evaluate(comp='below_floor').recommendation,'medium')
 def test_location_uncertainty_is_not_eligible(self):
  r=review();r['location_supported']=False;result=self.evaluate(r=r);self.assertFalse(result.public['eligible']);self.assertEqual(result.recommendation,'medium')
 def test_bogus_boolean_does_not_pass_coverage(self):
  p=proposal();p['coverage_complete']='true';self.assertEqual(self.evaluate(p=p).recommendation,'review')
 def test_inventory_retains_details_and_excludes_assumptions(self):
  profile={'experience':[{'company':'Example','achievements':[{'summary':'Delivery','details':'Delivered an end-to-end platform program.'},{'summary':'Invented result','details':'Assumed twenty percent improvement'},{'summary':'Unverified claim','details':'Not confirmed achievement','verified':False}]}]}
  facts=build_inventory(profile);self.assertEqual(len(facts),1);self.assertIn('end-to-end',facts[0]['text'])
 def test_public_payload_contains_no_candidate_sources(self):
  result=self.evaluate();public=json.dumps(result.public);self.assertNotIn(FACTS[0]['text'],public);self.assertNotIn('evidence_ids',public)
 def test_missing_jd_is_review_without_model_call(self):
  result=assess({'experience':[{'achievements':[{'details':'Delivered a shared platform program.'}]}]},JOB,'',client=FakeClient([]),model='test',rules=SKILL_RULES);self.assertEqual(result.recommendation,'review')

class ReviewerCorrections(unittest.TestCase):
 def test_review_can_correct_primary_coverage_without_weakening_matches(self):
  p=proposal();p['coverage_complete']=False
  result=review_result(p,review(),FACTS,JD,JOB,'meets_floor')
  self.assertEqual(result.recommendation,'strong')
  r=review();r['requirements'][1]['match']='unknown'
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
 def test_review_can_correct_generic_leadership_miscategorized_as_domain(self):
  p=proposal();p['requirements'][0]['category']='domain';r=review();r['requirements'][0]['category']='program'
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'strong')

class CapabilityBridges(unittest.TestCase):
 def test_documented_program_bridge_is_allowed_but_not_unknown(self):
  p=proposal();p['requirements'].append({'id':'R4','quote':QUOTES[0],'category':'program','critical':True,'required':True,'match':'transferable','evidence_ids':['E1']})
  r=review();r['requirements'].append({'id':'R4','match':'transferable','evidence_ids':['E1']})
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'strong')
  r['requirements'][-1]['match']='unknown'
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
 def test_people_leadership_shortfall_is_not_filled_by_program_bridge(self):
  p=proposal();p['requirements'][1]['category']='people';r=review();r['requirements'][1]['match']='transferable'
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
 def test_curly_apostrophe_scope_citation_matches_plain_apostrophe(self):
  p=proposal();p['scope']['quote']="You'll own senior organization-wide program outcomes."
  jd=JD+" You\u2019ll own senior organization-wide program outcomes."
  self.assertEqual(review_result(p,review(),FACTS,jd,JOB,'meets_floor').recommendation,'strong')

class FinalReviewSafety(unittest.TestCase):
 def test_reviewer_can_restore_a_substantive_required_technical_qualification(self):
  p=proposal();p['requirements'][1].update(critical=False,required=False)
  r=review();r['requirements'][1].update(critical=True,required=True,category='domain',match='unknown')
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
 def test_differentiator_can_include_a_skill_when_concrete_achievement_also_cited(self):
  facts=copy.deepcopy(FACTS)+[{'id':'skill','kind':'skill','text':'Program leadership'}]
  p=proposal();p['differentiators'][0]['evidence_ids'].append('skill');r=review();r['accepted_differentiators']=['org_builder']
  self.assertEqual(review_result(p,r,facts,JD,JOB,'meets_floor').recommendation,'strong')
 def test_entirely_unsupported_specialist_core_is_pass_not_middle(self):
  r=review()
  for row in r['requirements']:row['match']='unknown'
  self.assertEqual(review_result(proposal(),r,FACTS,JD,JOB,'meets_floor').recommendation,'not')
 def test_repair_is_reviewed_and_bounded(self):
  r=review();r['coverage_complete']=False;r['missing_critical_requirements']=['A required responsibility was omitted']
  for final_review,expected in [(review(),'strong'),(r,'review')]:
   fake=FakeClient([json.dumps(proposal()),json.dumps(r),json.dumps(final_review)])
   with patch('src.evidence_fit.build_inventory',return_value=FACTS):
    result=assess({},JOB,JD*3,client=fake,model='extractor',rules={**SKILL_RULES,'evidence_review_model':'reviewer'})
   self.assertEqual(result.public['career_fit'],expected)
   self.assertTrue(result.private['repair_attempted']);self.assertEqual(len(fake.calls),3)
   self.assertEqual(fake.calls[1]['model'],'reviewer')

class StructuredOutputSafety(unittest.TestCase):
 def test_malformed_model_output_is_private_review_not_strong(self):
  profile={'experience':[{'achievements':[{'details':'Delivered a shared software platform program.'}]}]}
  result=assess(profile,JOB,JD*3,client=FakeClient(['malformed private model output']),model='test',rules=SKILL_RULES)
  self.assertEqual(result.recommendation,'review')
  self.assertEqual(result.private['model_error']['phase'],'extraction')
  self.assertNotIn('malformed private model output',json.dumps(result.public))
 def test_credentials_are_available_as_evidence(self):
  facts=build_inventory({'education':[{'degree':'Bachelor of Computer Science','school':'Example school'}]})
  self.assertEqual(facts[0]['kind'],'credential')
 def test_verifiable_section_prefix_is_removed_without_inventing_words(self):
  p=proposal();p['requirements'][1]['quote']='Minimum qualifications: '+QUOTES[1]
  result=review_result(p,review(),FACTS,JD,JOB,'meets_floor')
  self.assertEqual(result.recommendation,'strong')
  self.assertEqual(result.private['requirements'][1]['quote'],QUOTES[1])

class EvidenceCache(unittest.TestCase):
 def test_profile_and_jd_changes_invalidate_cache(self):
  profile={'experience':[{'achievements':[{'details':'Delivered a shared software platform program.'}]}]};rules={'evidence_required':True,'fit_policy_version':'test'};key='test:example:1';now=datetime(2026,10,15,tzinfo=timezone.utc)
  def score(profile,job,jd,**kwargs):
   return EvidenceVerdict('strong','Reviewed','none',{'input_hash':input_hash(profile,job,jd,rules,fit._fit_model())},{'inventory':FACTS})
  with patch('src.fit_scorer.score_fit',side_effect=score) as scorer,patch('src.evidence_batch.save_private'):
   first=fit.run(jobs=[JOB],profile=profile,existing={},rules=rules,jd_text_for={key:JD},fake_client=FakeClient([]),now=now)
   fit.run(jobs=[JOB],profile=profile,existing=first,rules=rules,jd_text_for={key:JD},fake_client=FakeClient([]),now=now+timedelta(hours=1));self.assertEqual(scorer.call_count,1)
   altered=copy.deepcopy(profile);altered['experience'][0]['achievements'][0]['details']+=' Additional verified scope.'
   fit.run(jobs=[JOB],profile=altered,existing=first,rules=rules,jd_text_for={key:JD},fake_client=FakeClient([]),now=now+timedelta(hours=1));self.assertEqual(scorer.call_count,2)
   fit.run(jobs=[JOB],profile=profile,existing=first,rules=rules,jd_text_for={key:JD+' Changed qualifications.'},fake_client=FakeClient([]),now=now+timedelta(hours=7));self.assertEqual(scorer.call_count,3)

class PrivateReferenceProvenance(unittest.TestCase):
 def test_documented_kb_references_keep_source_and_exclude_assumptions(self):
  rules={'reference_evidence':[{'status':'documented','text':'Led a shared platform roadmap across consuming product teams.','source':'Private KB:127','company':'Example'}, {'status':'documented','assumed':True,'text':'Hypothetical ownership of inference fleet routing.'}, {'status':'unconfirmed','text':'Designed an undisclosed production architecture.'}]}
  facts=build_inventory({},rules)
  self.assertEqual(len(facts),1);self.assertEqual(facts[0]['source'],'Private KB:127');self.assertEqual(facts[0]['kind'],'kb_reference')

class ProviderUnavailable(unittest.TestCase):
 def test_credit_block_retains_ratings_without_model_calls(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'profile.json').write_text('{}');(root/'rules.json').write_text(json.dumps({'fit_api_blocked':True,'calibration_pending':True,'fit_policy_version':'previous'}))
   with patch.object(fit,'PROFILE_FILE',root/'profile.json'),patch.object(fit,'FIT_RULES_FILE',root/'rules.json'),patch.object(fit,'DOCS_DIR',root),patch.object(fit,'run') as scorer:
    self.assertEqual(fit.main([]),0);scorer.assert_not_called()
   policy=json.loads((root/'fit_policy.json').read_text());self.assertTrue(policy['calibration_pending']);self.assertEqual(policy['provider_status'],'insufficient_credits')


class ExplicitArchitectureExclusion(unittest.TestCase):
 def evaluate(self, quote, *, excluded=True, required=True):
  p=proposal();r=review()
  p['requirements'][0].update(quote=quote,category='program',required=required)
  r['requirements'][0].update(required=required)
  rules={'never_claim':['Architecture ownership (engages on trade-offs only)']} if excluded else {}
  return review_result(p,r,FACTS,JD+' '+quote,JOB,'meets_floor',rules)
 def test_explicit_required_ownership_overrides_model_direct_match(self):
  result=self.evaluate('Own the agent platform architecture: orchestration and production deployment.')
  self.assertEqual(result.recommendation,'not')
  self.assertEqual(result.private['requirements'][0]['exclusion_rule_key'],'never_claim')
 def test_coordination_is_not_architecture_ownership(self):
  result=self.evaluate('Coordinate with engineers who own the agent platform architecture.')
  self.assertEqual(result.recommendation,'strong')
 def test_program_architecture_is_not_technical_architecture(self):
  self.assertEqual(self.evaluate('Own the program architecture and delivery mechanisms.').recommendation,'strong')
 def test_preferred_ownership_is_not_a_hard_gate(self):
  self.assertNotEqual(self.evaluate('Own the agent platform architecture.',required=False).recommendation,'not')
 def test_no_implicit_exclusion_without_explicit_user_rule(self):
  self.assertEqual(self.evaluate('Own the agent platform architecture.',excluded=False).recommendation,'strong')
