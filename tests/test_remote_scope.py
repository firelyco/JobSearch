import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from src.remote_jobs import is_remote
from src import fit_backfill as b
from src.scorer import score_job
from src.evidence_batch import snapshot_hash

class RemoteScopeTests(unittest.TestCase):
    def job(self, location='Remote; London, UK', **fields):
        return dict(source='test',company='example',id='1',title='Staff Technical Program Manager',location=location,posted_at='2026-10-25T10:00:00Z',**fields)
    def test_explicit_remote_only_not_body_mentions(self):
        for job in [self.job(), self.job('Berlin, Germany',is_remote=True), self.job('Berlin',workplace_type='remote')]:
            self.assertTrue(is_remote(job)); self.assertTrue(b.in_scope(job,{}))
        for location in ['London, UK','Hybrid; London, UK','Not remote; London, UK','On-site; London, UK']:
            j=self.job(location,description='We collaborate with remote colleagues')
            self.assertFalse(is_remote(j)); self.assertFalse(b.in_scope(j,{}))
        self.assertFalse(is_remote(self.job(is_remote=False)))
        self.assertFalse(b.in_scope(self.job(title_override='x') | {'title':'Software Engineer'},{}))
    def test_remote_metadata_does_not_invalidate_completed_cache(self):
        j=self.job(); self.assertEqual(snapshot_hash(j),snapshot_hash(j | {'is_remote':True}))
    def test_unchanged_completed_remote_is_not_reassessed(self):
        j=self.job(); now=datetime(2026,10,25,14,tzinfo=timezone.utc)
        state=b.initialize(now); state['scope_mode']='rolling_relevant'
        score={'context_hash':'ctx','scorer_revision':b.scorer_revision(),'job_snapshot_hash':snapshot_hash(j),'recommendation':'medium'}
        result=b.plan([j],{b.key(j):score},state,'ctx',{})
        self.assertEqual(result['counts']['assessed'],1); self.assertEqual(result['selected'],[])
    def test_us_default_queue_precedes_remote_and_cutoff_remains(self):
        us=self.job('Boston, MA'); overseas=self.job() | {'id':'2'}
        old=self.job() | {'id':'3','posted_at':'2026-09-01T00:00:00Z'}
        state=b.initialize(datetime(2026,10,25,14,tzinfo=timezone.utc)); state['scope_mode']='rolling_relevant'
        result=b.plan([overseas,old,us],{},state,'ctx',{})
        self.assertEqual([j['id'] for j in result['selected']],['1','2'])
