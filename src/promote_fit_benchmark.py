"""Publish only a fully passing, current, privately persisted calibration."""
import hashlib,json
from datetime import datetime,timezone
from src import fit,jd_fetch
from src.evidence_fit import build_inventory,input_hash,review_result,scorer_revision,ENGINE_VERSION
from src.evidence_batch import context_hash,snapshot_hash
from src.fit_scorer import salary_text,verified_base_comp_status
from src.private_fit_store import load_private

def prepare(report,config,profile,rules,jobs,existing,*,fetch=jd_fetch.get_jd_text,load=load_private):
    if not rules.get('evidence_required'):raise ValueError('Evidence policy is not enabled')
    if report.get('scorer_revision')!=scorer_revision():raise ValueError('Benchmark uses a different scorer revision')
    cases=report.get('cases',[])
    if not cases or len(cases)!=len(config['cases']) or not all(case.get('passed') is True and not case.get('error') for case in cases):
        raise ValueError('Benchmark must pass every reviewed case before publication')
    expected={case['job_key']:case for case in config['cases']}
    if set(expected)!={case['job_key'] for case in cases}:raise ValueError('Benchmark case set differs from reviewed configuration')
    current={fit._key(job):job for job in jobs};scores={key:value for key,value in existing.items() if key in current}
    inventory=build_inventory(profile,rules);model=fit._fit_model();context=context_hash(profile,rules,model)
    stamp=datetime.now(timezone.utc).isoformat()
    for case in cases:
        key=case['job_key'];job=current.get(key)
        if not job:raise ValueError('A benchmark posting is no longer available')
        jd=fetch(job)
        if hashlib.sha256(salary_text(jd).encode()).hexdigest()!=expected[key]['jd_hash']:raise ValueError('A benchmark job description changed')
        fingerprint=input_hash(profile,job,jd,rules,model);audit=load(key)
        if not audit or audit.get('input_hash')!=fingerprint or case.get('input_hash')!=fingerprint:raise ValueError('Private assessment does not match current inputs')
        if audit.get('inventory')!=inventory:raise ValueError('Private assessment uses different career evidence')
        verdict=review_result(audit.get('extraction',{}),audit.get('independent_review',{}),inventory,salary_text(jd),job,verified_base_comp_status(jd,'',rules['comp_floor_usd']),rules)
        if verdict.public['career_fit']!=case['career_fit'] or verdict.recommendation!=case['recommendation']:raise ValueError('Benchmark decision is not reproducible')
        scores[key]={**verdict.public,'input_hash':fingerprint,'recommendation':verdict.recommendation,'reason':verdict.reason,'step':verdict.step,
            'policy_version':rules['fit_policy_version'],'context_hash':context,'job_snapshot_hash':snapshot_hash(job),
            'jd_checked_at':stamp,'scored_at':stamp,'scorer_revision':scorer_revision()}
    policy={'version':rules['fit_policy_version'],'evidence_required':True,'engine_version':ENGINE_VERSION,'scorer_revision':scorer_revision(),'context_hash':context}
    return scores,policy

def main():
    from src.fitment_skill import attach
    rules=attach(fit.load_json(fit.FIT_RULES_FILE,{}))
    scores,policy=prepare(fit.load_json(fit.DOCS_DIR/'fit_benchmark.json',{}),fit.load_json(fit.CONFIG_DIR/'fit_benchmark.json',{}),
        fit.load_json(fit.PROFILE_FILE,{}),rules,fit.load_json(fit.JOBS_FILE,[]),fit.load_json(fit.FIT_FILE,{}))
    fit.save(scores)
    (fit.DOCS_DIR/'fit_policy.json').write_text(json.dumps(policy)+'\n',encoding='utf-8')
    print('Published the current private calibration assessments; other roles remain pending reassessment.')
    return 0

if __name__=='__main__':raise SystemExit(main())
