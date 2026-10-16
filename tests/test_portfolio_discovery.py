import json,unittest
from unittest.mock import patch
from src import portfolio_discovery as p,discovery
from src.harmonic_discovery import employer_boards

class PortfolioDiscovery(unittest.TestCase):
 def test_sources_and_disabled_network(self):
  self.assertEqual(set(p.SOURCES),{'a16z','sequoia','greylock','yc','techstars'})
  with patch.object(p,'fetch_html') as fetch:self.assertEqual(p.discover({},{}),([],[]));fetch.assert_not_called()
 def test_company_profiles_exclude_social_and_job_detail_links(self):
  html='<a href="/companies/startup">Startup</a><a href="/companies/startup/jobs/1">Job</a><a href="https://linkedin.com/company/startup">Social</a>'
  self.assertEqual(p.company_profiles(html,'https://www.ycombinator.com/jobs','yc'),['https://www.ycombinator.com/companies/startup'])
  html='<a href="https://new.example/">Company</a><a href="https://linkedin.com/company/new">Social</a>'
  self.assertEqual(p.company_profiles(html,'https://www.techstars.com/portfolio','techstars'),['https://new.example'])
 def test_profile_resolves_official_website_not_news_or_social(self):
  html='<a href="https://linkedin.com/company/startup">LinkedIn</a><a href="https://news.example/article">News</a><a href="https://startup.example">startup.example</a>'
  self.assertEqual(p.profile_website(html,'https://sequoiacap.com/companies/startup'),'https://startup.example')
 def test_sequoia_public_api_discovers_board_and_uses_pagination_sequence(self):
  from unittest.mock import Mock
  responses=[{'jobs':[{'company':{'domain':'startup.example'},'jobs':[{'applyUrl':'https://jobs.ashbyhq.com/startup/abc'}]}],'meta':{'sequence':'next-page'}}, {'jobs':[],'meta':{}}]
  response=Mock();response.json.side_effect=responses
  client=Mock();client.get.return_value.text='"csrfToken":"public-session-token"';client.post.return_value=response
  with patch.object(p,'TERMS',['technical program']),patch.object(p.requests,'Session',return_value=client):
   post=client.post
   boards,candidates,complete,failures=p.sequoia_pages({'max_pages_per_query':2},{})
  self.assertEqual(boards,[{'source':'ashby','config':'startup'}]);self.assertEqual(failures,0)
  self.assertEqual(post.call_args_list[1].kwargs['json']['meta']['sequence'],'next-page')
  self.assertEqual(post.call_args.kwargs['json']['query']['titlePrefix'],'technical program')
 def test_corporate_parent_is_not_an_external_employer(self):
  self.assertFalse(p.external('https://a16z.com/careers','https://jobs.a16z.com/jobs'))
 def test_upper_level_board_page_leads_do_not_become_jobs(self):
  html='<a href="https://jobs.ashbyhq.com/startup/abc">Apply</a>'
  with patch.object(p,'fetch_html',return_value=(html,p.SOURCES['a16z']['url'])):
   boards,candidates,complete,failures=p.listing_pages('a16z',{}, {})
  self.assertEqual(boards,[{'source':'ashby','config':'startup'}]);self.assertEqual(candidates,[]);self.assertEqual(failures,0)
 def test_pagination_rotates_and_does_not_repeat_first_page_forever(self):
  calls=[]
  def fetch(url):
   calls.append(url)
   return '<a href="?search=technical+program&page=2">Next</a><a href="https://jobs.ashbyhq.com/startup">Apply</a>',url
  cache={}
  with patch.object(p,'TERMS',['technical program']),patch.object(p,'fetch_html',side_effect=fetch):
   _,_,complete,_=p.listing_pages('greylock',{'max_pages_per_query':1},cache)
   self.assertFalse(complete)
   p.listing_pages('greylock',{'max_pages_per_query':1},cache)
  self.assertIn('page=2',calls[-1]);self.assertIn('search=technical+program',calls[-1])
 def test_blocked_job_board_uses_directory_and_is_reported_partial(self):
  def fetch(url):
   if 'jobs.techstars' in url:raise ValueError('HTTP 403')
   if 'techstars.com/portfolio' in url:return '<a href="https://startup.example">Startup</a>',url
   raise AssertionError(url)
  with patch.object(p,'fetch_html',side_effect=fetch),patch.object(p,'employer_boards',return_value={'boards':[{'source':'ashby','config':'startup'}],'complete':True}):
   boards,health=p.discover({'enabled':True,'sources':['techstars']},{})
  self.assertEqual(boards,[{'source':'ashby','config':'startup'}]);self.assertEqual(health[0]['status'],'partial');self.assertEqual(health[0]['listing_failures'],1)
 def test_no_results_or_changed_markup_is_not_successful_coverage(self):
  with patch.object(p,'fetch_html',return_value=('<html>Sign in</html>','https://jobs.a16z.com/jobs')):
   boards,health=p.discover({'enabled':True,'sources':['a16z']},{})
  self.assertEqual(boards,[]);self.assertEqual(health[0]['status'],'failed')
 def test_rotation_preserves_provenance_and_keeps_application_keys(self):
  board={'source':'ashby','config':'Startup'};key=discovery.board_key(board)
  cache={'portfolio:board_origins':{key:{'a16z':'https://jobs.a16z.com/jobs','yc':'https://www.ycombinator.com/jobs'}}}
  job={'source':'ashby','company':'startup','id':'original','discovery_sources':['harmonic']}
  p.tag_jobs([job],cache);p.tag_jobs([job],cache)
  self.assertEqual(job['id'],'original');self.assertEqual(job['source'],'ashby');self.assertEqual(job['discovery_sources'],['harmonic','a16z','yc'])
 def test_greenhouse_embed_is_a_real_board_not_embed_tenant(self):
  self.assertEqual(discovery.board_from_url('https://boards.greenhouse.io/embed/job_board?for=startup'),{'source':'greenhouse','config':'startup'})
  self.assertIsNone(discovery.board_from_url('https://boards.greenhouse.io/embed/job_board'))
 def test_employer_redirect_to_ats_learns_board(self):
  with patch('src.harmonic_discovery.fetch_html',return_value=('<html>Enable Javascript</html>','https://jobs.ashbyhq.com/startup')):
   self.assertEqual(employer_boards({'url':'https://startup.example/careers'})['boards'],[{'source':'ashby','config':'startup'}])
 def test_one_changed_source_does_not_stop_other_sources(self):
  def pages(source,*args):
   if source=='a16z':raise AttributeError('Changed external schema')
   return [{'source':'ashby','config':'startup'}],[],True,0
  with patch.object(p,'listing_pages',side_effect=pages):
   boards,health=p.discover({'enabled':True,'sources':['a16z','greylock']},{})
  self.assertEqual(boards,[{'source':'ashby','config':'startup'}]);self.assertEqual(health[0]['status'],'failed');self.assertEqual(health[1]['status'],'ok')
 def test_all_sources_integrate_without_changing_job_facts(self):
  board={'source':'ashby','config':'startup'}
  with patch.object(p,'discover',return_value=([board],[{'source':'a16z','status':'ok'}])),patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':''}):
   jobs,boards,health=discovery.discover({'enabled':True,'startup_portfolios':{'enabled':True}},{})
  self.assertEqual(jobs,[]);self.assertEqual(boards,[board]);self.assertTrue(any(h['source']=='a16z' for h in health))

if __name__=='__main__':unittest.main()
