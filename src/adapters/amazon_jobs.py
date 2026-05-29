"""Amazon Jobs adapter.

Amazon does NOT expose a public Workday board — it runs its own system at
amazon.jobs. This adapter uses the public search.json endpoint:

  GET https://www.amazon.jobs/en/search.json?base_query=...&offset=N&result_limit=100

which returns {"hits": int, "jobs": [...]} where each job has id_icims, title,
normalized_location, job_path, posted_date, description, etc. No auth.

Config (companies.yml) is a list of search query strings, e.g.:
  amazon_jobs:
    - "technical program manager"
    - "program management"
Each query is fetched separately; the dedupe stage collapses overlaps by id.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
BASE = "https://www.amazon.jobs"
RESULT_LIMIT = 100
PAGE_CAP = 3            # up to 300 results per query
TIMEOUT = 20
DEFAULT_COUNTRY = "USA"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)",
    "Accept": "application/json",
}


def _parse_posted(s: str) -> str:
    """Amazon returns posted_date like 'May 29, 2026'. Convert to ISO."""
    s = (s or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.strptime(s, "%B %d, %Y").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ""


def fetch(query: str) -> list[Job]:
    """Fetch Amazon jobs matching a search query. Returns [] on any error."""
    if not query:
        return []
    results: list[Job] = []
    offset = 0
    for _ in range(PAGE_CAP):
        params = {
            "base_query": query,
            "offset": offset,
            "result_limit": RESULT_LIMIT,
            "sort": "recent",
            "country": DEFAULT_COUNTRY,
        }
        try:
            r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning("amazon_jobs %r returned HTTP %d", query, r.status_code)
                break
            data = r.json()
        except requests.RequestException as e:
            log.warning("amazon_jobs %r request failed: %s", query, e)
            break
        except ValueError as e:
            log.warning("amazon_jobs %r invalid JSON: %s", query, e)
            break

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        hits = int(data.get("hits", 0)) if isinstance(data, dict) else 0
        if not jobs:
            break

        for j in jobs:
            if not isinstance(j, dict):
                continue
            job_id = safe_str(j.get("id_icims") or j.get("id"))
            if not job_id:
                continue
            job_path = safe_str(j.get("job_path"))
            url = f"{BASE}{job_path}" if job_path else ""
            location = safe_str(j.get("normalized_location") or j.get("location"))
            results.append(Job(
                id=job_id,
                source="amazon_jobs",
                company="amazon",
                title=safe_str(j.get("title")),
                location=location,
                url=url,
                posted_at=_parse_posted(safe_str(j.get("posted_date"))),
                updated_at="",
            ))

        offset += RESULT_LIMIT
        if offset >= hits:
            break

    log.info("amazon_jobs %r: %d jobs", query, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the full JD HTML for a single Amazon job (jd_fetch strips it).

    The per-job .json endpoint returns 406, but the public HTML job page
    carries the description. Returns "" on failure.
    """
    url = job.get("url", "")
    if not url:
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("amazon_jobs detail %s HTTP %d", job.get("id"), r.status_code)
            return ""
        return r.text
    except requests.RequestException as e:
        log.warning("amazon_jobs detail %s failed: %s", job.get("id"), e)
        return ""
