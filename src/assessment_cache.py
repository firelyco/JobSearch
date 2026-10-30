"""Semantic assessment identity and persistent per-input paid-attempt budget."""
import json,os
from src.evidence_fit import build_inventory,digest,scorer_revision
from src.private_fit_store import load_private,save_private

FINAL={'strong','medium','not'}
MAX_PAID_ATTEMPTS=2


def context(profile,rules):
    # Provider, model, transport, retry and concurrency settings are execution details.
    policy={'skill_hash':rules['fitment_skill_hash'],'comp_floor_usd':rules.get('comp_floor_usd')} if rules.get('_fitment_skill') else {k:v for k,v in rules.items() if k not in {'evidence_review_model','fit_api_blocked','calibration_pending'}}
    return digest({'version':'assessment-cache-v1','revision':scorer_revision(),'inventory':build_inventory(profile,rules),'policy':policy})


def content_hash(jd):
    return digest(' '.join(str(jd).split()))


def identity(profile,job,jd,rules):
    return digest({'context':context(profile,rules),'jd':content_hash(jd),'job':{k:job.get(k) for k in ('title','company','location','url')}})


class AttemptBudgetExhausted(RuntimeError):pass


def reserve(job_key,signature,inventory,stamp,*,force=False):
    """Persist before inference, so a timeout or a later workflow cannot reset spend."""
    namespace='attempts:'+job_key
    previous=load_private(namespace,input_hash=signature) or {}
    count=int(previous.get('paid_attempts',0))
    campaigns=dict(previous.get('retry_campaigns',{}))
    campaign=os.environ.get('FIT_RETRY_CAMPAIGN','')
    allowed=job_key in json.loads(os.environ.get('FIT_RETRY_JOB_KEYS','[]'))
    if campaign and allowed and campaign not in campaigns and previous.get('status')!='complete':
        campaigns[campaign]=max(MAX_PAID_ATTEMPTS,count)+2
    limit=campaigns.get(campaign,MAX_PAID_ATTEMPTS) if allowed else MAX_PAID_ATTEMPTS
    if not force and (previous.get('status')=='complete' or count>=limit):
        raise AttemptBudgetExhausted('Paid assessment already completed or retry budget exhausted; explicit rescore required')
    attempt={'inventory':inventory,'input_hash':signature,'paid_attempts':count+1,'status':'in_progress','last_attempt_at':stamp,'retry_campaigns':campaigns}
    save_private(attempt,job_key=namespace)
    return attempt


def finish(job_key,attempt,status):
    if attempt is not None:save_private({**attempt,'status':status},job_key='attempts:'+job_key)
