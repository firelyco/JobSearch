"""Refresh published salary evidence independently of AI assessment/cache."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
import logging
import re
import time
import requests
from src import fit,jd_fetch
from src.compensation import VERSION,parse_salary,page_salary_metadata
from src.evidence_batch import key,snapshot_hash

log=logging.getLogger(__name__)


def retrieve(job,floor=250000):
    started=time.monotonic()
    # Stored discovery descriptions can omit compensation sections; fetch detail.
    text=jd_fetch.get_jd_text({**job,'description':''})
    result=parse_salary(text,job,floor)
    source='employer job description / salary fields'
    if result['status']=='unknown':
        try:
            response=requests.get(job['url'],headers={'User-Agent':'Mozilla/5.0 (compatible; JobSearchBot/1.0)'},timeout=25)
            if response.status_code==200:
                visible=jd_fetch.strip_html(response.text)
                mixed=re.search(r'including\s+base\s+and\s+variable\s+compensation',text+' '+visible,re.I)
                metadata='' if mixed else page_salary_metadata(response.text,job)
                result=parse_salary(text+'\n'+visible+'\n'+metadata,job,floor)
                source='employer page and structured salary metadata' if metadata else 'employer page'
                if not text:text=visible
                if mixed and result['status']=='unknown':result['verification']='base_and_variable_combined'
        except requests.RequestException:
            pass
    if not text or len(text)<200:result['verification']='source_unavailable'
    result.update(checked_at=datetime.now(timezone.utc).isoformat(),source_url=job.get('url',''),
                  job_snapshot_hash=snapshot_hash(job),source_method=source,seconds=round(time.monotonic()-started,3))
    return result


def apply_check(score,check):
    updated=dict(score)
    if check['verification']=='source_unavailable':
        updated['salary_check_attempt']=check
        return updated
    updated.update(comp_status=check['status'],salary_check=check)
    if score.get('career_fit')=='strong':
        reason=str(score.get('reason') or 'Reviewed evidence supports the role.')
        for marker in (' Base salary is unverified;', ' The published base range reaches', ' Posted base salary is below', ' Base salary remains unverified from the captured employer posting.', ' Published base salary range verified;', ' Published base maximum is below'):
            reason=reason.split(marker)[0]
        updated['reason']=reason+(' Published base maximum is below the salary threshold.' if check['status']=='below_floor' else
            ' Published base salary range verified; bonus, equity and benefits are additional.' if check['status']!='unknown' else
            ' Base salary remains unverified from the captured employer posting.')
    if score.get('career_fit')=='strong' and score.get('validated') is True:
        location_ok=score.get('location_status')=='eligible' or bool(score.get('fitment_skill_hash') and score.get('location_status')=='conditional')
        eligible=check['status']!='below_floor' and location_ok
        updated.update(eligible=eligible,recommendation='strong' if eligible else 'medium',
                       step='none' if eligible else 'comp' if check['status']=='below_floor' else 'location')
    if 'verdict' in updated:
        updated['verdict']={'strong':'strong','medium':'middle','not':'pass'}.get(updated.get('recommendation'))
    return updated


def needs_check(job,score,now):
    previous=score.get('salary_check') or {}
    if previous.get('parser_version')!=VERSION or previous.get('job_snapshot_hash')!=snapshot_hash(job):return True
    try:return (now-datetime.fromisoformat(previous['checked_at'])).total_seconds()>=24*3600
    except (KeyError,ValueError,TypeError):return True


def run(jobs,scores,policy,*,max_jobs=100,force=False,fetcher=retrieve,on_saved=None):
    now=datetime.now(timezone.utc)
    candidates=[j for j in jobs if (scores.get(key(j),{}).get('career_fit')=='strong' or scores.get(key(j),{}).get('recommendation')=='strong')
        and scores[key(j)].get('context_hash')==policy.get('context_hash')
        and scores[key(j)].get('scorer_revision')==policy.get('scorer_revision')
        and scores[key(j)].get('job_snapshot_hash')==snapshot_hash(j)
        and (force or needs_check(j,scores[key(j)],now))]
    candidates.sort(key=lambda j:j.get('posted_at') or j.get('first_seen_at') or '',reverse=True)
    selected=candidates[:max(0,max_jobs)]
    updated=dict(scores);stats={'checked':0,'verified':0,'below_floor':0,'unverified':0,'failed':0,'remaining':max(0,len(candidates)-len(selected))}
    if selected:
        with ThreadPoolExecutor(max_workers=min(5,len(selected))) as pool:
            futures={pool.submit(fetcher,j):j for j in selected}
            for future in as_completed(futures):
                job=futures[future]
                try:
                    check=future.result();updated[key(job)]=apply_check(updated[key(job)],check)
                    stats['checked']+=1
                    if check['verification']=='source_unavailable':stats['failed']+=1
                    elif check['status']=='unknown':stats['unverified']+=1
                    else:stats['verified']+=1
                    if check['status']=='below_floor':stats['below_floor']+=1
                    if on_saved:on_saved(updated)
                    log.info('Salary check %s status=%s seconds=%s',key(job),check['status'],check.get('seconds'))
                except Exception as error:
                    stats['failed']+=1
                    log.warning('Salary check failed %s (%s)',key(job),type(error).__name__)
    return updated,stats


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--max',type=int,default=100);parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    scores,stats=run(fit.load_json(fit.JOBS_FILE,[]),fit.load_json(fit.FIT_FILE,{}),fit.load_json(fit.DOCS_DIR/'fit_policy.json',{}),
                     max_jobs=args.max,force=args.force,on_saved=fit.save)
    fit.save(scores)
    # Refresh only the shortlist count, leaving assessment completion/attempts intact.
    from src import fit_backfill as backfill
    import yaml
    state=fit.load_json(backfill.STATE_FILE,{})
    if state.get('enabled'):
        jobs=fit.load_json(fit.JOBS_FILE,[]);policy=fit.load_json(fit.DOCS_DIR/'fit_policy.json',{})
        cfg=yaml.safe_load((fit.CONFIG_DIR/'role_config.yml').read_text(encoding='utf-8'))
        calculated=backfill.plan(jobs,scores,state,policy.get('context_hash'),cfg)
        state.setdefault('counts',{})['strong']=calculated['counts']['strong']
        state['salary_checked_at']=datetime.now(timezone.utc).isoformat()
        backfill.STATE_FILE.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(stats))
    if __import__('os').environ.get('GITHUB_STEP_SUMMARY'):
        with open(__import__('os').environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as out:out.write('Salary verification (no AI calls): '+json.dumps(stats)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
