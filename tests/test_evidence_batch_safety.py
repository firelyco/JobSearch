import unittest
from unittest.mock import patch
from src.evidence_fit import EvidenceVerdict
from src.evidence_batch import run

JOB={'source':'test','company':'example','id':'1','title':'Principal TPM'}
KEY='test:example:1'

class PrivatePublicationSafety(unittest.TestCase):
    def execute(self,existing=None,max_jobs=40):
        return run(jobs=[JOB],profile={},existing=existing or {},rules={'fit_policy_version':'test'},
            max_jobs=max_jobs,rescore=True,fake_client=object(),jd_text_for={KEY:'job description'},
            stamp='2026-10-15T00:00:00+00:00',model='test',on_scored=None,job_keys=None)

    def test_private_storage_failure_cannot_publish_new_strong(self):
        verdict=EvidenceVerdict('strong','Reviewed','none',{}, {'inventory':[]})
        with patch('src.evidence_batch.fit_scorer.score_fit',return_value=verdict),patch('src.evidence_batch.save_private',side_effect=RuntimeError('offline')):
            self.assertNotIn(KEY,self.execute())

    def test_assessment_failure_does_not_invent_replacement(self):
        existing={KEY:{'recommendation':'medium','input_hash':'old'}}
        with patch('src.evidence_batch.fit_scorer.score_fit',side_effect=RuntimeError('offline')),patch('src.evidence_batch.save_private') as storage:
            self.assertEqual(self.execute(existing),existing)
            storage.assert_not_called()

    def test_zero_budget_does_not_call_model(self):
        with patch('src.evidence_batch.fit_scorer.score_fit') as scorer:
            self.assertEqual(self.execute(max_jobs=0),{})
            scorer.assert_not_called()
