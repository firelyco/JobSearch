import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
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
    def test_drain_newest_discovery_first_preserves_completed_and_cutoff(self):
        newer=job('new-discovery',100,first_seen_at=NOW.isoformat())
        older=job('old-discovery',1,first_seen_at=(NOW-timedelta(days=1)).isoformat())
        done=job('done',2)
        ancient=job('ancient',posted_at='2026-09-01T00:00:00+00:00')
        attempt={'context_hash':'context','job_snapshot_hash':b.snapshot_hash(newer),'count':2,'last_outcome':'failed','pipeline_version':b.pipeline_version()}
        self.state['attempts'][b.key(newer)]=attempt.copy()
        scores={b.key(done):verdict(done)}
        jobs=[older,newer,done,ancient]
        result=b.prepare_drain(self.state,jobs,scores,'context',CFG,NOW,'campaign')
        self.assertEqual(result['selected'],[newer,older])
        self.assertEqual(self.state['attempts'][b.key(newer)],attempt)
        self.assertEqual(self.state['retry_limits'][b.key(newer)],4)
        self.state['attempts'][b.key(newer)]['count']=4
        result=b.prepare_drain(self.state,jobs,scores,'context',CFG,NOW,'campaign')
        self.assertEqual(self.state['retry_limits'][b.key(newer)],4)
        self.assertEqual(result['selected'],[older])
        self.assertEqual(result['counts']['assessed'],1)

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
    def test_custom_start_covers_all_tracked_roles_and_locations(self):
        since=datetime(2026,10,1,4,tzinfo=timezone.utc)
        state=b.initialize(NOW,since=since,all_jobs=True)
        included=job('foreign-sales',posted_at=since.isoformat(),title='Sales',location='London, UK')
        before=job('before',posted_at=(since-timedelta(seconds=1)).isoformat())
        result=b.plan([included,before],{},state,'context',CFG)
        self.assertEqual(result['counts']['total'],1)
        self.assertEqual(state['window_count'],32)
        self.assertEqual(result['windows'][-1]['start'],since.isoformat())
        self.assertEqual(result['selected'],[included])
        self.assertIsNone(b.window_index(before,state))

    def test_actual_posting_date_takes_precedence_over_discovery(self):
        j=job('old',200,first_seen_at=NOW.isoformat())
        self.assertIsNone(b.window_index(j,self.state))
        j['posted_at']='invalid'
        self.assertEqual(b.window_index(j,self.state),0)
    def test_us_scope_and_relevant_roles(self):
        self.assertTrue(b.in_scope(job('yes'),CFG))
        self.assertTrue(b.in_scope(job('foreign',location='Remote; London, UK'),CFG))
        self.assertTrue(b.in_scope(job('unknown',location='Remote'),CFG))
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
    def test_strong_refresh_precedes_fixed_cohort_without_changing_its_counts(self):
        old=job('strong-outside',200);new=job('new',1)
        self.state['requested_job_keys']=[b.key(new)]
        self.state['priority_job_keys']=[b.key(new)]
        scores={b.key(old):{**verdict(old),'context_hash':'old-context','validated':True}}
        result=b.plan([new,old],scores,self.state,'context',CFG)
        self.assertEqual(result['selected'],[old]);self.assertEqual(result['phase'],'strong_reassessment')
        self.assertEqual(result['counts']['total'],1);self.assertEqual(result['strong_refresh_pending'],1)
        self.state['status']='completed'
        self.assertTrue(b.continuation_needed(self.state,[new,old],scores,'context',CFG,NOW))
    def test_strong_refresh_orders_newest_first_and_skips_completed_and_exhausted(self):
        older=job('older',200);newer=job('newer',190);done=job('done',180);failed=job('failed',170);new=job('new')
        scores={b.key(j):{**verdict(j),'context_hash':'old-context','validated':True} for j in [older,newer,failed]}
        scores[b.key(done)]={**verdict(done),'validated':True}
        self.state['attempts'][b.key(failed)]={'context_hash':'context','job_snapshot_hash':b.snapshot_hash(failed),'count':2,'last_outcome':'failed','pipeline_version':b.pipeline_version()}
        result=b.plan([new,older,newer,done,failed],scores,self.state,'context',CFG)
        self.assertEqual(result['selected'],[newer,older]);self.assertEqual(result['strong_refresh_unresolved'],1)
        self.assertEqual(self.state['attempts'][b.key(failed)]['count'],2)
    def test_strong_refresh_outside_cohort_stays_running_until_worker_finishes(self):
        old=job('strong-outside',200)
        scores={b.key(old):{**verdict(old),'context_hash':'old-context','validated':True}}
        self.state['attempts'][b.key(old)]={'context_hash':'context','job_snapshot_hash':b.snapshot_hash(old),'count':1,'last_outcome':'in_progress','pipeline_version':b.pipeline_version()}
        self.state['current_batch']={'phase':'strong_reassessment','window':None}
        b.refresh(self.state,[old],scores,'context',CFG,NOW)
        self.assertEqual(self.state['status'],'running');self.assertEqual(self.state['strong_refresh_in_progress'],1)

    def test_rolling_relevant_scope_advances_and_excludes_only_current_low_salary(self):
        state=b.initialize(NOW-timedelta(days=1),since=NOW-timedelta(days=7))
        state['scope_mode']='rolling_relevant'
        new=job('new');low=job('low');stale=job('stale');foreign=job('foreign',location='London, UK')
        scores={b.key(low):{**verdict(low),'comp_status':'below_floor'},b.key(stale):{**verdict(stale),'context_hash':'old','comp_status':'below_floor'}}
        result=b.refresh(state,[new,low,stale,foreign],scores,'context',CFG,NOW)
        self.assertEqual(state['as_of'],NOW.isoformat());self.assertEqual(result['counts']['total'],2)
        self.assertEqual({b.key(j) for j in result['selected']},{b.key(new),b.key(stale)})
    def test_rolling_scope_fills_batches_across_sparse_windows_newest_first(self):
        self.state['scope_mode']='rolling_relevant'
        old=job('old',30);new=job('new',1);middle=job('middle',14)
        result=b.plan([old,new,middle],{},self.state,'context',CFG)
        self.assertEqual(result['selected'],[new,middle,old]);self.assertEqual(result['current_window'],0)

    def test_queue_covers_staff_qualifiers_and_workday_city_state_without_foreign_guess(self):
        for title in ['Staff TPM, Security','Staff Glasses Technical Program Manager, XR','Staff Systems Technical Program Manager (Hardware)']:
            self.assertTrue(b.in_scope(job('role',title=title),CFG),title)
        workday=job('multi',source='workday',location='2 Locations',url='https://capitalone.wd12.myworkdayjobs.com/en-US/jobs/job/McLean-VA/Director_R1')
        self.assertTrue(b.in_scope(workday,CFG))
        for place in ['Toronto-CA','London-ON','Remote-CA']:
            self.assertFalse(b.in_scope({**workday,'url':workday['url'].replace('McLean-VA',place)},CFG),place)

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
    def test_continuation_reopens_completed_period_for_new_or_changed_jobs(self):
        done=job('done');new=job('late');scores={b.key(done):verdict(done)}
        for status in ('completed','completed_with_issues'):
            self.state['status']=status
            self.assertTrue(b.continuation_needed(self.state,[done,new],scores,'context',CFG,NOW))
            self.assertEqual(self.state['counts']['assessed'],1)
        self.assertFalse(b.continuation_needed(self.state,[done],scores,'context',CFG,NOW))
        done['description']='Updated job requirements'
        self.assertTrue(b.continuation_needed(self.state,[done],scores,'context',CFG,NOW))

    def test_retry_unfinished_preserves_completed_scores_and_stops_after_budget(self):
        done=job('done');failed=job('failed');scores={b.key(done):verdict(done)}
        self.run_batch([failed],runner=lambda **kw:kw['existing'])
        self.run_batch([failed],runner=lambda **kw:kw['existing'])
        self.assertEqual(b.plan([done,failed],scores,self.state,'context',CFG)['counts']['pending'],0)
        self.assertTrue(b.continuation_needed(self.state,[done,failed],scores,'context',CFG,NOW))  # saved private reviews may still need recovery
        reset=b.retry_unfinished(self.state,[done,failed],scores,'context',CFG,NOW)
        self.assertEqual(reset,[b.key(failed)])
        self.assertEqual(self.state['retry_history'][0]['attempt']['count'],2)
        self.assertEqual(b.outcome(done,scores,self.state,'context'),'assessed')
        self.assertEqual(b.outcome(failed,scores,self.state,'context'),'pending')
        def runner(**kw):
            self.assertEqual(kw['job_keys'],[b.key(failed)])
            return kw['existing']
        self.run_batch([done,failed],scores,runner)
        self.run_batch([done,failed],scores,runner)
        self.assertEqual(b.plan([done,failed],scores,self.state,'context',CFG)['counts']['pending'],0)
        self.assertTrue(b.continuation_needed(self.state,[done,failed],scores,'context',CFG,NOW))  # saved private reviews may still need recovery

    def test_provider_pause_and_disabled_period_do_not_continue(self):
        for status in ('archived','waiting_for_provider'):
            self.state['status']=status
            self.state['updated_at']=NOW.isoformat()
            self.assertFalse(b.continuation_needed(self.state,[job('new')],{},'context',CFG,NOW))
        self.state.update(status='running',enabled=False)
        self.assertFalse(b.continuation_needed(self.state,[job('new')],{},'context',CFG,NOW))

    def test_continuation_does_not_force_paid_rescore_and_accepts_cache_hits(self):
        j=job('cached')
        def runner(**kwargs):
            self.assertFalse(kwargs['rescore'])
            updated={b.key(j):{**verdict(j),'scored_at':(NOW-timedelta(days=2)).isoformat(),'jd_checked_at':NOW.isoformat()}}
            kwargs['on_scored'](updated)
            return updated
        self.run_batch([j],runner=runner)
        self.assertEqual(self.state['attempts'][b.key(j)]['last_outcome'],'strong')
        self.assertEqual(self.state['counts']['assessed'],1)

    def test_checkpoint_writer_keeps_results_when_publishing_fails(self):
        from src.fit_backfill import CheckpointWriter
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            statefile=Path(directory)/'queue.json';publisher=Mock()
            publisher.publish.side_effect=RuntimeError('competing push')
            with patch.object(b,'STATE_FILE',statefile),patch.object(b.fit,'save') as save,patch.object(b.time,'monotonic',return_value=10):
                writer=CheckpointWriter([],publisher,interval=60)
                writer(self.state,{'done':{'recommendation':'strong'}})
                self.assertTrue(self.state['publication_pending'])
                self.assertEqual(save.call_count,1)
                writer(self.state,{},force=True)
                self.assertEqual(publisher.publish.call_count,2)

    def test_recover_exhausted_private_completion_before_public_retry_gate(self):
        j=job('saved')
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        attempt=copy.deepcopy(self.state['attempts'])
        self.assertEqual(b.outcome(j,{},self.state,'context'),'unresolved')
        def replay(**kw):
            self.assertEqual(b.os.environ['FIT_CACHE_ONLY'],'true')
            self.assertFalse(kw['rescore'])
            return {b.key(j):verdict(j)}
        result=b.recover_completed(jobs=[j],profile={},rules={},scores={},state=self.state,
            model='nvidia',role_config=CFG,runner=replay)
        self.assertEqual(b.outcome(j,result,self.state,'context'),'assessed')
        self.assertEqual(self.state['attempts'],attempt)
        self.assertFalse(b.recovery_candidates([j],result,self.state,'context',CFG))

    def test_private_recovery_uses_real_batch_path_with_inference_and_reservation_forbidden(self):
        from src.evidence_fit import EvidenceVerdict
        from src import evidence_batch
        j=job('private-complete');k=b.key(j)
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        saved=EvidenceVerdict('medium','Saved review','evidence',{'validated':True},
            {'inventory':[],'extraction':{},'independent_review':{},'execution':{'calls_this_attempt':0}})
        with patch.object(evidence_batch,'context_hash',return_value='context'), \
             patch.object(evidence_batch.jd_fetch,'get_jd_text',return_value='A real employer description'), \
             patch.object(evidence_batch,'load_private',return_value={'extraction':{'saved':True},'independent_review':{'saved':True}}), \
             patch.object(evidence_batch,'replay_same_skill',return_value=saved), \
             patch.object(evidence_batch,'save_private'), \
             patch.object(evidence_batch.cache,'reserve',side_effect=AssertionError('No new reservation')) as reserve, \
             patch.object(evidence_batch.llm_client,'build_client',side_effect=AssertionError('No AI client')) as client:
            result=b.recover_completed(jobs=[j],profile={},rules={'evidence_required':True,'_fitment_skill':'private','fitment_skill_hash':'test','comp_floor_usd':250000},
                scores={},state=self.state,model='nvidia',role_config=CFG)
            self.assertEqual(result[k]['recommendation'],'medium')
            self.assertEqual(result[k]['cache_status'],'revalidated_without_ai')
            reserve.assert_not_called();client.assert_not_called()
            self.assertEqual(self.state['attempts'][k]['count'],2)

    def test_cache_miss_does_not_reset_budget_or_repeat_unchanged_recovery(self):
        j=job('missing')
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        result=b.recover_completed(jobs=[j],profile={},rules={},scores={},state=self.state,
            model='nvidia',role_config=CFG,runner=lambda **kw:kw['existing'])
        self.assertEqual(self.state['attempts'][b.key(j)]['count'],2)
        self.assertFalse(b.recovery_candidates([j],result,self.state,'context',CFG))
        self.assertEqual(b.outcome(j,result,self.state,'context'),'unresolved')

    def test_new_pipeline_retries_only_unresolved_and_keeps_completed_scores(self):
        j=job('failed');done=job('done')
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.run_batch([j],runner=lambda **kw:kw['existing'])
        self.assertEqual(b.outcome(j,{},self.state,'context'),'unresolved')
        with patch.dict(b.os.environ,{'FIT_RESUME_PIPELINE':'true'}):
            self.assertEqual(b.outcome(j,{},self.state,'context'),'pending')
            self.assertEqual(b.outcome(done,{b.key(done):verdict(done)},self.state,'context'),'assessed')

    def test_fixed_requested_cohort_does_not_expand_to_unrequested_jobs(self):
        requested=job('requested');other=job('other')
        self.state['requested_job_keys']=[b.key(requested)]
        p=b.plan([requested,other],{},self.state,'context',CFG)
        self.assertEqual(p['counts']['total'],1)
        self.assertEqual(p['selected'],[requested])

    def test_old_large_batch_variable_cannot_overrun_worker_timeout(self):
        with patch.dict(b.os.environ,{'FIT_WORKERS':'3'}):
            self.assertEqual(b.worker_batch_size(24),6)
            self.assertEqual(b.worker_batch_size(2),2)
        with patch.dict(b.os.environ,{'FIT_WORKERS':'6'}):
            self.assertEqual(b.worker_batch_size(24),12)
        with self.assertRaises(ValueError):b.worker_batch_size(0)

    def test_reject_zero_batch_size(self):
        with self.assertRaises(ValueError): self.run_batch([],max_jobs=0)

if __name__=='__main__':unittest.main()
