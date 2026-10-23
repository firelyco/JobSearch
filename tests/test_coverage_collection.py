import unittest
from unittest.mock import Mock, patch
from src.adapters import github,microsoft,workday
from src.source_health import fetch_with_health
from src.coverage_audit import audit

def response(data):
    r=Mock(status_code=200);r.json.return_value=data;return r

class CoverageCollectionTests(unittest.TestCase):
    def test_workday_fetches_beyond_old_200_limit(self):
        def page(*args,**kwargs):
            offset=kwargs['json']['offset']
            return response({'total':240,'jobPostings':[{'externalPath':f'/job/x/{i}','title':'Principal Technical Program Manager'} for i in range(offset,min(offset+20,240))]})
        with patch.object(workday.requests,'post',side_effect=page) as call:
            jobs,health=fetch_with_health('workday','example',workday.fetch,{'tenant':'example','site':'jobs'})
        self.assertEqual(len(jobs),240);self.assertEqual(call.call_count,12)
        self.assertTrue(health['enumeration_complete']);self.assertEqual(health['status'],'ok')

    def test_workday_repeating_page_is_incomplete_not_success(self):
        page=response({'total':60,'jobPostings':[{'externalPath':'/job/x/1','title':'TPM'}]})
        with patch.object(workday.requests,'post',return_value=page) as call:
            jobs,health=fetch_with_health('workday','x',workday.fetch,{'tenant':'x','site':'s'})
        self.assertEqual(len(jobs),1);self.assertEqual(call.call_count,2)
        self.assertEqual(health['status'],'partial');self.assertFalse(health['enumeration_complete'])

    def test_workday_200_total_is_complete_not_limit_reached(self):
        def page(*args,**kwargs):
            offset=kwargs['json']['offset']
            self.assertEqual(kwargs['json']['appliedFacets'],{'jobFamily':['tpm']})
            return response({'total':200,'jobPostings':[{'externalPath':f'/job/x/{i}','title':'TPM'} for i in range(offset,offset+20)]})
        with patch.object(workday.requests,'post',side_effect=page):
            jobs,health=fetch_with_health('workday','x',workday.fetch,{'tenant':'x','site':'s','applied_facets':{'jobFamily':['tpm']}})
        self.assertEqual(len(jobs),200);self.assertEqual(health['status'],'ok')
        self.assertTrue(health['enumeration_complete'])

    def test_multilocation_keeps_all_cities(self):
        self.assertEqual(workday.location_from_info({'location':'Atlanta, GA','additionalLocations':['Seattle, WA',{'descriptor':'San Francisco, CA'}]}),'Atlanta, GA | Seattle, WA | San Francisco, CA')

    def test_github_missing_director_is_imported_with_real_posted_date(self):
        page=response({'totalCount':1,'jobs':[{'data':{'slug':'5625','title':'Director, Technical Program Management','location_name':'US Remote','posted_date':'2026-07-27','description':'Required qualifications'}}]})
        with patch.object(github.requests,'get',return_value=page):jobs=github.fetch()
        self.assertEqual(jobs[0]['id'],'5625');self.assertEqual(jobs[0]['posted_at'],'2026-07-27')
        self.assertTrue(jobs.coverage['enumeration_complete'])

    def test_microsoft_paginates_and_never_reports_failed_page_complete(self):
        pages=[response({'status':200,'data':{'count':2,'positions':[{'id':1,'name':'Principal TPM','locations':['Redmond, WA']}]}}),response({'status':200,'data':{'count':2,'positions':[{'id':2,'name':'Principal TPM','locations':['Redmond, WA']}]}})]
        with patch.object(microsoft.requests,'get',side_effect=pages) as call, patch.object(microsoft.time,'sleep'):jobs=microsoft.fetch('technical program manager')
        self.assertEqual(len(jobs),2);self.assertTrue(jobs.coverage['enumeration_complete'])
        self.assertIn('start=1',call.call_args[0][0]);self.assertIn('technical%20program',call.call_args[0][0])

    def test_watchlist_distinguishes_exact_from_related_and_missing(self):
        job={'source':'github','company':'github','id':'5625','title':'Director Technical Program Management','location':'US Remote','posted_at':'2026-07-27','url':'https://www.github.careers/jobs/5625'}
        watch=[{'label':'exact','company':'github','id':'5625'}, {'label':'similar','company':'github','title_pattern':'Director','location_pattern':'Remote'}, {'label':'missing','company':'microsoft','id':'absent'}]
        result=audit([job],[job],watch,{},[])
        self.assertEqual([v['status'] for v in result['watchlist']],['collected','candidate_match_needs_exact_link','discovery_gap'])
        self.assertFalse(result['watchlist'][0]['matches'][0]['assessment_date_eligible'])
        self.assertFalse(result['internet_coverage_verified'])

if __name__=='__main__':unittest.main()


class TitleCoverageTests(unittest.TestCase):
    def test_international_director_is_not_an_intern(self):
        import yaml
        from pathlib import Path
        from src.scorer import score_job
        role=yaml.safe_load((Path(__file__).parents[1]/'config/role_config.yml').read_text(encoding='utf-8'))
        for title in ['Senior Director, Technical Program Management - International','Associate Director, Technical Program Management','Technical Program Manager, Platform']:
            self.assertGreater(score_job({'title':title,'company':'unknown-startup','location':'US Remote'},role)[0],0,title)
        for title in ['Technical Program Manager Intern','Associate Technical Program Manager','Junior Technical Program Manager']:
            self.assertEqual(score_job({'title':title,'company':'unknown-startup','location':'US Remote'},role)[0],0,title)


class PublicFeedRetryTests(unittest.TestCase):
    def test_transient_non_json_page_retries_without_losing_records(self):
        from src.adapters.public_json import get_json
        bad=response({});bad.json.side_effect=ValueError('temporary HTML response')
        with patch('src.adapters.public_json.requests.get',side_effect=[bad,response({'jobs':[]})]) as call, patch('src.adapters.public_json.time.sleep'):
            self.assertEqual(get_json('https://www.github.careers/api/jobs'),{'jobs':[]})
        self.assertEqual(call.call_count,2)

    def test_search_uses_next_page_and_marks_index_limit(self):
        import os
        from src import discovery
        pages=[{'web':{'results':[{'url':'https://jobs.ashbyhq.com/new/1'}]},'query':{'more_results_available':True}},
               {'web':{'results':[{'url':'https://jobs.lever.co/another/1'}]},'query':{'more_results_available':False}}]
        with patch.dict(os.environ,{'BRAVE_SEARCH_API_KEY':'test'}),patch.object(discovery,'get_json',side_effect=pages) as call:
            jobs,boards,health=discovery.discover({'enabled':True,'queries':['technical program'],'search_pages_per_query':3},{})
        self.assertEqual(len(boards),2);self.assertEqual(call.call_count,2)
        self.assertEqual(call.call_args.kwargs['params']['offset'],1)
        self.assertEqual(health[0]['pages'],2)

    def test_retained_job_is_not_reported_as_freshly_verified(self):
        job={'source':'github','company':'github','id':'5625','title':'Director Technical Program Management','open_status':'unverified_source_failure'}
        result=audit([job],[job],[{'label':'GitHub','company':'github','id':'5625'}],{},[])
        self.assertEqual(result['watchlist'][0]['status'],'retained_source_unverified')


class ObservedPostingRegressionTests(unittest.TestCase):
    def role(self):
        import yaml
        from pathlib import Path
        return yaml.safe_load((Path(__file__).parents[1]/'config/role_config.yml').read_text(encoding='utf-8'))

    def test_every_screenshot_title_survives_intake_at_an_unconfigured_employer(self):
        from src.scorer import score_job
        titles=['Technical Program Manager Lead, Google Search Observability and Reliability',
                'Staff Technical Program Manager','Director, Technical Program Management',
                'Staff Technical Program Manager, Growth','Principal Technical Program Manager',
                'Technical Program Manager, ML NPI System SW','Technical Program Manager, Strategic Initiatives',
                'Distinguished, Technical Program Manager','Director, Global B2B TPM',
                '(USA) Senior Director, Technical Program Management']
        for title in titles:
            job={'company':'brand-new-employer','title':title,'location':'Seattle, WA, USA'}
            self.assertGreater(score_job(job,self.role())[0],0,title)

    def test_foreign_office_does_not_hide_us_location_on_same_posting(self):
        from src.scorer import score_job
        base={'company':'new-employer','title':'Staff Technical Program Manager'}
        self.assertGreater(score_job({**base,'location':'Seattle, WA, USA | Bengaluru, India'},self.role())[0],0)
        self.assertEqual(score_job({**base,'location':'Remote - Bengaluru, India'},self.role())[0],0)

    def test_exact_posting_dropped_by_screening_is_not_reported_as_collected(self):
        job={'company':'example','source':'workday','id':'123','title':'Director Technical Program Management'}
        result=audit([job],[],[{'label':'example','company':'example','id':'123'}],self.role(),[])
        self.assertEqual(result['watchlist'][0]['status'],'screened_out')

    def test_location_resolution_makes_growth_posting_eligible_for_us_queue(self):
        from src.fit_backfill import in_scope
        job={'source':'workday','company':'salesforce','id':'growth','title':'Staff Technical Program Manager, Growth',
             'location':workday.location_from_info({'location':'Georgia - Atlanta','additionalLocations':['New York - New York','Washington - Seattle','California - San Francisco']})}
        self.assertTrue(in_scope(job,self.role()))


class PollFailureAndLocationRegressionTests(unittest.TestCase):
    def test_failed_source_keeps_known_job_and_growth_locations_reach_output(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from src import poll,discovery
        repository=Path(__file__).parents[1]
        old={'source':'github','company':'github','id':'5625','title':'Director, Technical Program Management',
             'url':'https://www.github.careers/experienced-professionals/jobs/5625','location':'US Remote','posted_at':'2026-07-27'}
        growth={'source':'workday','company':'salesforce','id':'/job/Georgia---Atlanta/Growth_JR365306-1',
                'title':'Staff Technical Program Manager, Growth','location':'6 Locations',
                'url':'https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site/job/Georgia---Atlanta/Growth_JR365306-1',
                'posted_at':'2026-10-22'}
        stats={'github':{'ok':0,'fail':1,'jobs':0,'sources':[{'source':'github','label':'all','status':'failed','jobs':0}]}}
        with TemporaryDirectory() as folder:
            base=Path(folder)
            for name in ('config','docs','state'):(base/name).mkdir()
            for name in ('companies.yml','role_config.yml','discovery.yml','coverage_watchlist.json'):
                (base/'config'/name).write_bytes((repository/'config'/name).read_bytes())
            (base/'docs/jobs.json').write_text(json.dumps([old]),encoding='utf-8')
            with patch.multiple(poll,CONFIG_DIR=base/'config',DOCS_DIR=base/'docs',STATE_DIR=base/'state',STATE_FILE=base/'state/seen_jobs.json',JOBS_FILE=base/'docs/jobs.json',META_FILE=base/'docs/meta.json'),patch.object(poll,'fetch_all',return_value=([growth],stats)),patch.object(discovery,'discover',return_value=([],[],[])),patch.object(poll.workday,'fetch_info',return_value={'location':'Georgia - Atlanta','additionalLocations':['Washington - Seattle','New York - New York','California - San Francisco']}):
                self.assertEqual(poll.main(),0)
            output=json.loads((base/'docs/jobs.json').read_text(encoding='utf-8'))
            saved={job['company']:job for job in output}
            self.assertIn('github',saved)
            self.assertEqual(saved['github']['open_status'],'unverified_source_failure')
            for city in ('Seattle','New York','San Francisco'):self.assertIn(city,saved['salesforce']['location'])
            report=json.loads((base/'docs/coverage_audit.json').read_text(encoding='utf-8'))
            github=next(row for row in report['watchlist'] if row['label']=='GitHub Director TPM')
            self.assertEqual(github['status'],'retained_source_unverified')
            for label in ('Salesforce Growth, SF','Salesforce Growth, Seattle','Slack Growth, Seattle','Slack Growth, NYC'):
                row=next(r for r in report['watchlist'] if r['label']==label)
                self.assertTrue(row['matches']);self.assertTrue(row['matches'][0]['published'])
