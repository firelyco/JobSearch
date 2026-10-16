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
def review():
 return {'requirements':[{'id':f'R{i+1}','match':'direct','critical':True,'required':True,'hard_gate_supported':False,'explanation':'Original evidence supports this match.'} for i in range(3)],'accepted_differentiators':['org_builder','ai_platform_delivery'],'scope_supported':True,'location_supported':True,'coverage_complete':True,'missing_critical_requirements':[]}

class EvidenceDecision(unittest.TestCase):
 def evaluate(self,p=None,r=None,facts=None,comp='meets_floor',rules=None):return review_result(p or proposal(),r or review(),facts or FACTS,JD,JOB,comp,rules)
 def test_strong_requires_direct_reviewed_requirements(self):
  result=self.evaluate();self.assertEqual(result.recommendation,'strong');self.assertEqual(result.public['tier'],'top_tier')
 def test_two_edges_cannot_cover_unknown_domain(self):
  r=review();r['requirements'][1]['match']='unknown';result=self.evaluate(r=r);self.assertEqual(result.recommendation,'reach')
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
  facts=copy.deepcopy(FACTS);facts[2]['kind']='skill';self.assertEqual(self.evaluate(facts=facts).recommendation,'reach')
 def test_missing_evidence_is_not_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(hard_gate=True,evidence_ids=[]);r['requirements'][1].update(match='gap',hard_gate_supported=True)
  self.assertEqual(self.evaluate(p=p,r=r).recommendation,'reach')
 def test_confirmed_exclusion_is_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(hard_gate=True,evidence_ids=[],exclusion_rule_key='hard_domain_gates',exclusion_quote='Specialized silicon design');r['requirements'][1].update(match='gap',hard_gate_supported=True)
  self.assertEqual(self.evaluate(p=p,r=r,rules={'hard_domain_gates':['Specialized silicon design']}).recommendation,'not')
 def test_preferred_requirement_cannot_be_hard_gate(self):
  p=proposal();r=review();p['requirements'][1].update(required=False,hard_gate=True);r['requirements'][1].update(required=False,match='unknown',hard_gate_supported=True)
  self.assertNotEqual(self.evaluate(p=p,r=r).recommendation,'not')
 def test_compensation_does_not_change_career_fit(self):
  result=self.evaluate(comp='potentially_meets_floor');self.assertEqual(result.public['career_fit'],'strong');self.assertEqual(result.recommendation,'medium')
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
  result=assess({'experience':[{'achievements':[{'details':'Delivered a shared platform program.'}]}]},JOB,'',client=FakeClient([]),model='test',rules={});self.assertEqual(result.recommendation,'review')

class ReviewerCorrections(unittest.TestCase):
 def test_review_can_correct_primary_coverage_without_weakening_matches(self):
  p=proposal();p['coverage_complete']=False
  result=review_result(p,review(),FACTS,JD,JOB,'meets_floor')
  self.assertEqual(result.recommendation,'strong')
  r=review();r['requirements'][1]['match']='unknown'
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'reach')
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

class StructuredOutputSafety(unittest.TestCase):
 def test_malformed_model_output_is_private_review_not_strong(self):
  profile={'experience':[{'achievements':[{'details':'Delivered a shared software platform program.'}]}]}
  result=assess(profile,JOB,JD*3,client=FakeClient(['malformed private model output']),model='test',rules={})
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
