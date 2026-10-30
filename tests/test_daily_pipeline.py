import copy,io,json,os,unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch,Mock,MagicMock
from src import daily_batch as b,fit_backfill
from history_service.lib.daily_logic import decide
from history_service.api import daily,daily_status

NOW=datetime(2026,10,30,5,0,tzinfo=timezone.utc)  # 1 AM Eastern

def batch(**counts):return {'day':'2026-10-30','batch_id':'public-id','collection_completed':True,'counts':{'waiting':0,'processing':0,'blocked':0,'unverified_links':0,**counts},'coverage_issues':0}

class CoordinatorDecisions(unittest.TestCase):
    def test_missing_github_cron_is_recovered_by_independent_clock(self):
        self.assertEqual(decide({}, {},{},NOW)['action'],'poll')
    def test_duplicate_cron_delivery_does_not_duplicate_dispatch(self):
        first=decide({}, {},{},NOW)
        second=decide({}, {},first,NOW+timedelta(minutes=5))
        self.assertIsNone(second['action']);self.assertEqual(second['dispatches']['poll']['count'],1)
    def test_active_workers_are_never_dispatched_twice(self):
        for kind,doc in [('poll',{}),('fit',batch(waiting=2))]:
            self.assertIsNone(decide(doc,{kind:[{'status':'in_progress'}]}, {},NOW)['action'])
    def test_interrupted_worker_resumes_unfinished_assessments(self):
        result=decide(batch(processing=2),{'fit':[{'status':'completed','conclusion':'failure'}]}, {},NOW)
        self.assertEqual(result['action'],'fit')
    def test_green_workflow_with_unresolved_work_is_not_complete(self):
        for doc in [batch(blocked=1),batch(unverified_links=1)]:
            result=decide(doc,{'fit':[{'status':'completed','conclusion':'success'}]}, {},NOW,published=True)
            self.assertEqual(result['status'],'blocked');self.assertIsNone(result['action'])
    def test_incomplete_sources_retry_boundedly_in_same_batch(self):
        doc={**batch(),'coverage_issues':1}
        self.assertEqual(decide(doc,{}, {},NOW)['action'],'poll')
        result=decide(doc,{}, {'dispatches':{'poll':{'count':3}}},NOW)
        self.assertEqual(result['status'],'blocked');self.assertIsNone(result['action'])
    def test_publication_failure_is_not_complete(self):
        self.assertEqual(decide(batch(),{}, {},NOW,published=False)['status'],'publishing')
        self.assertEqual(decide(batch(),{}, {},NOW,published=True)['status'],'complete')
    def test_completion_is_verified_before_deadline_and_late_is_explicit(self):
        late=NOW+timedelta(hours=6)
        self.assertEqual(decide(batch(waiting=1),{}, {},late)['status'],'missed_deadline')
        self.assertEqual(decide(batch(),{}, {},late,published=True)['status'],'completed_late')
        early=decide(batch(),{}, {},NOW,published=True)
        self.assertEqual(decide(batch(),{},early,late,published=True)['status'],'complete')
    def test_stale_checkpoint_cannot_claim_completion(self):
        result=decide({**batch(),'checkpoint_stale':True},{},{},NOW,published=True)
        self.assertNotEqual(result['status'],'complete');self.assertEqual(result['action'],'fit')
    def test_restart_limits_do_not_loop_forever(self):
        result=decide({}, {},{'dispatches':{'poll':{'count':3}}},NOW)
        self.assertIsNone(result['action']);self.assertEqual(result['status'],'blocked')
    def test_dst_fall_back_shares_one_day_and_does_not_duplicate(self):
        first=decide({}, {},{},datetime(2026,11,1,5,55,tzinfo=timezone.utc))
        second=decide({}, {},first,datetime(2026,11,1,6,0,tzinfo=timezone.utc))
        self.assertEqual(first['day'],second['day']);self.assertIsNone(second['action'])

class DailyBatchTests(unittest.TestCase):
    def setUp(self):
        self.job={'source':'greenhouse','company':'example','id':'1','title':'Principal Technical Program Manager','url':'https://example.com/job/1','location':'United States','description':'Public program leadership posting','first_seen_at':'2026-10-30T05:00:00+00:00','posted_at':'2026-10-29T00:00:00+00:00'}
        self.key=fit_backfill.key(self.job);self.policy={'context_hash':'same'};self.queue={'max_attempts':2,'attempts':{}}
        self.meta={'polled_at':'2026-10-30T05:00:00+00:00','stats':{},'discovery':[]}
    def build(self,scores=None,previous=None,jobs=None,email=None,**kwargs):
        with patch('src.llm_client.build_client',side_effect=AssertionError('Daily progress never calls AI')):
            return b.build([self.job] if jobs is None else jobs,scores or {},self.policy,self.queue,self.meta,email or {},{},previous,now=NOW,**kwargs)
    def score(self):return {'validated':True,'recommendation':'strong','context_hash':'same','scorer_revision':fit_backfill.scorer_revision(),'job_snapshot_hash':fit_backfill.snapshot_hash(self.job)}
    def test_saved_assessment_is_reused_without_paid_calls(self):
        result=self.build({self.key:self.score()})
        self.assertEqual(result['counts']['completed'],1);self.assertEqual(result['counts']['strong'],1)
    def test_frozen_members_are_not_silently_lost_after_refresh(self):
        first=self.build();second=self.build(previous=first,jobs=[])
        self.assertEqual(second['counts']['found'],1);self.assertEqual(second['counts']['blocked'],1)
    def test_public_requisition_change_requires_review_without_resetting_budget(self):
        first=self.build({self.key:self.score()})
        changed={**self.job,'description':'Changed mandatory specialist qualifications'}
        result=self.build({self.key:self.score()},previous=first,jobs=[changed])
        self.assertEqual(result['counts']['completed'],0);self.assertEqual(result['counts']['waiting'],1)
    def test_exhausted_inputs_stay_blocked_without_resetting_attempts(self):
        self.queue['attempts'][self.key]={'count':2,'pipeline_version':'resume-v1','context_hash':'same','job_snapshot_hash':fit_backfill.snapshot_hash(self.job)}
        original=copy.deepcopy(self.queue)
        with patch.dict(os.environ,{'FIT_RESUME_PIPELINE':'true'}):result=self.build()
        self.assertEqual(result['counts']['blocked'],1);self.assertEqual(self.queue,original)
    def test_newly_imported_yesterday_email_belongs_to_today_work(self):
        email={'rows':[{'linkedin_id':'4481477507','first_alert_at':'2026-10-29T20:00:00Z','collection':'possible_match'}]}
        result=self.build(email=email,new_link_ids={'4481477507'})
        self.assertEqual(result['counts']['unverified_links'],1)
        later=self.build(email=email,previous=result)
        self.assertEqual(later['counts']['unverified_links'],1)
    def test_missing_sources_prevent_false_green(self):
        self.meta['discovery']=[{'source':'brave','status':'failed'}]
        self.assertEqual(self.build({self.key:self.score()})['status'],'blocked')
    def test_progress_fingerprint_is_stable_without_new_work(self):
        first=self.build();second=self.build(previous=first)
        self.assertEqual(first['batch_id'],second['batch_id'])

class DailyApiTests(unittest.TestCase):
    def test_overlapping_observations_use_persisted_dispatch_reservation(self):
        store={};db=MagicMock()
        def execute(sql,args=None):
            if sql.startswith('SELECT payload'):return SimpleNamespace(fetchone=lambda:(store['state'],) if 'state' in store else None)
            if sql.startswith('INSERT INTO'):store['state']=args[1].obj
            return MagicMock()
        db.execute.side_effect=execute;connection=MagicMock();connection.__enter__.return_value=db
        with patch.dict(os.environ,{'JOBSEARCH_GITHUB_TOKEN':'test-only'}),patch.object(daily,'observe',return_value=({}, {},False)),patch.object(daily,'connect',return_value=connection),patch.object(daily,'request_json') as send:
            daily.run(NOW);daily.run(NOW+timedelta(seconds=1))
        self.assertEqual(send.call_count,1)
    def test_dry_run_never_dispatches_or_updates_state(self):
        db=MagicMock();db.execute.return_value.fetchone.return_value=None;connection=MagicMock();connection.__enter__.return_value=db
        with patch.dict(os.environ,{'JOBSEARCH_GITHUB_TOKEN':'test-only'}),patch.object(daily,'observe',return_value=({}, {},False)),patch.object(daily,'connect',return_value=connection),patch.object(daily,'request_json') as send:
            result=daily.run(NOW,dry_run=True)
        self.assertEqual(result['action'],'poll');send.assert_not_called();self.assertFalse(any(c.args[0].startswith('INSERT') for c in db.execute.call_args_list))
    def test_unauthorized_cron_cannot_start_workers(self):
        handler=daily.handler.__new__(daily.handler);handler.headers={};handler.respond=Mock()
        with patch.dict(os.environ,{'CRON_SECRET':'test-only'}),patch.object(daily,'run') as run:handler.do_GET()
        self.assertEqual(handler.respond.call_args.args[0],401);run.assert_not_called()
    def test_missing_dispatch_credential_is_explicit(self):
        with patch.dict(os.environ,{'JOBSEARCH_GITHUB_TOKEN':''}):result=daily.run(NOW)
        self.assertEqual(result['status'],'configuration_required')
    def test_public_status_does_not_expose_dispatch_reservations_or_secrets(self):
        db=MagicMock();db.execute.return_value.fetchone.return_value=({'day':'2026-10-30','status':'blocked','reason':'Review required','dispatches':{'sensitive':'must-not-publish'},'credential':'must-not-publish'},)
        connection=MagicMock();connection.__enter__.return_value=db
        handler=daily_status.handler.__new__(daily_status.handler);handler.wfile=io.BytesIO();handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
        with patch.dict(os.environ,{'JOBSEARCH_GITHUB_TOKEN':'test-only'}),patch.object(daily_status,'connect',return_value=connection):handler.do_GET()
        self.assertNotIn('must-not-publish',handler.wfile.getvalue().decode())
