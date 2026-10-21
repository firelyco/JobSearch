import copy,json,unittest
from datetime import datetime,timezone
from unittest.mock import patch
from src import compensation as c,refresh_salaries as refresh
from src.evidence_batch import snapshot_hash

JOB={'source':'workday','company':'example','id':'1','url':'https://example.wd5.myworkdayjobs.com/en-US/jobs/job/US-MA-Boston/TPM','location':'Boston, MA','title':'Principal TPM'}

class SalaryFormats(unittest.TestCase):
 def check(self,text,expected,job=JOB):self.assertEqual(c.parse_salary(text,job)['status'],expected,text)
 def test_actual_employer_formats(self):
  cases=[
   ('The base pay range for this position is between $154,000-180,000.','below_floor'),
   ('US: Hiring Range in USD from: $116,600 to $264,100 per annum.','potentially_meets_floor'),
   ('The expected base pay is listed below. Zone 1 Pay Range $223,800 — $307,650 USD','potentially_meets_floor'),
   ('At Apple, base pay is one part of our total compensation package. The base pay range for this role is between $144,600 and $263,800.','potentially_meets_floor'),
   ('The base salary range is listed below. '+('Benefits are additional. '*25)+'USA, NY, New York - 163,600.00 - 221,300.00 USD annually','below_floor'),
   ('Annual USD base salary: $200,000 - $250,000','potentially_meets_floor'),
   ('Annual USD base salary: $250,000 - $320,000','potentially_meets_floor'),
   ('Annual USD base salary: $251,000 - $320,000','meets_floor'),
   ('Annual base salary range is 272,000 USD - 425,500 USD.','meets_floor')]
  for text,expected in cases:
   with self.subTest(text=text):self.check(text,expected)
 def test_google_separate_us_salary_line(self):
  self.check('Individual pay depends on experience. US: $192000 - $278000 (USD) + 20% bonus target + equity.', 'potentially_meets_floor',{**JOB,'source':'google'})
 def test_total_comp_and_other_currencies_never_become_base(self):
  for text in ['Total compensation: $300,000 - $500,000 USD','Total pay range $300,000 - $500,000 USD',
   'Annual Salary: $300,000 - $500,000 USD OTE including commissions',
   'Compensation for engineers ranges from $100k - $500k including base and variable compensation targets.',
   'Annual CAD base salary $300,000 - $500,000','Monthly USD base salary: $260,000 - $300,000']:
   with self.subTest(text=text):self.check(text,'unknown')
 def test_usd_is_not_assumed_for_ambiguous_foreign_or_remote_job(self):
  for loc in ['Remote','Toronto, Canada','San Francisco; Toronto, Canada','']:
   self.check('Base salary: $200,000 - $300,000','unknown',{'location':loc})
 def test_unrelated_budget_and_bonus_cannot_inflate_base(self):
  self.check('Annual USD base salary: $200,000. Bonus: $500,000.','below_floor')
  self.check('Annual USD base salary: $200,000. '+('About this company. '*15)+'Budget: $1,000,000.','below_floor')
 def test_lower_bound_without_upper_bound_does_not_exclude(self):
  self.check('Annual USD base salary starting at $200,000','unknown')
  self.assertEqual(c.structured_salary({'currency':'USD','value':{'unitText':'YEAR','minValue':200000}}),'')
 def test_ote_without_commission_words_is_not_base(self):
  self.check('Annual Salary: $300,000 - $500,000 USD OTE','unknown')
 def test_typed_metadata_excludes_hourly_foreign_and_equity(self):
  self.assertEqual(c.structured_salary({'currency':'CAD','value':{'unitText':'YEAR','minValue':200000,'maxValue':300000}}),'')
  self.assertEqual(c.structured_salary({'currency':'USD','value':{'unitText':'HOUR','minValue':100,'maxValue':200}}),'')
  p={'compensation':{'summaryComponents':[{'compensationType':'Salary','currencyCode':'USD','interval':'1 YEAR','minValue':200000,'maxValue':275000},{'compensationType':'Bonus','currencyCode':'USD','interval':'1 YEAR','minValue':100000,'maxValue':1000000}]}}
  self.check(c.ashby_salary(p),'potentially_meets_floor')
  self.assertNotIn('1,000,000',c.ashby_salary(p))
 def test_jsonld_does_not_borrow_related_job_pay(self):
  nodes=[{'@type':'JobPosting','url':JOB['url'],'baseSalary':{'currency':'USD','value':{'unitText':'YEAR','minValue':150000,'maxValue':180000}}},
         {'@type':'JobPosting','url':'https://example.com/another','baseSalary':{'currency':'USD','value':{'unitText':'YEAR','minValue':300000,'maxValue':500000}}}]
  page='<script type="application/ld+json">'+json.dumps(nodes)+'</script>'
  self.check(c.page_salary_metadata(page,JOB),'below_floor')
  self.assertEqual(c.page_salary_metadata(page,{'url':'https://example.com/unknown'}),'')

class SalaryRefresh(unittest.TestCase):
 def score(self):return {'recommendation':'strong','career_fit':'strong','validated':True,'location_status':'eligible','comp_status':'unknown','input_hash':'private-original','context_hash':'ctx','scorer_revision':'rev','job_snapshot_hash':snapshot_hash(JOB)}
 def test_below_floor_excludes_without_changing_career_assessment_or_hash(self):
  score=self.score();check=c.parse_salary('Base pay $154,000-$180,000',JOB)
  after=refresh.apply_check(score,check)
  self.assertEqual(after['recommendation'],'medium');self.assertEqual(after['career_fit'],'strong')
  self.assertEqual(after['input_hash'],score['input_hash']);self.assertEqual(score['recommendation'],'strong')
 def test_overlapping_range_and_missing_pay_remain_eligible(self):
  for text in ['Annual USD base salary $212,600-$319,800','No pay information in this capture']:
   after=refresh.apply_check(self.score(),c.parse_salary(text,JOB))
   self.assertEqual(after['recommendation'],'strong')
 def test_skill_conditional_location_is_preserved_by_salary_refresh(self):
  score={**self.score(),'fitment_skill_hash':'current-skill','location_status':'conditional'}
  for text in ['Annual USD base salary $230,400-$263,000','No published pay']:
   after=refresh.apply_check(score,c.parse_salary(text,JOB))
   self.assertEqual(after['recommendation'],'strong');self.assertTrue(after['eligible'])
  self.assertEqual(refresh.apply_check(score,c.parse_salary('Annual USD base salary $200,000-$240,000',JOB))['recommendation'],'medium')
  for location in ['unknown','ineligible']:
   self.assertEqual(refresh.apply_check({**score,'location_status':location},c.parse_salary('No published pay',JOB))['recommendation'],'medium')
 def test_source_failure_keeps_previous_published_evidence(self):
  score={**self.score(),'comp_status':'potentially_meets_floor'}
  after=refresh.apply_check(score,{'status':'unknown','verification':'source_unavailable'})
  self.assertEqual(after['comp_status'],'potentially_meets_floor');self.assertIn('salary_check_attempt',after)
 def test_no_ai_calls_needed_to_refresh(self):
  with patch('src.llm_client.build_client',side_effect=AssertionError('No AI allowed')):
   scores,stats=refresh.run([JOB],{'workday:example:1':self.score()},{'context_hash':'ctx','scorer_revision':'rev'},fetcher=lambda j:c.parse_salary('Base pay $154,000-$180,000',j))
  self.assertEqual(stats['below_floor'],1);self.assertEqual(scores['workday:example:1']['career_fit'],'strong')
 def test_parser_version_and_job_snapshot_control_independent_cache(self):
  now=datetime.now(timezone.utc);score=self.score();score['salary_check']={'parser_version':c.VERSION,'checked_at':now.isoformat(),'job_snapshot_hash':snapshot_hash(JOB)}
  self.assertFalse(refresh.needs_check(JOB,score,now))
  self.assertTrue(refresh.needs_check({**JOB,'url':'https://example.com/new'},score,now))

if __name__=='__main__':unittest.main()
