"""Real-posting calibration. Candidate evidence stays in private PostgreSQL."""
import hashlib,json,logging,concurrent.futures,os
from datetime import datetime,timezone
from src import fit,jd_fetch,llm_client
from src.evidence_fit import assess,review_result,digest,scorer_revision,REVIEW_PROMPT,canonical,build_inventory
from src.fit_scorer import salary_text,verified_base_comp_status
from src.private_fit_store import save_private,load_private

log=logging.getLogger(__name__)

def main():
    config=fit.load_json(fit.CONFIG_DIR/'fit_benchmark.json',{})
    profile=fit.load_json(fit.PROFILE_FILE,{})
    rules=fit.load_json(fit.FIT_RULES_FILE,{})
    rules={**rules,'evidence_required':True,'fit_policy_version':'evidence-v1'}
    jobs={fit._key(j):j for j in fit.load_json(fit.JOBS_FILE,[])}
    def evaluate(pair):
        index,case=pair
        client=llm_client.build_client()
        job=jobs.get(case['job_key'])
        result={'job_key':case['job_key'],'title':case['title'],'url':case['url'],'expected':case['allowed_career_fit']}
        try:
            if not job:raise ValueError('Posting is no longer in the current snapshot')
            jd=jd_fetch.get_jd_text(job)
            current_hash=hashlib.sha256(salary_text(jd).encode()).hexdigest()
            if current_hash!=case['jd_hash']:raise ValueError('Posting changed since manual benchmark review')
            cached=load_private(case['job_key']) if os.environ.get('FIT_BENCHMARK_REPLAY')=='true' else None
            if cached:cached['inventory']=build_inventory(profile,rules)
            if cached and cached.get('extraction') and hashlib.sha256(cached['job_text'].encode()).hexdigest()==case['jd_hash']:
                if os.environ.get('FIT_BENCHMARK_REVIEW')=='true':
                    response=llm_client.call(client,model=rules.get('evidence_review_model') or fit._fit_model(),system=REVIEW_PROMPT+'\nPRIVATE EVIDENCE AND RULES:\n'+canonical({'inventory':cached['inventory'],'rules':rules}),user=canonical({'job_text':cached['job_text'],'job':{k:job.get(k) for k in ('title','company','location')},'proposal':cached['extraction']}),max_tokens=6000,temperature=0.0,cache_system=True)
                    text=response.text
                    cached['independent_review']=json.loads(text[text.find('{'):text.rfind('}')+1])
                verdict=review_result(cached['extraction'],cached['independent_review'],cached['inventory'],cached['job_text'],job,verified_base_comp_status(jd,'',float(rules.get('comp_floor_usd',0))),rules)
                fingerprint=digest(['replay',cached['input_hash'],scorer_revision()])
                verdict.public['input_hash']=fingerprint
                verdict.private.update(input_hash=fingerprint,engine_version='evidence-v1',job=cached['job'],job_text=cached['job_text'],replayed_from=cached['input_hash'])
            else:
                verdict=assess(profile,job,jd,client=client,model=fit._fit_model(),rules=rules)
            if verdict.recommendation=='review':
                verdict=assess(profile,job,jd,client=client,model=fit._fit_model(),rules=rules)
            save_private(verdict.private,job_key=case['job_key'])
            result.update(career_fit=verdict.public['career_fit'],recommendation=verdict.recommendation,
                input_hash=verdict.public['input_hash'],passed=verdict.public['career_fit'] in case['allowed_career_fit'],
                evidence_complete=verdict.public['evidence_complete'],direct_count=verdict.public['direct_count'],critical_count=verdict.public['critical_count'])
        except Exception as error:
            # Never publish model output, private profile text or credentials.
            result.update(passed=False,error=type(error).__name__)
        log.info('benchmark %d/%d %s %s',index+1,len(config['cases']),case['job_key'],'PASS' if result['passed'] else 'FAIL')
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(evaluate,enumerate(config['cases'])))
    report={'scorer_revision':scorer_revision(),'extraction_model':fit._fit_model(),'review_model':rules.get('evidence_review_model') or fit._fit_model(),'version':config['version'],'completed_at':datetime.now(timezone.utc).isoformat(),'total':len(results),
        'passed':sum(row['passed'] for row in results),'cases':results}
    (fit.DOCS_DIR/'fit_benchmark.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1

if __name__=='__main__':raise SystemExit(main())
