import copy,unittest
from unittest.mock import patch
from src import assessment_cache as cache
from src import evidence_batch as batch
from src.evidence_fit import EvidenceVerdict

JOB={'source':'test','company':'example','id':'1','title':'Principal TPM','location':'Boston, MA','url':'https://example.com/1'}
KEY=batch.key(JOB);JD='Lead cross-functional technical programs and manage delivery risks.'
RULES={'fit_policy_version':'v1','evidence_required':True}

class PaidAssessmentCache(unittest.TestCase):
 def run_jobs(self,existing=None,job=JOB,model='model-a',stamp='2026-10-21T00:00:00+00:00',rescore=False):
  return batch.run(jobs=[job],profile={},existing=existing or {},rules=RULES,max_jobs=5,rescore=rescore,fake_client=object(),jd_text_for={KEY:JD},stamp=stamp,model=model,on_scored=None,job_keys=None)
 def test_model_and_review_model_do_not_change_assessment_context(self):
  self.assertEqual(batch.context_hash({},RULES,'a'),batch.context_hash({},RULES,'b'))
  self.assertEqual(cache.context({},RULES),cache.context({},{**RULES,'evidence_review_model':'other'}))
 def test_meaningful_changes_invalidate_identity_but_timestamps_and_whitespace_do_not(self):
  original=cache.identity({},JOB,JD,RULES)
  self.assertEqual(original,cache.identity({},{**JOB,'updated_at':'tomorrow'},JD+'\n ',RULES))
  self.assertNotEqual(original,cache.identity({},{**JOB,'title':'ML Research Manager'},JD,RULES))
  self.assertNotEqual(original,cache.identity({},JOB,JD+' Require automotive expertise.',RULES))
  self.assertNotEqual(original,cache.identity({},JOB,JD,{**RULES,'comp_floor_usd':300000}))
 def test_repeated_run_model_switch_and_source_timestamp_change_make_zero_ai_calls(self):
  v=EvidenceVerdict('medium','Reviewed','evidence',{'validated':True},{'inventory':[]})
  with patch('src.evidence_batch.fit_scorer.score_fit',return_value=v) as assess,patch('src.evidence_batch.save_private'):
   first=self.run_jobs();self.assertEqual(assess.call_count,1)
  with patch('src.evidence_batch.llm_client.build_client',side_effect=AssertionError('Paid call forbidden')) as client,patch('src.evidence_batch.load_private',side_effect=AssertionError('No private replay needed')):
   second=self.run_jobs(first,model='new-model',stamp='2026-10-23T00:00:00+00:00')
   third=self.run_jobs(second,job={**JOB,'updated_at':'2026-10-23'},model='another-model',stamp='2026-10-23T01:00:00+00:00')
   client.assert_not_called()
  self.assertEqual(second[KEY]['cache_status'],'reused');self.assertEqual(third[KEY]['job_snapshot_hash'],batch.snapshot_hash({**JOB,'updated_at':'2026-10-23'}))
  self.assertEqual(first[KEY]['scored_at'],third[KEY]['scored_at'])
 def test_legacy_exact_fingerprint_migration_does_not_repeat_or_change_assessment_date(self):
  old={'validated':True,'recommendation':'medium','context_hash':'legacy-model-context','input_hash':batch.input_hash({},JOB,JD,RULES,'model-a'),'scored_at':'2026-10-15T00:00:00+00:00'}
  with patch('src.evidence_batch.llm_client.build_client',side_effect=AssertionError('Paid call forbidden')):
   result=self.run_jobs({KEY:old})[KEY]
  self.assertEqual(result['scored_at'],old['scored_at']);self.assertEqual(result['context_hash'],cache.context({},RULES))
  self.assertEqual(result['assessment_cache_key'],cache.identity({},JOB,JD,RULES));self.assertEqual(result['cache_status'],'reused')
 def test_cache_only_validation_cannot_create_client_or_reserve_paid_attempt(self):
  with patch.dict('os.environ',{'FIT_CACHE_ONLY':'true'}),patch('src.evidence_batch.cache.reserve') as reserve,patch('src.evidence_batch.llm_client.build_client') as client:
   self.assertEqual(self.run_jobs(),{});reserve.assert_not_called();client.assert_not_called()
 def test_unavailable_posting_never_spends_or_overwrites_prior_assessment(self):
  old={KEY:{'recommendation':'medium','validated':True}}
  with patch('src.evidence_batch.llm_client.build_client') as client,patch('src.evidence_batch.cache.reserve') as reserve:
   result=batch.run(jobs=[JOB],profile={},existing=old,rules=RULES,max_jobs=1,rescore=False,fake_client=None,jd_text_for={KEY:''},stamp='2026-10-21',model='test',on_scored=None,job_keys=None)
   self.assertEqual(result,old);client.assert_not_called();reserve.assert_not_called()
 def test_pending_strong_precedes_new_jobs_and_freshness_only_checks(self):
  strong={**JOB,'id':'strong'};new={**JOB,'id':'new'};current={**JOB,'id':'current'}
  existing={batch.key(strong):{'recommendation':'strong','validated':True,'context_hash':'old'},batch.key(current):{'recommendation':'strong','validated':True,'context_hash':cache.context({},RULES),'job_snapshot_hash':batch.snapshot_hash(current),'jd_checked_at':'2020-01-01'}}
  v=EvidenceVerdict('medium','Reviewed','evidence',{'validated':True},{'inventory':[]})
  with patch('src.evidence_batch.fit_scorer.score_fit',return_value=v) as assess,patch('src.evidence_batch.save_private'):
   batch.run(jobs=[current,new,strong],profile={},existing=existing,rules=RULES,max_jobs=1,rescore=False,fake_client=object(),jd_text_for={batch.key(j):JD for j in [current,new,strong]},stamp='2026-10-21T00:00:00+00:00',model='test',on_scored=None,job_keys=None)
   self.assertEqual(assess.call_args.args[1]['id'],'strong')
 def test_removed_posting_does_not_delete_completed_record(self):
  result=batch.run(jobs=[],profile={},existing={KEY:{'recommendation':'strong'}},rules=RULES,max_jobs=0,rescore=False,fake_client=None,jd_text_for={},stamp='2026-10-21',model='test',on_scored=None,job_keys=None)
  self.assertIn(KEY,result)
 def test_exhausted_budget_stops_before_client_creation(self):
  with patch('src.evidence_batch.cache.reserve',side_effect=cache.AttemptBudgetExhausted()),patch('src.evidence_batch.llm_client.build_client') as client:
   result=batch.run(jobs=[JOB],profile={},existing={},rules=RULES,max_jobs=1,rescore=False,fake_client=None,jd_text_for={KEY:JD},stamp='2026-10-21',model='test',on_scored=None,job_keys=None)
   self.assertEqual(result,{});client.assert_not_called()

class DurableBudget(unittest.TestCase):
 def setUp(self):
  self.records={}
  self.read=patch('src.assessment_cache.load_private',side_effect=lambda k,input_hash:copy.deepcopy(self.records.get((k,input_hash))))
  self.write=patch('src.assessment_cache.save_private',side_effect=lambda p,job_key:self.records.__setitem__((job_key,p['input_hash']),copy.deepcopy(p)))
  self.read.start();self.write.start();self.addCleanup(self.read.stop);self.addCleanup(self.write.stop)
 def reserve(self,signature='same',force=False):return cache.reserve(KEY,signature,[],'2026-10-21',force=force)
 def test_budget_survives_new_worker_and_model_change(self):
  a=self.reserve();self.assertEqual(a['paid_attempts'],1)
  b=self.reserve();self.assertEqual(b['paid_attempts'],2)
  with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve()
  self.assertEqual(self.reserve('changed-jd')['paid_attempts'],1)
  self.assertEqual(self.reserve(force=True)['paid_attempts'],3)
 def test_completed_assessment_cannot_be_charged_again(self):
  a=self.reserve();cache.finish(KEY,a,'complete')
  with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve()
 def test_explicit_campaign_allows_two_extra_attempts_once_across_workers(self):
  self.reserve();self.reserve()
  with patch.dict('os.environ',{'FIT_RETRY_CAMPAIGN':'one','FIT_RETRY_JOB_KEYS':'["'+KEY+'"]'}):
   self.assertEqual(self.reserve()['paid_attempts'],3)
   self.assertEqual(self.reserve()['paid_attempts'],4)
   with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve()
  with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve()
 def test_campaign_cannot_repeat_completed_or_unlisted_jobs(self):
  a=self.reserve();cache.finish(KEY,a,'complete')
  with patch.dict('os.environ',{'FIT_RETRY_CAMPAIGN':'one','FIT_RETRY_JOB_KEYS':'["'+KEY+'"]'}):
   with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve()
  self.reserve('other');self.reserve('other')
  with patch.dict('os.environ',{'FIT_RETRY_CAMPAIGN':'one','FIT_RETRY_JOB_KEYS':'[]'}):
   with self.assertRaises(cache.AttemptBudgetExhausted):self.reserve('other')
 def test_reservation_failure_aborts_before_spend(self):
  with patch('src.assessment_cache.save_private',side_effect=RuntimeError('database unavailable')):
   with self.assertRaises(RuntimeError):self.reserve()

if __name__=='__main__':unittest.main()
