import unittest
from unittest.mock import patch
import requests
from src import harmonic_discovery as h, discovery

class HarmonicDiscovery(unittest.TestCase):
 def test_latest_public_quarter_rolls_forward_and_rejects_external_lookalike(self):
  html='<a href="/hot-25-startups/q4-2026">old</a><a href="/hot-25-startups/q1-2027">new</a><a href="https://evil.example/hot-25-startups/q4-2099">fake</a>'
  self.assertEqual(h.latest_list(html),'https://harmonic.ai/hot-25-startups/q1-2027')
  with self.assertRaises(ValueError):h.latest_list('<h1>Sign in</h1>')
 def test_company_links_are_leads_not_postings_and_ignore_founders_and_duplicates(self):
  html='<a href="https://startup.example">1 New Startup &#8599;</a><a href="https://startup.example">New Startup &#8599;</a><a href="http://second.example">Second &#8599;</a><a href="https://linkedin.com/founder">Founder &#8599;</a><a href="https://harmonic.ai/careers">Careers &#8599;</a>'
  leads=h.company_leads(html,'https://harmonic.ai/hot-25-startups/q4-2026')
  self.assertEqual([x['name'] for x in leads],['New Startup','Second'])
  self.assertEqual(leads[1]['url'],'https://second.example')
  self.assertNotIn('title',leads[0])
 def test_follow_employer_careers_and_learn_verified_ats_board(self):
  pages={'https://startup.example':('<a href="/careers">Join our team</a><a href="https://unrelated.example/careers">Other</a>','https://startup.example'),
   'https://startup.example/careers':('<a href="https://jobs.ashbyhq.com/new-startup/abc">TPM</a>','https://startup.example/careers')}
  with patch.object(h,'fetch_html',side_effect=lambda url:pages[url]) as fetch:
   result=h.employer_boards({'url':'https://startup.example'})
  self.assertTrue(result['complete']);self.assertEqual(result['boards'],[{'source':'ashby','config':'new-startup'}]);self.assertEqual(fetch.call_count,2)
 def test_partial_coverage_is_visible_and_cached_without_deleting_known_boards(self):
  company={'name':'New','url':'https://startup.example','list_url':'https://harmonic.ai/hot-25-startups/q4-2026'}
  cache={};cfg={'enabled':True,'minimum_companies':1}
  with patch.object(h,'fetch_html',return_value=('<a href="/hot-25-startups/q4-2026">list</a>','https://harmonic.ai/')),patch.object(h,'company_leads',return_value=[company]),patch.object(h,'employer_boards',return_value={'boards':[],'complete':False}) as employer:
   for _ in range(2):
    boards,health=h.discover(cfg,cache);self.assertEqual(boards,[]);self.assertEqual(health[1]['status'],'partial')
   self.assertEqual(employer.call_count,1)
 def test_markup_change_or_fetch_failure_is_not_reported_as_empty_success(self):
  for result in ['<html>login</html>',None]:
   with patch.object(h,'fetch_html',side_effect=requests.Timeout() if result is None else None,return_value=(result,'https://harmonic.ai/')):
    boards,health=h.discover({'enabled':True},{})
   self.assertEqual(boards,[]);self.assertEqual(health[0]['status'],'failed')
 def test_integrates_without_api_key_and_disabled_source_does_no_network(self):
  with patch.object(h,'fetch_html') as fetch:self.assertEqual(h.discover({},{}),([],[]));fetch.assert_not_called()
  board={'source':'ashby','config':'new-startup'}
  with patch.object(h,'discover',return_value=([board],[{'source':'harmonic','status':'ok','leads':1}])),patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':''}):
   jobs,boards,health=discovery.discover({'enabled':True,'harmonic':{'enabled':True}},{})
  self.assertEqual(jobs,[]);self.assertEqual(boards,[board]);self.assertTrue(any(x['source']=='harmonic' for x in health))
 def test_script_embedded_board_is_found_without_running_javascript(self):
  with patch.object(h,'fetch_html',return_value=('<script src="https://jobs.ashbyhq.com/emerald-ai/embed"></script>','https://emeraldai.co/careers')):
   result=h.employer_boards({'url':'https://emeraldai.co/careers'})
  self.assertEqual(result['boards'],[{'source':'ashby','config':'emerald-ai'}])
 def test_provenance_preserves_company_and_application_identity(self):
  job={'source':'ashby','company':'emerald-ai','id':'abc'}
  cache={'harmonic:board_origins':{discovery.board_key({'source':'ashby','config':'emerald-ai'}):{'list_url':'https://harmonic.ai/hot-25-startups/q4-2026'}}}
  h.tag_jobs([job],cache);h.tag_jobs([job],cache)
  self.assertEqual((job['source'],job['company'],job['id']),('ashby','emerald-ai','abc'))
  self.assertEqual(job['discovery_sources'],['harmonic'])
 def test_non_public_urls_and_http_credentials_are_rejected(self):
  for url in ['https://127.0.0.1','https://test.local/','https://name:secret@jobs.example/path','file:///test']:
   self.assertEqual(h.public_url(url),'')

class HarmonicPollPriority(unittest.TestCase):
 def test_new_board_is_polled_before_rotation_and_cached_old_jobs_survive(self):
  import json
  from tempfile import TemporaryDirectory
  from pathlib import Path
  from src import poll
  root=Path(__file__).resolve().parents[1]
  old={'source':'ashby','config':'old'};new={'source':'ashby','config':'new'}
  def job(company):return {'source':'ashby','company':company,'id':'1','title':'Director Technical Program Management','location':'Boston, MA','url':'https://jobs.ashbyhq.com/'+company+'/1','posted_at':'2026-10-01','updated_at':''}
  health=lambda board:{'source':'ashby','label':discovery.board_key(board),'status':'ok','jobs':1}
  with TemporaryDirectory() as directory:
   work=Path(directory)
   for name in ['config','docs','state']:(work/name).mkdir()
   (work/'config/companies.yml').write_text('{}')
   (work/'config/role_config.yml').write_bytes((root/'config/role_config.yml').read_bytes())
   (work/'config/discovery.yml').write_text('enabled: true\nmax_discovered_boards_per_poll: 1\n')
   (work/'state/discovered_boards.json').write_text(json.dumps([old]))
   (work/'state/discovery_cache.json').write_text(json.dumps({'board:'+discovery.board_key(old):{'jobs':[job('old')],'health':health(old),'checked_at':'2026-10-16T00:00:00+00:00'}}))
   with patch.multiple(poll,CONFIG_DIR=work/'config',DOCS_DIR=work/'docs',STATE_DIR=work/'state',STATE_FILE=work/'state/seen_jobs.json',JOBS_FILE=work/'docs/jobs.json',META_FILE=work/'docs/meta.json'),patch.object(poll,'fetch_all',return_value=([],{})),patch.object(discovery,'discover',return_value=([],[new],[])),patch.object(poll,'fetch_with_health',return_value=([job('new')],health(new))) as fetch:
    self.assertEqual(poll.main(),0)
   self.assertEqual(fetch.call_args.args[3],'new')
   self.assertEqual({j['company'] for j in json.loads((work/'docs/jobs.json').read_text())},{'old','new'})

if __name__=='__main__':unittest.main()
