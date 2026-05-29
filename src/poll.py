"""Main poll loop.

  1. Load configs (companies, role)
  2. Concurrently poll all configured ATS sources
  3. Score each job; drop those that don't pass the threshold
  4. Dedupe against state/seen_jobs.json — tag new vs known
  5. Write docs/jobs.json (read by the dashboard) and state/seen_jobs.json

Designed to be run by GitHub Actions cron, but also runnable locally:
    cd JobSearch
    python -m src.poll
"""
from __future__ import annotations
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.adapters import greenhouse, lever, ashby, workday, amazon_jobs
from src import dedupe, scorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("poll")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs"
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "seen_jobs.json"
JOBS_FILE = DOCS_DIR / "jobs.json"
META_FILE = DOCS_DIR / "meta.json"

MAX_WORKERS = 20


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_fetch_tasks(companies: dict) -> list[tuple[str, str, callable, object]]:
    """Return a list of (source, label, fn, arg) tuples to dispatch."""
    tasks: list[tuple[str, str, callable, object]] = []
    for tenant in companies.get("greenhouse", []) or []:
        tasks.append(("greenhouse", tenant, greenhouse.fetch, tenant))
    for tenant in companies.get("lever", []) or []:
        tasks.append(("lever", tenant, lever.fetch, tenant))
    for tenant in companies.get("ashby", []) or []:
        tasks.append(("ashby", tenant, ashby.fetch, tenant))
    for cfg in companies.get("workday", []) or []:
        if isinstance(cfg, dict):
            tasks.append(("workday", cfg.get("tenant", "?"), workday.fetch, cfg))
    for query in companies.get("amazon_jobs", []) or []:
        tasks.append(("amazon_jobs", str(query), amazon_jobs.fetch, query))
    return tasks


def fetch_all(companies: dict) -> tuple[list[dict], dict]:
    """Concurrently fetch all configured sources. Returns (jobs, stats)."""
    tasks = build_fetch_tasks(companies)
    stats = {
        "greenhouse": {"ok": 0, "fail": 0, "jobs": 0},
        "lever": {"ok": 0, "fail": 0, "jobs": 0},
        "ashby": {"ok": 0, "fail": 0, "jobs": 0},
        "workday": {"ok": 0, "fail": 0, "jobs": 0},
        "amazon_jobs": {"ok": 0, "fail": 0, "jobs": 0},
    }
    all_jobs: list[dict] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fn, arg): (source, label) for (source, label, fn, arg) in tasks}
        for fut in as_completed(futures):
            source, label = futures[fut]
            try:
                jobs = fut.result()
                stats[source]["ok"] += 1
                stats[source]["jobs"] += len(jobs)
                all_jobs.extend(jobs)
            except Exception as e:
                log.warning("%s %s raised: %s", source, label, e)
                stats[source]["fail"] += 1

    return all_jobs, stats


def main() -> int:
    log.info("starting poll")
    start = datetime.now(timezone.utc)

    companies = load_yaml(CONFIG_DIR / "companies.yml")
    role = load_yaml(CONFIG_DIR / "role_config.yml")

    log.info(
        "config: %d greenhouse + %d lever + %d ashby + %d workday + %d amazon queries",
        len(companies.get("greenhouse", []) or []),
        len(companies.get("lever", []) or []),
        len(companies.get("ashby", []) or []),
        len(companies.get("workday", []) or []),
        len(companies.get("amazon_jobs", []) or []),
    )

    all_jobs, stats = fetch_all(companies)
    log.info("fetched %d raw jobs across all sources", len(all_jobs))

    scored: list[dict] = []
    dropped_age = 0
    max_age = int(role.get("max_posted_age_days", 0))
    for j in all_jobs:
        score, reasons = scorer.score_job(j, role)
        bucket = scorer.classify(score, role)
        if bucket == "drop":
            continue
        if max_age > 0 and not scorer.is_recent(j.get("posted_at", ""), max_age):
            dropped_age += 1
            continue
        j["score"] = score
        j["score_reasons"] = reasons
        j["bucket"] = bucket
        scored.append(j)
    log.info("after scoring: %d kept (score > 0); %d dropped as > %d days old",
             len(scored), dropped_age, max_age)

    seen = dedupe.load_seen(STATE_FILE)
    new_jobs, updated_seen = dedupe.diff_and_update(scored, seen)
    log.info("new jobs this run: %d (total tracked: %d)", len(new_jobs), len(updated_seen))

    current_keys = {f"{j['source']}:{j['company']}:{j['id']}" for j in scored}
    pruned_seen = dedupe.prune(updated_seen, current_keys, grace_days=30)
    dedupe.save_seen(STATE_FILE, pruned_seen)

    scored.sort(key=lambda j: (-j["score"], j.get("first_seen_at", "")))

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(scored, f, indent=2)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    meta = {
        "polled_at": start.isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "stats": stats,
        "total_jobs_fetched": len(all_jobs),
        "total_jobs_kept": len(scored),
        "new_this_run": len(new_jobs),
        "tracked_total": len(pruned_seen),
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    log.info("done in %.1fs — wrote %s and %s", elapsed, JOBS_FILE, META_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
