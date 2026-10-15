"""Real-posting calibration. Candidate evidence stays in private PostgreSQL."""
import hashlib,json,logging
from datetime import datetime,timezone
from src import fit,jd_fetch,llm_client
from src.evidence_fit import assess
from src.fit_scorer import salary_text
from src.private_fit_store import save_private

log=logging.getLogger(__name__)

def main():
    config=fit.load_json(fit.CONFIG_DIR/'fit_benchmark.json',{})
    profile=fit.load_json(fit.PROFILE_FILE,{})
    rules=fit.load_json(fit.FIT_RULES_FILE,{})
    rules={**rules,'evidence_required':True,'fit_policy_version':'evidence-v1'}
    jobs={fit._key(j):j for j in fit.load_json(fit.JOBS_FILE,[])}
    client=llm_client.build_client();results=[]
    for index,case in enumerate(config['cases']):
        job=jobs.get(case['job_key'])
        result={'job_key':case['job_key'],'title':case['title'],'url':case['url'],'expected':case['allowed_career_fit']}
        try:
            if not job:raise ValueError('Posting is no longer in the current snapshot')
            jd=jd_fetch.get_jd_text(job)
            current_hash=hashlib.sha256(salary_text(jd).encode()).hexdigest()
            if current_hash!=case['jd_hash']:raise ValueError('Posting changed since manual benchmark review')
            verdict=assess(profile,job,jd,client=client,model=fit._fit_model(),rules=rules)
            save_private(verdict.private,job_key=case['job_key'])
            result.update(career_fit=verdict.public['career_fit'],recommendation=verdict.recommendation,
                input_hash=verdict.public['input_hash'],passed=verdict.public['career_fit'] in case['allowed_career_fit'],
                evidence_complete=verdict.public['evidence_complete'],direct_count=verdict.public['direct_count'],critical_count=verdict.public['critical_count'])
        except Exception as error:
            # Never publish model output, private profile text or credentials.
            result.update(passed=False,error=type(error).__name__)
        results.append(result)
        log.info('benchmark %d/%d %s %s',index+1,len(config['cases']),case['job_key'],'PASS' if result['passed'] else 'FAIL')
    report={'version':config['version'],'completed_at':datetime.now(timezone.utc).isoformat(),'total':len(results),
        'passed':sum(row['passed'] for row in results),'cases':results}
    (fit.DOCS_DIR/'fit_benchmark.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1

if __name__=='__main__':raise SystemExit(main())
