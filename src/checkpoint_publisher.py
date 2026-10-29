"""Publish public checkpoints from an isolated checkout, retrying competing pushes.

The assessment worker and its runtime/private configuration are never rebased.
Only changes relative to its initial snapshot are merged into current main.
"""
from __future__ import annotations
import argparse, json, subprocess, tempfile, time, sys
from pathlib import Path

FILES=('docs/email_alerts.json','docs/fit_scores.json','docs/fit_policy.json','docs/fit_backfill.json','docs/fit_benchmark.json')
FINAL={'strong','medium','not'}

def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def score_time(score):
    # Source checks must not supersede a newer completed review.
    return score.get('scored_at') or score.get('metrics_completed_at') or ''

def merge_scores(base, local, remote):
    result=dict(remote)
    for key,value in local.items():
        if value==base.get(key):continue
        other=remote.get(key,{})
        if other!=base.get(key):
            completed=lambda s:s.get('validated') is True and s.get('recommendation') in FINAL
            if completed(other) and (not completed(value) or score_time(other)>score_time(value)):continue
        result[key]=value
    return result

def merge_state(base,local,remote):
    result=dict(remote)
    for name,value in local.items():
        if value!=base.get(name) and name not in ('attempts','recovery_checks'):result[name]=value
    for field in ('attempts','recovery_checks'):
        rows=dict(remote.get(field,{}))
        for key,value in local.get(field,{}).items():
            if value==base.get(field,{}).get(key):continue
            other=rows.get(key,{})
            if field=='attempts':
                same=all(value.get(k)==other.get(k) for k in ('context_hash','job_snapshot_hash','pipeline_version'))
                newer=value if value.get('last_attempt_at','')>=other.get('last_attempt_at','') else other
                rows[key]={**newer,'count':max(value.get('count',0),other.get('count',0))} if same else newer
            else:rows[key]=max((other,value),key=lambda v:v.get('checked_at',''))
        if rows:result[field]=rows
    return result

class Publisher:
    def __init__(self,root):
        self.root=Path(root)
        self.baseline={p:read(self.root/p) for p in FILES}
        self.metrics_baseline={str(p.relative_to(self.root)).replace('\\','/'):p.read_bytes() for p in (self.root/'docs/metrics').glob('????-??/*.json')}

    def git(self,*args,cwd=None,check=True):
        return subprocess.run(['git',*args],cwd=cwd or self.root,check=check,capture_output=True,text=True,encoding='utf-8')

    def publish(self,attempts=4,delay=2):
        local={p:read(self.root/p) for p in FILES}
        changed={p:v for p,v in local.items() if v!=self.baseline[p]}
        metrics={str(p.relative_to(self.root)).replace('\\','/'):p.read_bytes() for p in (self.root/'docs/metrics').glob('????-??/*.json')}
        metrics={p:v for p,v in metrics.items() if v!=self.metrics_baseline.get(p)}
        if not changed and not metrics:return False
        for attempt in range(attempts):
            try:
                self.git('fetch','origin','main')
                with tempfile.TemporaryDirectory(prefix='jobsearch-publish-') as directory:
                    scratch=Path(directory)/'checkout'
                    self.git('worktree','add','--detach',str(scratch),'origin/main')
                    try:
                        for path,value in changed.items():
                            target=scratch/path; remote=read(target);base=self.baseline[path]
                            if path.endswith('fit_scores.json'):value=merge_scores(base,value,remote)
                            elif path.endswith('fit_backfill.json'):value=merge_state(base,value,remote)
                            elif remote!=base and remote!=value:
                                raise RuntimeError('Concurrent assessment policy/benchmark changed; retain checkpoint for recovery')
                            target.parent.mkdir(parents=True,exist_ok=True)
                            target.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
                        for path,data in metrics.items():
                            target=scratch/path;old=read(target)
                            incoming=json.loads(data);rows={e['id']:e for e in old.get('events',[])}
                            rows.update({e['id']:e for e in incoming.get('events',[])})
                            incoming['events']=sorted(rows.values(),key=lambda e:(e['at'],e['id']))
                            target.parent.mkdir(parents=True,exist_ok=True)
                            target.write_text(json.dumps(incoming,separators=(',',':')),encoding='utf-8')
                        if 'docs/fit_backfill.json' in changed:
                            # Counters describe the merged results and latest employer snapshot.
                            subprocess.run([sys.executable,'-m','src.checkpoint_publisher','--reconcile'],cwd=scratch,check=True,capture_output=True,text=True)
                        folder=scratch/'docs/metrics'
                        if folder.exists():
                            (folder/'index.txt').write_text(''.join(str(p.relative_to(folder)).replace('\\','/')+'\n' for p in sorted(folder.glob('????-??/*.json'))),encoding='utf-8')
                        subprocess.run(['node','src/validate_shortlist.cjs'],cwd=scratch,check=True,capture_output=True,text=True)
                        paths=[p for p in (*FILES,'docs/shortlist_validation.json','docs/metrics') if (scratch/p).exists()]
                        self.git('add','--',*paths,cwd=scratch)
                        if self.git('diff','--cached','--quiet',cwd=scratch,check=False).returncode==0:return False
                        self.git('-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','fit: publish durable assessment checkpoint',cwd=scratch)
                        subprocess.run(['node','src/validate_shortlist.cjs','--check-only'],cwd=scratch,check=True,capture_output=True,text=True)
                        self.git('push','origin','HEAD:main',cwd=scratch)
                        self.baseline=local
                        self.metrics_baseline.update(metrics)
                        return True
                    finally:
                        # Only remove the checkout created inside this temporary directory.
                        if scratch.resolve().parent!=Path(directory).resolve():raise RuntimeError('Unsafe scratch path')
                        self.git('worktree','remove','--force',str(scratch),check=False)
            except subprocess.CalledProcessError as error:
                # Retry fetch/setup/competing pushes; the guard is rerun each time.
                if attempt+1==attempts:raise RuntimeError('Public checkpoint publication failed; private completed reviews are retained') from error
                time.sleep(delay*(attempt+1))
        return False

def reconcile():
    import yaml
    from datetime import datetime,timezone
    from src import fit_backfill as b
    root=Path(__file__).resolve().parents[1]
    state=read(root/'docs/fit_backfill.json');policy=read(root/'docs/fit_policy.json')
    if state.get('enabled') and policy.get('context_hash'):
        role=yaml.safe_load((root/'config/role_config.yml').read_text(encoding='utf-8'))
        paused=state.get('status')=='waiting_for_provider'
        paused_at=state.get('updated_at')
        b.refresh(state,read(root/'docs/jobs.json'),read(root/'docs/fit_scores.json'),policy['context_hash'],role,datetime.now(timezone.utc))
        if paused and state.get('status')=='running':state.update(status='waiting_for_provider',updated_at=paused_at)
        (root/'docs/fit_backfill.json').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reconcile',action='store_true');args=parser.parse_args()
    if args.reconcile:return reconcile()
    root=Path(__file__).resolve().parents[1]
    publisher=Publisher(root)
    # CLI finalization compares the worker's outputs with its original Git snapshot.
    for path in FILES:
        result=publisher.git('show','HEAD:'+path,check=False)
        publisher.baseline[path]=json.loads(result.stdout) if result.returncode==0 else {}
    publisher.metrics_baseline={}
    publisher.publish()

if __name__=='__main__':main()
