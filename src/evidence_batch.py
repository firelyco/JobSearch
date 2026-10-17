"""Bounded parallel scoring with private persistence before public publication."""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from src import fit_scorer,jd_fetch,llm_client
from src.evidence_fit import build_inventory,digest,scorer_revision,input_hash
from src.private_fit_store import save_private,load_private
log=logging.getLogger(__name__)

def key(job):return ':'.join(str(job.get(k)) for k in ('source','company','id'))
def snapshot_hash(job):return digest({k:job.get(k) for k in ('title','company','location','url','description','updated_at')})
def context_hash(profile,rules,model):return digest({'revision':scorer_revision(),'inventory':build_inventory(profile,rules),'rules':rules,'model':model})

def run(*,jobs,profile,existing,rules,max_jobs,rescore,fake_client,jd_text_for,stamp,model,on_scored,job_keys):
    current={key(j) for j in jobs};scores={k:v for k,v in existing.items() if k in current}
    context=context_hash(profile,rules,model)
    def needs(job):
        cached=scores.get(key(job),{})
        if rescore or cached.get('context_hash')!=context or cached.get('job_snapshot_hash')!=snapshot_hash(job):return True
        try:return (datetime.fromisoformat(stamp)-datetime.fromisoformat(cached.get('jd_checked_at',''))).total_seconds()>=6*3600
        except (ValueError,TypeError):return True
    pending=[j for j in jobs if (job_keys is None or key(j) in job_keys) and needs(j)]
    pending.sort(key=lambda j:not(scores.get(key(j),{}).get('recommendation')=='strong' or scores.get(key(j),{}).get('step')=='comp'))
    selected=pending[:max(0,max_jobs)]
    if not selected:return scores
    def evaluate(job):
        job_key=key(job)
        try:
            fetch_started=time.monotonic()
            jd=jd_text_for.get(job_key,'') if jd_text_for is not None else jd_fetch.get_jd_text(job)
            log.info('Evidence JD fetch seconds=%.3f',time.monotonic()-fetch_started)
            fingerprint=input_hash(profile,job,jd,rules,model)
            if not rescore and scores.get(job_key,{}).get('input_hash')==fingerprint and scores.get(job_key,{}).get('recommendation')!='review':
                return job_key,{**scores[job_key],'jd_checked_at':stamp}
            client=llm_client.build_client(fake=fake_client) if fake_client else llm_client.build_client()
            if os.environ.get('FIT_RESUME_PIPELINE')=='true':
                from src.evidence_pipeline import assess
                checkpoint_key='checkpoint:'+job_key
                read_started=time.monotonic()
                resume=load_private(checkpoint_key,input_hash=fingerprint)
                log.info('Evidence checkpoint read seconds=%.3f hit=%s',time.monotonic()-read_started,resume is not None)
                def checkpoint(payload):
                    checkpoint_started=time.monotonic()
                    save_private(payload,job_key=checkpoint_key)
                    log.info('Evidence checkpoint write seconds=%.3f',time.monotonic()-checkpoint_started)
                verdict=assess(profile,job,jd,client=client,model=model,rules=rules,resume=resume,
                    checkpoint=checkpoint)
            else:
                verdict=fit_scorer.score_fit(profile,job,jd,client=client,model=model,rules=rules)
            if verdict.public.get('career_fit')=='strong' and verdict.public.get('validated') is True:
                # Verify new shortlist entries before publication, independently of AI.
                from src.refresh_salaries import retrieve,apply_check
                check=retrieve(job)
                updated=apply_check({**verdict.public,'recommendation':verdict.recommendation,
                    'reason':verdict.reason,'step':verdict.step},check)
                verdict.recommendation=updated.pop('recommendation')
                verdict.reason=updated.pop('reason');verdict.step=updated.pop('step')
                verdict.public.update(updated)
                verdict.private['public']=dict(verdict.public)
                verdict.private['salary_check']=check
            storage_started=time.monotonic()
            save_private(verdict.private,job_key=job_key)
            log.info('Evidence final storage seconds=%.3f',time.monotonic()-storage_started)
            return job_key,{**verdict.public,'recommendation':verdict.recommendation,'reason':verdict.reason,'step':verdict.step,
                'policy_version':rules.get('fit_policy_version'),'context_hash':context,'job_snapshot_hash':snapshot_hash(job),
                'jd_checked_at':stamp,'scored_at':stamp,'scorer_revision':scorer_revision()}
        except Exception as error:
            log.warning('Assessment pending for %s (%s); no unpersisted verdict published',job_key,type(error).__name__)
            return job_key,None
    with ThreadPoolExecutor(max_workers=min(3,len(selected))) as pool:
        for future in as_completed([pool.submit(evaluate,j) for j in selected]):
            job_key,verdict=future.result()
            if verdict is None:continue
            scores[job_key]=verdict
            if on_scored:on_scored(scores)
            log.info('Evidence rating %s %s',job_key,verdict['recommendation'])
    log.info('Evidence refresh checked %d roles; %d remain',len(selected),len(pending)-len(selected))
    return scores
