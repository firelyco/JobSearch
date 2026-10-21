"""Bounded parallel scoring with private persistence before public publication."""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
from src import fit_scorer,jd_fetch,llm_client
from src.evidence_fit import build_inventory,digest,scorer_revision,input_hash
from src.private_fit_store import save_private,load_private
from src import assessment_cache as cache
log=logging.getLogger(__name__)

def key(job):return ':'.join(str(job.get(k)) for k in ('source','company','id'))
def snapshot_hash(job):return digest({k:job.get(k) for k in ('title','company','location','url','description','updated_at')})
def context_hash(profile,rules,model):return cache.context(profile,rules)

def replay_same_skill(prior,profile,job,jd,rules,model,fingerprint):
    """Revalidate a full saved skill review only when its source inputs match.

    A new skill, evidence inventory, JD or role needs review; a model switch alone does not.
    Decision/transport fixes can revalidate existing citations without another
    paid inference. Invalid reviews fall through to a fresh model assessment.
    """
    if not isinstance(prior,dict) or prior.get('public',{}).get('fitment_skill_hash')!=rules.get('fitment_skill_hash'):return None
    independent=prior.get('review_method')=='independent_skill_review'
    seed=reusable_extraction(prior,profile,job,jd,rules,model,fingerprint)
    if seed is None:return None
    review=prior.get('independent_review') or {}
    from src.evidence_fit import review_result,ENGINE_VERSION
    from src.compensation import parse_salary
    from src.fit_scorer import salary_text
    comp=parse_salary(jd,job,rules['comp_floor_usd'])['status']
    verdict=review_result(seed['extraction'],review,seed['inventory'],salary_text(jd),job,comp,rules)
    if verdict.recommendation=='review':
        prior['_revalidation_errors']=verdict.private.get('validation_errors',[])
        return None
    verdict.public['input_hash']=fingerprint
    if independent:
        verdict.public['review_method']='independent_skill_review'
        verdict.private.update(review_method='independent_skill_review',reviewer=prior.get('reviewer','Codex'))
    verdict.private.update(job=seed['job'],job_text=salary_text(jd),input_hash=fingerprint,
        engine_version=ENGINE_VERSION,model=prior.get('model') or model,review_model=prior.get('review_model') or prior.get('model') or model,cache_reused_with_model=model,
        rules_version=rules.get('fit_policy_version'),review_replayed_from=prior.get('input_hash'),
        execution={'pipeline':'skill-review-revalidation','calls_this_attempt':0})
    return verdict


def reusable_extraction(prior,profile,job,jd,rules,model,fingerprint):
    """Reuse factual extraction only with identical evidence, full JD and role.

    The previous verdict/review is never reused across policies. The complete
    JD and original inventory go through a new skill review and bounded repair.
    """
    if not isinstance(prior,dict):return None
    from src.evidence_pipeline import validate_proposal,PIPELINE_VERSION
    from src.fit_scorer import salary_text
    inventory=build_inventory(profile,rules)
    facts={k:job.get(k) for k in ('title','company','location','url')}
    if prior.get('inventory')!=inventory or prior.get('job_text')!=salary_text(jd) or prior.get('job')!=facts:return None
    try:validate_proposal(prior.get('extraction'))
    except (ValueError,TypeError,KeyError):return None
    return {'pipeline_version':PIPELINE_VERSION,'input_hash':fingerprint,'inventory':inventory,
        'job':facts,'model':model,'review_model':rules.get('evidence_review_model') or model,
        'stage':'review','extraction':prior['extraction'],'metrics':[],
        'extraction_reused_from':prior.get('input_hash'),
        'repair_errors':(prior.get('_revalidation_errors') or prior.get('validation_errors',[])) if prior.get('public',{}).get('fitment_skill_hash')==rules.get('fitment_skill_hash') else []}


def run(*,jobs,profile,existing,rules,max_jobs,rescore,fake_client,jd_text_for,stamp,model,on_scored,job_keys):
    # Keep completed records when a posting leaves the feed; reappearance is not a new assessment.
    scores=dict(existing)
    context=context_hash(profile,rules,model)
    def needs(job):
        cached=scores.get(key(job),{})
        if rescore or cached.get('recommendation') not in cache.FINAL or cached.get('validated') is not True or cached.get('context_hash')!=context or cached.get('job_snapshot_hash')!=snapshot_hash(job):return True
        try:return (datetime.fromisoformat(stamp)-datetime.fromisoformat(cached.get('jd_checked_at',''))).total_seconds()>=6*3600
        except (ValueError,TypeError):return True
    pending=[j for j in jobs if (job_keys is None or key(j) in job_keys) and needs(j)]
    def priority(job):
        score=scores.get(key(job),{})
        changed=score.get('context_hash')!=context or score.get('job_snapshot_hash')!=snapshot_hash(job)
        if changed and score.get('recommendation')=='strong' and score.get('validated') is True:return 0
        if score.get('recommendation') not in cache.FINAL:return 1
        return 2 if changed else 3  # Freshness-only source checks follow real assessment work.
    pending.sort(key=lambda j:j.get('posted_at') or j.get('first_seen_at') or '',reverse=True)
    pending.sort(key=priority)
    selected=pending[:max(0,max_jobs)]
    if not selected:return scores
    def evaluate(job):
        job_key=key(job)
        attempt=None
        try:
            fetch_started=time.monotonic()
            jd=jd_text_for.get(job_key,'') if jd_text_for is not None else jd_fetch.get_jd_text(job)
            log.info('Evidence JD fetch seconds=%.3f',time.monotonic()-fetch_started)
            if not str(jd or '').strip():
                log.warning('Employer description unavailable for %s; keeping previous assessment and spending no AI calls',job_key)
                return job_key,None
            fingerprint=input_hash(profile,job,jd,rules,model)
            semantic_key=cache.identity(profile,job,jd,rules)
            previous=scores.get(job_key,{})
            if not rescore and previous.get('assessment_cache_key')==semantic_key and previous.get('validated') is True and previous.get('recommendation') in cache.FINAL:
                log.info('Assessment cache hit %s; zero AI calls',job_key)
                return job_key,{**previous,'context_hash':context,'job_snapshot_hash':snapshot_hash(job),'jd_checked_at':stamp,'cache_status':'reused'}
            if not rescore and previous.get('input_hash')==fingerprint and previous.get('validated') is True and previous.get('recommendation') in cache.FINAL:
                log.info('Legacy assessment cache migrated %s; zero AI calls',job_key)
                return job_key,{**previous,'context_hash':context,'job_snapshot_hash':snapshot_hash(job),'assessment_cache_key':semantic_key,'jd_checked_at':stamp,'cache_status':'reused'}
            verdict=None
            prior=load_private(job_key) if rules.get('_fitment_skill') and not rescore else None
            if prior is not None:
                if not prior.get('extraction') or not prior.get('independent_review'):
                    previous=load_private('checkpoint:'+job_key)
                    if previous and previous.get('input_hash')==prior.get('input_hash') and previous.get('inventory')==prior.get('inventory'):
                        prior=dict(prior)
                        if previous.get('extraction'):prior['extraction']=previous['extraction']
                        if previous.get('review'):prior['independent_review']=previous['review']
                verdict=replay_same_skill(prior,profile,job,jd,rules,model,fingerprint)
            if verdict is None:
                if os.environ.get('FIT_CACHE_ONLY')=='true':
                    raise RuntimeError('Cache-only validation: paid inference is disabled')
                if fake_client is None:
                    attempt=cache.reserve(job_key,semantic_key,build_inventory(profile,rules),stamp,force=rescore)
                client=llm_client.build_client(fake=fake_client) if fake_client else llm_client.build_client()
                if rules.get('_fitment_skill') or os.environ.get('FIT_RESUME_PIPELINE')=='true':
                    from src.evidence_pipeline import assess
                    checkpoint_key='checkpoint:'+job_key
                    read_started=time.monotonic()
                    resume=load_private(checkpoint_key,input_hash=fingerprint)
                    if resume is None and rules.get('_fitment_skill'):
                        resume=reusable_extraction(prior,profile,job,jd,rules,model,fingerprint)
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
            verdict.private.update(assessment_cache_key=semantic_key,semantic_context_hash=context)
            save_private(verdict.private,job_key=job_key)
            cache.finish(job_key,attempt,'complete' if verdict.recommendation in cache.FINAL else 'incomplete')
            log.info('Evidence final storage seconds=%.3f',time.monotonic()-storage_started)
            return job_key,{**verdict.public,'recommendation':verdict.recommendation,'reason':verdict.reason,'step':verdict.step,
                'policy_version':rules.get('fit_policy_version'),'context_hash':context,'job_snapshot_hash':snapshot_hash(job),
                'jd_checked_at':stamp,'scored_at':stamp,'scorer_revision':scorer_revision(),
                'assessment_cache_key':semantic_key,'cache_status':'revalidated_without_ai' if verdict.private.get('execution',{}).get('calls_this_attempt')==0 else 'assessed',
                'paid_attempts':(attempt or {}).get('paid_attempts',0)}
        except Exception as error:
            if attempt is not None:
                try:cache.finish(job_key,attempt,'failed')
                except Exception:log.warning('Could not finalize attempt ledger for %s; reserved attempt remains counted',job_key)
            category=str(error) if isinstance(error,RuntimeError) and str(error).startswith(('Private assessment read HTTP ','Private evidence storage HTTP ')) else type(error).__name__
            log.warning('Assessment pending for %s (%s); no unpersisted verdict published',job_key,category)
            return job_key,None
    with ThreadPoolExecutor(max_workers=min(max(1,min(6,int(os.environ.get('FIT_WORKERS','3')))),len(selected))) as pool:
        for future in as_completed([pool.submit(evaluate,j) for j in selected]):
            job_key,verdict=future.result()
            if verdict is None:continue
            scores[job_key]=verdict
            if on_scored:on_scored(scores)
            log.info('Evidence rating %s %s',job_key,verdict['recommendation'])
    log.info('Evidence refresh checked %d roles; %d remain',len(selected),len(pending)-len(selected))
    return scores
