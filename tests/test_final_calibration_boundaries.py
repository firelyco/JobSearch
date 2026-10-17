import copy,unittest
from src.evidence_fit import review_result
from tests.test_evidence_fit import proposal,review,FACTS,JD,JOB

class LocationAndEvidenceCorrections(unittest.TestCase):
 def test_bay_area_option_uses_stated_openness_not_current_residence(self):
  r=review();r['location_supported']=False;r['location']={'status':'conditional','quote':'Unverified location','reason':'Current residence unknown'}
  job={**JOB,'location':'San Francisco, CA | Seattle, WA'}
  self.assertEqual(review_result(proposal(),r,FACTS,JD,job,'meets_floor').recommendation,'strong')
 def test_seattle_only_remains_conditional(self):
  r=review();r['location']={'status':'conditional','quote':'Seattle, WA','reason':'Relocation question'}
  result=review_result(proposal(),r,FACTS,JD,{**JOB,'location':'Seattle, WA'},'meets_floor')
  self.assertEqual(result.recommendation,'medium');self.assertFalse(result.public['eligible'])
 def test_boston_in_uk_is_not_automatically_approved(self):
  r=review();r['location_supported']=False
  self.assertNotEqual(review_result(proposal(),r,FACTS,JD,{**JOB,'location':'Boston, UK'},'meets_floor').public['location_status'],'eligible')
 def test_corrected_differentiator_uses_real_source_and_requirement(self):
  p=proposal();p['differentiators']=[];r=review();r['accepted_differentiators']=[]
  r['accepted_differentiator_evidence']=[{'kind':'ai_platform_delivery','evidence_ids':['E1'],'requirement_ids':['R1']}]
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').public['differentiator_count'],1)
  r['accepted_differentiator_evidence'][0]['evidence_ids']=['invented']
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').public['differentiator_count'],0)
 def test_unknown_differentiator_kind_cannot_raise_rating(self):
  p=proposal();p['differentiators']=[];r=review();r['accepted_differentiator_evidence']=[{'kind':'employer_brand','evidence_ids':['E1'],'requirement_ids':['R1']}]
  self.assertEqual(review_result(p,r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
 def test_corrected_edge_never_overrides_specialist_gap(self):
  r=review();r['requirements'][1]['match']='unknown';r['accepted_differentiator_evidence']=[{'kind':'ai_platform_delivery','evidence_ids':['E1'],'requirement_ids':['R1']}]
  self.assertEqual(review_result(proposal(),r,FACTS,JD,JOB,'meets_floor').recommendation,'medium')
