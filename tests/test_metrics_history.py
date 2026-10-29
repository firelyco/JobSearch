import json,os,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from src import metrics_history as m

class MetricsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.docs=Path(self.temp.name)
        self.patch=patch.object(m,'DOCS',self.docs);self.patch.start();self.addCleanup(self.patch.stop)
        self.env=patch.dict(os.environ,{'JOBSEARCH_METRICS':'1','GITHUB_RUN_ID':'100','GITHUB_RUN_ATTEMPT':'1'});self.env.start();self.addCleanup(self.env.stop)
        self.job={'source':'test','company':'Example','id':'1','title':'Director TPM','location':'USA - Remote','url':'https://employer.example/job/1','first_seen_at':'2026-10-22T19:00:00Z','description':'DO NOT PUBLISH THIS DESCRIPTION','private_evidence':'PRIVATE'}
    def events(self):return [e for f in (self.docs/'metrics').glob('????-??/*.json') for e in json.loads(f.read_text())['events']]
    def test_repeated_refresh_does_not_inflate_discovery(self):
        for run in ['100','101']:
            with patch.dict(os.environ,{'GITHUB_RUN_ID':run}):m.append([m.event('discovered',self.job,at=self.job['first_seen_at'],identity=m.key(self.job))],'poll')
        self.assertEqual(len(self.events()),1)
    def test_threads_and_runs_preserve_all_events(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i:m.append([m.event('attempt_started',self.job,identity=str(i),attempt_id=str(i))]),range(15)))
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'101'}):m.append([m.event('blocked',self.job,reason='APITimeoutError')])
        self.assertEqual(len(self.events()),16)
        self.assertEqual(len((self.docs/'metrics/index.txt').read_text().splitlines()),2)
    def test_no_private_payload_leaks(self):
        m.append([m.event('discovered',self.job)])
        text=json.dumps(self.events());self.assertNotIn('DO NOT PUBLISH',text);self.assertNotIn('private_evidence',text);self.assertNotIn('PRIVATE',text)
    def test_timeout_preserves_attempt_and_unknown_usage(self):
        token=m.begin(self.job,'nvidia-model');m.call_usage(error='APITimeoutError');m.finish(token,'failed',error='APITimeoutError')
        rows=self.events();self.assertEqual({e['type'] for e in rows},{'attempt_started','attempt_finished'})
        last=next(e for e in rows if e['type']=='attempt_finished');self.assertEqual(last['calls'],1);self.assertEqual(last['unknown_usage_calls'],1);self.assertEqual(last['error'],'APITimeoutError')
        self.assertIsNone(m.ACTIVE.get())
    def test_reported_usage_and_context_cleanup(self):
        token=m.begin(self.job,'model');m.call_usage(SimpleNamespace(input_tokens=15,output_tokens=5));m.finish(token,'complete',verdict='strong')
        last=next(e for e in self.events() if e['type']=='attempt_finished');self.assertEqual(last['input_tokens']+last['output_tokens'],20);self.assertEqual(last['unknown_usage_calls'],0)
    def test_telemetry_failure_cannot_fail_assessment(self):
        with patch.object(m,'_append',side_effect=OSError('disk full')):
            token=m.begin(self.job,'model');m.finish(token,'complete',verdict='strong')
        self.assertIsNone(m.ACTIVE.get())
    def test_completed_cache_and_historical_links_survive(self):
        score={m.key(self.job):{'validated':True,'recommendation':'strong','scored_at':'2026-10-23T14:00:00Z','job_snapshot_hash':'same','context_hash':'ctx'}}
        state={'updated_at':'2026-10-27T15:00:00Z','max_attempts':2,'attempts':{m.key(self.job):{'count':2,'last_outcome':'failed','last_attempt_at':'2026-10-22T20:00:00Z','job_snapshot_hash':'different','context_hash':'ctx'}}}
        m.capture_fit([self.job],score,state);m.capture_fit([self.job],score,state)
        rows=self.events();self.assertEqual(len([e for e in rows if e['type']=='completed']),1)
        self.assertEqual(next(e for e in rows if e['type']=='queue_observed')['status'],'unresolved')
        self.assertEqual(next(e for e in rows if e['type']=='completed')['job']['url'],self.job['url'])
        self.assertFalse(any(e['type']=='attempt_started' for e in rows))
    def test_authoritative_queue_snapshot_matches_saved_retry_budget_without_ai(self):
        from src import fit_backfill as backfill
        job={**self.job,'posted_at':'2026-10-22T19:00:00Z'}
        state={'updated_at':'2026-10-29T18:00:00+00:00','context_hash':'ctx','max_attempts':2,
            'attempts':{m.key(job):{'count':2,'last_outcome':'failed','pipeline_version':'resume-v1',
                'context_hash':'ctx','job_snapshot_hash':backfill.snapshot_hash(job)}}}
        with patch.dict(os.environ,{'FIT_RESUME_PIPELINE':'true'}),patch('src.llm_client.build_client',side_effect=AssertionError('No AI in metrics')):
            m.capture_fit([job],{},state)
        snapshot=next(e for e in self.events() if e['type']=='queue_snapshot')
        self.assertEqual(snapshot['jobs'],[{'job_key':m.key(job),'status':'unresolved','verdict':None}])
        self.assertFalse(any(e['type']=='attempt_started' for e in self.events()))

    def test_opt_in_and_cache_reuse_never_count_as_attempt(self):
        with patch.dict(os.environ,{'JOBSEARCH_METRICS':'0'}):m.append([m.event('blocked',self.job)])
        self.assertEqual(self.events(),[])
        m.reused(self.job,'cache_hit');self.assertEqual(self.events()[0]['type'],'reused')

    def test_llm_wrapper_records_usage_without_recording_prompts(self):
        from src import llm_client
        token=m.begin(self.job,'model')
        with patch.object(llm_client,'_call_anthropic',return_value=llm_client.LLMResponse(text='PRIVATE OUTPUT',input_tokens=9,output_tokens=4,model='model')):
            llm_client.call(SimpleNamespace(messages=True),model='model',system='PRIVATE SYSTEM',user='PRIVATE PROMPT')
        m.finish(token,'complete',verdict='strong')
        end=next(e for e in self.events() if e['type']=='attempt_finished')
        self.assertEqual(end['calls'],1);self.assertEqual(end['input_tokens']+end['output_tokens'],13)
        self.assertNotIn('PRIVATE',json.dumps(self.events()))
    def test_git_union_keeps_concurrent_run_catalog_entries(self):
        import subprocess
        repo=self.docs/'repo';repo.mkdir()
        def git(*args):return subprocess.check_output(['git',*args],cwd=repo,stderr=subprocess.STDOUT,encoding='utf-8')
        git('init','-b','main');git('config','user.name','Metrics test');git('config','user.email','metrics@example.invalid')
        (repo/'.gitattributes').write_text('index.txt merge=union\n')
        (repo/'index.txt').write_text('base.json\n');git('add','.');git('commit','-m','base')
        git('checkout','-b','poll');(repo/'index.txt').write_text('base.json\npoll.json\n');git('add','.');git('commit','-m','poll')
        git('checkout','main');(repo/'index.txt').write_text('base.json\nfit.json\n');git('add','.');git('commit','-m','fit')
        git('merge','poll','--no-edit')
        self.assertEqual(set((repo/'index.txt').read_text().splitlines()),{'base.json','poll.json','fit.json'})
