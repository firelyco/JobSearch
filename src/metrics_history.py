"""Public, append-only operational history. Never stores prompts or career evidence."""
from __future__ import annotations
import argparse, contextvars, hashlib, json, os, re, subprocess, threading, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'
LOCK=threading.RLock()
ACTIVE=contextvars.ContextVar('jobsearch_metrics_attempt',default=None)
KNOWN={}
FIELDS=('source','company','id','title','url','location','posted_at','first_seen_at','discovery_source','discovery_sources','is_remote')

def now():return datetime.now(timezone.utc).isoformat()
def enabled():return os.environ.get('JOBSEARCH_METRICS')=='1'
def key(job):return ':'.join(str(job.get(k,'')) for k in ('source','company','id'))
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:32]
def read(path,fallback):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return fallback
def public_job(job):
    result={k:job.get(k) for k in FIELDS if job.get(k) is not None}
    if not re.match(r'^https?://',str(result.get('url',''))):result['url']=''
    result['location']=job.get('resolved_locations') or job.get('location') or ''
    return result

def event(kind,job=None,at=None,identity=None,**data):
    stamp=at or now();job_key=key(job) if job else ''
    result={'id':digest([kind,job_key,identity or stamp]),'type':kind,'at':stamp,**data}
    if job:result.update(job_key=job_key,job=public_job(job))
    run=os.environ.get('GITHUB_RUN_ID')
    if run:result['run_url']='https://github.com/firelyco/JobSearch/actions/runs/'+run
    return result

def append(events,kind='fit',force=False):
    try:_append(events,kind,force)
    except Exception as error:
        # Telemetry must never turn a completed review into a failed assessment.
        import logging
        logging.getLogger(__name__).warning('Metrics write unavailable (%s)',type(error).__name__)

def _append(events,kind='fit',force=False):
    if not (force or enabled()) or not events:return
    with LOCK:
        folder=DOCS/'metrics'
        cache_key=str(folder.resolve())
        if cache_key not in KNOWN:
            KNOWN[cache_key]={e['id'] for p in folder.glob('????-??/*.json') for e in read(p,{}).get('events',[])}
        events=[e for e in events if e['id'] not in KNOWN[cache_key]]
        if not events:return
        # Each run owns its shards. Concurrent poll/fit workers never overwrite
        # one another; the newline catalog has Git's built-in union merge driver.
        run=re.sub(r'[^a-zA-Z0-9_-]','-',os.environ.get('GITHUB_RUN_ID','local'))
        attempt=os.environ.get('GITHUB_RUN_ATTEMPT','1')
        groups={}
        for e in events:
            month=str(e['at'])[:7]
            if re.fullmatch(r'\d{4}-\d{2}',month):groups.setdefault(month,[]).append(e)
        for month,rows in groups.items():
            relative=f'{month}/{run}-{attempt}-{kind}.json'
            path=DOCS/'metrics'/relative;path.parent.mkdir(parents=True,exist_ok=True)
            old=read(path,{'schema':1,'events':[]})
            merged={e['id']:e for e in old['events']}
            merged.update({e['id']:e for e in rows})
            payload={'schema':1,'updated_at':now(),'events':sorted(merged.values(),key=lambda e:(e['at'],e['id']))}
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(payload,separators=(',',':')),encoding='utf-8');temp.replace(path)
        catalog()
        KNOWN[cache_key].update(e['id'] for e in events)

def catalog():
    folder=DOCS/'metrics';folder.mkdir(parents=True,exist_ok=True)
    paths=sorted(str(p.relative_to(folder)).replace('\\','/') for p in folder.glob('????-??/*.json'))
    (folder/'index.txt').write_text('\n'.join(paths)+'\n',encoding='utf-8')

def poll_snapshot(raw,kept,meta,role):
    if not enabled():return
    from src import scorer,assessment_scope,fit_backfill
    stamp=meta['polled_at'];published={key(j) for j in kept};events=[]
    for j in raw:
        k=key(j)
        # Raw retail/engineering feed volume is aggregated, not misrepresented
        # as role candidates or copied into a huge job-level public history.
        if k not in published and not re.search(r'technical.*program|engineering.*program|\btpm\b|\bpmo\b|program.*management|technology.*portfolio',j.get('title',''),re.I):continue
        score,reasons=scorer.score_job(j,role)
        eligible=k in published and assessment_scope.allowed(j) and fit_backfill.in_scope(j,role)
        reason='Eligible for assessment' if eligible else 'Employer posting predates September 15, 2026' if k in published and not assessment_scope.allowed(j) else 'Outside routine role/location scope' if k in published else '; '.join(reasons)
        events.append(event('screened',j,at=stamp,identity=[meta['polled_at'],k],retained=k in published,eligible=eligible,reason=reason))
        if k in published:
            seen=j.get('first_seen_at') or stamp
            events.append(event('discovered',j,at=seen,identity=k,provenance='recorded'))
    health=[h for v in meta.get('stats',{}).values() for h in v.get('sources',[])]+meta.get('discovery',[])
    sources=[{k:h.get(k) for k in ('source','label','query','status','jobs','collected','reported_total','deferred') if h.get(k) is not None} for h in health]
    events.append(event('refresh',at=stamp,identity=stamp,raw_fetched=meta.get('total_jobs_fetched'),retained=meta.get('total_jobs_kept'),new_reported=meta.get('new_this_run'),seconds=meta.get('elapsed_seconds'),sources=sources))
    append(events,'poll')

def capture_fit(jobs,scores,state=None,force=False,provenance='recorded'):
    if not (enabled() or force):return
    by_key={key(j):j for j in jobs};rows=[]
    for k,s in scores.items():
        if s.get('validated') is not True or s.get('recommendation') not in {'strong','medium','not'} or not s.get('scored_at'):continue
        j=by_key.get(k) or {'id':k,'title':'Posting details not recorded'}
        e=event('completed',j,at=s.get('metrics_completed_at') or s['scored_at'],identity=[k,s['scored_at'],s.get('job_snapshot_hash')],verdict=s['recommendation'],career_fit=s.get('career_fit'),salary_status=s.get('comp_status','unknown'),provenance=provenance)
        e['job_key']=k;rows.append(e)
    if state:
        from src import fit_backfill as backfill
        import yaml
        role=yaml.safe_load((ROOT/'config/role_config.yml').read_text(encoding='utf-8'))
        active=set(backfill.plan(jobs,scores,state,state.get('context_hash',''),role)['cohort_keys']) if state.get('cutoff') else set(by_key)
        observed=state.get('updated_at') or now()
        # The live queue is authoritative: historical waiting records from jobs
        # no longer in scope must not inflate today's outstanding backlog.
        queue_rows=[]
        for k in sorted(active):
            if k not in by_key:continue
            status=backfill.outcome(by_key[k],scores,state,state.get('context_hash',''))
            queue_rows.append({'job_key':k,'status':status,'verdict':scores.get(k,{}).get('recommendation') if status=='assessed' else None})
        rows.append(event('queue_snapshot',at=observed,identity=[observed,queue_rows],jobs=queue_rows))
        from src import assessment_scope
        for k,j in by_key.items():
            eligible=k in active
            reason='In current assessment scope' if eligible else 'Employer posting predates September 15, 2026' if not assessment_scope.allowed(j) else 'Outside routine role/location scope' if not backfill.in_scope(j,role) else 'Published base salary maximum below $250K' if backfill.fresh(j,scores.get(k,{}),state.get('context_hash','')) and scores.get(k,{}).get('comp_status')=='below_floor' else 'Outside selected assessment period'
            rows.append(event('eligibility_observed',j,at=observed,identity=[k,backfill.snapshot_hash(j),state.get('context_hash'),eligible,reason],eligible=eligible,retained=True,reason=reason,provenance='policy_observation'))
        for k,a in state.get('attempts',{}).items():
            j=by_key.get(k) or {'id':k,'title':'Posting details not recorded'}
            score=scores.get(k,{})
            completed=k in by_key and score.get('recommendation') in {'strong','medium','not'} and backfill.fresh(by_key[k],score,state.get('context_hash',''))
            status='completed' if completed else 'processing' if a.get('last_outcome')=='in_progress' else 'unresolved' if a.get('count',0)>=state.get('max_attempts',2) else 'waiting'
            e=event('queue_observed',j,at=observed,identity=[k,a.get('last_attempt_at'),a.get('count'),a.get('last_outcome'),status,k in active],status=status,in_queue=k in active,attempt_count=a.get('count',0),last_attempt_at=a.get('last_attempt_at'),last_outcome=a.get('last_outcome'),reason='Retry limit reached' if status=='unresolved' else '',provenance=provenance)
            e['job_key']=k;rows.append(e)
    append(rows,'history' if force else 'fit',force=force)

def begin(job,model):
    if not enabled():return None
    stamp=now();identity=digest([key(job),stamp,os.environ.get('GITHUB_RUN_ID')])
    details={'job':job,'identity':identity,'started':time.monotonic(),'calls':0,'input_tokens':0,'output_tokens':0,'unknown_usage_calls':0,'model':model}
    token=ACTIVE.set(details)
    append([event('attempt_started',job,at=stamp,identity=identity,attempt_id=identity,model=model,provider=os.environ.get('LLM_PROVIDER','unknown'))])
    return token

def call_usage(response=None,error=None):
    a=ACTIVE.get()
    if a is None:return
    a['calls']+=1
    if response:
        a['input_tokens']+=response.input_tokens;a['output_tokens']+=response.output_tokens
    else:a['unknown_usage_calls']+=1

def finish(token,status,error=None,verdict=None):
    if token is None:return
    a=ACTIVE.get();ACTIVE.reset(token)
    append([event('attempt_finished',a['job'],identity=a['identity'],attempt_id=a['identity'],status=status,error=error,verdict=verdict,seconds=round(time.monotonic()-a['started'],3),calls=a['calls'],input_tokens=a['input_tokens'],output_tokens=a['output_tokens'],unknown_usage_calls=a['unknown_usage_calls'],model=a['model'])])

def reused(job,mode):
    if enabled():append([event('reused',job,mode=mode)])

def blocked(job,reason):
    if enabled():append([event('blocked',job,reason=reason)])

def bootstrap():
    # Recover supported public history from one end-of-day Git snapshot per day.
    # Saved first_seen/scored_at timestamps, not commit dates, date the events.
    try:
        lines=subprocess.check_output(['git','log','--since=2026-09-15','--format=%H %cs','--','docs/jobs.json','docs/fit_scores.json'],cwd=ROOT,encoding='utf-8').splitlines()
        daily={}
        for line in lines:
            commit,date=line.split()
            daily.setdefault(date,commit)
        for commit in reversed(list(daily.values())[:90]):
            def version(name):
                try:return json.loads(subprocess.check_output(['git','show',commit+':docs/'+name],cwd=ROOT,encoding='utf-8',stderr=subprocess.DEVNULL))
                except (subprocess.SubprocessError,ValueError):return {}
            archived=version('jobs.json');saved=version('fit_scores.json')
            if not isinstance(archived,list):continue
            append([event('discovered',j,at=j['first_seen_at'],identity=key(j),provenance='recovered') for j in archived if j.get('first_seen_at')],'history',force=True)
            capture_fit(archived,saved,force=True,provenance='recovered')
    except (subprocess.SubprocessError,ValueError):pass
    jobs=read(DOCS/'jobs.json',[]);scores=read(DOCS/'fit_scores.json',{});state=read(DOCS/'fit_backfill.json',{})
    rows=[event('discovered',j,at=j['first_seen_at'],identity=key(j),provenance='recovered') for j in jobs if j.get('first_seen_at')]
    append(rows,'history',force=True);capture_fit(jobs,scores,state,force=True,provenance='recovered')
    # The last refresh's source totals and timestamp are retained exactly as
    # published. Earlier source runs are unavailable, not invented as zeros.
    meta=read(DOCS/'meta.json',{})
    if meta.get('polled_at'):
        sources=[h for v in meta.get('stats',{}).values() for h in v.get('sources',[])]+meta.get('discovery',[])
        safe_sources=[{k:h.get(k) for k in ('source','label','query','status','jobs','collected','reported_total','deferred') if h.get(k) is not None} for h in sources]
        append([event('refresh',at=meta['polled_at'],identity=meta['polled_at'],raw_fetched=meta.get('total_jobs_fetched'),retained=meta.get('total_jobs_kept'),new_reported=meta.get('new_this_run'),seconds=meta.get('elapsed_seconds'),sources=safe_sources,provenance='recovered')],'history',force=True)
    # Current snapshot is a baseline observation, never a fabricated past run.
    append([event('history_baseline',at=now(),note='Earlier discovery dates and saved completion dates recovered. Historical attempt times, source runs and token usage are incomplete.')],'history',force=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bootstrap',action='store_true');parser.add_argument('--index',action='store_true');args=parser.parse_args()
    if args.bootstrap:bootstrap()
    else:catalog()
