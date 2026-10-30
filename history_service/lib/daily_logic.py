"""Pure daily orchestration decisions. No model calls or private career data."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo

TZ=ZoneInfo('America/New_York')
ACTIVE={'queued','in_progress','waiting','pending','requested'}

def parsed(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.replace(tzinfo=result.tzinfo or timezone.utc)
    except (ValueError,TypeError):return None

def decide(batch,runs,state,now,*,published=False):
    local=now.astimezone(TZ);day=local.date().isoformat()
    result={'day':day,'deadline_at':local.replace(hour=6,minute=0,second=0,microsecond=0).isoformat(),
        'checked_at':now.isoformat(),'action':None,'status':'not_started','reason':'Collection starts at 1 AM Eastern.',
        'dispatches':dict(state.get('dispatches',{}))}
    if local.hour<1:return result
    current=batch if batch.get('day')==day else {}
    counts=current.get('counts',{})
    result.update(counts=counts,batch_id=current.get('batch_id'))
    def active(kind):return any(r.get('status') in ACTIVE for r in runs.get(kind,[]))
    def dispatch(kind,reason):
        prior=result['dispatches'].get(kind,{})
        checked=parsed(prior.get('requested_at'))
        if checked and (now-checked).total_seconds()<600:
            result.update(status='recovering',reason='Recent dispatch is awaiting worker confirmation.');return
        limit=3 if kind=='poll' else 36
        if prior.get('count',0)>=limit:
            result.update(status='blocked',reason=f'{kind} restart limit reached; intervention required.');return
        result['dispatches'][kind]={'count':prior.get('count',0)+1,'requested_at':now.isoformat()}
        result.update(action=kind,status='collecting' if kind=='poll' else 'assessing',reason=reason)
    if not current.get('collection_completed'):
        if active('poll'):result.update(status='collecting',reason='Daily collection is queued or running.')
        else:dispatch('poll','Start or recover the single daily collection batch.')
    elif current.get('checkpoint_stale') or counts.get('waiting',0) or counts.get('processing',0):
        if active('fit'):result.update(status='assessing',reason='Assessment worker is queued or running.')
        else:dispatch('fit','Resume unfinished assessments; completed inputs and paid attempt limits are preserved.')
    elif current.get('coverage_issues',0) and result['dispatches'].get('poll',{}).get('count',0)<3:
        if active('poll'):result.update(status='collecting',reason='Recovering incomplete sources in the same daily batch.')
        else:dispatch('poll','Retry incomplete collection in the same daily batch; attempts are bounded.')
    elif counts.get('blocked',0) or counts.get('unverified_links',0) or current.get('coverage_issues',0):
        result.update(status='blocked',reason='Daily batch has unresolved assessments, unverified job links, or incomplete sources; it is not complete.')
    elif published:
        result.update(status='complete',reason='Collection, eligible assessments, and dashboard publication verified.',completed_at=state.get('completed_at') or now.isoformat())
        if parsed(result['completed_at'])>parsed(result['deadline_at']):
            result.update(status='completed_late',reason='Processing is complete, but completion was not verified by the 6 AM deadline.')
    else:
        result.update(status='publishing',reason='Waiting to verify this batch on the live dashboard.')
    if local.hour==5 and local.minute>=30 and result['status']!='complete':
        result['work_status']=result['status'];result['status']='at_risk'
        result['reason']='Less than 30 minutes remain before the 6 AM deadline. '+result['reason']
    if local.hour>=8:result['action']=None
    if local.hour>=6 and result['status'] not in ('complete','completed_late'):
        result['work_status']=result['status'];result['status']='missed_deadline'
        result['reason']='6 AM completion deadline missed. '+result['reason']
    return result
