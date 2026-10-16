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

from src.adapters import greenhouse, lever, ashby, workday, amazon_jobs, oracle_cloud, netflix, apple, google
from src import dedupe, scorer, discovery, jd_fetch
import re
from src.source_health import fetch_with_health

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
    for cfg in companies.get("oracle_cloud", []) or []:
        if isinstance(cfg, dict):
            tasks.append(("oracle_cloud", cfg.get("company", "?"), oracle_cloud.fetch, cfg))
    for query in companies.get("netflix", []) or []:
        tasks.append(("netflix", str(query), netflix.fetch, query))
    for query in companies.get("apple", []) or []:
        tasks.append(("apple", str(query), apple.fetch, query))
    for cfg in companies.get("google", []) or []:
        label = cfg.get("query", "?") if isinstance(cfg, dict) else str(cfg)
        tasks.append(("google", label, google.fetch, cfg))
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
        "oracle_cloud": {"ok": 0, "fail": 0, "jobs": 0},
        "netflix": {"ok": 0, "fail": 0, "jobs": 0},
        "apple": {"ok": 0, "fail": 0, "jobs": 0},
        "google": {"ok": 0, "fail": 0, "jobs": 0},
    }
    all_jobs: list[dict] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_with_health, source, label, fn, arg): (source, label) for (source, label, fn, arg) in tasks}
        for fut in as_completed(futures):
            source, label = futures[fut]
            try:
                jobs, health = fut.result()
                stats[source].setdefault("sources", []).append(health)
                if health["status"] in {"failed", "partial"}:
                    stats[source]["fail"] += 1
                else:
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
    previous = json.loads(JOBS_FILE.read_text(encoding="utf-8")) if JOBS_FILE.exists() else []
    cache_file = STATE_DIR / "discovery_cache.json"
    registry_file = STATE_DIR / "discovered_boards.json"
    def read_state(path, fallback):
        try: return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError): return fallback
    cache = read_state(cache_file, {})
    registry = read_state(registry_file, [])
    discovery_config = load_yaml(CONFIG_DIR / "discovery.yml") if (CONFIG_DIR / "discovery.yml").exists() else {}
    new_jobs, learned, discovery_health = discovery.discover(discovery_config, cache)
    previous_board_keys = {discovery.board_key(b) for b in registry}
    fresh_board_keys = {discovery.board_key(b) for b in learned} - previous_board_keys
    registry = list({discovery.board_key(b): b for b in registry + learned}.values())
    modules = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday}
    configured = {(source, str(arg.get("tenant")) if isinstance(arg, dict) else str(arg))
                  for source, _, _, arg in build_fetch_tasks(companies)}
    unconfigured = [b for b in registry if (b["source"], str(b["config"].get("tenant")) if isinstance(b["config"], dict) else b["config"]) not in configured]
    cap = int(discovery_config.get("max_discovered_boards_per_poll", 24))
    cursor = int(cache.get("board_cursor", 0))
    rotated = unconfigured[cursor % len(unconfigured):] + unconfigured[:cursor % len(unconfigured)] if unconfigured else []
    # Fetch newly discovered startup boards immediately, then resume rotation.
    rotated = [b for b in rotated if discovery.board_key(b) in fresh_board_keys] + [b for b in rotated if discovery.board_key(b) not in fresh_board_keys]
    if discovery_config.get("enabled", False):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_with_health, b["source"], discovery.board_key(b), modules[b["source"]].fetch, b["config"]) for b in rotated[:cap] if b["source"] in modules]
            for future in as_completed(futures):
                jobs, health = future.result()
                cache["board:" + health["label"]] = {"jobs": jobs, "health": health, "checked_at": start.isoformat()}
                new_jobs.extend(jobs); discovery_health.append(health)
        # Rotation must not remove jobs from discovered boards not polled this run.
        for board in rotated[cap:]:
            cached = cache.get("board:" + discovery.board_key(board), {})
            new_jobs.extend(cached.get("jobs", []))
            if cached:
                discovery_health.append({**cached["health"], "status": "cached", "checked_at": cached["checked_at"]})
        cache["board_cursor"] = cursor + cap
    all_jobs = discovery.merge_jobs(all_jobs, new_jobs)
    # A failed source is unknown, not proof that yesterday's postings closed.
    failures = [h for info in stats.values() for h in info.get("sources", []) if h["status"] in {"failed", "partial"}]
    failures += [h for h in discovery_health if h["status"] in {"failed", "partial"}]
    def affected(job):
        for h in failures:
            if h["source"] != job["source"]: continue
            if h["source"] in {"greenhouse", "lever", "ashby", "workday"}:
                label = h.get("label", "")
                try:
                    board = json.loads(label); cfg = board["config"]
                    label = cfg.get("tenant", "") if isinstance(cfg, dict) else cfg
                except (ValueError, TypeError, KeyError): pass
                if label != job["company"]: continue
            return True
        return False
    retained = [{**j, "open_status": "unverified_source_failure"} for j in previous if affected(j)]
    all_jobs = discovery.merge_jobs(all_jobs, retained)
    from src.harmonic_discovery import tag_jobs
    tag_jobs(all_jobs, cache)
    from src.portfolio_discovery import tag_jobs as tag_portfolio_jobs
    tag_portfolio_jobs(all_jobs, cache)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache), encoding="utf-8")
    registry_file.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    log.info("fetched %d raw jobs across all sources", len(all_jobs))

    # Bounded description enrichment prevents unusual titles disappearing before fit.
    candidates = []
    candidate_urls = set()
    for job in all_jobs:
        if job.get("description") or scorer.score_job(job, role)[0] > 0: continue
        if scorer._matches_any(job.get("title", ""), role.get("excluded_title_patterns", [])): continue
        if not re.search(r"technical.*program|engineering.*program|\btpm\b|portfolio|engineering.*operations", job.get("title", ""), re.I): continue
        if job.get("url") in candidate_urls: continue
        candidate_urls.add(job.get("url")); candidates.append(job)
    def enrich(job):
        try:
            body, status = discovery.cached_call(cache, "jd:" + job["url"], 6, lambda: jd_fetch.get_jd_text(job))
            if body: job["description"] = body
            return {"source": "description_check", "url": job["url"], "status": status if body else "failed"}
        except Exception:
            return {"source": "description_check", "url": job.get("url", ""), "status": "failed"}
    enriched = min(40, len(candidates))
    with ThreadPoolExecutor(max_workers=8) as executor:
        discovery_health.extend(executor.map(enrich, candidates[:40]))
    if len(candidates) > 40:
        discovery_health.append({"source": "description_check", "status": "limit_reached", "deferred": len(candidates) - 40})
    cache_file.write_text(json.dumps(cache), encoding="utf-8")
    scored: list[dict] = []
    seen_keys: set[str] = set()
    dropped_age = 0
    max_age = int(role.get("max_posted_age_days", 0))
    for j in all_jobs:
        # Overlapping queries (e.g. two amazon_jobs searches) return the same
        # job more than once; keep the first so jobs.json has one row per key.
        k = f"{j['source']}:{j['company']}:{j['id']}"
        if k in seen_keys:
            continue
        seen_keys.add(k)
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
        "discovery": discovery_health,
        "discovered_boards": len(registry),
        "description_checks": enriched,
        "description_check_limit": 40,
        "coverage_note": "Configured feeds plus employer-independent discovery; indexing, provider availability and pagination limit coverage.",
    }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    log.info("done in %.1fs — wrote %s and %s", elapsed, JOBS_FILE, META_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
