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

class ResumablePublicationSafety(unittest.TestCase):
    execute=PrivatePublicationSafety.execute
    def test_checkpoint_namespace_is_private_and_exact_input_is_requested(self):
        from src.evidence_batch import input_hash
        verdict=EvidenceVerdict('medium','Reviewed','capability',{}, {'inventory':[]})
        def assess(*args,**kwargs):
            kwargs['checkpoint']({'inventory':[],'input_hash':'checkpoint'})
            return verdict
        with patch.dict('os.environ',{'FIT_RESUME_PIPELINE':'true'}),patch('src.evidence_batch.load_private',return_value=None) as load,patch('src.evidence_pipeline.assess',side_effect=assess),patch('src.evidence_batch.save_private') as save:
            scores=self.execute()
        self.assertEqual(load.call_args.args,('checkpoint:'+KEY,))
        self.assertIn('input_hash',load.call_args.kwargs)
        self.assertEqual([c.kwargs['job_key'] for c in save.call_args_list],['checkpoint:'+KEY,KEY])
        self.assertEqual(scores[KEY]['recommendation'],'medium')
    def test_failed_checkpoint_read_does_not_repeat_model_calls(self):
        with patch.dict('os.environ',{'FIT_RESUME_PIPELINE':'true'}),patch('src.evidence_batch.load_private',side_effect=RuntimeError('storage unavailable')),patch('src.evidence_pipeline.assess') as assess:
            self.assertNotIn(KEY,self.execute())
            assess.assert_not_called()

class NewShortlistSalarySafety(unittest.TestCase):
    execute=PrivatePublicationSafety.execute
    def test_published_low_base_is_excluded_before_new_strong_is_published(self):
        verdict=EvidenceVerdict('strong','Reviewed','none',{'career_fit':'strong','validated':True,'location_status':'eligible','comp_status':'unknown'},{'inventory':[]})
        check={'status':'below_floor','verification':'verified','bands':[{'min':154000,'max':180000}]}
        with patch('src.evidence_batch.fit_scorer.score_fit',return_value=verdict),patch('src.refresh_salaries.retrieve',return_value=check),patch('src.evidence_batch.save_private') as save:
            scores=self.execute()
        self.assertEqual(scores[KEY]['recommendation'],'medium')
        self.assertEqual(scores[KEY]['career_fit'],'strong')
        self.assertEqual(scores[KEY]['comp_status'],'below_floor')
        self.assertEqual(save.call_args.args[0]['public']['comp_status'],'below_floor')
