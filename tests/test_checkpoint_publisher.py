import json,shutil,subprocess,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from src.checkpoint_publisher import Publisher,merge_scores,merge_state
from src.assessment_watchdog import should_dispatch

def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True)

class MergeTests(unittest.TestCase):
    def test_newer_completed_review_beats_pending_and_freshness_only_old_review(self):
        old={'validated':True,'recommendation':'strong','scored_at':'2026-10-20','jd_checked_at':'2026-10-29'}
        new={**old,'recommendation':'medium','scored_at':'2026-10-28'}
        self.assertEqual(merge_scores({}, {'a':old,'b':{'recommendation':'review'}},{'a':new,'b':new}),{'a':new,'b':new})
        self.assertEqual(merge_scores({'a':old},{'a':old},{'a':new}),{'a':new})

    def test_attempt_counts_never_reset_or_sum_on_concurrent_merge(self):
        a={'context_hash':'same','job_snapshot_hash':'same','pipeline_version':'legacy','count':2,'last_attempt_at':'2026-10-27','last_outcome':'failed'}
        b={**a,'count':1,'last_attempt_at':'2026-10-28','last_outcome':'strong'}
        merged=merge_state({}, {'attempts':{'x':b}}, {'attempts':{'x':a}})
        self.assertEqual(merged['attempts']['x']['count'],2)
        self.assertEqual(merged['attempts']['x']['last_outcome'],'strong')

    def test_watchdog_never_duplicates_worker_and_cools_down_failures(self):
        now=datetime.now(timezone.utc);state={'enabled':True}
        self.assertFalse(should_dispatch(state,[{}],{},now))
        self.assertFalse(should_dispatch(state,[],{'conclusion':'failure','updatedAt':now.isoformat()},now))
        self.assertTrue(should_dispatch(state,[],{'conclusion':'failure','updatedAt':(now-timedelta(hours=1)).isoformat()},now))
        self.assertFalse(should_dispatch({'enabled':False},[],{},now))

@unittest.skipUnless(shutil.which('git') and shutil.which('node'),'git and node required')
class GitPublicationTests(unittest.TestCase):
    def fixture(self,folder):
        bare=folder/'origin.git';git(folder,'init','--bare',str(bare))
        worker=folder/'worker';git(folder,'clone',str(bare),str(worker));git(worker,'checkout','-b','main')
        git(worker,'config','user.name','test');git(worker,'config','user.email','test@example.com')
        for name in ('docs','src','config'):(worker/name).mkdir()
        (worker/'docs/fit_scores.json').write_text(json.dumps({'existing':{'validated':True,'recommendation':'strong','scored_at':'2026-10-28'}}))
        (worker/'docs/fit_policy.json').write_text('{}')
        (worker/'src/validate_shortlist.cjs').write_text("if(require('fs').existsSync('reject-publication'))process.exit(1);require('fs').writeFileSync('docs/shortlist_validation.json','{}');")
        (worker/'config/tailor_config.yml').write_text('tracked')
        git(worker,'add','.');git(worker,'commit','-m','initial');git(worker,'push','-u','origin','main')
        rival=folder/'rival';git(folder,'clone','--branch','main',str(bare),str(rival))
        git(rival,'config','user.name','rival');git(rival,'config','user.email','rival@example.com')
        return worker,rival

    def test_competing_push_retries_and_preserves_both_results_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,rival=self.fixture(Path(tmp));publisher=Publisher(worker)
            (worker/'config/tailor_config.yml').write_text('private runtime override')
            (worker/'config/profile.json').write_text('private evidence must never be staged')
            scores=json.loads((worker/'docs/fit_scores.json').read_text());scores['ours']={'recommendation':'medium'}
            (worker/'docs/fit_scores.json').write_text(json.dumps(scores))
            local=worker/'docs/metrics/2026-10/run-fit.json';local.parent.mkdir(parents=True)
            local.write_text(json.dumps({'events':[{'id':'ours','at':'2026-10-29'}]}))
            original=publisher.git; raced=[]
            def competing(*args,**kwargs):
                if args[0]=='push' and not raced:
                    raced.append(True)
                    (rival/'docs/fit_scores.json').write_text(json.dumps({'existing':{'validated':True,'recommendation':'not','scored_at':'2026-10-29'},'theirs':{'recommendation':'strong'}}))
                    remote=rival/'docs/metrics/2026-10/rival-poll.json';remote.parent.mkdir(parents=True)
                    remote.write_text(json.dumps({'events':[{'id':'theirs','at':'2026-10-29'}]}))
                    git(rival,'add','.');git(rival,'commit','-m','concurrent poll');git(rival,'push')
                return original(*args,**kwargs)
            with patch.object(publisher,'git',side_effect=competing):self.assertTrue(publisher.publish(delay=0))
            git(rival,'pull','--ff-only');result=json.loads((rival/'docs/fit_scores.json').read_text())
            self.assertEqual(set(result),{'existing','ours','theirs'})
            self.assertEqual(result['existing']['recommendation'],'not')
            self.assertEqual(len((rival/'docs/metrics/index.txt').read_text().splitlines()),2)
            self.assertEqual((worker/'config/tailor_config.yml').read_text(),'private runtime override')
            self.assertFalse((rival/'config/profile.json').exists())
            self.assertEqual(git(worker,'rev-parse','HEAD').stdout.strip(),git(worker,'rev-list','--max-parents=0','HEAD').stdout.strip())

    def test_guard_failure_retains_local_checkpoint_without_pushing(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker,rival=self.fixture(Path(tmp));publisher=Publisher(worker)
            before=git(rival,'rev-parse','HEAD').stdout
            (rival/'reject-publication').write_text('test failure');git(rival,'add','.');git(rival,'commit','-m','guard fixture');git(rival,'push')
            before=git(rival,'rev-parse','HEAD').stdout
            (worker/'docs/fit_scores.json').write_text('{"saved":{"recommendation":"strong"}}')
            with self.assertRaises(RuntimeError):publisher.publish(attempts=2,delay=0)
            git(rival,'fetch','origin','main')
            self.assertEqual(git(rival,'rev-parse','origin/main').stdout,before)
            self.assertIn('saved',(worker/'docs/fit_scores.json').read_text())

if __name__=='__main__':unittest.main()
