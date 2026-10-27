import copy,json,unittest,yaml
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from src import poll,discovery,scorer,fit_backfill
from src.role_intake import pmo_leadership
from src.coverage_audit import audit

ROOT=Path(__file__).resolve().parents[1]
CONFIG=yaml.safe_load((ROOT/'config/role_config.yml').read_text(encoding='utf-8'))

class PmoIntakeTests(unittest.TestCase):
    def test_general_employers_and_title_variants_reach_assessment(self):
        for title in ['Vice President, Enterprise PMO','Vice President of Product and Technology PMO','Director, PMO','PMO Manager','Head of Program Management Office','Director of Project Management Office','VP Portfolio Management Office']:
            j={'title':title,'company':'unconfigured-employer','location':'USA - Remote'}
            self.assertTrue(pmo_leadership(title),title)
            score,reasons=scorer.score_job(j,CONFIG)
            self.assertGreater(score,0,title)
            self.assertTrue(fit_backfill.in_scope(j,CONFIG),title)
            self.assertNotIn('recommendation',j)
    def test_junior_and_nonleadership_pmo_not_promoted(self):
        for title in ['PMO Analyst','PMO Intern','Junior PMO Manager','PMO Internship','Program Manager, Global Sales Organization, PMO']:
            j={'title':title,'company':'example','location':'USA - Remote'}
            if title.startswith('Program Manager'):
                # Retain for the full discipline review; never assign a verdict.
                self.assertNotIn('recommendation',j)
            else:
                self.assertEqual(scorer.score_job(j,CONFIG)[0],0,title)
    def test_workiva_roles_survive_full_poll_and_appear_in_coverage(self):
        jobs=json.loads((ROOT/'tests/fixtures/workiva_pmo_jobs.json').read_text(encoding='utf-8'))
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ['config','docs','state']:(root/name).mkdir()
            for name in ['companies.yml','role_config.yml','discovery.yml','coverage_watchlist.json']:
                (root/'config'/name).write_bytes((ROOT/'config'/name).read_bytes())
            with patch.multiple(poll,CONFIG_DIR=root/'config',DOCS_DIR=root/'docs',STATE_DIR=root/'state',STATE_FILE=root/'state/seen_jobs.json',JOBS_FILE=root/'docs/jobs.json',META_FILE=root/'docs/meta.json'),patch.object(poll,'fetch_all',return_value=([],{})),patch.object(discovery,'discover',return_value=(copy.deepcopy(jobs),[],[])),patch.object(poll.jd_fetch,'get_jd_text',side_effect=AssertionError('Intake must not require a JD or AI call')):
                self.assertEqual(poll.main(),0)
            published=json.loads((root/'docs/jobs.json').read_text(encoding='utf-8'))
            self.assertEqual({j['id'] for j in jobs},{j['id'] for j in published})
            self.assertTrue(all(fit_backfill.in_scope(j,CONFIG) for j in published))
            report=json.loads((root/'docs/coverage_audit.json').read_text(encoding='utf-8'))
            statuses=[w['status'] for w in report['watchlist'] if w['label'].startswith('Workiva ')]
            self.assertEqual(statuses,['collected','collected'])
    def test_rejected_pmo_is_visible_in_coverage_audit(self):
        j={'source':'example','company':'example','id':'1','title':'PMO Analyst'}
        result=audit([j],[],[],CONFIG,[])
        self.assertEqual(len(result['screened_out_role_candidates']),1)
