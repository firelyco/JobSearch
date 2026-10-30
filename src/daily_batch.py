"""Daily discovery cohort and completion contract. Reuses saved fit results only."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib,json
from src import fit_backfill as backfill,assessment_scope,scorer

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('America/New_York')

def read(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def local_day(value):
    stamp=backfill.timestamp(value)
    return stamp.astimezone(TZ).date().isoformat() if stamp else ''

def build(jobs,scores,policy,queue,meta,email,role,previous=None,*,now=None,new_link_ids=()):
    now=now or datetime.now(timezone.utc);day=now.astimezone(TZ).date().isoformat()
    previous=previous or {};same=previous.get('day')==day
    old={row['key']:row for row in previous.get('members',[])} if same else {}
    by_key={backfill.key(job):job for job in jobs}
    linked_keys={match['key'] for row in email.get('rows',[]) if row.get('linkedin_id') in new_link_ids and row.get('collection')=='tracked' for match in row.get('matches',[]) if match.get('key') in by_key}
    keys=set(old)|linked_keys|{key for key,job in by_key.items() if local_day(job.get('first_seen_at'))==day}
    members=[];counts={name:0 for name in ('found','eligible','completed','strong','middle','pass','waiting','processing','blocked','excluded','unverified_links')}
    context=policy.get('context_hash','')
    for key in sorted(keys):
        job=by_key.get(key);saved=scores.get(key,{})
        row=dict(old.get(key,{}))
        if job:
            row.update(key=key,title=job.get('title',''),url=job.get('url',''),source=job.get('source',''),job_snapshot_hash=backfill.snapshot_hash(job))
            eligible=assessment_scope.allowed(job) and backfill.in_scope(job,role)
            fresh=bool(context and backfill.fresh(job,saved,context) and saved.get('validated') is True and saved.get('recommendation') in backfill.FINAL)
            if fresh and saved.get('comp_status')=='below_floor':eligible=False
            if not eligible:status='excluded';reason='Outside agreed assessment criteria.'
            elif fresh:status='completed';reason='Existing validated result reused.'
            else:
                attempt=queue.get('attempts',{}).get(key,{})
                same=attempt.get('context_hash')==context and attempt.get('job_snapshot_hash')==row['job_snapshot_hash']
                status='processing' if same and attempt.get('last_outcome')=='in_progress' else 'blocked' if same and attempt.get('count',0)>=queue.get('max_attempts',2) else 'waiting'
                reason='Paid retry budget exhausted; review required.' if status=='blocked' else 'Unfinished assessment.'
        else:
            eligible=row.get('eligible',True)
            fresh=bool(saved.get('validated') and saved.get('context_hash')==context and saved.get('job_snapshot_hash')==row.get('job_snapshot_hash') and saved.get('recommendation') in backfill.FINAL)
            status='excluded' if not eligible else 'completed' if fresh else 'blocked';reason='Saved result retained.' if fresh else 'Frozen daily posting is no longer available; review required.'
        row.update(eligible=eligible,status=status,reason=reason,verdict={'strong':'strong','medium':'middle','not':'pass'}.get(saved.get('recommendation')) if status=='completed' else None)
        counts['found']+=1;counts['eligible']+=bool(eligible);counts[status]+=1
        if row['verdict']:counts[row['verdict']]+=1
        members.append(row)
    # Email leads remain visibly unfinished until the employer requisition is
    # confirmed. Similar titles are never silently treated as exact matches.
    link_ids=(set(previous.get('link_ids',[])) if same else set())|set(new_link_ids)
    for row in email.get('rows',[]):
        if local_day(row.get('first_alert_at'))==day:link_ids.add(row['linkedin_id'])
    refs={row['linkedin_id']:row for row in email.get('rows',[])}
    counts['unverified_links']=sum(identifier not in refs or refs[identifier].get('collection') not in ('tracked','screened_out') for identifier in link_ids)
    health=[h for value in meta.get('stats',{}).values() for h in value.get('sources',[])]+meta.get('discovery',[])
    issues=[{'source':h.get('source'),'label':h.get('label',h.get('source')),'status':h.get('status')} for h in health if h.get('status') in ('failed','partial','not_configured')]
    result={'schema':1,'day':day,'start_hour_eastern':1,'deadline_hour_eastern':6,
        'collection_completed':local_day(meta.get('polled_at'))==day,'collection_at':meta.get('polled_at'),
        'counts':counts,'members':members,'link_ids':sorted(link_ids),'coverage_issues':len(issues),'incomplete_sources':issues,
        'checked_at':now.isoformat(),'legacy_backlog':queue.get('counts',{})}
    digest={key:value for key,value in result.items() if key not in ('checked_at','legacy_backlog')}
    result['batch_id']=hashlib.sha256(json.dumps(digest,sort_keys=True).encode()).hexdigest()
    result['status']='not_started' if not result['collection_completed'] else 'assessing' if counts['waiting'] or counts['processing'] else 'blocked' if counts['blocked'] or counts['unverified_links'] or issues else 'ready_to_publish'
    return result

def update(*,now=None,root=None,new_link_ids=()):
    import yaml
    root=Path(root) if root is not None else ROOT
    docs=root/'docs';path=docs/'daily_batch.json'
    result=build(read(docs/'jobs.json',[]),read(docs/'fit_scores.json',{}),read(docs/'fit_policy.json',{}),read(docs/'fit_backfill.json',{}),read(docs/'meta.json',{}),read(docs/'email_alerts.json',{}),yaml.safe_load((root/'config/role_config.yml').read_text(encoding='utf-8')),read(path,{}),now=now,new_link_ids=new_link_ids)
    result['input_blobs']={}
    for name in ('jobs.json','fit_scores.json','fit_backfill.json','meta.json'):
        file=docs/name
        if file.exists():
            data=file.read_bytes()
            result['input_blobs'][name]=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    digest={key:value for key,value in result.items() if key not in ('batch_id','checked_at','legacy_backlog')}
    result['batch_id']=hashlib.sha256(json.dumps(digest,sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

def safe_update(*,root=None,new_link_ids=()):
    root=Path(root) if root is not None else ROOT
    if not (root/'config/role_config.yml').exists():return None
    try:return update(root=root,new_link_ids=new_link_ids)
    except (OSError,ValueError,KeyError) as error:
        import logging
        logging.getLogger(__name__).warning('Daily completion record unavailable (%s); completion remains unverified',type(error).__name__)
        return None

if __name__=='__main__':
    result=update();print(json.dumps({'day':result['day'],'status':result['status'],'counts':result['counts'],'coverage_issues':result['coverage_issues']}))
