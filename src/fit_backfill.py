"""Resumable, finite date-range assessment backfill. Public state contains no career evidence."""
from __future__ import annotations
import argparse
import math
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src import fit,assessment_scope
from src.evidence_batch import context_hash, key, snapshot_hash
from src.evidence_fit import scorer_revision
from src.scorer import _is_us_or_remote
from src.remote_jobs import is_remote
from src.role_intake import pmo_leadership

STATE_FILE = fit.DOCS_DIR / 'fit_backfill.json'
FINAL = {'strong', 'medium', 'not'}

def pipeline_version():
    return 'resume-v1' if os.environ.get('FIT_RESUME_PIPELINE') == 'true' else 'legacy'



def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def posting_time(job):
    return timestamp(job.get('posted_at')) or timestamp(job.get('first_seen_at'))


def in_scope(job, role_config):
    location = re.sub(r'\bremote\b', '', job.get('resolved_locations') or job.get('location') or '', flags=re.I)
    us = _is_us_or_remote(location) or bool(re.search(r'/job/US-', job.get('url') or '', re.I))
    if not us and re.fullmatch(r'(?:\d+\s+locations?)?',location.strip(),re.I) and job.get('source')=='workday':
        from urllib.parse import urlparse,unquote
        url=urlparse(job.get('url') or '')
        part=re.search(r'/job/([^/]+)',url.path,re.I)
        primary=unquote(part.group(1)) if part else ''
        foreign=re.search(r'(?:^|-)(?:remote|canada|toronto|vancouver|montreal|ottawa|ontario|quebec|calgary|waterloo|uk|united-kingdom|england|australia|india|germany|france|singapore)(?:-|$)',primary,re.I)
        us=bool(url.scheme=='https' and (url.hostname or '').endswith('.myworkdayjobs.com') and not foreign and re.search(r'-[A-Z]{2}$',primary,re.I) and _is_us_or_remote(primary.replace('-',', ')))
    reasons = job.get('score_reasons') or []
    role=any(str(r).startswith(('broader responsibility match','level not in title')) for r in reasons)
    title=job.get('title') or ''
    patterns=[r'\b(vp|vice\s+president|head\s+of)\b.*\bprogram',
      r'\bdirector\b.*(\btpm\b|technical\s+program|program\s+management)',
      r'(sr\.?\s*manager|senior\s+manager).*(\btpm\b|technical\s+program|program\s+management)',
      r'\bmanager\b.*(\btpm\b|technical\s+program|program\s+management)|\b(?:tpm|technical\s+program\s+management)\s+manager\b',
      r'\b(principal|staff|distinguished)\b.*\b(technical\s+program\s+manager|tpm)\b',
      r'\b(?:senior|sr\.?|lead|leadership)\b.*\b(?:technical\s+program\s+manager|tpm)\b',
      r'engineering\s+program\s+manage',
      r'\b(head\s+of|director|vp|vice\s+president)\b.*(engineering\s+operations|\bai\b.*(program|delivery))']
    return bool((us or is_remote(job)) and (role or pmo_leadership(title) or any(re.search(pattern,title,re.I) for pattern in patterns)))


def initialize(as_of, priority_keys=(), *, since=None, all_jobs=False):
    cutoff = since or (as_of-timedelta(days=7))
    if cutoff > as_of: raise ValueError('Start date must not be after end date')
    windows = max(1, math.ceil((as_of-cutoff).total_seconds()/(12*3600)))
    return {'schema': 1, 'enabled': True, 'status': 'running', 'as_of': as_of.isoformat(),
            'cutoff': cutoff.isoformat(), 'window_hours': 12,
            'window_count': windows, 'all_jobs': all_jobs, 'max_attempts': 2, 'priority_job_keys': list(dict.fromkeys(priority_keys)),
            'attempts': {}, 'batches': 0, 'created_at': as_of.isoformat()}


def window_index(job, state):
    posted = posting_time(job)
    end = timestamp(state['as_of']); start = timestamp(state['cutoff'])
    if posted is None or not start <= posted <= end:
        return None
    return min(state.get('window_count',14)-1, int((end-posted).total_seconds() // (12*3600)))


def fresh(job, score, context):
    return (score.get('context_hash') == context and score.get('scorer_revision') == scorer_revision()
            and score.get('job_snapshot_hash') == snapshot_hash(job))


def outcome(job, scores, state, context):
    score = scores.get(key(job), {})
    if fresh(job, score, context) and score.get('recommendation') in FINAL:
        return 'assessed'
    attempt = state.get('attempts', {}).get(key(job), {})
    same = (attempt.get('context_hash') == context and attempt.get('job_snapshot_hash') == snapshot_hash(job)
            and attempt.get('pipeline_version','legacy') == pipeline_version())
    if same and attempt.get('last_outcome') == 'in_progress':
        return 'in_progress'
    if same and attempt.get('count', 0) >= state['max_attempts']:
        return 'unresolved'
    return 'pending'


def plan(jobs, scores, state, context, role_config):
    jobs=[j for j in jobs if assessment_scope.allowed(j)]
    if state.get('scope_mode')=='rolling_relevant':
        jobs=[j for j in jobs if in_scope(j,role_config) and not (fresh(j,scores.get(key(j),{}),context) and scores.get(key(j),{}).get('comp_status')=='below_floor')]
    priority = set(state.get('priority_job_keys', []))
    chosen = [j for j in jobs if key(j) in priority or ((state.get('all_jobs') or in_scope(j, role_config)) and window_index(j, state) is not None)]
    if state.get('requested_job_keys'):
        requested=set(state['requested_job_keys'])
        chosen=[j for j in chosen if key(j) in requested]
    statuses = {key(j): outcome(j, scores, state, context) for j in chosen}
    counts = {name: sum(value == name for value in statuses.values()) for name in ('assessed', 'pending', 'unresolved', 'in_progress')}
    counts['total'] = len(chosen)
    counts['strong'] = sum(statuses[key(j)] == 'assessed' and scores[key(j)]['recommendation'] == 'strong' for j in chosen)
    windows = []
    end = timestamp(state['as_of'])
    for index in range(state.get('window_count',14)):
        members = [j for j in chosen if window_index(j, state) == index]
        window_end = end-timedelta(hours=12*index)
        windows.append({'index': index, 'start': max(timestamp(state['cutoff']),window_end-timedelta(hours=12)).isoformat(),
                        'end': window_end.isoformat(), 'total': len(members),
                        **{name: sum(statuses[key(j)] == name for j in members) for name in ('assessed','pending','unresolved','in_progress')}})
    # Strong refresh is a separate queue: a frozen date cohort must not hide it.
    strong_refresh = [j for j in jobs if scores.get(key(j),{}).get('recommendation')=='strong' and scores.get(key(j),{}).get('validated') is True and not fresh(j,scores[key(j)],context)]
    strong_pending = [j for j in strong_refresh if outcome(j,scores,state,context)=='pending']
    strong_in_progress = sum(outcome(j,scores,state,context)=='in_progress' for j in strong_refresh)
    strong_unresolved = sum(outcome(j,scores,state,context)=='unresolved' for j in strong_refresh)
    pending_priority = [j for j in chosen if key(j) in priority and statuses[key(j)] == 'pending']
    selected = strong_pending or pending_priority
    phase = 'strong_reassessment' if strong_pending else 'original_cohort' if selected else 'windows'
    current_window = None
    if not selected:
        if state.get('scope_mode')=='rolling_relevant':
            # Fill a batch across dates instead of booting one workflow per sparse window.
            selected=[j for j in chosen if statuses[key(j)]=='pending']
            if selected:current_window=min(window_index(j,state) for j in selected)
        else:
            for window in windows:
                selected = [j for j in chosen if window_index(j, state) == window['index'] and statuses[key(j)] == 'pending']
                if selected:
                    current_window = window['index']; break
    # Keep US work first; within the international lane prefer explicit remote
    # opportunities. A title screen never assigns a Strong fit verdict.
    selected.sort(key=lambda j: (
        phase != 'strong_reassessment' and _is_us_or_remote(re.sub(r'\bremote\b', '', j.get('resolved_locations') or j.get('location') or '', flags=re.I)),
        phase != 'strong_reassessment' and is_remote(j),
        posting_time(j) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return {'counts': counts, 'windows': windows, 'selected': selected, 'phase': phase, 'cohort_keys':[key(j) for j in chosen],
            'strong_refresh_pending':len(strong_pending),'strong_refresh_unresolved':strong_unresolved,'strong_refresh_in_progress':strong_in_progress,
            'current_window': current_window, 'windows_processed': sum(w['pending'] == 0 and w['in_progress'] == 0 for w in windows)}


def refresh(state, jobs, scores, context, role_config, now):
    if state.get('scope_mode')=='rolling_relevant':
        state['as_of']=now.isoformat()
        state['window_count']=max(1,math.ceil((now-timestamp(state['cutoff'])).total_seconds()/(12*3600)))
    result = plan(jobs, scores, state, context, role_config)
    state.update({name: result[name] for name in ('counts','windows','phase','current_window','windows_processed','strong_refresh_pending','strong_refresh_unresolved','strong_refresh_in_progress')})
    if (result['counts']['in_progress'] or result['strong_refresh_in_progress']) and state.get('current_batch'):
        state['phase'] = state['current_batch']['phase']
        state['current_window'] = state['current_batch']['window']
    state['updated_at'] = now.isoformat()
    state['context_hash'] = context
    if not result['counts']['pending'] and not result['counts']['in_progress'] and not result['strong_refresh_pending'] and not result['strong_refresh_in_progress']:
        state['status'] = 'completed_with_issues' if result['counts']['unresolved'] or result['strong_refresh_unresolved'] else 'completed'
    else:
        state['status'] = 'running'
    return result


def continuation_needed(state, jobs, scores, context, role_config, now):
    """Reconcile completed batches against refreshed postings without resetting retries."""
    if not state.get('enabled') or state.get('status') == 'archived':
        return False
    if not context or not state.get('cutoff') or not state.get('as_of'):
        return False
    if state.get('status')=='waiting_for_provider' and not any(scores.get(key(j),{}).get('recommendation')=='strong' and scores.get(key(j),{}).get('validated') is True and outcome(j,scores,state,context)=='pending' and assessment_scope.allowed(j) for j in jobs):
        return False
    refresh(state,jobs,scores,context,role_config,now)
    return state['status'] == 'running'


def retry_unfinished(state, jobs, scores, context, role_config, now):
    """Explicitly renew exhausted attempts, preserving valid ratings and retry history."""
    if not state.get('enabled'): raise RuntimeError('No enabled assessment period')
    reset=[]
    for job in jobs:
        if not assessment_scope.allowed(job):continue
        if window_index(job,state) is None and key(job) not in state.get('priority_job_keys',[]): continue
        if not state.get('all_jobs') and not in_scope(job,role_config) and key(job) not in state.get('priority_job_keys',[]): continue
        if outcome(job,scores,state,context) != 'unresolved': continue
        attempt=state['attempts'][key(job)]
        state.setdefault('retry_history',[]).append({'job_key':key(job), 'attempt':dict(attempt), 'resumed_at':now.isoformat()})
        attempt['count']=0
        attempt['last_outcome']='explicit_retry'
        reset.append(key(job))
    state['status']='running'
    refresh(state,jobs,scores,context,role_config,now)
    return reset


def run_batch(*, jobs, profile, rules, scores, state, model, role_config, max_jobs=6,
              now=None, runner=None, persist=None):
    if max_jobs < 1: raise ValueError('max_jobs must be positive')
    now = now or datetime.now(timezone.utc)
    context = context_hash(profile, rules, model)
    # A killed worker consumes one attempt, but can never strand a job in-flight forever.
    for attempt in state.get('attempts', {}).values():
        if attempt.get('last_outcome') == 'in_progress':
            attempt['last_outcome'] = 'interrupted'
    result = refresh(state, jobs, scores, context, role_config, now)
    selected = result['selected'][:max(0, max_jobs)]
    if not selected:
        if persist: persist(state, scores)
        return scores
    for job in selected:
        previous = state['attempts'].get(key(job), {})
        same = (previous.get('context_hash') == context and previous.get('job_snapshot_hash') == snapshot_hash(job)
                and previous.get('pipeline_version','legacy') == pipeline_version())
        state['attempts'][key(job)] = {'count': (previous.get('count',0) if same else 0)+1,
            'context_hash': context, 'job_snapshot_hash': snapshot_hash(job),
            'last_attempt_at': now.isoformat(), 'last_outcome': 'in_progress','pipeline_version':pipeline_version()}
    state['batches'] += 1
    state['current_batch'] = {'started_at': now.isoformat(), 'job_keys': [key(j) for j in selected],
                             'phase': result['phase'], 'window': result['current_window']}
    state['status'] = 'running'
    refresh(state, jobs, scores, context, role_config, now)
    if persist: persist(state, scores)
    def on_scored(updated):
        nonlocal scores
        scores = updated
        for job in selected:
            score = updated.get(key(job), {})
            if fresh(job, score, context) and any(timestamp(score.get(field)) and timestamp(score[field]) >= now for field in ('scored_at','jd_checked_at')):
                state['attempts'][key(job)]['last_outcome'] = score.get('recommendation', 'review')
        refresh(state, jobs, updated, context, role_config, datetime.now(timezone.utc))
        if persist: persist(state, updated)
    try:
        scores = (runner or fit.run)(jobs=jobs, profile=profile, rules=rules, existing=scores,
            max_jobs=len(selected), rescore=False, job_keys=[key(j) for j in selected], on_scored=on_scored)
    finally:
        for job in selected:
            attempt = state['attempts'][key(job)]
            if attempt['last_outcome'] == 'in_progress': attempt['last_outcome'] = 'failed'
        refresh(state, jobs, scores, context, role_config, datetime.now(timezone.utc))
        if persist: persist(state, scores)
    return scores


def publish_checkpoint():
    """Publish only public scoring/progress files; preserve runtime model overrides."""
    active_config = fit.TAILOR_CONFIG_FILE.read_text(encoding='utf-8')
    def git(*args, check=True):
        return subprocess.run(['git', *args], cwd=fit.ROOT, check=check, capture_output=True, text=True)
    try:
        # Rebase creates commits too; identity must persist beyond the initial commit.
        git('config','user.name','github-actions[bot]')
        git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
        git('restore', '--', 'config/tailor_config.yml')
        subprocess.run(['node','src/validate_shortlist.cjs'],cwd=fit.ROOT,check=True)
        git('add', 'docs/fit_scores.json', 'docs/fit_policy.json', 'docs/fit_backfill.json', 'docs/shortlist_validation.json')
        git('add', 'docs/metrics')
        if git('diff', '--cached', '--quiet', check=False).returncode == 0: return
        git('-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit','-m','fit: checkpoint assessment backfill')
        for attempt in range(3):
            git('pull', '--rebase', 'origin', 'main')
            subprocess.run(['node','src/validate_shortlist.cjs','--check-only'],cwd=fit.ROOT,check=True)
            if git('push', check=False).returncode == 0: return
        raise RuntimeError('Could not publish backfill checkpoint')
    finally:
        fit.TAILOR_CONFIG_FILE.write_text(active_config, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--as-of')
    parser.add_argument('--since')
    parser.add_argument('--all-jobs', action='store_true')
    parser.add_argument('--priority-json', default='[]')
    parser.add_argument('--enabled', action='store_true')
    parser.add_argument('--should-continue', action='store_true')
    parser.add_argument('--retry-unfinished', action='store_true')
    parser.add_argument('--provider-error', action='store_true')
    parser.add_argument('--max', type=int, default=6)
    args = parser.parse_args(argv)
    state = fit.load_json(STATE_FILE, {})
    if args.enabled: return 0 if state.get('enabled') else 1
    if args.should_continue or args.retry_unfinished:
        import yaml
        policy=fit.load_json(fit.DOCS_DIR/'fit_policy.json',{})
        jobs=fit.load_json(fit.JOBS_FILE,[]); scores=fit.load_json(fit.FIT_FILE,{})
        role_config=yaml.safe_load((fit.CONFIG_DIR/'role_config.yml').read_text(encoding='utf-8'))
        now=datetime.now(timezone.utc)
        if args.retry_unfinished:
            reset=retry_unfinished(state,jobs,scores,policy.get('context_hash'),role_config,now)
            print(json.dumps({'reset_unfinished':len(reset),'counts':state['counts']}))
        needed=continuation_needed(state,jobs,scores,policy.get('context_hash'),role_config,now)
        STATE_FILE.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
        return 0 if needed else 1
    if args.init:
        anchor = timestamp(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if anchor is None: parser.error('Invalid --as-of timestamp')
        since = timestamp(args.since) if args.since else None
        if args.since and since is None: parser.error('Invalid --since timestamp')
        state = initialize(anchor, json.loads(args.priority_json), since=since, all_jobs=args.all_jobs)
        STATE_FILE.write_text(json.dumps(state, indent=2)+'\n', encoding='utf-8')
        return 0
    if args.provider_error:
        if state.get('enabled'):
            state.update(status='waiting_for_provider', updated_at=datetime.now(timezone.utc).isoformat())
            STATE_FILE.write_text(json.dumps(state, indent=2)+'\n', encoding='utf-8')
        return 0
    if not state.get('enabled'): raise RuntimeError('Initialize the backfill before running it')
    import yaml
    profile = fit.load_json(fit.PROFILE_FILE,{})
    from src.fitment_skill import attach
    rules = attach(fit.load_json(fit.FIT_RULES_FILE,{}))
    if not profile or not rules.get('evidence_required'): raise RuntimeError('Private evidence scoring is required')
    model = fit._fit_model()
    policy = {'version':rules.get('fit_policy_version',''), 'evidence_required':True,
              'engine_version':'evidence-v1', 'scorer_revision':scorer_revision(),
              'context_hash':context_hash(profile,rules,model),'fitment_skill_hash':rules['fitment_skill_hash'],'verdicts':['strong','middle','pass'],'assessment_not_before':assessment_scope.cutoff().isoformat()}
    (fit.DOCS_DIR/'fit_policy.json').write_text(json.dumps(policy)+'\n',encoding='utf-8')
    role_config = yaml.safe_load((fit.CONFIG_DIR/'role_config.yml').read_text(encoding='utf-8'))
    jobs = fit.load_json(fit.JOBS_FILE,[]); scores = fit.load_json(fit.FIT_FILE,{})
    def persist(progress, results):
        progress['run_url'] = 'https://github.com/'+os.environ.get('GITHUB_REPOSITORY','firelyco/JobSearch')+'/actions/runs/'+os.environ.get('GITHUB_RUN_ID','')
        fit.save(results)
        from src.metrics_history import capture_fit
        capture_fit(jobs,results,progress)
        STATE_FILE.write_text(json.dumps(progress,indent=2)+'\n',encoding='utf-8')
        if os.environ.get('FIT_BACKFILL_PUBLISH') == 'true': publish_checkpoint()
    run_batch(jobs=jobs,profile=profile,rules=rules,scores=scores,state=state,model=model,
              role_config=role_config,max_jobs=args.max,persist=persist)
    print(json.dumps({'status':state['status'], 'counts':state['counts'], 'windows_processed':state['windows_processed']}))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as out:
            out.write('Assessment backfill: '+json.dumps(state['counts'])+'; status: '+state['status']+'\n')
    return 0

if __name__ == '__main__': raise SystemExit(main())
