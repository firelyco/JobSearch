"""Semantic assessment identity and persistent per-input paid-attempt budget."""
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
    if not force and (previous.get('status')=='complete' or count>=MAX_PAID_ATTEMPTS):
        raise AttemptBudgetExhausted('Paid assessment already completed or retry budget exhausted; explicit rescore required')
    attempt={'inventory':inventory,'input_hash':signature,'paid_attempts':count+1,'status':'in_progress','last_attempt_at':stamp}
    save_private(attempt,job_key=namespace)
    return attempt


def finish(job_key,attempt,status):
    if attempt is not None:save_private({**attempt,'status':status},job_key='attempts:'+job_key)
