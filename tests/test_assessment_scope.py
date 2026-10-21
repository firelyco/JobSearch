import unittest
from unittest.mock import patch
from src import assessment_scope as scope,evidence_batch as batch,fit_backfill as backfill
from src.evidence_fit import scorer_revision
from datetime import datetime,timezone

JOB={'source':'test','company':'example','id':'1','title':'Principal TPM','location':'Boston, MA','url':'https://example.com/1'}
class AssessmentCutoff(unittest.TestCase):
 def test_persisted_cutoff_and_inclusive_boundary(self):
  self.assertEqual(scope.cutoff().isoformat(),'2026-09-15')
  self.assertFalse(scope.allowed({**JOB,'posted_at':'2026-09-14T23:59:59-04:00'}))
  self.assertTrue(scope.allowed({**JOB,'posted_at':'2026-09-15T00:00:00-04:00'}))
  self.assertTrue(scope.allowed({**JOB,'posted_at':'2026-09-15'}))
 def test_recent_discovery_cannot_override_old_employer_date(self):
  self.assertFalse(scope.allowed({**JOB,'posted_at':'2026-08-01','first_seen_at':'2026-10-21'}))
 def test_missing_date_falls_back_to_first_discovery(self):
  self.assertTrue(scope.allowed({**JOB,'posted_at':'invalid','first_seen_at':'2026-09-15'}))
  self.assertFalse(scope.allowed({**JOB,'first_seen_at':'2026-09-14'}))
 def test_old_explicit_rescore_never_fetches_or_calls_model_and_retains_history(self):
  job={**JOB,'posted_at':'2026-09-14','first_seen_at':'2026-10-21'};key=batch.key(job)
  prior={key:{'recommendation':'strong','validated':True}}
  with patch('src.evidence_batch.jd_fetch.get_jd_text') as fetch,patch('src.evidence_batch.llm_client.build_client') as client:
   result=batch.run(jobs=[job],profile={},existing=prior,rules={},max_jobs=100,rescore=True,fake_client=None,jd_text_for=None,stamp='2026-10-21',model='test',on_scored=None,job_keys=[key])
   fetch.assert_not_called();client.assert_not_called();self.assertEqual(result,prior)
 def test_old_strong_and_original_priority_never_enter_backfill(self):
  old={**JOB,'posted_at':'2026-09-14','id':'old'};new={**JOB,'posted_at':'2026-09-15','id':'new'}
  state=backfill.initialize(datetime(2026,10,21,tzinfo=timezone.utc),[batch.key(old)],since=datetime(2026,9,1,tzinfo=timezone.utc),all_jobs=True)
  scores={batch.key(old):{'recommendation':'strong','validated':True,'context_hash':'old','scorer_revision':scorer_revision()}}
  result=backfill.plan([old,new],scores,state,'current',{})
  self.assertEqual(result['counts']['total'],1);self.assertEqual(result['selected'],[new]);self.assertEqual(result['strong_refresh_pending'],0)

if __name__=='__main__':unittest.main()
