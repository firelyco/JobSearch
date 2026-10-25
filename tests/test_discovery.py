import json
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import yaml
from src import discovery, scorer, poll, explain_missing
from src.source_health import fetch_with_health

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

class SnapshotRegression(unittest.TestCase):
    def test_full_poll_preserves_snapshot_while_adding_unconfigured_employer(self):
        baseline = json.loads((FIXTURES / "baseline_jobs.json").read_text(encoding="utf-8"))
        extra = {"source": "ashby", "company": "new-startup", "id": "new", "url": "https://jobs.ashbyhq.com/new-startup/new", "title": "Director Technical Program Management", "location": "US Remote", "posted_at": "2024-01-01", "updated_at": ""}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("config", "docs", "state"): (root / name).mkdir()
            for name in ("companies.yml", "role_config.yml", "discovery.yml"):
                (root / "config" / name).write_bytes((ROOT / "config" / name).read_bytes())
            (root / "docs/jobs.json").write_text(json.dumps(baseline))
            stats = {"ashby": {"ok": 1, "fail": 0, "jobs": len(baseline)}}
            with patch.multiple(poll, CONFIG_DIR=root / "config", DOCS_DIR=root / "docs", STATE_DIR=root / "state", STATE_FILE=root / "state/seen_jobs.json", JOBS_FILE=root / "docs/jobs.json", META_FILE=root / "docs/meta.json"), patch.object(poll, "fetch_all", return_value=(baseline, stats)), patch.object(discovery, "discover", return_value=([extra], [], [])), patch.object(poll.workday,"fetch_info",return_value={}):
                self.assertEqual(poll.main(), 0)
            output = json.loads((root / "docs/jobs.json").read_text())
            keys = lambda records: {f"{j['source']}:{j['company']}:{j['id']}" for j in records}
            self.assertEqual(keys(output), keys(baseline + [extra]))
            # An old but still listed job survives; publication age is not closure.
            self.assertTrue(any(j["id"] == "new" for j in output))

    def test_existing_company_tasks_are_identical(self):
        old = yaml.safe_load((FIXTURES / "baseline_companies.yml").read_text(encoding="utf-8"))
        new = yaml.safe_load((ROOT / "config" / "companies.yml").read_text(encoding="utf-8"))
        before = [(s, label, arg) for s, label, _, arg in poll.build_fetch_tasks(old)]
        after = [(s, label, arg) for s, label, _, arg in poll.build_fetch_tasks(new)]
        def identity(task):
            source,label,arg=task
            if source=="workday" and label=="walmart":return source,label
            return source,label,json.dumps(arg,sort_keys=True)
        self.assertTrue({identity(t) for t in before}.issubset({identity(t) for t in after}))

    def test_all_136_existing_jobs_keep_scores_reasons_and_keys(self):
        jobs = json.loads((FIXTURES / "baseline_jobs.json").read_text(encoding="utf-8"))
        expected = json.loads((FIXTURES / "baseline_scores.json").read_text(encoding="utf-8"))
        config = yaml.safe_load((ROOT / "config" / "role_config.yml").read_text(encoding="utf-8"))
        self.assertEqual(len(jobs), 136)
        for job in jobs:
            key = f"{job['source']}:{job['company']}:{job['id']}"
            actual = list(scorer.score_job(job, config))
            from src.remote_jobs import is_remote
            import re
            explicit_us = scorer._is_us_or_remote(re.sub(r'\bremote\b', '', job.get('location') or '', flags=re.I))
            if is_remote(job) and not explicit_us:
                # Intentional policy change: remote is no longer proof of US
                # eligibility. Preserve collection, remove the misleading bonus.
                self.assertIn('remote opportunity; US work eligibility unverified', actual[1])
                self.assertFalse(any('remote/US eligible' in r for r in actual[1]))
            else:
                self.assertEqual(actual, expected[key], key)
            self.assertGreater(scorer.score_job(job, config)[0], 0)
        self.assertEqual(discovery.merge_jobs(jobs, []), jobs)
        self.assertEqual(config["max_posted_age_days"], 0)

class DiscoveryTests(unittest.TestCase):
    def test_missing_posting_explains_published_vs_undiscovered(self):
        jobs = json.loads((FIXTURES / "baseline_jobs.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            root = Path(directory); (root / "docs").mkdir(); (root / "config").mkdir()
            (root / "docs/jobs.json").write_text(json.dumps(jobs))
            (root / "config/role_config.yml").write_bytes((ROOT / "config/role_config.yml").read_bytes())
            with patch.object(explain_missing, "ROOT", root):
                self.assertEqual(explain_missing.explain(jobs[0]["url"])["stage"], "published")
                self.assertEqual(explain_missing.explain("https://jobs.ashbyhq.com/new-startup/new")["stage"], "not_discovered")
                result = explain_missing.explain("https://jobs.ashbyhq.com/new-startup/new", learn=True)
                self.assertTrue(result["learned"])
                self.assertEqual(json.loads((root / "state/discovered_boards.json").read_text())[0]["config"], "new-startup")
    def test_learns_new_startup_boards_without_company_config(self):
        self.assertEqual(discovery.board_from_url("https://jobs.ashbyhq.com/new-startup/abc"), {"source": "ashby", "config": "new-startup"})
        board = discovery.board_from_url("https://expedia.wd108.myworkdayjobs.com/en-US/External/job/Seattle/Director_R-123")
        self.assertEqual(board["config"]["tenant"], "expedia")
        self.assertEqual(board["config"]["site"], "External")
        self.assertIsNone(discovery.board_from_url("https://jobs.ashbyhq.com.attacker.com/evil"))

    def test_verified_employer_page_learns_apply_board_and_rejects_expired_jobs(self):
        doc = {"@type": "JobPosting", "title": "Director TPM", "hiringOrganization": {"name": "New Company"}, "validThrough": "2026-12-01", "description": "AI program leadership"}
        html = '<script type="application/ld+json">' + json.dumps({"@graph": [doc]}) + '</script><a href="https://expedia.wd108.myworkdayjobs.com/en-US/External/job/a">Apply</a>'
        jobs, boards = discovery.jobs_from_page(html, "https://careers.example.com/job/123", datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertEqual(len(jobs), 1); self.assertEqual(len(boards), 1)
        jobs, _ = discovery.jobs_from_page(html, "https://careers.example.com/job/123", datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(jobs, [])

    def test_duplicates_preserve_existing_application_identity(self):
        old = {"source": "ashby", "company": "startup", "id": "original", "url": "https://jobs.ashbyhq.com/startup/123"}
        duplicate = {**old, "source": "web_discovery", "id": "new", "url": old["url"] + "?utm_source=search"}
        self.assertEqual(discovery.merge_jobs([old], [duplicate]), [old])
        self.assertNotEqual(discovery.canonical_url("https://example.com/jobs?gh_jid=1"), discovery.canonical_url("https://example.com/jobs?gh_jid=2"))

    def test_missing_search_key_is_visible_not_a_successful_empty_search(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
            jobs, boards, health = discovery.discover({"enabled": True}, {})
        self.assertEqual(jobs, []); self.assertEqual(boards, [])
        self.assertEqual(health[0]["status"], "not_configured")

    def test_search_snippet_is_only_a_lead_not_a_job(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test-key"}), patch.object(discovery, "get_json", return_value={"web": {"results": [{"url": "https://jobs.ashbyhq.com/unconfigured/abc", "title": "Director TPM"}]}}):
            jobs, boards, health = discovery.discover({"enabled": True, "queries": ["director TPM"]}, {})
        self.assertEqual(jobs, []); self.assertEqual(boards[0]["config"], "unconfigured")

    def test_feed_failure_is_visible_and_calls_are_cached(self):
        cache = {}
        with patch.object(discovery, "remotive_jobs", return_value=[{"id": "1"}]) as fetch, patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
            cfg = {"enabled": True, "public_feeds": ["remotive"]}
            discovery.discover(cfg, cache); _, _, health = discovery.discover(cfg, cache)
            self.assertEqual(fetch.call_count, 1); self.assertEqual(health[0]["status"], "cached")
        with patch.object(discovery, "remotive_jobs", side_effect=ValueError("bad schema")):
            _, _, health = discovery.discover(cfg, {})
        self.assertEqual(health[0]["status"], "failed")

    def test_adapter_failure_is_not_reported_as_healthy_empty(self):
        def failed(_):
            logging.getLogger("src.adapters.workday").warning("HTTP 503")
            return []
        jobs, health = fetch_with_health("workday", "example", failed, {})
        self.assertEqual(jobs, []); self.assertEqual(health["status"], "failed")
        _, health = fetch_with_health("workday", "example", lambda _: [], {})
        self.assertEqual(health["status"], "empty")

    def test_adjacent_role_enters_profile_assessment_but_junior_is_excluded(self):
        config = yaml.safe_load((ROOT / "config" / "role_config.yml").read_text(encoding="utf-8"))
        role = {"title": "Director of Platform Delivery", "company": "brand-new-startup", "location": "Boston, MA", "description": "Lead cross-functional engineering programs and manage dependencies."}
        score, reasons = scorer.score_job(role, config)
        self.assertGreater(score, 0); self.assertTrue(any("needs profile assessment" in r for r in reasons))
        self.assertEqual(scorer.score_job({**role, "title": "Intern, Platform Delivery"}, config)[0], 0)
        self.assertEqual(scorer.score_job({**role, "title": "Director of Retail Sales", "description": "Sales account management"}, config)[0], 0)

if __name__ == "__main__": unittest.main()
