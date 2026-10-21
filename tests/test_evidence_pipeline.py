import copy,json,os,unittest
from unittest.mock import patch
from src import evidence_pipeline as p,llm_client
from tests.test_evidence_fit import FACTS,JOB,JD,proposal,review,SKILL_RULES
from tests.test_verify import FakeClient

class ResumePipeline(unittest.TestCase):
 def setUp(self):
  self.saved=[]
  self.inventory=patch.object(p.core,'build_inventory',return_value=FACTS)
  self.inventory.start();self.addCleanup(self.inventory.stop)
 def run_assessment(self,responses,resume=None):
  client=FakeClient([json.dumps(r) if isinstance(r,dict) else r for r in responses])
  result=p.assess({},JOB,JD*3,client=client,model='test',rules=SKILL_RULES,resume=resume,checkpoint=self.saved.append)
  return result,client
 def test_normal_path_two_calls_and_same_decision(self):
  result,client=self.run_assessment([proposal(),review()])
  self.assertEqual(result.recommendation,'strong');self.assertEqual(len(client.calls),2)
  self.assertEqual(self.saved[-1]['stage'],'complete')
  self.assertEqual(result.private['execution']['calls_this_attempt'],2)
 def test_malformed_review_preserves_extraction_and_retry_uses_one_call(self):
  first,client=self.run_assessment([proposal(),'{"requirements":['])
  self.assertEqual(first.recommendation,'review')
  checkpoint=self.saved[-1]
  self.assertEqual(checkpoint['stage'],'review');self.assertEqual(checkpoint['extraction'],proposal())
  second,client=self.run_assessment([review()],checkpoint)
  self.assertEqual(second.recommendation,'strong');self.assertEqual(len(client.calls),1)
  self.assertEqual(second.private['execution']['resumed_from'],'review')
 def test_completed_checkpoint_recomputes_decision_without_model_calls(self):
  self.run_assessment([proposal(),review()])
  result,client=self.run_assessment([],self.saved[-1])
  self.assertEqual(result.recommendation,'strong');self.assertEqual(len(client.calls),0)
 def test_changed_input_cannot_reuse_previous_checkpoint(self):
  self.run_assessment([proposal(),review()]);checkpoint=self.saved[-1]
  checkpoint['input_hash']='different'
  result,client=self.run_assessment([proposal(),review()],checkpoint)
  self.assertEqual(len(client.calls),2)
 def test_network_failure_keeps_last_successful_stage(self):
  responses=iter([llm_client.LLMResponse(json.dumps(proposal()),1,1,'test'),TimeoutError('gateway')])
  def call(*a,**kw):
   value=next(responses)
   if isinstance(value,Exception):raise value
   return value
  with patch.object(p.llm_client,'call',side_effect=call),self.assertRaises(TimeoutError):
   self.run_assessment([])
  self.assertEqual(self.saved[-1]['stage'],'review')
  self.assertEqual(self.saved[-1]['metrics'][-1]['error'],'TimeoutError')
 def test_truncated_output_never_becomes_a_verdict(self):
  response=llm_client.LLMResponse(json.dumps(proposal()),10,8000,'test',finish_reason='length')
  with patch.object(p.llm_client,'call',return_value=response):result,_=self.run_assessment([])
  self.assertEqual(result.recommendation,'review')
  self.assertEqual(self.saved[-1]['stage'],'extraction')
  self.assertEqual(self.saved[-1]['metrics'][-1]['error'],'truncated_output')
 def test_targeted_repair_preserves_unmodified_requirements(self):
  original=proposal();original['requirements'][0]['quote']='An invented quote.'
  fixed=proposal();fixed['requirements']=fixed['requirements'][:1]
  result,client=self.run_assessment([original,review(),fixed,review()])
  self.assertEqual(result.recommendation,'strong')
  self.assertEqual(result.private['requirements'][1]['quote'],proposal()['requirements'][1]['quote'])
  self.assertEqual(len(client.calls),4)
  request=json.loads(client.calls[2]['messages'][0]['content'])
  self.assertEqual(len(request['broken_requirements']),1)
 def test_repair_cannot_remove_or_change_intact_requirements(self):
  delta=proposal();delta['requirements'][1]['quote']='Different requirement'
  with self.assertRaises(ValueError):p.merge_repair(proposal(),delta,{'R1'},[])
 def test_repair_cannot_add_unrequested_requirements(self):
  delta=proposal();delta['requirements']=[{**delta['requirements'][0],'id':'R99'}]
  with self.assertRaises(ValueError):p.merge_repair(proposal(),delta,set(),['An actually omitted requirement'])
 def test_repair_is_independently_reviewed_and_bounded(self):
  bad=review();bad.update(coverage_complete=False,missing_critical_requirements=['R1'])
  result,client=self.run_assessment([proposal(),bad,bad])
  self.assertEqual(result.recommendation,'review');self.assertEqual(len(client.calls),3)
  again,client=self.run_assessment([review()],self.saved[-1])
  self.assertEqual(again.recommendation,'strong');self.assertEqual(len(client.calls),1)
 def test_checkpoint_storage_failure_prevents_new_rating(self):
  with self.assertRaises(RuntimeError):
   p.assess({},JOB,JD*3,client=FakeClient([json.dumps(proposal())]),model='test',rules=SKILL_RULES,
            checkpoint=lambda state:(_ for _ in ()).throw(RuntimeError('storage offline')))
 def test_invented_evidence_still_cannot_be_strong(self):
  bad=proposal();bad['requirements'][0]['evidence_ids']=['invented']
  result,_=self.run_assessment([bad,review(),review()])
  self.assertNotEqual(result.recommendation,'strong')
 def test_missing_jd_does_not_call_model(self):
  result=p.assess({},JOB,'',client=FakeClient([]),model='test',rules=SKILL_RULES)
  self.assertEqual(result.recommendation,'review')
 def test_no_private_information_in_public_metrics(self):
  result,_=self.run_assessment([proposal(),review()])
  public=json.dumps(result.public)
  self.assertNotIn(FACTS[0]['text'],public);self.assertNotIn('metrics',public)


 def test_redundant_intact_rows_do_not_overwrite_originals(self):
  delta=proposal();delta['requirements'][1]['match']='unknown'
  result=p.merge_repair(proposal(),delta,{'R1'},[])
  self.assertEqual(result['requirements'][1],proposal()['requirements'][1])
 def test_new_missing_quote_with_colliding_id_gets_a_new_id(self):
  delta=proposal();delta['requirements']=[{**delta['requirements'][0],'quote':'A genuinely omitted mandatory requirement.'}]
  result=p.merge_repair(proposal(),delta,set(),['A genuinely omitted mandatory requirement.'])
  self.assertEqual(len(result['requirements']),4)
  self.assertEqual(result['requirements'][0],proposal()['requirements'][0])
  self.assertEqual(result['requirements'][-1]['id'],'R4')

if __name__=='__main__':unittest.main()
