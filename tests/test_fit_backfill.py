import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from src import fit_backfill as b

NOW = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
CFG = {'title_patterns': [r'Principal.*Technical Program']}

def job(name, hours=1, **fields):
    return {'source':'test','company':'example','id':name,
            'title':'Principal Technical Program Manager','location':'Boston, MA',
            'posted_at':(NOW-timedelta(hours=hours)).isoformat(), **fields}

def verdict(j, recommendation='strong', context='context'):
    return {'context_hash':context, 'scorer_revision':b.scorer_revision(),
            'job_snapshot_hash':b.snapshot_hash(j), 'recommendation':recommendation,
            'scored_at':NOW.isoformat()}

class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.state=b.initialize(NOW)
        self.context=patch.object(b,'context_hash',return_value='context')
        self.context.start();self.addCleanup(self.context.stop)
    def run_batch(self,jobs,scores=None,runner=None,**kw):
        return b.run_batch(jobs=jobs,profile={},rules={},scores=scores or {},state=self.state,
                           model='nvidia',role_config=CFG,now=NOW,runner=runner,**kw)
    def test_windows_cover_exact_seven_days_without_overlap(self):
        for i in range(14):
            self.assertEqual(b.window_index(job(str(i),12*i),self.state),i)
        self.assertEqual(b.window_index(job('cutoff',168),self.state),13)
        self.assertIsNone(b.window_index(job('old',168.001),self.state))
        self.assertIsNone(b.window_index(job('future',-1),self.state))
        self.assertIsNone(b.window_index(job('invalid',posted_at='bad'),self.state))
        windows=b.plan([],{},self.state,'context',CFG)['windows']
        self.assertEqual(windows[0]['start'],windows[1]['end'])
        self.assertEqual(windows[-1]['start'],self.state['cutoff'])
    def test_actual_posting_date_takes_precedence_over_discovery(self):
        j=job('old',200,first_seen_at=NOW.isoformat())
        self.assertIsNone(b.window_index(j,self.state))
        j['posted_at']='invalid'
        self.assertEqual(b.window_index(j,self.state),0)
    def test_us_scope_and_relevant_roles(self):
        self.assertTrue(b.in_scope(job('yes'),CFG))
        self.assertFalse(b.in_scope(job('foreign',location='Remote; London, UK'),CFG))
        self.assertFalse(b.in_scope(job('unknown',location='Remote'),CFG))
        self.assertTrue(b.in_scope(job('url',location='3 Locations',url='https://example.com/job/US-CA'),CFG))
        self.assertFalse(b.in_scope(job('other',title='Sales Representative'),CFG))
        self.assertTrue(b.in_scope(job('adjacent',title='AI Operations Lead',score_reasons=['broader responsibility match']),CFG))
    def test_original_cohort_then_newest_unfinished_window(self):
        older=job('original',20);newer=job('new',1);oldest=job('old',30)
        self.state['priority_job_keys']=[b.key(older)]
        p=b.plan([oldest,newer,older],{},self.state,'context',CFG)
        self.assertEqual([b.key(j) for j in p['selected']],[b.key(older)])
        p=b.plan([oldest,newer,older],{b.key(older):verdict(older)},self.state,'context',CFG)
        self.assertEqual(p['current_window'],0)
        self.assertEqual([b.key(j) for j in p['selected']],[b.key(newer)])
    def test_reuses_completed_ratings_even_after_six_hours(self):
        j=job('cached');score=verdict(j);score['jd_checked_at']='2020-01-01'
        def forbidden(**kw): self.fail('Completed job was reassessed')
        self.run_batch([j],{b.key(j):score},forbidden)
        self.assertEqual(self.state['status'],'completed')
        self.assertEqual(self.state['batches'],0)
    def test_two_failed_attempts_do_not_block_older_windows(self):
        j=job('fail');older=job('older',13);calls=[]
        def failed(**kw): calls.extend(kw['job_keys']);return kw['existing']
        self.run_batch([j,older],runner=failed)
        self.run_batch([j,older],runner=failed)
        self.assertEqual(self.state['counts']['unresolved'],1)
        self.run_batch([j,older],runner=failed)
        self.assertEqual(calls[-1],b.key(older))
        self.run_batch([j,older],runner=failed)
        self.assertEqual(self.state['status'],'completed_with_issues')
        self.assertEqual(self.state['counts']['assessed'],0)
        self.assertEqual(len(calls),4)
    def test_partial_results_survive_runner_exception(self):
        one=job('one');two=job('two');saved=[]
        def partial(**kw):
            updated={b.key(one):verdict(one)}
            kw['on_scored'](updated)
            raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            self.run_batch([one,two],runner=partial,persist=lambda s,r:saved.append(copy.deepcopy(r)))
        self.assertIn(b.key(one),saved[-1])
        self.assertEqual(self.state['counts']['assessed'],1)
        self.assertEqual(self.state['attempts'][b.key(two)]['last_outcome'],'failed')
    def test_interrupted_batch_resumes_without_stranded_inflight(self):
        j=job('crash')
        self.state['attempts'][b.key(j)]={'context_hash':'context','job_snapshot_hash':b.snapshot_hash(j),
                                        'count':1,'last_outcome':'in_progress'}
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.assertEqual(self.state['counts']['in_progress'],0)
        self.assertEqual(self.state['status'],'completed_with_issues')
    def test_only_changed_inputs_reset_attempt_budget(self):
        j=job('changed')
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        j['description']='Changed responsibilities'
        self.assertEqual(b.outcome(j,{},self.state,'context'),'pending')
        self.assertEqual(b.outcome(j,{},self.state,'new-context'),'pending')
    def test_late_discovery_reopens_window(self):
        j=job('late',25)
        b.refresh(self.state,[],{},'context',CFG,NOW)
        self.assertEqual(self.state['status'],'completed')
        b.refresh(self.state,[j],{},'context',CFG,NOW)
        self.assertEqual(self.state['status'],'running')
        self.assertEqual(self.state['current_window'],2)
    def test_review_is_not_completed_and_active_phase_remains_visible(self):
        j=job('review');seen=[]
        self.state['priority_job_keys']=[b.key(j)]
        def reviewed(**kw):
            updated={b.key(j):verdict(j,'review')};kw['on_scored'](updated);return updated
        self.run_batch([j],runner=reviewed,persist=lambda s,r:seen.append(copy.deepcopy(s)))
        self.assertEqual(seen[0]['phase'],'original_cohort')
        self.assertEqual(seen[0]['counts']['in_progress'],1)
        self.assertEqual(self.state['counts']['assessed'],0)
        self.assertEqual(self.state['counts']['pending'],1)
    def test_reject_zero_batch_size(self):
        with self.assertRaises(ValueError): self.run_batch([],max_jobs=0)

if __name__=='__main__':unittest.main()
